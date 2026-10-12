#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_liqpay_callback.py — ланцюг LiqPay-колбек → payment_confirmed → черга форварду (12.10.2026).
Першоджерело статусів: https://www.liqpay.ua/en/doc/api/callback («wait_accept — Кошти з клієнта списані, але магазин ще не пройшов перевірку.
Якщо магазин не пройде активацію протягом 60 днів, платежі будуть автоматично скасовані»). Раніше wait_accept рахувався оплатою: товар поїхав би покупцю,
а платіж могли б скасувати. Інваріанти: лише success (і sandbox у пісочниці) підтверджує замовлення; pending/reversed — НЕ підтверджують, але алертять
власника; валюта ≠ UAH або відсутня, розбіжність суми, чужий підпис, sandbox у бою — 400 без зміни стану; повторний success ідемпотентний.
Мережа/Telegram замокані; orders.db тимчасова. `python test_liqpay_callback.py` → exit 0/1."""
import base64
import json
import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.pop("AUDIT_NO_TELEGRAM", None)          # хочемо перевірити саме алерт (він замокано нижче)
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
os.environ["LIQPAY_PUBLIC_KEY"] = "sandbox_i0000000000"
os.environ["LIQPAY_PRIVATE_KEY"] = "sandbox_testprivatekey"

import liqpay_client
import orders_db
import site_order_api as api
import telegram_notify

alerts = []
telegram_notify.send_throttled_alert = lambda key, text, cooldown_sec=0: (alerts.append((key, text)) or True)
telegram_notify.send_telegram_message = lambda *a, **k: (alerts.append(("plain", a)) or True)

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def signed(status, order_id="PT-T1", amount=100, currency="UAH"):
    data = base64.b64encode(json.dumps({"status": status, "order_id": order_id, "amount": amount, "currency": currency}).encode()).decode()
    return {"data": data, "signature": liqpay_client._signature(data)}


def callback(form):
    out = []
    h = api.Handler.__new__(api.Handler)
    h._read_form = lambda: form
    h._text = lambda code, text: out.append((code, text))
    h._handle_callback()
    return out[0]


def state(oid="site_PT-T1"):
    with orders_db.get_connection() as c:
        o = orders_db.get_order(c, oid)
        ready = [x["internal_order_id"] for x in orders_db.get_orders_ready_to_forward(c)]
    return o["payment_confirmed"], oid in ready


def new_order(oid="PT-T1"):
    orders_db.init_db()
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": "site", "payment_method": "prepaid", "payment_confirmed": 0, "customer_name": "Іван Петренко",
                                   "phone": "380671112233", "np_branch": "Київ, Відділення №1", "items": [{"toysi_code": "1", "name": "x", "qty": 1, "price": 100}]})
        c.execute("UPDATE orders SET site_charged_total = 100 WHERE internal_order_id = ?", (f"site_{oid}",))
        c.commit()


new_order()
chk("до оплати: payment_confirmed=0, у черзі форварду нема", state() == (0, False))

# ── не оплачено / pending / reversed
for st in ("wait_accept", "hold_wait", "wait_secure", "wait_compensation", "wait_card"):
    alerts.clear()
    code, _ = callback(signed(st))
    chk(f"{st}: 200, замовлення НЕ підтверджено, у форвард не йде, власника сповіщено", code == 200 and state() == (0, False) and len(alerts) == 1 and st in alerts[0][1])
alerts.clear()
code, _ = callback(signed("failure"))
chk("failure: 200, без змін, без алерту (звичайна невдала оплата)", code == 200 and state() == (0, False) and not alerts)
code, _ = callback(signed("subscribed"))
chk("subscribed (підписка, не оплата) НЕ підтверджує замовлення", code == 200 and state() == (0, False))
alerts.clear()
code, _ = callback(signed("reversed"))
chk("reversed: 200, алерт про повернення з підказкою по чеку", code == 200 and len(alerts) == 1 and "повернення чека" in alerts[0][1])

# ── відмови без зміни стану
code, _ = callback({"data": signed("success")["data"], "signature": "підроблений"})
chk("чужий підпис → 400, стан не змінено", code == 400 and state() == (0, False))
code, txt = callback(signed("success", amount=99))
chk("сума ≠ зафіксованій → 400", code == 400 and "amount mismatch" in txt and state() == (0, False))
code, txt = callback(signed("success", currency="USD"))
chk("валюта USD → 400 currency mismatch", code == 400 and "currency mismatch" in txt and state() == (0, False))
code, txt = callback(signed("success", currency=None))
chk("валюта відсутня → 400", code == 400 and "currency mismatch" in txt and state() == (0, False))
liqpay_client.LIQPAY_SANDBOX = False
code, txt = callback(signed("sandbox"))
chk("sandbox-статус у бойовому режимі → 400, стан не змінено", code == 400 and "sandbox" in txt and state() == (0, False))
liqpay_client.LIQPAY_SANDBOX = True
code, txt = callback(signed("success", order_id="PT-ЩО-НЕМА"))
chk("невідоме замовлення → 404", code == 404)

# ── успіх
code, _ = callback(signed("success", currency="uah"))
chk("success (валюта «uah» без регістру): payment_confirmed=1 і замовлення у черзі форварду", code == 200 and state() == (1, True))
code, _ = callback(signed("success"))
chk("повторний success ідемпотентний", code == 200 and state() == (1, True))

# ── sandbox у пісочниці
new_order("PT-T2")
code, _ = callback(signed("sandbox", order_id="PT-T2"))
chk("sandbox у пісочниці: оплату зафіксовано (payment_confirmed=1), але замовлення НЕ йде у форвард (status sandbox_test: тестовий платіж не має потрапляти в Toysi й під чек)", code == 200 and state("site_PT-T2") == (1, False))

# ── словник статусів
chk("PAID_STATUSES = лише success + sandbox", liqpay_client.PAID_STATUSES == {"success", "sandbox"})
chk("wait_accept у PENDING, не в PAID", "wait_accept" in liqpay_client.PENDING_STATUSES and "wait_accept" not in liqpay_client.PAID_STATUSES)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
