#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_price_state.py — 14.10.2026 («Знижка тижня», SMM/власник): історія цін для правила «стара ціна реальна». Приватний стан {id: [ціна, з якої дати не змінювалась]}, лише запис, на сайт/замовлення/ціни НЕ впливає.
Нові id → [ціна, сьогодні]; та сама ціна → дата не міняється; інша ціна → відлік заново; id, що зник → видаляється; зламаний файл НЕ перезаписується; запис атомарний (без залишків tmp); price_unchanged_days рахує повні дні."""
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
path = str(tmp / "site_price_state.json")
I = lambda *pairs: [{"id": str(i), "price": p} for i, p in pairs]  # noqa: E731

r1 = bs.update_price_state(I((1, 100), (2, 250), (3, 999)), date(2026, 10, 14), path)
st = json.load(open(path, encoding="utf-8"))
chk("перший запис: усі нові, дата = сьогодні, версія 1", r1 == {"new": 3, "changed": 0, "dropped": 0, "total": 3} and st["v"] == 1 and st["updated"] == "2026-10-14" and st["p"]["1"] == [100, "2026-10-14"] and st["p"]["3"] == [999, "2026-10-14"])
r2 = bs.update_price_state(I((1, 100), (2, 250), (3, 999)), date(2026, 10, 20), path)
st = json.load(open(path, encoding="utf-8"))
chk("та сама ціна через 6 днів — «з якої дати» НЕ міняється", r2["new"] == 0 and r2["changed"] == 0 and st["p"]["1"] == [100, "2026-10-14"])
chk("price_unchanged_days: 6 повних днів на 20.10, 0 у день зміни, None для невідомого", bs.price_unchanged_days(st["p"], 1, date(2026, 10, 20)) == 6 and bs.price_unchanged_days(st["p"], 1, date(2026, 10, 14)) == 0 and bs.price_unchanged_days(st["p"], 777, date(2026, 10, 20)) is None)
r3 = bs.update_price_state(I((1, 100), (2, 251), (4, 50)), date(2026, 10, 21), path)
st = json.load(open(path, encoding="utf-8"))
chk("змінена ціна (250→251) — відлік заново; новий id 4; зниклий id 3 видалено", r3 == {"new": 1, "changed": 1, "dropped": 1, "total": 3} and st["p"]["2"] == [251, "2026-10-21"] and st["p"]["4"] == [50, "2026-10-21"] and "3" not in st["p"] and st["p"]["1"] == [100, "2026-10-14"])
r4 = bs.update_price_state(I((1, 100), (3, 999)), date(2026, 10, 22), path)
st = json.load(open(path, encoding="utf-8"))
chk("id, що повернувся після відсутності, починає відлік заново (ціна під час відсутності невідома)", st["p"]["3"] == [999, "2026-10-22"] and r4["new"] == 1)
r5 = bs.update_price_state([{"id": "9", "price": 0}, {"id": "10", "price": None}, {"id": "11", "price": "abc"}, {"id": "1", "price": 100}], date(2026, 10, 22), path)
st = json.load(open(path, encoding="utf-8"))
chk("сміттєві/нульові/нечислові ціни пропускаються без винятку", set(st["p"]) == {"1"} and r5["total"] == 1)
# зламаний файл: не перезаписуємо
open(path, "w", encoding="utf-8").write("{не json")
before = open(path, encoding="utf-8").read()
r6 = bs.update_price_state(I((1, 100)), date(2026, 10, 23), path)
chk("зламаний файл стану: повертає None, файл НЕ перезаписано (історію не губимо мовчки)", r6 is None and open(path, encoding="utf-8").read() == before)
open(path, "w", encoding="utf-8").write(json.dumps({"v": 1, "p": []}))
chk("p не словник — теж None без перезапису", bs.update_price_state(I((1, 100)), date(2026, 10, 23), path) is None)
# запис у недоступну папку — None, без винятку
chk("недоступний шлях запису — None без винятку", bs.update_price_state(I((1, 100)), date(2026, 10, 23), str(tmp / "нема" / "x" / "state.json")) is None)
chk("атомарність: tmp-файлів не лишається", not [p for p in os.listdir(tmp) if ".tmp" in p])
gi = (HERE / ".gitignore").read_text(encoding="utf-8")
chk("site_price_state.json у .gitignore (приватний runtime-стан, не комітити)", "site_price_state.json" in gi)
src = (HERE / "site" / "build_site.py").read_text(encoding="utf-8")
chk("збірка викликає update_price_state у try/except (збій історії не валить збірку); стан лежить поза публічним site/", "update_price_state(prods + oos_prods)" in src and "os.path.dirname(OUT)" in src)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
