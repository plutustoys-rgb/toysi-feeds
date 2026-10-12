#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_liqpay_golive_safety.py — безпека запуску LiqPay (12.10.2026: власник повідомив, що оплату погодили; є ТЕСТОВІ й БОЙОВІ ключі).
Інваріанти: (1) тестовий (sandbox) платіж фіксується, але НЕ йде в Toysi й під фіскальний чек (status sandbox_test, поза get_orders_ready_to_forward), сповіщення власнику
позначене «ТЕСТ»; (2) розбіжність режиму й ключів (бойовий режим з тестовими ключами, тестовий з бойовими, публічний/приватний з різних наборів) → чекаут НЕ будується, повідомлення
для покупця «менеджер звʼяжеться», алерт у журнал; узгоджені режими працюють; (3) справжній success у бойовому режимі йде у форвард як і раніше.
Мережа/Telegram замокані; БД тимчасова. `python test_liqpay_golive_safety.py` → exit 0/1."""
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.pop("AUDIT_NO_TELEGRAM", None)
os.environ.pop("TELEGRAM_ORDERS_ONLY", None)
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
os.environ["LIQPAY_PUBLIC_KEY"] = "sandbox_i0000000000"
os.environ["LIQPAY_PRIVATE_KEY"] = "sandbox_testprivatekey"

import liqpay_client as lq
import orders_db
import site_order_api as api
import telegram_notify as tn

tn.ALERTS_LOG_FILE = Path(tempfile.mkdtemp()) / "telegram_alerts.md"
tn.ALERT_THROTTLE_FILE = tn.ALERTS_LOG_FILE.parent / ".t.json"
tn.TELEGRAM_BOT_TOKEN, tn.TELEGRAM_CHAT_ID = "T", "C"
posts = []


class _R:
    def raise_for_status(self): pass
    def json(self): return {"ok": True}


tn.requests.post = lambda url, json=None, timeout=0: (posts.append(json["text"]) or _R())
api._NO_TELEGRAM = False
orders_db.init_db()
api.price_map = lambda: {"1": {"name": "Тестовий товар", "price": 100}}
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def set_keys(pub, prv, sandbox):
    lq.LIQPAY_PUBLIC_KEY, lq.LIQPAY_PRIVATE_KEY, lq.LIQPAY_SANDBOX = pub, prv, sandbox


# ── (2) mode_problem
set_keys("sandbox_i1", "sandbox_k1", True)
chk("тестовий режим + тестові ключі → без проблем", lq.mode_problem() == "")
set_keys("i123456", "private123", False)
chk("бойовий режим + бойові ключі → без проблем", lq.mode_problem() == "")
set_keys("sandbox_i1", "sandbox_k1", False)
chk("БОЙОВИЙ режим + ТЕСТОВІ ключі → проблема", "ТЕСТОВІ" in lq.mode_problem())
set_keys("i123456", "private123", True)
chk("ТЕСТОВИЙ режим + БОЙОВІ ключі → проблема", "БОЙОВІ" in lq.mode_problem())
set_keys("sandbox_i1", "private123", True)
chk("публічний тестовий, приватний бойовий → проблема «різних наборів»", "різних наборів" in lq.mode_problem())
set_keys("", "", True)
chk("ключів нема → проблеми нема (це «не налаштовано»)", lq.mode_problem() == "")


def post_order(pm="prepaid", phone="+380671112233"):
    out = []
    h = api.Handler.__new__(api.Handler)
    h.command, h.path = "POST", "/api/order"
    h._read_json = lambda: {"items": [{"id": "1", "qty": 1}], "name": "Іван Петренко", "phone": phone, "city_name": "Київ", "warehouse_name": "Відділення №1",
                            "np_warehouse_number": "1", "payment_method": pm}
    h._json = lambda code, obj: out.append((code, obj))
    h._handle_order()
    return out[0]


def callback(status, order_id, currency="UAH"):
    data = base64.b64encode(json.dumps({"status": status, "order_id": order_id, "amount": 100, "currency": currency, "payment_id": 777}).encode()).decode()
    out = []
    h = api.Handler.__new__(api.Handler)
    h._read_form = lambda: {"data": data, "signature": lq._signature(data)}
    h._text = lambda code, text: out.append((code, text))
    h._handle_callback()
    return out[0]


# ── чекаут: розбіжність → не будується; узгоджено → будується
set_keys("sandbox_i1", "sandbox_k1", False)          # бойовий режим з тестовими ключами
posts.clear()
code, resp = post_order(phone="+380671110001")
chk("розбіжність: власник отримує в Telegram «Нове замовлення … ОЧІКУЄ ОПЛАТУ, чекаут недоступний» (інакше покупець лишився б без відповіді)", len(posts) == 1 and posts[0].startswith("🛒 Нове замовлення") and "ОЧІКУЄ ОПЛАТУ" in posts[0])
chk("розбіжність режиму/ключів: замовлення прийнято, але чекаут НЕ побудовано (liqpay=None) і покупцю кажуть «менеджер звʼяжеться»", code == 200 and resp["liqpay"] is None and "менеджер" in resp["message"])
set_keys("sandbox_i1", "sandbox_k1", True)
code, resp = post_order(phone="+380671110002")
chk("тестовий режим + тестові ключі: чекаут побудовано (sandbox=true)", code == 200 and resp["liqpay"] and resp["liqpay"]["sandbox"] is True)
oid_test = resp["order_id"]

# ── (1) тестовий платіж: зафіксовано, але не у форвард; сповіщення з позначкою ТЕСТ
posts.clear()
code, _ = callback("sandbox", oid_test)
with orders_db.get_connection() as c:
    o = orders_db.get_order(c, f"site_{oid_test}")
    ready = [x["internal_order_id"] for x in orders_db.get_orders_ready_to_forward(c)]
chk("sandbox-оплата: payment_confirmed=1, status=sandbox_test, у черзі форварду НЕМАЄ", code == 200 and o["payment_confirmed"] == 1 and o["status"] == "sandbox_test" and f"site_{oid_test}" not in ready)
chk("сповіщення власнику: одне, з позначкою «ТЕСТ LiqPay … у Toysi НЕ передається, чек НЕ видається»", len(posts) == 1 and "ТЕСТ LiqPay" in posts[0] and posts[0].startswith("🛒 Нове замовлення"))
chk("тестове замовлення: у Telegram видно, що пішло б у Toysi (позиції, доставка, оплата), без телефону/ПІБ", "Що пішло б у Toysi" in posts[0] and "• " in posts[0] and "Доставка:" in posts[0] and "Оплата:" in posts[0] and "+380" not in posts[0])

# ── (3) справжній success у бойовому режимі йде у форвард
set_keys("i123456", "private123", False)
code, resp = post_order(phone="+380671110003")
oid_live = resp["order_id"]
posts.clear()
code, _ = callback("success", oid_live)
with orders_db.get_connection() as c:
    o = orders_db.get_order(c, f"site_{oid_live}")
    ready = [x["internal_order_id"] for x in orders_db.get_orders_ready_to_forward(c)]
chk("бойовий success: payment_confirmed=1, у черзі форварду Є, сповіщення БЕЗ позначки ТЕСТ",
    code == 200 and o["payment_confirmed"] == 1 and o["status"] != "sandbox_test" and f"site_{oid_live}" in ready and len(posts) == 1 and "ТЕСТ" not in posts[0])

# ── щоденний звіт: тестова оплата не «застрягло»
import daily_report
with orders_db.get_connection() as c:
    c.execute("UPDATE orders SET created_at = '2026-01-01T00:00:00' WHERE internal_order_id = ?", (f"site_{oid_test}",))
    c.commit()
    sec = daily_report._open_orders_detail_section(c)
chk("щоденний звіт: тестове (sandbox_test) замовлення НЕ показується як «застрягло»", oid_test not in sec)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
