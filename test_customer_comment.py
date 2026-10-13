#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_customer_comment.py — коментар покупця з чекауту сайту (запит власника 13.10: «чому нема поля коментаря?»).
Інваріанти: (1) колонка customer_comment є в новій БД І мігрується в стару (без колонки); (2) insert_order зберігає очищений коментар, без коментаря → NULL, старі виклики без поля не ламаються;
(3) clean_customer_comment: керуючі символи/переноси/зайві пробіли, ≤300, порожнє → None; (4) build_toysi_order кладе коментар покупця ПЕРШИМ у comment Toysi, а обрізання до 500 в toysi_order_submit
зачіпає лише автоматичну частину; без коментаря comment як раніше; (5) Telegram про нове замовлення сайту несе коментар. Мережа не потрібна. `python test_customer_comment.py` → exit 0/1."""
import os
import sqlite3
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import orders_db
import order_router as orr

orr.settlement_raion = lambda *a, **k: ""
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


# ── clean
cc = orders_db.clean_customer_comment
chk("None/порожнє/пробіли → None", cc(None) is None and cc("") is None and cc("   \n\t ") is None)
chk("керуючі символи й переноси → один пробіл", cc("Привіт\x00\x07,\n\nпозвоніть\tперед  доставкою") == "Привіт , позвоніть перед доставкою")
chk("довгий текст ≤300", len(cc("а" * 1000)) == 300)
chk("звичайний текст не змінюється", cc("Подарунок, пакуйте, будь ласка") == "Подарунок, пакуйте, будь ласка")

# ── БД: нова + міграція старої
tmp = tempfile.mkdtemp()
orders_db.DB_PATH = os.path.join(tmp, "orders.db")
orders_db.init_db()
with orders_db.get_connection() as c:
    cols = [r[1] for r in c.execute("PRAGMA table_info(orders)")]
chk("нова БД: колонка customer_comment є", "customer_comment" in cols)
# стара БД (таблиця без колонки) → міграція через _ensure_column
old = os.path.join(tmp, "old.db")
try:
    c2 = sqlite3.connect(old)
    c2.execute("CREATE TABLE orders (internal_order_id TEXT PRIMARY KEY, order_id TEXT, platform TEXT, status TEXT, payment_method TEXT, payment_confirmed INTEGER NOT NULL DEFAULT 0, customer_name TEXT, phone TEXT, email TEXT, np_branch TEXT, items TEXT, created_at TEXT)")
    c2.commit()
    orders_db._ensure_column(c2, "orders", "customer_comment", "customer_comment TEXT")
    cols2 = [r[1] for r in c2.execute("PRAGMA table_info(orders)")]
    chk("міграція: _ensure_column додає customer_comment у стару таблицю", "customer_comment" in cols2)
    c2.close()
except Exception as e:
    chk(f"міграція не впала ({e})", False)


def order(oid, **kw):
    base = dict(order_id=oid, platform="site", payment_method="cod", customer_name="Тест Тестович", phone="+380501112233",
                np_branch="Київ (Київська обл.), Відділення №1", items=[{"toysi_code": "1", "name": "x", "qty": 1, "price": 100}])
    base.update(kw)
    return base


with orders_db.get_connection() as c:
    orders_db.insert_order(c, order("T1", customer_comment="  Подарунок \n пакуйте  "))
    orders_db.insert_order(c, order("T2"))
    orders_db.insert_order(c, order("T3", customer_comment="x" * 800))
    c.commit()
    r1 = orders_db.get_order(c, "site_T1")
    r2 = orders_db.get_order(c, "site_T2")
    r3 = orders_db.get_order(c, "site_T3")
chk("insert: коментар очищений і збережений", r1["customer_comment"] == "Подарунок пакуйте")
chk("insert без поля (старі виклики) → NULL, нічого не ламається", r2["customer_comment"] is None)
chk("insert: надто довгий обрізається до 300 на межі БД", len(r3["customer_comment"]) == 300)

# ── Toysi comment
o = dict(r1, internal_order_id="site_T1", carrier="nova_poshta")
b = orr.build_toysi_order(o)
chk("Toysi comment: коментар покупця ПЕРШИМ, далі «Автоматично»", b["comment"].startswith("Коментар покупця: Подарунок пакуйте · Автоматично: site #T1"))
o2 = dict(r2, internal_order_id="site_T2", carrier="nova_poshta")
b2 = orr.build_toysi_order(o2)
chk("без коментаря comment як раніше (без «Коментар покупця»)", b2["comment"].startswith("Автоматично: site #T2") and "Коментар покупця" not in b2["comment"])
o3 = dict(r3, internal_order_id="site_T3", carrier="nova_poshta")
b3 = orr.build_toysi_order(o3)
import toysi_order_submit as tos
full = b3["comment"]
chk("300-символьний коментар + автоматична частина: обрізання до 500 не зачіпає текст покупця", len(full[:500]) >= len("Коментар покупця: ") + 300 and full[:500].startswith("Коментар покупця: " + "x" * 300))

# ── Telegram
import site_order_api as soa
sent = []
import telegram_notify as tn
tn.notify_new_order = lambda platform, oid, amount, n, pay, test=False, note="": sent.append((platform, oid, test, note))
soa._NO_TELEGRAM = False
soa._notify_new_order(r1, 100)
chk("Telegram: коментар покупця в повідомленні про нове замовлення", sent and "Коментар покупця: Подарунок пакуйте" in sent[0][3])
sent.clear()
soa._notify_new_order(r2, 100)
chk("Telegram: без коментаря note порожній", sent and sent[0][3] == "")
sent.clear()
soa._notify_new_order(r1, 100, test=True, note="тест")
chk("Telegram: з нотаткою і коментарем обидва зберігаються", sent and sent[0][3].startswith("тест · Коментар покупця:"))

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
