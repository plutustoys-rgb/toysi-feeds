#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_kv_order_safety.py — безпека замовлень з товарами KIDDISVIT (kv-/kvu-, 17.11.2026), ДО відкриття воріт SITE_KV_LIVE. Інваріанти:
(1) кошик з kv-позицією + cod / невідомий / відсутній спосіб → OrderError (лише передоплата); prepaid проходить; (2) змішаний кошик kv + Toysi / kv + rt відхиляється ЗАВЖДИ (і при SITE_MIXED_ORDERS=1);
(3) склад kv-/kvu- fail-closed: нема знімка / ключа / нечисловий / менше замовленого → «немає», Toysi — fail-open як раніше; (4) orders_db: оплачене kv-замовлення НЕ кандидат на форвард у Toysi;
(5) order_router.route_order: kv-позиція не передається в Toysi (до каталогу навіть не доходить); (6) orders_admin: постачальник «KIDDISVIT», stage не падає; (7) поведінка Toysi- і rt-замовлень без змін."""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
TMP = tempfile.mkdtemp()
os.environ["ORDERS_DB_PATH"] = os.path.join(TMP, "orders.db")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
import site_order_api as so  # noqa: E402
import order_router as orr  # noqa: E402
import orders_db  # noqa: E402
import orders_admin as oa  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def raises(fn):
    try:
        fn()
    except so.OrderError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


so.site_rejected_baskets.log_rejected = lambda *a, **k: None
so.price_map = lambda: {"100": {"price": 250, "name": "Тест"}, "rt-101": {"price": 400, "name": "RT"}, "kv-201": {"price": 900, "name": "KV"}, "kvu-201": {"price": 700, "name": "KV уцінка"}}
so.stock_snapshot = lambda: ({"100": 5, "rt-101": 3, "kv-201": 4, "kvu-201": 2}, None)
base = {"items": [{"id": "kv-201", "qty": 1}], "name": "Іван Петренко", "phone": "0501234567", "city_name": "Київ", "warehouse_name": "відділення 5"}
chk("kv + cod → OrderError", raises(lambda: so.build_order(dict(base, payment_method="cod"))))
chk("kv + спосіб не вказано → OrderError (не мовчки cod)", raises(lambda: so.build_order(dict(base))))
chk("kv + сміття у способі → OrderError", raises(lambda: so.build_order(dict(base, payment_method="COD "))))
o, t = so.build_order(dict(base, payment_method="prepaid"))
chk("kv + prepaid → замовлення створено, prepaid, сума 900", o["payment_method"] == "prepaid" and t == 900)
o2, t2 = so.build_order(dict(base, items=[{"id": "kvu-201", "qty": 2}], payment_method="prepaid"))
chk("kvu- (Уцінка) + prepaid → створено (2 × 700), у межах залишку 2", t2 == 1400)
try:
    so.build_order(dict(base, items=[{"id": "kvu-201", "qty": 3}], payment_method="prepaid"))
    ok3 = False
except so.StockChanged:
    ok3 = True
chk("kvu-: просять 3, у знімку 2 → StockChanged (409)", ok3)

mixed = dict(base, items=[{"id": "100", "qty": 1}, {"id": "kv-201", "qty": 1}])
chk("змішаний кошик kv + Toysi відхиляється і з cod, і з prepaid", raises(lambda: so.build_order(dict(mixed, payment_method="cod"))) and raises(lambda: so.build_order(dict(mixed, payment_method="prepaid"))))
mixed_rt = dict(base, items=[{"id": "rt-101", "qty": 1}, {"id": "kv-201", "qty": 1}])
chk("змішаний кошик kv + rt відхиляється (prepaid теж)", raises(lambda: so.build_order(dict(mixed_rt, payment_method="prepaid"))))
os.environ["SITE_MIXED_ORDERS"] = "1"
chk("навіть при SITE_MIXED_ORDERS=1 кошик з kv + інше постачальником відхиляється (нема поділу на три замовлення)", raises(lambda: so.build_order(dict(mixed, payment_method="prepaid"))) and raises(lambda: so.build_order(dict(mixed_rt, payment_method="prepaid"))))
mixed_tr = dict(base, items=[{"id": "100", "qty": 1}, {"id": "rt-101", "qty": 1}])
chk("Toysi + rt при SITE_MIXED_ORDERS=1 працює як раніше (поведінка RoyalToys без змін)", not raises(lambda: so.build_order(dict(mixed_tr, payment_method="prepaid"))))
os.environ.pop("SITE_MIXED_ORDERS", None)
plain = dict(base, items=[{"id": "100", "qty": 1}])
chk("Toysi без kv: cod за замовчуванням, як раніше", so.build_order(plain)[0]["payment_method"] == "cod")


def items(pid, q=1):
    return [{"toysi_code": pid, "name": "x", "qty": q}]


chk("kv: у знімку 4, просять 5 → немає (available 4)", so.check_stock(items("kv-201", 5))[0]["available"] == 4)
chk("kvu: у знімку 2, просять 3 → немає", so.check_stock(items("kvu-201", 3))[0]["available"] == 2)
so.stock_snapshot = lambda: ({"100": 5}, None)
chk("kv/kvu: ключа нема у знімку → немає (fail-closed); Toysi не чіпаємо", so.check_stock(items("kv-201"))[0]["available"] == 0 and so.check_stock(items("kvu-201"))[0]["available"] == 0 and so.check_stock(items("999")) == [])
so.stock_snapshot = lambda: ({"kv-201": "abc"}, None)
chk("kv: нечисловий залишок → немає", so.check_stock(items("kv-201"))[0]["available"] == 0)
so.stock_snapshot = lambda: (None, None)
chk("знімка нема: kv → немає, Toysi → не чіпаємо", so.check_stock(items("kv-201"))[0]["available"] == 0 and so.check_stock(items("100")) == [])
try:
    so.build_order(dict(base, payment_method="prepaid"))
    ok = False
except so.StockChanged:
    ok = True
chk("kv без знімка → StockChanged (409), замовлення не створюється", ok)
chk("is_kv_id: kv-1, KV-1, kvu-7 — так; kx-1, 123, rt-1, None — ні", all(so.is_kv_id(x) for x in ("kv-1", "KV-1", " kvu-7")) and not any(so.is_kv_id(x) for x in ("kx-1", "123", "rt-1", None)))

# БД: кандидати на форвард
orders_db.init_db()
conn = sqlite3.connect(os.environ["ORDERS_DB_PATH"])
conn.row_factory = sqlite3.Row


def ins(oid, code):
    its = [{"toysi_code": code, "name": "Іграшка", "qty": 1, "price": 100}]
    conn.execute("insert into orders (internal_order_id, order_id, platform, status, payment_method, payment_confirmed, customer_name, phone, items, created_at) values (?,?,?,?,?,?,?,?,?,?)",
                 (f"site_{oid}", oid, "site", "new", "prepaid", 1, "Тест", "380670000000", json.dumps(its, ensure_ascii=False), "2026-11-17T10:00:00"))


ins("T-TOYSI", "251827")
ins("T-RT", "rt-5")
ins("T-KV", "kv-201")
ins("T-KVU", "kvu-201")
conn.commit()
ready = [r["internal_order_id"] for r in orders_db.get_orders_ready_to_forward(conn)]
chk("кандидати на форвард: лише Toysi; rt-, kv-, kvu- — ні", ready == ["site_T-TOYSI"])

orr._ensure_site_prepaid_receipt = lambda *a, **k: True
orr._check_prom_not_cancelled = lambda *a, **k: True
orr._check_eva_not_cancelled = lambda *a, **k: True
orr._check_rozetka_not_cancelled = lambda *a, **k: True
called = []
orr.fetch_toysi_catalog = lambda *a, **k: called.append(1) or {}
orr.site_np_hold_reason = lambda o, t: called.append("hold") or "stop"
orr.send_throttled_alert = lambda *a, **k: None
for code in ("kv-201", "kvu-201"):
    orr.route_order(conn, {"internal_order_id": "site_T-KV", "platform": "site", "items": [{"toysi_code": code, "qty": 1, "price": 100}]})
chk("route_order: kv-/kvu-позиція не передається в Toysi (до каталогу навіть не доходить)", called == [])
orr.route_order(conn, {"internal_order_id": "site_T-RT", "platform": "site", "items": [{"toysi_code": "rt-5", "qty": 1, "price": 100}]})
chk("route_order: rt- як і раніше не передається", called == [])

row = {"platform": "site", "status": "new", "payment_method": "prepaid", "payment_confirmed": 1, "created_at": "2026-11-17T10:00:00", "items": [{"code": "kv-201", "name": "n", "qty": 1, "price": 100}]}
st = oa.stage(row, datetime(2026, 11, 17, 13, 0, 0))
chk("orders_admin: постачальник KIDDISVIT; оплачене kv-замовлення без передачі понад год → «нікуди не передано» + увага", oa.supplier_of(row) == "KIDDISVIT" and st[0] == "stalled" and st[2] is True)
chk("orders_admin: змішаний перелік підписується « + »", oa.supplier_of({"items": [{"code": "kvu-1"}, {"code": "rt-2"}, {"code": "55"}]}) == "KIDDISVIT + RoyalToys + Toysi")

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
