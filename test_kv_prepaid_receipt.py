#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_kv_prepaid_receipt.py — фіскальний чек передоплати для KIDDISVIT-замовлень сайту (18.11.2026, слово власника: «чек автоматично»; аудит #1697: kv-замовлення виключені з get_orders_ready_to_forward,
тож route_order() і його чек для них не викликались). Інваріанти: (1) оплачене kv-/kvu- замовлення сайту отримує РІВНО один чек через order_router._issue_kv_receipts; (2) повторний цикл чек НЕ дублює;
(3) збій Checkbox → чека нема, прапорця нема, алерт, наступний цикл повторює і успішно; (4) неоплачене / COD / не-сайт / не-kv замовлення не зачеплено; (5) сума ≠ site_charged_total → чек не видається (money-safety);
(6) route_pending_orders викликає _issue_kv_receipts. `python test_kv_prepaid_receipt.py` → exit 0/1."""
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
alerts = []
receipt_calls = []
mode = {"fail": False}


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def fake_receipt(**kw):
    receipt_calls.append(kw)
    if mode["fail"]:
        raise checkbox_client.CheckboxAPIError("Checkbox 422: тест")
    return {"id": f"rcpt-{len(receipt_calls)}"}


liqpay_client.LIQPAY_SANDBOX = False
checkbox_client.create_receipt = fake_receipt
ost.create_receipt = fake_receipt
orr.send_throttled_alert = lambda key, text, cooldown_sec=0: (alerts.append((key, text)) or True)
orr.send_telegram_message = lambda *a, **k: True


def new_order(oid, code="kv-201", pm="prepaid", platform="site", charged=900, price=900):
    orders_db.init_db()
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": platform, "payment_method": pm, "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": "380671112233",
                                   "email": "buyer@example.com", "np_branch": "Київ, Відділення №1", "np_warehouse_number": "1", "carrier": "nova_poshta",
                                   "items": [{"toysi_code": code, "name": "Тестовий товар KV", "qty": 1, "price": price}]})
        if charged is not None:
            c.execute("UPDATE orders SET site_charged_total=? WHERE internal_order_id=?", (charged, f"{platform}_{oid}"))
        c.commit()


def pay(order_id, payment_id="777001", amount=900):
    data = base64.b64encode(json.dumps({"status": "success", "order_id": order_id, "amount": amount, "currency": "UAH", "payment_id": payment_id}).encode()).decode()
    out = []
    h = api.Handler.__new__(api.Handler)
    h._read_form = lambda: {"data": data, "signature": liqpay_client._signature(data)}
    h._text = lambda code, text: out.append((code, text))
    api._NO_TELEGRAM = True
    h._handle_callback()
    return out[0][0]


def get(oid, platform="site"):
    with orders_db.get_connection() as c:
        return orders_db.get_order(c, f"{platform}_{oid}")


def cycle():
    with orders_db.get_connection() as c:
        orr._issue_kv_receipts(c)
        c.commit()


def reset():
    receipt_calls.clear(); alerts.clear()


# ── 1. щасливий шлях
new_order("PT-K1")
chk("колбек success зберіг оплату kv-замовлення", pay("PT-K1") == 200 and get("PT-K1")["payment_confirmed"] == 1)
with orders_db.get_connection() as c:
    chk("запит get_kv_orders_needing_receipt бачить оплачене kv-замовлення без чека", [o["internal_order_id"] for o in orders_db.get_kv_orders_needing_receipt(c)] == ["site_PT-K1"])
    chk("get_orders_ready_to_forward його НЕ бачить (у Toysi не йде — тому й потрібен окремий шлях чека)", "site_PT-K1" not in [o["internal_order_id"] for o in orders_db.get_orders_ready_to_forward(c)])
reset()
cycle()
o = get("PT-K1")
chk("оплачене kv-замовлення → РІВНО один чек, прапорець «чек видано» стоїть", len(receipt_calls) == 1 and o.get("checkbox_ettn_registered_at"))
chk("чек на повну суму позицій (900) з кодом позиції kv-201", abs(sum(float(g["price"]) * g.get("qty", 1) for g in receipt_calls[0]["goods"]) - 900) < 0.01 and receipt_calls[0]["goods"][0]["code"] == "kv-201")
cycle()
cycle()
chk("повторні цикли чек НЕ дублюють", len(receipt_calls) == 1)
with orders_db.get_connection() as c:
    chk("після чека замовлення зникає з get_kv_orders_needing_receipt", orders_db.get_kv_orders_needing_receipt(c) == [])

# ── 2. «Уцінка» kvu-
reset()
new_order("PT-K2", code="kvu-201", charged=700, price=700)
pay("PT-K2", "777002", 700)
cycle()
chk("kvu- («Уцінка») теж отримує чек", len(receipt_calls) == 1 and get("PT-K2").get("checkbox_ettn_registered_at"))

# ── 3. збій Checkbox → алерт, чека нема, повтор у наступному циклі
reset()
new_order("PT-K3")
pay("PT-K3", "777003")
mode["fail"] = True
cycle()
o3 = get("PT-K3")
chk("збій Checkbox: прапорця «чек видано» нема (замовлення лишається в черзі чека)", not o3.get("checkbox_ettn_registered_at"))
mode["fail"] = False
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET receipt_claim_at = NULL WHERE internal_order_id = 'site_PT-K3'")      # claim знімається при відомій відмові (4xx/відмова до POST) — як у бойовому коді
    c.commit()
n_before = len(receipt_calls)
cycle()
chk("після збою наступний цикл повторює і видає чек", len(receipt_calls) == n_before + 1 and get("PT-K3").get("checkbox_ettn_registered_at"))

# ── 4. не зачеплені: неоплачене, COD, не-сайт, не-kv
reset()
new_order("PT-K4")                                          # не оплачене
new_order("PT-K5", pm="cod")
new_order("PT-K6", code="1", charged=900)                   # не kv
new_order("PT-K7", platform="eva")
pay("PT-K6", "777006")
cycle()
chk("неоплачене kv, COD, не-kv, не-сайт → чек НЕ видається", len(receipt_calls) == 0)

# ── 5. money-safety: сума позицій ≠ зафіксованій
reset()
new_order("PT-K8", charged=850, price=900)
pay("PT-K8", "777008", 850)
cycle()
chk("сума позицій ≠ site_charged_total → чек НЕ видано (money-safety), алерт є", len(receipt_calls) == 0 and not get("PT-K8").get("checkbox_ettn_registered_at") and alerts)

# ── 6. підключення до циклу
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "order_router.py"), encoding="utf-8").read()
i = src.index("def route_pending_orders")
chk("route_pending_orders викликає _issue_kv_receipts (незалежно від наявності кандидатів на форвард)", "_issue_kv_receipts(conn, test_mode)" in src[i:i + 2500])

if F:
    print("\nFAIL:", len(F))
    sys.exit(1)
print("\nOK: усі перевірки пройдені")
