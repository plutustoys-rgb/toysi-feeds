#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_notify_new_order_wiring.py — підключення «🛒 Нове замовлення» (режим «лише замовлення», 12.10.2026) у трьох місцях, реальним кодом на тимчасовій БД:
 • orders_watcher.poll_once: рівно одне повідомлення на нове замовлення, повторний прогін не дублює, без ПІБ/телефону/адреси;
 • site_order_api._handle_order: COD → одне повідомлення при створенні; передоплата → нуль при створенні;
 • site_order_api._handle_callback: передоплата → одне при ПЕРШОМУ success, повторний колбек нуль; pending (wait_accept) нуль.
Мережа/Telegram замокані (requests.post). `python test_notify_new_order_wiring.py` → exit 0/1."""
import base64
import json
import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.pop("AUDIT_NO_TELEGRAM", None)
os.environ.pop("TELEGRAM_ORDERS_ONLY", None)
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
os.environ["LIQPAY_PUBLIC_KEY"] = "sandbox_i0000000000"
os.environ["LIQPAY_PRIVATE_KEY"] = "sandbox_testprivatekey"

import liqpay_client
import orders_db
import orders_watcher
import site_order_api as api
import telegram_notify as tn

tn.ALERTS_LOG_FILE = __import__("pathlib").Path(tempfile.mkdtemp()) / "telegram_alerts.md"
tn.ALERT_THROTTLE_FILE = tn.ALERTS_LOG_FILE.parent / ".t.json"
tn.TELEGRAM_BOT_TOKEN, tn.TELEGRAM_CHAT_ID = "T", "C"
posts = []


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True}


tn.requests.post = lambda url, json=None, timeout=0: (posts.append(json["text"]) or _Resp())
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


PII = ("Іван Петренко", "380671112233", "Київ", "Відділення", "Тестовий товар")

# ── orders_watcher.poll_once
raw = {"x": 1}
norm = {"order_id": "414634349", "platform": "prom", "payment_method": "cod", "payment_confirmed": False, "customer_name": "Іван Петренко", "phone": "380671112233",
        "np_branch": "Київ, Відділення №1", "items": [{"toysi_code": "1", "name": "Тестовий товар", "qty": 2, "price": 100}, {"toysi_code": "2", "name": "Інший", "qty": 1, "price": 55}]}
orders_watcher.fetch_new_orders_prom = lambda: [raw]
orders_watcher.fetch_new_orders_rozetka = lambda: []
orders_watcher.fetch_new_orders_eva = lambda: []
orders_watcher.normalize_order = lambda r: dict(norm)
posts.clear()
orders_watcher.poll_once()
chk("poll_once: рівно одне повідомлення про нове замовлення, сума 255, 2 поз.", posts == ["🛒 Нове замовлення · prom №414634349 · 255 грн · 2 поз. · накладений платіж"])
chk("poll_once: без ПІБ/телефону/адреси/назв товарів", not any(p in posts[0] for p in PII))
posts.clear()
orders_watcher.poll_once()
chk("poll_once: повторний прогін (замовлення вже в БД) НЕ дублює", not posts)

# ── сайт
api.price_map = lambda: {"1": {"name": "Тестовий товар", "price": 100}}
api.SITE_COD_PHONE_DAILY_LIMIT = 100


def post_order(pm, phone="+380671112233"):
    out = []
    h = api.Handler.__new__(api.Handler)
    h.command, h.path = "POST", "/api/order"
    h._read_json = lambda: {"items": [{"id": "1", "qty": 1}], "name": "Іван Петренко", "phone": phone, "city_name": "Київ", "warehouse_name": "Відділення №1",
                            "np_warehouse_number": "1", "payment_method": pm}
    h._json = lambda code, obj: out.append((code, obj))
    h._handle_order()
    return out[0]


posts.clear()
code, resp = post_order("cod")
chk("сайт COD: 200 і рівно одне повідомлення без ПІБ/телефону/адреси", code == 200 and len(posts) == 1 and posts[0].startswith("🛒 Нове замовлення · site №PT-") and not any(p in posts[0] for p in PII))
chk("сайт COD: у повідомленні сума 100 грн, 1 поз., накладений платіж", "100 грн · 1 поз. · накладений платіж" in posts[0])

posts.clear()
code, resp = post_order("prepaid", phone="+380671112244")
oid = resp["order_id"]
chk("сайт передоплата: при створенні повідомлення НЕМАЄ (неоплачене не «надходження»)", code == 200 and not posts)


def callback(status, order_id, amount=100):
    data = base64.b64encode(json.dumps({"status": status, "order_id": order_id, "amount": amount, "currency": "UAH"}).encode()).decode()
    out = []
    h = api.Handler.__new__(api.Handler)
    h._read_form = lambda: {"data": data, "signature": liqpay_client._signature(data)}
    h._text = lambda code, text: out.append((code, text))
    h._handle_callback()
    return out[0]


posts.clear()
callback("wait_accept", oid)
chk("колбек wait_accept: повідомлення про замовлення НЕМАЄ (оплата не підтверджена)", not posts)
code, _ = callback("success", oid)
chk("колбек success (перше підтвердження): рівно одне повідомлення, передоплата", code == 200 and len(posts) == 1 and f"site №{oid}" in posts[0] and posts[0].endswith("передоплата"))
posts.clear()
callback("success", oid)
chk("повторний колбек success НЕ дублює (атомарний перехід 0→1)", not posts)
with orders_db.get_connection() as _c:
    chk("orders_db.mark_payment_confirmed: повертає 0 для вже підтвердженого", orders_db.mark_payment_confirmed(_c, f"site_{oid}") == 0)

# ── збій відправки не ламає прийом замовлення
tn.requests.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("мережа лягла"))
code, _ = post_order("cod", phone="+380671112255")
chk("сайт COD: збій Telegram не ламає прийом (200)", code == 200)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
