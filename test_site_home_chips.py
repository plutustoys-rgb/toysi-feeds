#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_home_chips.py — 12.10.2026 (SMM): чипи над каруселлю головної «До 300 ₴» / «300–700 ₴» / «Набори» (+ «Усі»). Набори — у <template> (інертні), без перезавантаження, події home_chip_<ключ>.
Поведінку в реальному Chromium (перемикання, цикл після перемикання, події, 1280/390) перевірено окремо при розробці."""
import os, re, sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


_n = [0]


def P(price, contrib, cat, name=None, stock=5):
    _n[0] += 1
    return {"id": str(5000 + _n[0]), "name": name or f"Іграшка \"Лінія{_n[0]}\"", "category": cat, "price": price, "contribution": contrib, "stock": stock, "photo": "https://x/p.jpg"}


prods = [P(60 + i * 20, 55 + i, f"Кат{i % 12}") for i in range(12)]           # 60..280 ₴, внесок 55+
prods += [P(320 + i * 30, 105 + i, f"Кат{i % 12}") for i in range(12)]          # 320..650 ₴, внесок 105+
prods += [P(200 + i, 60, "Інша творчість", name=f"Набір для творчості \"Серія{i}\"") for i in range(5)]
sets = bs.home_chip_sets(prods)
chk("є всі три набори: u300, m700, nab", set(sets) == {"u300", "m700", "nab"})
chk("«До 300 ₴»: 50 ≤ ціна < 300, внесок ≥ 50, ≤10 карток", all(50 <= p["price"] <= 299.99 and p["contribution"] >= 50 for p in sets["u300"]) and 0 < len(sets["u300"]) <= 10)
chk("«300–700 ₴»: 300 ≤ ціна ≤ 700, внесок ≥ 100", all(300 <= p["price"] <= 700 and p["contribution"] >= 100 for p in sets["m700"]) and len(sets["m700"]) > 0)
chk("«Набори»: лише з «набір» у назві (відбір /nabory.html), ≤10", all("набір" in p["name"].lower() for p in sets["nab"]) and 0 < len(sets["nab"]) <= 10)
chk("основна добірка не змінилась: home_pick() без параметрів тримає 150–700 і внесок ≥100", all(150 <= p["price"] <= 700 and p["contribution"] >= 100 for p in bs.home_pick(prods)))
chk("порожній набір не повертається (немає «набір» → чипа «Набори» нема)", "nab" not in bs.home_chip_sets([P(60, 55, "К"), P(400, 120, "К2")]))
chk("не в наявності/без фото відсіюються з чипів", bs.home_chip_sets([dict(P(100, 80, "К"), stock=0), dict(P(100, 80, "К2"), photo="")]) == {})

src = (HERE / "site" / "build_site.py").read_text(encoding="utf-8")
chk("розмітка: чипи (Усі активний, aria-pressed), набори в <template id=car-set-*>, плитки НЕ eager, з бейджем; без JS чипи сховані (noscript)", 'data-set="all" aria-pressed="true">Усі</button>' in src and '<template id="car-set-{k}">' in src and 'tile(p, cls="slide", badge=True) for p in chip_sets[k]' in src and "<noscript><style>.home-chips{display:none}</style></noscript>" in src)
app = (HERE / "site/assets/app.js").read_text(encoding="utf-8")
car = app[app.index("function initHomeCarousel()"):app.index("function initListTracking()")]
chk("JS: перемикання прибирає клони, підставляє картки з <template>, перераховує slides/loop, скидає на початок, шле view_item_list home_chip_<ключ>", all(x in car for x in ("function teardownLoop()", 'querySelectorAll("[data-clone]")', "tpl.innerHTML", 'name="home_chip_"+key', "slides=Array.prototype.slice.call(car.querySelectorAll(\"a.card\"))", "track.scrollLeft=0;", "setupLoop();")))
chk("«Усі» повертає збережену основну добірку (baseHTML, зняту ДО клонування) і її item_list_name", "var baseHTML = track ? track.innerHTML" in car and "html=baseHTML; name=baseName;" in car and car.index("var baseHTML") < car.index("setupLoop();\n      track.addEventListener"))
css = (HERE / "site/assets/styles.css").read_text(encoding="utf-8")
chk("CSS: місце під чипи зарезервовано (visibility), .js — visible (без CLS)", ".home-chips{visibility:hidden}" in css and ".home-chips.js{visibility:visible}" in css)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
