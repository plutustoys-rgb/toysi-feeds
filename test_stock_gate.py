#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_stock_gate.py — свіжий залишок на сайті: stock_snapshot.py (site/stock.json) + перевірка при оформленні в site_order_api (рішення власника 13.10.2026, політика Консультанта).
Інваріанти: (1) знімок = {ts, n, s:{id: залишок}}, пишеться АТОМАРНО; порожній чи усічений каталог НЕ перезаписує останній добрий файл; (2) свіжий знімок і залишок < замовленого → 409 stock_changed з рядками
(id, назва, requested, available), замовлення НЕ створюється; (3) залишок ≥ замовленого → замовлення створюється; (4) політика «збій перевірки не блокує»: знімка нема, файл пошкоджений, знімок старший за 45 хв,
id немає у знімку → замовлення ПРИЙМАЄТЬСЯ; (5) один товар у кількох рядках сумується; (6) залишок рівно дорівнює замовленому → ок; (7) решта поведінки (валідація, 400) без змін.
Мережа/Telegram не потрібні, БД тимчасова. `python test_stock_gate.py` → exit 0/1."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")

import orders_db
import site_order_api as api
import stock_snapshot as ss

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
alerts = []
ss._alert = lambda key, text: alerts.append((key, text))

# ── stock_snapshot
cat = {str(i): {"stock": i % 4} for i in range(30000)}      # 30 000 позицій: вище порога «усічений каталог»
snap = ss.build_snapshot(cat, datetime(2026, 10, 13, 17, 0, 0))
chk("build_snapshot: ts З ПОЯСОМ, n, залишки цілі й невід'ємні", snap["ts"].startswith("2026-10-13T17:00:00") and datetime.fromisoformat(snap["ts"]).tzinfo is not None and snap["n"] == 30000 and snap["s"]["7"] == 3 and snap["s"]["8"] == 0)
chk("діапазон залишку «5...10»: у знімок іде ВЕРХНЯ межа (10), а не мінімум", ss.build_snapshot({"1": {"stock": 5, "stock_max": 10}, "2": {"stock": 1}})["s"] == {"1": 10, "2": 1})
import parser as tp
chk("parser: _parse_ostatok_max('5...10 шт.')=10, '10...50 шт.'=50, '1 шт.'=1, ''=0", (tp._parse_ostatok_max("5...10 шт."), tp._parse_ostatok_max("10...50 шт."), tp._parse_ostatok_max("1 шт."), tp._parse_ostatok_max("")) == (10, 50, 1, 0))
chk("parser: _parse_ostatok (мінімум) не змінився: '5...10 шт.'=5", tp._parse_ostatok("5...10 шт.") == 5)
chk("залишок не число → «невідомо» (id НЕ в знімку), мінус → 0", ss.build_snapshot({"1": {"stock": "abc"}, "2": {"stock": -5}})["s"] == {"2": 0})
chk("stock_known=False → id не пишемо; stock_known=True і 0 → пишемо 0", ss.build_snapshot({"1": {"stock": 0, "stock_known": False}, "2": {"stock": 0, "stock_known": True}})["s"] == {"2": 0})
import xml.etree.ElementTree as _ET
_x = ("<yml_catalog><shop><offers>"
      "<offer id='1'><name>a</name><price>10</price><ostatok>0</ostatok></offer>"
      "<offer id='2'><name>b</name><price>10</price><ostatok>3 шт.</ostatok></offer>"
      "<offer id='3'><name>c</name><price>10</price><ostatok>багато</ostatok></offer>"
      "<offer id='4'><name>d</name><price>10</price><ostatok></ostatok></offer>"
      "<offer id='5'><name>e</name><price>10</price></offer>"
      "</offers></shop></yml_catalog>").encode("utf-8")
_cat = tp._parse_xml(_x)
chk("parser stock_known: «0» і «3 шт.» відомі; «багато», порожнє, відсутнє — невідомі", [_cat[i]["stock_known"] for i in "12345"] == [True, True, False, False, False])
chk("snapshot з цього XML: лише 1 (0) і 2 (3)", ss.build_snapshot(_cat)["s"] == {"1": 0, "2": 3})
out = tmp / "stock.json"
ok = ss.run_once(fetch=lambda: cat, out=out, now=datetime(2026, 10, 13, 17, 0, 0), cost_out=tmp / "cost_stub.json")
d = json.loads(out.read_text(encoding="utf-8"))
chk("run_once пише файл атомарно (без .tmp), вміст коректний", ok and d["n"] == 30000 and not list(tmp.glob("*.tmp")))
good = out.read_text(encoding="utf-8")
ok = ss.run_once(fetch=lambda: {}, out=out, cost_out=tmp / "cost_stub.json")
chk("порожній каталог → файл НЕ перезаписано, сигнал", ok is False and out.read_text(encoding="utf-8") == good and any(k == "stock_snapshot_empty" for k, _ in alerts))
ok = ss.run_once(fetch=lambda: {str(i): {"stock": 1} for i in range(100)}, out=out, cost_out=tmp / "cost_stub.json")
chk("усічений каталог (100 позицій) → файл НЕ перезаписано, сигнал", ok is False and out.read_text(encoding="utf-8") == good and any(k == "stock_snapshot_small" for k, _ in alerts))
chk("збій fetch (виняток) → False, файл цілий, без винятку назовні", ss.run_once(fetch=lambda: (_ for _ in ()).throw(RuntimeError("мережа")), out=out, cost_out=tmp / "cost_stub.json") is False and out.read_text(encoding="utf-8") == good)

# ── бекенд
orders_db.init_db()
api.price_map = lambda: {"1": {"name": "Товар A", "price": 100}, "2": {"name": "Товар B", "price": 200}}
api.STOCK_FILE = str(tmp / "site_stock.json")
phones = iter(f"+38067{n:07d}" for n in range(1000001, 1000099))


def write_stock(s, age_min=0, raw=None):
    p = Path(api.STOCK_FILE)
    if raw is not None:
        p.write_text(raw, encoding="utf-8")
    else:
        p.write_text(json.dumps({"ts": (datetime.now() - timedelta(minutes=age_min)).isoformat(timespec="seconds"), "n": len(s), "s": s}), encoding="utf-8")
    api._stock_cache["mtime"] = None
    os.utime(p, (p.stat().st_mtime + 1, p.stat().st_mtime + 1))


def post(items):
    out = []
    h = api.Handler.__new__(api.Handler)
    h.command, h.path = "POST", "/api/order"
    h._read_json = lambda: {"items": items, "name": "Іван Петренко", "phone": next(phones), "city_name": "Київ", "warehouse_name": "Відділення №1",
                            "np_warehouse_number": "1", "payment_method": "cod"}
    h._json = lambda code, obj: out.append((code, obj))
    h._handle_order()
    return out[0]


def count_orders():
    with orders_db.get_connection() as c:
        return c.execute("SELECT count(*) FROM orders WHERE platform = 'site'").fetchone()[0]


write_stock({"1": 5, "2": 0})
n0 = count_orders()
code, resp = post([{"id": "1", "qty": 2}, {"id": "2", "qty": 1}])
chk("свіжий знімок: товару 2 нема → 409 stock_changed, лише цей рядок, замовлення НЕ створено", code == 409 and resp["code"] == "stock_changed" and resp["lines"] == [{"id": "2", "name": "Товар B", "requested": 1, "available": 0}] and count_orders() == n0)
code, resp = post([{"id": "1", "qty": 9}])
chk("залишку менше, ніж замовлено (5 < 9) → 409 з available=5 (запропонувати наявну кількість)", code == 409 and resp["lines"] == [{"id": "1", "name": "Товар A", "requested": 9, "available": 5}] and count_orders() == n0)
code, resp = post([{"id": "1", "qty": 5}])
chk("залишок рівно дорівнює замовленому (5 = 5) → замовлення створено", code == 200 and count_orders() == n0 + 1)
code, resp = post([{"id": "1", "qty": 3}, {"id": "1", "qty": 3}])
chk("один товар у двох рядках сумується (3+3=6 > 5) → 409 requested=6", code == 409 and resp["lines"][0]["requested"] == 6)
code, resp = post([{"id": "1", "qty": 2}])
chk("залишку вистачає → замовлення створено", code == 200)

n0 = count_orders()
write_stock({"1": 0, "2": 0}, age_min=90)
code, resp = post([{"id": "1", "qty": 1}])
chk("знімок СТАРШИЙ за 45 хв → перевірку пропускаємо, замовлення приймається (fail-open)", code == 200 and count_orders() == n0 + 1)
write_stock({}, raw="це не json{{")
code, resp = post([{"id": "1", "qty": 1}])
chk("файл знімка пошкоджений → замовлення приймається (fail-open)", code == 200)
Path(api.STOCK_FILE).unlink()
api._stock_cache["mtime"] = None
code, resp = post([{"id": "1", "qty": 1}])
chk("знімка нема → замовлення приймається (fail-open)", code == 200)
write_stock({"2": 5})
code, resp = post([{"id": "1", "qty": 1}])
chk("id немає у знімку (невідомий залишок) → рядок не чіпаємо, замовлення приймається", code == 200)

# ── інше без змін
write_stock({"1": 5, "2": 5})
code, resp = post([])
chk("порожній кошик → 400 як раніше", code == 400)
code, resp = post([{"id": "999", "qty": 1}])
chk("невідомий товар → 400 як раніше", code == 400)

# знімок з часовим поясом (+03:00 / Z): читач НЕ має мовчки вимкнути перевірку
from datetime import timezone
for label, tsv in (("локальний пояс", datetime.now().astimezone().isoformat(timespec="seconds")), ("UTC (Z-еквівалент)", datetime.now(timezone.utc).isoformat(timespec="seconds"))):
    write_stock(None, raw=json.dumps({"ts": tsv, "n": 2, "s": {"1": 5, "2": 0}}))
    code, resp = post([{"id": "2", "qty": 1}])
    chk(f"ts з поясом ({label}): перевірка діє (409), а не мовчки вимкнена", code == 409)
write_stock(None, raw=json.dumps({"ts": (datetime.now().astimezone() - timedelta(hours=3)).isoformat(timespec="seconds"), "n": 2, "s": {"1": 5, "2": 0}}))
code, resp = post([{"id": "2", "qty": 1}])
chk("ts з поясом, але старий (3 год) → знімок застарів, замовлення приймається (fail-open)", code == 200)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
