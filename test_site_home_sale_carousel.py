#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_home_sale_carousel.py — 16.10.2026 (власник: «Усі чіпи розмістити під каруселлю. Наповнення каруселі з акційних товарів. Чіпи залишити ті, які не увійшли у категорії»; «погоджуюсь»).
(1) Є акція (≥4 придатні акційні картки) → карусель «Знижки −N%» з акційних товарів (≤2 з категорії, ≤10), «Усі знижки →» веде на aktsii.html; кнопка чипа «Усі» вертає саме її.
(2) Акції нема / замало → звична «Добірка Плутуса» (catalog.html).
(3) Чипи — ПІД каруселлю; чипів-«категорій» (Набори, Тихі ігри, Активні ігри, Для двох гравців, У садок і школу) в розмітці нема; тематичні (День народження) і посилання «Уцінка» лишились."""
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_PICK_DATE"] = "2026-10-21"
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


IMG = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
KY = timezone(timedelta(hours=3))
_n = [0]


def P(name, cat, price, old=None, contrib=120):
    _n[0] += 1
    return {"id": str(8000 + _n[0]), "name": name, "category": cat, "price": price, "old_price": old, "contribution": contrib, "stock": 5, "photo": IMG, "desc": ""}


sale = [P(f"Акційна іграшка «Сорт{i}»", f"Акція{i % 4}", 300 + i * 10, old=380 + i * 10) for i in range(14)]            # 4 категорії по 3–4: ≤2 з категорії → 8 карток
normal = [P(f"Набір для творчості «Серія{i}»", f"Набори{i % 7}", 220 + i) for i in range(14)] + [P(f"Іграшка «Звичайна{i}»", f"Кат{i % 12}", 260 + i) for i in range(24)]
prods = sale + normal
cats = {}
for p in prods:
    cats.setdefault(p["category"], []).append(p)
cat_list = sorted(cats, key=lambda c: -len(cats[c]))
cat_slug = {c: bs.slugify(c) for c in cats}
START, END = datetime(2026, 10, 21, tzinfo=KY), datetime(2026, 10, 28, 23, 59, 59, tzinfo=KY)
SALE = {"week": "w", "pct": 15, "from": START, "until": END, "items": {p["id"]: (p["old_price"], p["price"]) for p in sale}}


def home(sale_active, ps=prods):
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    bs.SALE_ACTIVE = sale_active
    bs.write_home(ps, cats, cat_list, cat_slug)
    return (out / "index.html").read_text(encoding="utf-8")


def car_ids(h):
    c = h[h.index('id="home-car"'):h.index('id="home-chips"')]
    return re.findall(r'href="product-(\d+)\.html"', c)


h1 = home(SALE)
ids = car_ids(h1)
sale_ids = {p["id"] for p in sale}
chk(f"є акція: карусель з акційних товарів (усі {len(ids)} слайдів — акційні), ≥4, ≤10", 4 <= len(ids) <= 10 and set(ids) <= sale_ids)
chk("не більше 2 акційних з однієї категорії (різноманітність)", max(sum(1 for i in ids if next(p for p in prods if p["id"] == i)["category"] == c) for c in {p["category"] for p in sale}) <= 2)
chk("видимого заголовка «Знижки −15%» і посилання «Усі знижки →» нема (власник 16.10: відсоток видно на картках); h2 лишився для структури (sr-only)", '<h2 class="sr-only">Знижки −15%</h2>' in h1 and "<h2>Знижки" not in h1 and "Усі знижки" not in h1 and "Добірка Плутуса" not in h1)
chk("чипа «Усі» нема (власник 16.10), жоден чип не активний на старті; посилання home-car-all приховане", 'data-set="all"' not in h1 and 'class="chip active"' not in h1 and 'class="chip chip-theme active"' not in h1 and '<a class="chip-all" id="home-car-all" href="catalog.html" hidden></a>' in h1)
chk("слайди каруселі мають бейдж знижки", 'class="sale-badge"' in h1[h1.index('id="home-car"'):h1.index('id="home-chips"')])
chk("чипи ПІД каруселлю: блок чипів іде після каруселі в HTML", h1.index('id="home-car"') < h1.index('id="home-chips"'))
chk("секції «Більше іграшок» нема, замість неї кнопка «Усі товари» → catalog.html", "Більше іграшок" not in h1 and '<p class="home-more"><a class="btn" href="catalog.html">Усі товари</a></p>' in h1)
for k in ("nab", "school", "duo", "quiet", "active"):
    assert f'data-set="{k}"' not in h1, k
chk("чипів-«категорій» (nab/school/duo/quiet/active) у розмітці нема, шаблонів car-set- для них теж", all(f'data-set="{k}"' not in h1 and f'id="car-set-{k}"' not in h1 for k in ("nab", "school", "duo", "quiet", "active")))
chk("тематичний чип «День народження» лишився", 'data-set="bday"' in h1 and "День народження" in h1 and 'id="car-set-bday"' in h1)
chk("кнопка героя «Акції» поряд із «Каталог товарів»", '<a class="btn" href="catalog.html">Каталог товарів</a><a class="btn btn-sale" href="aktsii.html">' in h1)

h2 = home(None)
chk("акції нема: звична «Добірка Плутуса» (h2 sr-only, без видимого заголовка), чипи теж під каруселлю, чипа «Усі» нема", '<h2 class="sr-only">Добірка Плутуса</h2>' in h2 and h2.index('id="home-car"') < h2.index('id="home-chips"') and 'data-set="all"' not in h2)

few = {"week": "w", "pct": 15, "from": START, "until": END, "items": {p["id"]: (p["old_price"], p["price"]) for p in sale[:3]}}
h3 = home(few, [dict(p, old_price=(p["old_price"] if p["id"] in few["items"] else None)) for p in prods])
chk("акційних карток менше 4 → карусель лишається звичайною «Добірка Плутуса» (але кнопка «Акції» є)", '<h2 class="sr-only">Добірка Плутуса</h2>' in h3 and "Знижки −" not in h3 and 'href="aktsii.html">' in h3)
bs.SALE_ACTIVE = None
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
