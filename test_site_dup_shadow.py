#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_dup_shadow.py — 20.10.2026: «тінь» (rt-дубль Toysi у наявності, index.json dup=1, PR #920) не може потрапити в замовлення (застарілий localStorage / підроблений кошик): build_order -> DupShadow ДО перевірки складу, з id близнюка Toysi (rt_dup_map.json); звичайні товари не зачеплено."""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")      # тимчасова БД: бойова не чіпається
import orders_db  # noqa: E402
import site_order_api as so  # noqa: E402
orders_db.init_db()

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


# price_map із справжнього index.json-формату (dup=1 → тінь)
td = Path(tempfile.mkdtemp())
(td / "index.json").write_text(json.dumps([
    {"id": "100", "n": "Toysi товар", "pr": 250, "p": "x"},
    {"id": "rt-101", "n": "RT звичайна", "pr": 400, "p": "x"},
    {"id": "rt-102", "n": "RT тінь", "pr": 626, "dup": 1, "p": "x"},
], ensure_ascii=False), encoding="utf-8")
so.INDEX_PATH = str(td / "index.json")
so._price_cache.update({"mtime": -1.0, "map": {}})
pm = so.price_map()
chk("price_map зберігає прапорець dup (тінь) і не ламає інші записи", pm["rt-102"]["dup"] is True and pm["100"]["dup"] is False and pm["rt-101"]["price"] == 400)

so.stock_snapshot = lambda: ({"100": 5, "rt-101": 3, "rt-102": 3}, None)
base = {"name": "Іван Петренко", "phone": "0671112233", "city_name": "Київ", "warehouse_name": "Відділення №5", "payment_method": "prepaid"}
dm = td / "rt_dup_map.json"
so.DUP_MAP_PATH = str(dm)
had = False
backup = None
try:
    dm.write_text(json.dumps({"rt-102": "274643"}), encoding="utf-8")
    so._dup_cache.update({"mtime": -1.0, "map": {}})
    try:
        so.build_order(dict(base, items=[{"id": "rt-102", "qty": 1}]))
        r1 = None
    except so.DupShadow as e:
        r1 = e.lines
    chk("кошик із тінню -> DupShadow з id, назвою та dup_of (близнюк Toysi з rt_dup_map.json)", r1 == [{"id": "rt-102", "name": "RT тінь", "dup_of": "274643"}])
    try:
        so.build_order(dict(base, items=[{"id": "100", "qty": 1}, {"id": "rt-102", "qty": 2}]))
        r2 = None
    except so.DupShadow as e:
        r2 = [x["id"] for x in e.lines]
    chk("змішаний кошик (звичайний + тінь) -> замовлення НЕ створюється, у відповіді лише тінь", r2 == ["rt-102"])
    order, total = so.build_order(dict(base, items=[{"id": "rt-101", "qty": 1}]))
    chk("звичайний rt-товар без dup проходить як раніше", total == 400 and order["items"][0]["toysi_code"] == "rt-101")
    dm.write_text("не json", encoding="utf-8")
    so._dup_cache.update({"mtime": -1.0, "map": {}})
    try:
        so.build_order(dict(base, items=[{"id": "rt-102", "qty": 1}]))
        r3 = None
    except so.DupShadow as e:
        r3 = e.lines[0]["dup_of"]
    chk("rt_dup_map.json зіпсований -> тінь усе одно відхиляється (dup_of порожній, UI покаже без посилання)", r3 == "")
finally:
    if had:
        dm.write_text(backup, encoding="utf-8")
    elif dm.exists():
        dm.unlink()

# ---- HTTP-рівень (справжній _handle_order, тимчасова БД): 409 і НІ ОДНОГО нового рядка в orders ----
(td / "index.json").write_text(json.dumps([
    {"id": "100", "n": "Toysi товар", "pr": 250, "p": "x"},
    {"id": "rt-102", "n": "RT тінь", "pr": 626, "dup": 1, "p": "x"},
], ensure_ascii=False), encoding="utf-8")
so._price_cache.update({"mtime": -1.0, "map": {}})
so.STOCK_FILE = str(td / "no_stock.json")


def post(items):
    out = []
    h = so.Handler.__new__(so.Handler)
    h.command, h.path = "POST", "/api/order"
    body = {"items": items, "name": "Іван Петренко", "phone": "+380671112299", "city_name": "Київ", "warehouse_name": "Відділення №1", "np_warehouse_number": "1", "payment_method": "prepaid"}
    h._read_json = lambda: body
    h._json = lambda code, obj: out.append((code, obj))
    h._handle_order()
    return out[0]


def cnt():
    with orders_db.get_connection() as c:
        return c.execute("SELECT count(*) FROM orders").fetchone()[0]


n0 = cnt()
code, resp = post([{"id": "rt-102", "qty": 1}])
chk("HTTP: кошик лише з тіні -> 409 dup_shadow, у orders.db РІВНО СТІЛЬКИ Ж рядків (замовлення не вставляється)", code == 409 and resp["code"] == "dup_shadow" and resp["lines"][0]["id"] == "rt-102" and cnt() == n0)
code, resp = post([{"id": "100", "qty": 1}, {"id": "rt-102", "qty": 1}, {"id": "rt-102", "qty": 3}])
chk("HTTP: змішаний кошик + дубль id тіні -> 409, одна лінія для тіні, нових рядків у БД немає", code == 409 and [l["id"] for l in resp["lines"]] == ["rt-102"] and cnt() == n0)
code, resp = post([{"id": "100", "qty": 1}])
chk("HTTP: кошик без тіней створює замовлення як раніше (контроль, що тест справді бачить вставку)", code == 200 and cnt() == n0 + 1)
h = so.Handler.__new__(so.Handler)
h.command, h.path = "GET", "/api/version"
out = []
h._json = lambda code, obj: out.append((code, obj))
h.do_GET()
import re as _re
chk("GET /api/version: 12 hex хешу власного файлу + час старту; без секретів", out[0][0] == 200 and _re.fullmatch(r"[0-9a-f]{12}", out[0][1]["code_version"]) and out[0][1]["started_at"] and set(out[0][1]) == {"code_version", "started_at"})

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
