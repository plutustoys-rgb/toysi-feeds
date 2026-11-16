#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_legacy_supplier_guard.py — захист ІСТОРИЧНИХ замовлень знятого постачальника (прибрано 16.11.2026).
У БД можуть лишитись замовлення з id «dropt-…»: код мусить (1) не брати їх у кандидати на форвард у Toysi, (2) не передавати їх у route_order,
(3) читати їх без помилок (orders_admin.stage/supplier_of), (4) не ламати звичайні Toysi- і rt-замовлення. `python test_legacy_supplier_guard.py` → exit 0/1."""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TMP = tempfile.mkdtemp()
os.environ["ORDERS_DB_PATH"] = os.path.join(TMP, "orders.db")
import orders_admin as oa  # noqa: E402
import orders_db  # noqa: E402
import order_router as orr  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


orders_db.init_db()
conn = sqlite3.connect(os.environ["ORDERS_DB_PATH"])
conn.row_factory = sqlite3.Row


def ins(oid, code):
    items = [{"toysi_code": code, "name": "Іграшка", "qty": 1, "price": 100}]
    conn.execute("insert into orders (internal_order_id, order_id, platform, status, payment_method, payment_confirmed, customer_name, phone, items, created_at) values (?,?,?,?,?,?,?,?,?,?)",
                 (f"site_{oid}", oid, "site", "new", "prepaid", 1, "Тест", "380670000000", json.dumps(items, ensure_ascii=False), "2026-11-10T10:00:00"))


ins("T-TOYSI", "251827")
ins("T-RT", "rt-5")
ins("T-OLD", "dropt-57114")
conn.commit()
ready = [r["internal_order_id"] for r in orders_db.get_orders_ready_to_forward(conn)]
chk("кандидати на форвард: лише Toysi-замовлення; історичне і rt- — ні", ready == ["site_T-TOYSI"])

# route_order: історичне замовлення НЕ доходить до каталогу/форварду Toysi
orr._ensure_site_prepaid_receipt = lambda *a, **k: True
orr._check_prom_not_cancelled = lambda *a, **k: True
orr._check_eva_not_cancelled = lambda *a, **k: True
orr._check_rozetka_not_cancelled = lambda *a, **k: True
called = []
orr.fetch_toysi_catalog = lambda *a, **k: called.append(1) or {}
orr.route_order(conn, {"internal_order_id": "site_T-OLD", "items": [{"toysi_code": "dropt-57114", "qty": 1, "price": 100}]})
chk("route_order: позиція «dropt-…» не передається в Toysi (до каталогу навіть не доходить)", called == [])
orr.route_order(conn, {"internal_order_id": "site_T-RT", "items": [{"toysi_code": "rt-5", "qty": 1, "price": 100}]})
chk("route_order: rt-позиція як і раніше не передається (поведінка RoyalToys без змін)", called == [])

# читання історичного рядка адмінкою не падає
row = {"platform": "site", "status": "new", "payment_method": "prepaid", "payment_confirmed": 1, "created_at": "2026-11-10T10:00:00",
       "items": [{"code": "dropt-57114", "name": "n", "qty": 1, "price": 100}]}
try:
    st = oa.stage(row, datetime(2026, 11, 10, 15, 0, 0))
    sp = oa.supplier_of(row)
    ok = True
except Exception as e:  # noqa: BLE001
    ok, st, sp = False, None, None
chk("orders_admin читає історичний рядок з «dropt-» без винятку", ok and isinstance(st, tuple) and isinstance(sp, str))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
