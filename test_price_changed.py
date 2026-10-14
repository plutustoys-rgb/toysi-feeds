#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_price_changed.py — захист від «сюрприз-суми» (14.10.2026): /api/order приймає НЕОБОВ'ЯЗКОВЕ expected_total (сума, яку показав кошик); якщо сервер за поточним index.json рахує іншу
(ребілд сайту, акція почалась/закінчилась, Toysi підняв ціну) → 409 price_changed БЕЗ створення замовлення; збіг або поле відсутнє/некоректне → як раніше. Додатково: поле old/op в index.json
НЕ впливає на ціну (price_map читає лише id/pr/n). `python test_price_changed.py` → exit 0/1."""
import json
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")

import orders_db
import site_order_api as api

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
orders_db.init_db()
api.STOCK_FILE = str(tmp / "no_stock.json")
PRICES = {"A": {"name": "Товар A", "price": 100}, "B": {"name": "Товар B", "price": 120}}
api.price_map = lambda: PRICES
phones = iter(f"+38067{n:07d}" for n in range(2000001, 2000099))


def post(items, expected=None, pm="cod", with_expected=True):
    out = []
    h = api.Handler.__new__(api.Handler)
    h.command, h.path = "POST", "/api/order"
    body = {"items": items, "name": "Іван Петренко", "phone": next(phones), "city_name": "Київ", "warehouse_name": "Відділення №1", "np_warehouse_number": "1", "payment_method": pm}
    if with_expected:
        body["expected_total"] = expected
    h._read_json = lambda: body
    h._json = lambda code, obj: out.append((code, obj))
    h._handle_order()
    return out[0]


def count():
    with orders_db.get_connection() as c:
        return c.execute("SELECT count(*) FROM orders WHERE platform = 'site'").fetchone()[0]


n0 = count()
code, resp = post([{"id": "A", "qty": 2}], expected=200)
chk("expected_total збігається (200 = 2×100) → замовлення створено", code == 200 and resp["total"] == 200 and count() == n0 + 1)
n1 = count()
code, resp = post([{"id": "A", "qty": 2}], expected=170)
chk("сервер дорожчий за показане (170 vs 200) → 409 price_changed, замовлення НЕ створено", code == 409 and resp["code"] == "price_changed" and count() == n1)
chk("409 містить показану й актуальну суму та рядки з актуальними цінами", resp["expected_total"] == 170 and resp["total"] == 200 and resp["lines"] == [{"id": "A", "name": "Товар A", "qty": 2, "price": 100}])
code, resp = post([{"id": "A", "qty": 2}], expected=230)
chk("сервер ДЕШЕВШИЙ за показане (230 vs 200) → теж 409 (покупець бачив іншу суму)", code == 409 and resp["code"] == "price_changed" and count() == n1)
code, resp = post([{"id": "A", "qty": 1}], with_expected=False)
chk("поле відсутнє → як раніше: 200", code == 200 and count() == n1 + 1)
n2 = count()
for bad in (None, "abc", {"a": 1}, [100], True, 99.5, ""):
    code, resp = post([{"id": "B", "qty": 1}], expected=bad)
    chk(f"некоректне expected_total {bad!r} → ігнорується (200, не 500/409)", code == 200)
# входи, на яких попередня версія кидала виняток (аудит #759): мають давати None, а не 500
for label, bad in (("ціле з 400 цифр", 10 ** 400), ("'²'", "²"), ("'²³'", "²³"), ("рядок із 5000 цифр", "9" * 5000), ("арабські цифри '٢٠٠'", "٢٠٠"), ("' 200 ' з пробілами", " 200 "), ("'-12'", "-12"),
                   ("inf", float("inf")), ("nan", float("nan")), ("12.5", 12.5), ("1e308", 1e308)):
    try:
        v = api._expected_total(bad)
        ok = v is None
    except Exception:
        ok = False
    chk(f"_expected_total({label}) → None без винятку", ok)
chk("_expected_total: '1200' → 1200, 1200 → 1200, 1200.0 → 1200", (api._expected_total("1200"), api._expected_total(1200), api._expected_total(1200.0)) == (1200, 1200, 1200))
code, resp = post([{"id": "B", "qty": 1}], expected=10 ** 400)
chk("expected_total = ціле з 400 цифр → 200 (ігнорується), а не 500", code == 200)
chk("рядкове число '120' приймається і збігається", post([{"id": "B", "qty": 1}], expected="120")[0] == 200)
chk("float 120.0 збігається", post([{"id": "B", "qty": 1}], expected=120.0)[0] == 200)
n3 = count()
# зміна ціни між кошиком і оформленням
PRICES["A"] = {"name": "Товар A", "price": 85}
code, resp = post([{"id": "A", "qty": 1}], expected=100)
chk("ціна змінилась (акція почалась: 100→85), кошик показував 100 → 409, нове замовлення не створено", code == 409 and resp["total"] == 85 and count() == n3)
code, resp = post([{"id": "A", "qty": 1}], expected=85)
chk("кошик перерахувався (85) → замовлення створено за 85", code == 200 and resp["total"] == 85)

# old/op у index.json НЕ впливає на ціну: справжній price_map, читає лише id/pr/n
import importlib
importlib.reload(api)
idx = tmp / "index.json"
idx.write_text(json.dumps([{"id": "X", "n": "Іграшка", "pr": 85, "op": 100, "p": "u", "c": "k", "s": 1}], ensure_ascii=False), encoding="utf-8")
api.INDEX_PATH = str(idx)
api._price_cache["mtime"] = None
pm = api.price_map()
chk("price_map: ціна = pr (85), op (100) ігнорується", pm == {"X": {"price": 85, "name": "Іграшка"}})

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
