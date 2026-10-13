#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_prepaid_receipt.py — фіскальний чек онлайн-оплати сайту (рішення головного бухгалтера 12.10.2026, КОДВ_CHANNEL: чек SELL/CASHLESS одразу після success-колбека LiqPay
і ДО передачі в Toysi/НП; у тексті чека номер платежу LiqPay і номер замовлення; чек надсилається електронно). Ланцюг: колбек → liqpay_payment_id → order_router.route_order →
create_receipt → submit_order. Інваріанти: порядок «чек, потім форвард»; footer/сума/телефон/e-mail; збій чека = замовлення НЕ передається + алерт, наступний цикл повторює;
розбіжність суми з site_charged_total = чек не видається; ідемпотентність (трекер не дублює чек); не-сайт/COD/test_mode не зачеплені. Мережа замокана, БД тимчасова.
`python test_site_prepaid_receipt.py` → exit 0/1."""
import base64
import json
import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
os.environ["LIQPAY_PUBLIC_KEY"] = "sandbox_i0000000000"
os.environ["LIQPAY_PRIVATE_KEY"] = "sandbox_testprivatekey"

import checkbox_client
import liqpay_client
import order_router as orr
import order_status_tracker as ost
import orders_db
import site_order_api as api

F = []
events = []
alerts = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


# ── моки
receipt_calls = []
receipt_mode = {"fail": False}


def fake_receipt(**kw):
    events.append("receipt")
    receipt_calls.append(kw)
    if receipt_mode["fail"]:
        raise checkbox_client.CheckboxAPIError("Checkbox 422: тест")
    return {"id": "rcpt-1"}


liqpay_client.LIQPAY_SANDBOX = False      # бойовий режим для тесту (за замовчуванням sandbox і реальний чек там не видається — окремий тест нижче)
checkbox_client.create_receipt = fake_receipt
ost.create_receipt = fake_receipt
submits = []
orr.submit_order = lambda toysi_order, test_mode=False: (events.append("submit") or submits.append(toysi_order) or
                                                          {"accepted": True, "toysi_order_id": 777, "is_duplicate": False, "should_retry": False, "response_code": 1, "message": "ok"})
orr.send_purchase_event = lambda o: None
orr.settlement_raion = lambda *a, **k: ""
orr.count_cities_named = lambda *a, **k: 1
orr.send_throttled_alert = lambda key, text, cooldown_sec=0: (alerts.append((key, text)) or True)
orr.send_telegram_message = lambda *a, **k: True
CATALOG = {"1": {"stock": 10, "name": "Тестовий товар"}}


def new_order(oid, pm="prepaid", platform="site", charged=100, email="buyer@example.com"):
    orders_db.init_db()
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": platform, "payment_method": pm, "payment_confirmed": 0, "customer_name": "Іван Петренко",
                                   "phone": "380671112233", "email": email, "np_branch": "Київ, Відділення №1", "np_warehouse_number": "1", "carrier": "nova_poshta",
                                   "items": [{"toysi_code": "1", "name": "Тестовий товар", "qty": 1, "price": 100}]})
        if charged is not None:
            c.execute("UPDATE orders SET site_charged_total=? WHERE internal_order_id=?", (charged, f"{platform}_{oid}"))
        c.commit()


def callback(order_id, payment_id="123456", status="success"):
    data = base64.b64encode(json.dumps({"status": status, "order_id": order_id, "amount": 100, "currency": "UAH", "payment_id": payment_id}).encode()).decode()
    out = []
    h = api.Handler.__new__(api.Handler)
    h._read_form = lambda: {"data": data, "signature": liqpay_client._signature(data)}
    h._text = lambda code, text: out.append((code, text))
    api._NO_TELEGRAM = True
    h._handle_callback()
    return out[0]


def get(oid, platform="site"):
    with orders_db.get_connection() as c:
        return orders_db.get_order(c, f"{platform}_{oid}")


def route(oid, platform="site", test_mode=False):
    with orders_db.get_connection() as c:
        o = orders_db.get_order(c, f"{platform}_{oid}")
        orr.route_order(c, o, test_mode=test_mode, toysi_catalog=CATALOG)
        c.commit()


def reset():
    events.clear(); receipt_calls.clear(); submits.clear(); alerts.clear()


# ── 1. щасливий шлях: колбек → payment_id → чек ДО форварду
new_order("PT-R1")
code, _ = callback("PT-R1", "123456")
chk("колбек success зберіг payment_id LiqPay", code == 200 and get("PT-R1")["liqpay_payment_id"] == "123456")
reset()
route("PT-R1")
chk("порядок: спершу чек, потім передача в Toysi", events == ["receipt", "submit"])
rc = receipt_calls[0]
chk("чек: CASHLESS, сума 100, номер замовлення в order_id", rc["payment_type"] == "CASHLESS" and rc["total_amount"] == 100 and rc["order_id"] == "site_PT-R1")
chk("чек: footer містить номер платежу LiqPay і номер замовлення", rc["footer"] == "LiqPay payment_id 123456; замовлення PT-R1")
chk("чек: header = назва магазину (PlutusToys · plutustoys.com.ua)", rc.get("header") == "PlutusToys · plutustoys.com.ua")
chk("чек: телефон і e-mail покупця передані для електронного надсилання", rc["customer_phone"] == "380671112233" and rc["customer_email"] == "buyer@example.com")
chk("чек: позиції з ціною й кількістю", rc["goods"] == [{"code": "1", "name": "Тестовий товар", "price": 100, "qty": 1}])
o = get("PT-R1")
chk("прапорець чека виставлено, замовлення передане в Toysi", o["checkbox_ettn_registered_at"] and o["forwarded_to_toysi_at"] and o["status"] == "forwarded_to_supplier")

# ── 2. трекер не дублює чек
n_before = len(receipt_calls)
with orders_db.get_connection() as c:
    ost._maybe_issue_receipt(c, get("PT-R1"), "20450000000000", "shipped")
chk("ідемпотентність: трекер після нашого чека НЕ виписує другий", len(receipt_calls) == n_before)

# ── 3. збій чека: не передаємо, алертимо, наступний цикл повторює
new_order("PT-R2")
callback("PT-R2", "223344")
reset()
receipt_mode["fail"] = True
route("PT-R2")
o = get("PT-R2")
chk("збій чека: замовлення НЕ передано в Toysi", "submit" not in events and not o["forwarded_to_toysi_at"])
chk("збій чека: прапорець не виставлено, алерт самодіагностичний", not o["checkbox_ettn_registered_at"] and len(alerts) == 1 and "НЕ передається в Toysi" in alerts[0][1] and "site_PT-R2" in alerts[0][1])
receipt_mode["fail"] = False
reset()
route("PT-R2")
chk("наступний цикл: чек видано один раз, потім передача", events == ["receipt", "submit"] and get("PT-R2")["checkbox_ettn_registered_at"])

# ── 4. розбіжність суми
new_order("PT-R3", charged=99)
with orders_db.get_connection() as c:
    orders_db.mark_payment_confirmed(c, "site_PT-R3"); c.commit()
reset()
route("PT-R3")
chk("сума позицій ≠ site_charged_total: чек НЕ видано, замовлення НЕ передано, алерт", "receipt" not in events and "submit" not in events and len(alerts) == 1 and "money-safety" in alerts[0][1])

# ── 5. не наш випадок
new_order("PT-R4")      # передоплата, оплата НЕ підтверджена: крок нічого не робить
reset()
with orders_db.get_connection() as c:
    chk("передоплата без підтвердженої оплати: _ensure повертає True без чека", orr._ensure_site_prepaid_receipt(c, get("PT-R4")) is True and not receipt_calls)
new_order("PT-C1", pm="cod")
with orders_db.get_connection() as c:
    chk("site COD: крок не чіпає", orr._ensure_site_prepaid_receipt(c, get("PT-C1")) is True and not receipt_calls)
new_order("414634349", pm="prepaid", platform="prom")
with orders_db.get_connection() as c:
    orders_db.mark_payment_confirmed(c, "prom_414634349"); c.commit()
    chk("Prom передоплата: крок не чіпає (її чек видає трекер як раніше)", orr._ensure_site_prepaid_receipt(c, get("414634349", "prom")) is True and not receipt_calls)
new_order("PT-R5")
callback("PT-R5", "556677")
with orders_db.get_connection() as c:
    chk("test_mode: чек не видається", orr._ensure_site_prepaid_receipt(c, get("PT-R5"), test_mode=True) is True and not receipt_calls)

# ── 6. без e-mail/телефону чек усе одно видається
new_order("PT-R6", email=None)
callback("PT-R6", "667788")
reset()
route("PT-R6")
chk("без e-mail: чек видано, customer_email=None", events == ["receipt", "submit"] and receipt_calls[0]["customer_email"] in (None, ""))

# ── 8. аудит #698: гонка, збій прапорця, невідомий результат, форвард упав, sandbox, site_charged_total=None
import sqlite3


def stale(oid):
    return get(oid)       # «знімок» без прапорця, зроблений ДО того, як інший процес видав чек


new_order("PT-G1")
callback("PT-G1", "900001")
snap1, snap2 = stale("PT-G1"), stale("PT-G1")
reset()
with orders_db.get_connection() as c:
    r1 = orr._ensure_site_prepaid_receipt(c, snap1)
with orders_db.get_connection() as c:
    r2 = orr._ensure_site_prepaid_receipt(c, snap2)     # застарілий знімок: прапорець уже в БД
chk("гонка: два процеси зі застарілими знімками → РІВНО один чек, обидва йдуть далі", len(receipt_calls) == 1 and r1 is True and r2 is True)

new_order("PT-G2")
callback("PT-G2", "900002")
reset()
with orders_db.get_connection() as c:
    orders_db.claim_receipt(c, "site_PT-G2"); c.commit()       # інший процес уже тримає claim
    chk("claim іншого процесу: чек НЕ видається, повертає False, алерт про claim", orr._ensure_site_prepaid_receipt(c, get("PT-G2")) is False and not receipt_calls and len(alerts) == 1 and "claim" in alerts[0][1])

new_order("PT-G3")
callback("PT-G3", "900003")
reset()


class _Unknown(checkbox_client.CheckboxAPIError):
    maybe_created = True


def fake_unknown(**kw):
    receipt_calls.append(kw)
    raise _Unknown("таймаут POST /receipts/sell")


checkbox_client.create_receipt = fake_unknown
route("PT-G3")
chk("невідомий результат (таймаут): чек був у спробі, замовлення НЕ передано, алерт «МІГ бути створений»", len(receipt_calls) == 1 and not get("PT-G3")["forwarded_to_toysi_at"] and any("МІГ бути створений" in t for _, t in alerts))
route("PT-G3")
chk("невідомий результат: автоматичного повтору НЕМАЄ (claim лишився), другого чека не буде", len(receipt_calls) == 1 and get("PT-G3")["receipt_claim_at"])
checkbox_client.create_receipt = fake_receipt

new_order("PT-G4")
callback("PT-G4", "900004")
reset()
real_mark, real_sleep = orr.mark_checkbox_ettn_registered, orr.time.sleep


def bad_mark(*a, **k):
    raise sqlite3.OperationalError("database is locked")


orr.mark_checkbox_ettn_registered = bad_mark
orr.time.sleep = lambda s: None
route("PT-G4")
orr.mark_checkbox_ettn_registered, orr.time.sleep = real_mark, real_sleep
chk("збій запису прапорця ПІСЛЯ чека: чек є (1), замовлення продовжило шлях, алерт «ВИДАНО, прапорець не записано»",
    len(receipt_calls) == 1 and "submit" in events and any("ВИДАНО" in t and "прапорець" in t for _, t in alerts))
with orders_db.get_connection() as c:
    chk("після збою прапорця наступна спроба НЕ видає другий чек (claim)", orr._ensure_site_prepaid_receipt(c, get("PT-G4")) is False and len(receipt_calls) == 1)

new_order("PT-G5")
callback("PT-G5", "900005")
reset()
orr.submit_order, real_submit = (lambda toysi_order, test_mode=False: {"accepted": False, "should_retry": True, "response_code": 3, "message": "тимчасово", "toysi_order_id": None, "is_duplicate": False}), orr.submit_order
route("PT-G5")
route("PT-G5")
chk("чек видано, форвард упав двічі: чек РІВНО один (раннє повернення за прапорцем)", len(receipt_calls) == 1)
orr.submit_order = real_submit

new_order("PT-G6", charged=None)
with orders_db.get_connection() as c:
    orders_db.mark_payment_confirmed(c, "site_PT-G6"); c.commit()
reset()
route("PT-G6")
chk("site_charged_total=None: чек НЕ видано (fail-closed), замовлення не передано", not receipt_calls and "submit" not in events)

new_order("PT-G7")
callback("PT-G7", "900007")
reset()
liqpay_client.LIQPAY_SANDBOX = True
route("PT-G7")
chk("sandbox LiqPay: реальний фіскальний чек НЕ видається", not receipt_calls)
os.environ["SITE_RECEIPT_IN_SANDBOX"] = "1"
with orders_db.get_connection() as c:
    orr._ensure_site_prepaid_receipt(c, get("PT-G7"))
chk("SITE_RECEIPT_IN_SANDBOX=1 вмикає чек свідомо", len(receipt_calls) == 1)
os.environ.pop("SITE_RECEIPT_IN_SANDBOX", None)
liqpay_client.LIQPAY_SANDBOX = False

# справжня гонка: ОБИДВА процеси прочитали «чека нема» ДО того, як будь-хто взяв claim (інтерливінг між читанням і UPDATE) — друге виписування має провалити атомарний claim
new_order("PT-G8")
callback("PT-G8", "900008")
reset()
real_state = orr.get_receipt_state
orr.get_receipt_state = lambda conn, oid: (None, None)
with orders_db.get_connection() as c:
    ra = orr._ensure_site_prepaid_receipt(c, get("PT-G8"))
with orders_db.get_connection() as c:
    rb = orr._ensure_site_prepaid_receipt(c, get("PT-G8"))
orr.get_receipt_state = real_state
chk("інтерливінг читання→claim: чек РІВНО один, другий процес отримує False (атомарний claim)", len(receipt_calls) == 1 and ra is True and rb is False)

# ── 9. аудит #698 р.2: трекер НЕ виписує чек передоплати сайту (ні при claim без прапорця, ні після sandbox-пропуску роутера); маркетплейсна передоплата як раніше
new_order("PT-T1")
callback("PT-T1", "910001")
with orders_db.get_connection() as c:
    orders_db.claim_receipt(c, "site_PT-T1"); c.commit()       # claim є, прапорця «чек видано» нема (збій запису прапорця)
reset()
with orders_db.get_connection() as c:
    ost._maybe_issue_receipt(c, get("PT-T1"), "20450000000001", "shipped")
chk("трекер: site prepaid з claim без прапорця → ДРУГИЙ чек НЕ видається", not receipt_calls)
new_order("PT-T2")
callback("PT-T2", "910002")
reset()
liqpay_client.LIQPAY_SANDBOX = True
route("PT-T2")                      # роутер у sandbox чек не видав і передав замовлення
liqpay_client.LIQPAY_SANDBOX = False
with orders_db.get_connection() as c:
    ost._maybe_issue_receipt(c, get("PT-T2"), "20450000000002", "shipped")
chk("sandbox: роутер не видав чек, трекер теж НЕ видає реальний чек на тестовий платіж", not receipt_calls and "submit" in events)
new_order("429000001", platform="prom")
with orders_db.get_connection() as c:
    orders_db.mark_payment_confirmed(c, "prom_429000001"); c.commit()
reset()
with orders_db.get_connection() as c:
    ost._maybe_issue_receipt(c, get("429000001", "prom"), "20450000000003", "shipped")
chk("Prom передоплата: трекер видає чек як раніше (без змін)", len(receipt_calls) == 1 and receipt_calls[0]["payment_type"] == "CASHLESS")

# ── 7. checkbox_client: footer і e-mail потрапляють у тіло запиту
posted = {}


class _R:
    def raise_for_status(self): pass
    def json(self): return {"id": "x"}


import requests
checkbox_client_real = __import__("importlib").reload(checkbox_client)
checkbox_client_real._require_receipt_credentials = lambda: None
checkbox_client_real._authenticate_cashier = lambda: "tok"
checkbox_client_real._ensure_shift_open = lambda token: None
checkbox_client_real.requests.post = lambda url, headers=None, json=None, timeout=0: (posted.update(json) or _R())
checkbox_client_real.create_receipt(goods=[{"code": "1", "name": "Т", "price": 100, "qty": 1}], payment_type="CASHLESS", total_amount=100, order_id="site_X",
                                    customer_phone="0671112233", footer="LiqPay payment_id 1; замовлення X", customer_email="buyer@example.com")
chk("тіло Checkbox: footer, delivery.phone і delivery.emails", posted.get("footer") == "LiqPay payment_id 1; замовлення X" and posted["delivery"] == {"phone": "+380671112233", "emails": ["buyer@example.com"]})
posted.clear()
checkbox_client_real.create_receipt(goods=[{"code": "1", "name": "Т", "price": 100, "qty": 1}], payment_type="CASH", total_amount=100, customer_email="не-пошта")
chk("некоректний e-mail ігнорується, без footer поле не додається", "footer" not in posted and "delivery" not in posted)
checkbox_client_real.create_receipt(goods=[{"code": "1", "name": "Т", "price": 100, "qty": 1}], payment_type="CASHLESS", total_amount=100, order_id="site_X", header="PlutusToys · plutustoys.com.ua")
chk("тіло Checkbox: header потрапляє в запит; без header поля нема", posted.get("header") == "PlutusToys · plutustoys.com.ua")
posted.clear()
checkbox_client_real.create_receipt(goods=[{"code": "1", "name": "Т", "price": 100, "qty": 1}], payment_type="CASH", total_amount=100)
chk("без header поле header не додається", "header" not in posted)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
