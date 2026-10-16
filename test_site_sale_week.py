#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_sale_week.py — 14.10.2026 («Знижка тижня −15%», варіант «а», рішення власника; бекенд Code-Agent site_sale.py → site/sale_week.json): наш бік.
(1) load_sale_week: лише ДІЙСНИЙ файл у вікні from…until (ISO із поясом, new = ceil(old×0,85)); поза вікном/зламаний/нема — акції нема; (2) apply_sale: ціна pr=new, op=old ЛИШЕ коли поточна ціна == old, new < price і внесок після знижки ≥ запасу 25 ₴;
(3) index.json несе pr (акційна) і op; (4) картка: бейдж −15%, стара ціна перекреслена, нова червона; сторінка товару: «Акція до 25.10 23:59» (кінець партії товару), JSON-LD priceValidUntil = кінець акції; (5) /aktsii.html і кнопка героя «Акції» (червона наліпка) лише коли є придатні акційні позиції;
(6) зворотний відлік у браузері; (7) контраст білого тексту на #D93A3A ≥ 4.5:1."""
import json
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import http.server
import socketserver
from datetime import datetime, timedelta, timezone
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


KY = timezone(timedelta(hours=3))
START = datetime(2026, 10, 19, 0, 0, 0, tzinfo=KY)
END = datetime(2026, 10, 25, 23, 59, 59, tzinfo=KY)
NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=KY)
tmp = Path(tempfile.mkdtemp())
path = str(tmp / "sale_week.json")


def new_of(old, pct=15):
    return -(-old * (100 - pct) // 100)


def write(obj, bom=False, raw=None):
    data = raw if raw is not None else json.dumps(obj, ensure_ascii=False)
    Path(path).write_text(data, encoding="utf-8-sig" if bom else "utf-8")


def week_obj(items=None, **over):
    base = {"week": "2026-W43", "pct": 15, "from": START.isoformat(), "until": END.isoformat(), "created_at": "2026-10-18T18:00:00+03:00",
            "items": items if items is not None else {"111": {"old": 400, "new": new_of(400)}, "222": {"old": 333, "new": new_of(333)}}}
    base.update(over)
    return base


def sale_file(items=None, **over):   # формат Code-Agent (двотижневий): {"weeks":[чинний, наступний]}
    return {"weeks": [week_obj(items, **over)]}


# ── 1. load_sale_week ──
if os.path.exists(path):
    os.remove(path)
chk("нема файлу — акції нема, без винятку", bs.load_sale_week(NOW, path) is None)
# двотижневий файл (аудит Code #763): чинний + наступний; діє лише той, у чиє вікно потрапляє «зараз»
W2_START, W2_END = START + timedelta(days=7), END + timedelta(days=7)
two = {"weeks": [week_obj(week="2026-W43"), week_obj(items={"777": {"old": 200, "new": new_of(200)}}, week="2026-W44", **{"from": W2_START.isoformat(), "until": W2_END.isoformat()})]}
write(two)
w1, w2 = bs.load_sale_week(NOW, path), bs.load_sale_week(NOW + timedelta(days=7), path)
chk("двотижневий файл: у вікні тижня 1 діє тиждень 1 (позиції 111, 222), у вікні тижня 2 — лише тиждень 2 (позиція 777)", w1 and w1["week"] == "2026-W43" and set(w1["items"]) == {"111", "222"} and w2 and w2["week"] == "2026-W44" and set(w2["items"]) == {"777"})
chk("двотижневий файл у неділю 18:00 (чинний тиждень 1 ще діє, наступний уже записаний) — акція тижня 1 НЕ знімається", bs.load_sale_week(datetime(2026, 10, 25, 18, 0, 0, tzinfo=KY), path)["week"] == "2026-W43")
chk("у щілині між тижнями (0,5 с) і після обох — акції нема; опівночі (END+1 с = from тижня 2) вже діє тиждень 2", bs.load_sale_week(END + timedelta(milliseconds=500), path) is None and bs.load_sale_week(W2_END + timedelta(seconds=1), path) is None and bs.load_sale_week(END + timedelta(seconds=1), path)["week"] == "2026-W44")
write({"weeks": [week_obj(pct=0), week_obj(items={"888": {"old": 100, "new": 85}})]})
chk("зламаний тиждень пропускається, наступний валідний, що потрапляє у вікно, діє", bs.load_sale_week(NOW, path) and set(bs.load_sale_week(NOW, path)["items"]) == {"888"})
write({"weeks": "не список"})
chk("weeks не список — акції нема", bs.load_sale_week(NOW, path) is None)
write(week_obj())
chk("плоский формат (один тиждень без weeks) теж читається", bs.load_sale_week(NOW, path) and set(bs.load_sale_week(NOW, path)["items"]) == {"111", "222"})
write(sale_file())
s = bs.load_sale_week(NOW, path)
chk("валідний файл у вікні: pct, тиждень, дві позиції (old, new = ceil(old×0,85): 400→340, 333→284)", s and s["pct"] == 15 and s["week"] == "2026-W43" and s["items"] == {"111": (400, 340), "222": (333, 284)})
chk("поза вікном — файл ІГНОРУЄТЬСЯ (до from і після until), навіть якщо лишився", bs.load_sale_week(START - timedelta(seconds=1), path) is None and bs.load_sale_week(END + timedelta(seconds=1), path) is None and bs.load_sale_week(START, path) and bs.load_sale_week(END, path))
write(sale_file(), bom=True)
chk("BOM у файлі приймається", bs.load_sale_week(NOW, path) is not None)
bads = {
    "from без пояса": sale_file(**{"from": "2026-10-19T00:00:00"}), "until без пояса": sale_file(until="2026-10-25T23:59:59"), "from ≥ until": sale_file(**{"from": END.isoformat(), "until": START.isoformat()}),
    "pct=0": sale_file(pct=0), "pct=99": sale_file(pct=99), "pct рядок": sale_file(pct="15"), "pct bool": sale_file(pct=True),
    "new ≠ ceil(old×0,85)": sale_file(items={"111": {"old": 400, "new": 339}}), "new ≥ old": sale_file(items={"111": {"old": 400, "new": 400}}), "old/new float": sale_file(items={"111": {"old": 400.0, "new": 340}}),
    "old/new bool": sale_file(items={"111": {"old": True, "new": True}}), "позиція не dict": sale_file(items={"111": 5}), "items список": sale_file(items=[1, 2]),
}
for label, obj in bads.items():
    write(obj)
    chk(f"зламаний файл ({label}): акції нема, без винятку", bs.load_sale_week(NOW, path) is None)
write(sale_file(until=(START + timedelta(days=30)).isoformat(), **{"from": START.isoformat()}))
chk("вікно акції довше 8 днів (помилка в плані → акція «на місяці») — відхилено", bs.load_sale_week(NOW, path) is None)
write(None, raw="{не json")
chk("невалідний JSON — акції нема", bs.load_sale_week(NOW, path) is None)
write(sale_file(items={}))
chk("порожній items — акції нема", bs.load_sale_week(NOW, path) is None)

# ── 1б. ПОСТІЙНА акція: кілька партій одночасно (щоденна ротація, 15.10) ──
def batch(label, d0, items, pct=15):
    st = datetime(2026, 10, d0, 0, 0, 0, tzinfo=KY)
    return {"week": label, "pct": pct, "from": st.isoformat(), "until": (st + timedelta(days=8) - timedelta(seconds=1)).isoformat(), "items": {k: {"old": 400, "new": new_of(400, pct)} for k in items}}
write({"weeks": [batch("2026-10-19", 19, ["1", "2"]), batch("2026-10-20", 20, ["3", "4"]), batch("2026-10-21", 21, ["5"]), batch("2026-10-22", 22, ["6"])]})
m = bs.load_sale_week(NOW, path)
chk("кілька партій: діють ВСІ, чиє вікно містить «зараз» (19, 20, 21), завтрашня (22) ще ні — об'єднано 5 позицій", m and set(m["items"]) == {"1", "2", "3", "4", "5"} and m["week"] == "2026-10-19")
chk("кінець знижки — свій у кожного товару (партія 19 → 26.10 23:59:59, партія 21 → 28.10 23:59:59)", m["until_by_id"]["1"].isoformat() == "2026-10-26T23:59:59+03:00" and m["until_by_id"]["5"].isoformat() == "2026-10-28T23:59:59+03:00" and m["until"].isoformat() == "2026-10-28T23:59:59+03:00")
m2 = bs.load_sale_week(datetime(2026, 10, 27, 12, 0, 0, tzinfo=KY), path)
chk("через 6 днів: партія 19 вже закінчилась (26.10) — лишились 3, 4, 5, 6", set(m2["items"]) == {"3", "4", "5", "6"})
write({"weeks": [batch("2026-10-19", 19, ["1"]), batch("2026-10-20", 20, ["2"], pct=20)]})
m3 = bs.load_sale_week(NOW, path)
chk("партії з різним pct партії (15 і 20) об'єднуються: відсоток тепер на товарі, обидва діють", set(m3["items"]) == {"1", "2"} and m3["pct"] == 15)

# ── 1в. КОЖНА дата плану 15–30.10 (у т.ч. партії через перехід на зимовий час 25.10) проходить крізь _parse_sale_week (блокер аудиту #786) ──
import site_sale as _ss
_bad = []
for _d in range(15, 31):
    _run = datetime(2026, 10, _d, 18, 0, 0, tzinfo=_ss.KYIV)
    _st, _en, _lab = _ss.next_batch_window(_run)
    _raw = {"week": _lab, "pct": 15, "from": _st.isoformat(), "until": _en.isoformat(), "items": {"1": {"old": 400, "new": new_of(400)}}}
    if bs._parse_sale_week(_raw, _st + timedelta(hours=12)) is None:
        _bad.append(_lab)
chk(f"усі партії плану 15–30.10 (також через DST 25.10) приймає build_site (відхилені: {_bad})", not _bad)
_raw_long = {"week": "x", "pct": 15, "from": datetime(2026, 10, 19, tzinfo=KY).isoformat(), "until": datetime(2026, 10, 29, 23, 59, 59, tzinfo=KY).isoformat(), "items": {"1": {"old": 400, "new": new_of(400)}}}
chk("вікно 11 діб (помилка плану) як і раніше відхиляється", bs._parse_sale_week(_raw_long, datetime(2026, 10, 21, tzinfo=KY)) is None)

# ── 1г. знижка КОЖНОГО товару своя (5–15%), бейдж = реальний відсоток ──
_st = datetime(2026, 10, 21, 0, 0, 0, tzinfo=KY)
_raw = {"week": "2026-10-21", "pct": 15, "from": _st.isoformat(), "until": (_st + timedelta(days=8) - timedelta(seconds=1)).isoformat(),
        "items": {"1": {"old": 400, "new": new_of(400, 5), "pct": 5}, "2": {"old": 400, "new": new_of(400, 12), "pct": 12}, "3": {"old": 400, "new": new_of(400, 15), "pct": 15}, "4": {"old": 400, "new": new_of(400, 15)}}}
_ok = bs._parse_sale_week(_raw, NOW)
chk("pct на товар: 5/12/15 і без pct (= pct партії 15) приймаються, ціни new відповідають своєму %", _ok and _ok["items"]["1"] == (400, 380) and _ok["items"]["2"] == (400, 352) and _ok["items"]["3"] == (400, 340) and _ok["items"]["4"] == (400, 340))
_bad = dict(_raw, items={"1": {"old": 400, "new": new_of(400, 15), "pct": 5}})
chk("pct товару не збігається з new → партія відхилена (захист від хибної ціни)", bs._parse_sale_week(_bad, NOW) is None)
_bad2 = dict(_raw, items={"1": {"old": 400, "new": new_of(400, 5), "pct": 0}})
chk("pct товару поза 1..50 → відхилено", bs._parse_sale_week(_bad2, NOW) is None)
chk("бейдж картки = реальний відсоток із цін (380/400 → −5%, 352 → −12%, 340 → −15%)", all(f'−{n}%' in bs.sale_badge_html({"old_price": 400, "price": pr}) for pr, n in ((380, 5), (352, 12), (340, 15))) and bs.sale_badge_html({"old_price": None, "price": 300}) == "")

# ── 2. apply_sale: перевірки на кожній збірці ──
SALE = {"week": "w", "pct": 15, "from": START, "until": END, "items": {"111": (400, 340)}}
com = bs.cp.PAYMENT_COMMISSION.get("site", 0.0)
cost_ok = 340 - 340 * com - bs.SITE_FULFILL_PER_ORDER - 30          # внесок після знижки ≈ 30 ≥ 25
cost_low = 340 - 340 * com - bs.SITE_FULFILL_PER_ORDER - 20         # внесок ≈ 20 < 25
cost_edge = 340 - 340 * com - bs.SITE_FULFILL_PER_ORDER - 25.0           # внесок ТОЧНО 25 → застосовується
chk("apply_sale: внесок рівно 25,00 ₴ — знижка діє; 24,99 — ні (межа запасу)", bs.apply_sale("111", 400, SALE, cost_edge) == (340, 400) and bs.apply_sale("111", 400, SALE, cost_edge + 0.01) == (400, None))
chk("apply_sale: ціна == old, внесок ≥ 25 → (340, 400)", bs.apply_sale("111", 400, SALE, cost_ok) == (340, 400))
chk("apply_sale: внесок після знижки < 25 ₴ — без знижки (збитку/тонкої маржі нема)", bs.apply_sale("111", 400, SALE, cost_low) == (400, None))
chk("apply_sale: ціна сайту змінилась (≠ old) — без знижки (стара ціна має бути реальною)", bs.apply_sale("111", 401, SALE, cost_ok) == (401, None) and bs.apply_sale("111", 399, SALE, cost_ok) == (399, None))
chk("apply_sale: невідома собівартість (0/None) — без знижки; id не з акції — без змін; нема акції — без змін", bs.apply_sale("111", 400, SALE, 0) == (400, None) and bs.apply_sale("111", 400, SALE, None) == (400, None) and bs.apply_sale("999", 400, SALE, cost_ok) == (400, None) and bs.apply_sale("111", 400, None, cost_ok) == (400, None))
chk("apply_sale: new ≥ поточної ціни — без знижки", bs.apply_sale("111", 340, {"items": {"111": (400, 340)}}, cost_ok) == (340, None))

# ── 3. index.json ──
IMG = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
p_sale = {"id": "111", "name": "Акційна «Лялька»", "category": "Кат1", "price": 340, "old_price": 400, "contribution": 60, "stock": 5, "photo": IMG, "desc": ""}
p_norm = {"id": "333", "name": "Звичайна «Ведмідь»", "category": "Кат2", "price": 250, "old_price": None, "contribution": 90, "stock": 5, "photo": IMG, "desc": ""}
ie = bs.index_entries([p_sale, p_norm])
chk("index.json: акційна позиція має pr=340 і op=400; звичайна — без op", ie[0]["pr"] == 340 and ie[0]["op"] == 400 and "op" not in ie[1] and ie[1]["pr"] == 250)

# ── 4. розмітка (SALE_ACTIVE виставлено як у build) ──
bs.SALE_ACTIVE = SALE
t = bs.tile(p_sale)
chk("картка: бейдж «−15%», нова ціна червоного класу pr-sale, стара перекреслена (<s class=pr-old>)", 'class="sale-badge">−15%</span>' in t and '<span class="pr pr-sale">340 ₴</span>' in t and '<s class="pr-old">400 ₴</s>' in t)
tn = bs.tile(p_norm)
chk("звичайна картка без бейджа/старої ціни", "sale-badge" not in tn and "pr-old" not in tn and '<span class="pr">250 ₴</span>' in tn)
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.write_product(p_sale, related=None, cat_slug={}, similar=[])
bs.write_product(p_norm, related=None, cat_slug={}, similar=[])
ph = (out / "product-111.html").read_text(encoding="utf-8")
pn = (out / "product-333.html").read_text(encoding="utf-8")
chk("сторінка акційного товару: ціна 340 ₴ червона, стара 400 ₴ перекреслена, бейдж −15%, «Акція до 25.10 23:59» з відліком; кнопка «У кошик» несе ціну 340", 'class="price sale-price">340 ₴' in ph and '<s class="pr-old">400 ₴</s>' in ph and "sale-badge" in ph and "Акція до <b>25.10 23:59</b>" in ph and 'data-sale-until="2026-10-25T23:59:59+03:00"' in ph and '&quot;price&quot;: 340' in ph)
ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', ph.replace("\\u003c", "<"), re.S)
chk("JSON-LD Offer: price=340, priceValidUntil = кінець акції (2026-10-25)", '"price": "340"' in ph and '"priceValidUntil": "2026-10-25"' in ph)
chk("звичайний товар: без акційних елементів, priceValidUntil — стандартний", "sale-until" not in pn and "sale-badge" not in pn and '"priceValidUntil": "2026-10-25"' not in pn)

# ── 5. /aktsii.html і кнопка героя ──
prods = [p_sale, dict(p_sale, id="112", name="Акційна «Машинка»", price=290, old_price=340, category="Кат3")] + [dict(p_norm, id=str(500 + i), name=f"Звичайна «Іграшка{i}»", category=f"К{i}", price=300 + i, contribution=120) for i in range(12)]
pk = bs.aktsii_pick(prods)
chk("aktsii_pick: лише акційні позиції, ціна зростаюче (290, 340)", [p["id"] for p in pk] == ["112", "111"])
chk("write_aktsii пише aktsii.html", bs.write_aktsii(prods) == {"aktsii.html"})
ah = (out / "aktsii.html").read_text(encoding="utf-8")
chk("/aktsii: H1 лише для структури (sr-only «Акції», видимого заголовка нема — власник 16.10), title «Акції | PlutusToys», без «Знижка тижня»/«Знижки −N%»/лід-абзацу, сітка з бейджами (−N% у кутку картки), data-list=sale_week, без промокодів/підписок",
    '<h1 class="page cat-h1 sr-only">Акції</h1>' in ah and "<title>Акції | PlutusToys</title>" in ah and "Знижка тижня" not in ah and "Знижки −" not in ah and 'class="sale-lead"' not in ah and "Щодня — нові іграшки" not in ah and "Акція до <b>" not in ah
    and ah.count('class="sale-badge">−15%</span>') == 2 and 'data-list="sale_week"' in ah and 'id="sale-list"' in ah and "PLUTUS5" not in ah and "Instagram" not in ah.split("</header>")[1].split("<footer")[0])
chk("/aktsii: у мета-описі й title немає відсотків (знижка в кожного товару своя 5–15%)", "−15%" not in re.search(r'<title>.*?</title>', ah).group(0) and "−15%" not in re.search(r'<meta name="description" content="([^"]*)"', ah).group(1) and "%" not in re.search(r'<meta name="description" content="([^"]*)"', ah).group(1))
# 3 сторінки по 24 (власник 16.10): 60 акційних → aktsii.html, aktsii_2.html, aktsii_3.html (24+24+12), пагінація/canonical/rel як у каталозі
many = [dict(p_sale, id=str(900 + i), name=f"Акційна «Іграшка{i}»", category=f"А{i % 7}", price=300 + i, old_price=400 + i) for i in range(60)]
SALE_MANY = dict(SALE, items={p["id"]: (p["old_price"], p["price"]) for p in many})
bs.SALE_ACTIVE = SALE_MANY
pages_w = bs.write_aktsii(many)
chk(f"/aktsii на 3 сторінки: {sorted(pages_w)}", pages_w == {"aktsii.html", "aktsii_2.html", "aktsii_3.html"})
cnt = [len(re.findall(r'<a class="card"', (out / f).read_text(encoding="utf-8"))) for f in ("aktsii.html", "aktsii_2.html", "aktsii_3.html")]
chk(f"на сторінках 24 + 24 + 12 карток: {cnt}", cnt == [24, 24, 12])
a2 = (out / "aktsii_2.html").read_text(encoding="utf-8")
chk("сторінка 2: canonical на себе, rel=prev → aktsii.html, rel=next → aktsii_3.html, пейджер із «Далі →», title зі «сторінка 2»", 'rel="canonical" href="https://plutustoys.com.ua/aktsii_2.html"' in a2 and '<link rel="prev" href="aktsii.html">' in a2 and '<link rel="next" href="aktsii_3.html">' in a2 and "Далі →" in a2 and "сторінка 2" in re.search(r"<title>.*?</title>", a2).group(0))
a1 = (out / "aktsii.html").read_text(encoding="utf-8")
chk("сторінка 1: пейджер 1 2 3, ціна зростаюче (перша 300 ₴), список data-list=sale_week на всіх трьох", 'class="pg cur">1<' in a1 and 'href="aktsii_2.html"' in a1 and 'href="aktsii_3.html"' in a1 and a1.index("300 ₴") < a1.index("323 ₴") and all('data-list="sale_week"' in (out / f).read_text(encoding="utf-8") for f in ("aktsii.html", "aktsii_2.html", "aktsii_3.html")))
bs.SALE_ACTIVE = SALE
bs.write_aktsii(prods)   # повертаємо двосторінковий стан фікстури для наступних перевірок
for _f in ("aktsii_2.html", "aktsii_3.html"):
    (out / _f).unlink(missing_ok=True)
cats = {}
for p in prods:
    cats.setdefault(p["category"], []).append(p)
cl = sorted(cats, key=lambda c: -len(cats[c]))
os.environ["SITE_PICK_DATE"] = "2026-10-21"
bs.write_home(prods, cats, cl, {c: f"c{i}" for i, c in enumerate(cl)})
home = (out / "index.html").read_text(encoding="utf-8")
chk("герой: червона кнопка «Акції» (btn-sale, іконка-цінник) → aktsii.html + «Усі іграшки»", '<a class="btn btn-sale" href="aktsii.html">' in home and "<span>Акції</span>" in home and '<a class="btn" href="catalog.html">Каталог товарів</a><a class="btn btn-sale"' in home)
bs.SALE_ACTIVE = None
chk("нема акції (SALE_ACTIVE=None): aktsii_pick пустий, сторінка не пишеться, на головній кнопка «Каталог товарів» без «Акцій»", bs.aktsii_pick(prods) == [] and bs.write_aktsii(prods) == set())
bs.write_home(prods, cats, cl, {c: f"c{i}" for i, c in enumerate(cl)})
home2 = (out / "index.html").read_text(encoding="utf-8")
chk("головна без акції: «<div class=act><a class=btn href=catalog.html>Усі іграшки</a></div>», без aktsii.html", '<div class="act"><a class="btn" href="catalog.html">Каталог товарів</a></div>' in home2 and "aktsii.html" not in home2)
bs.SALE_ACTIVE = SALE
bs.SALE_WEEK_ENABLED = False
chk("прапор SALE_WEEK_ENABLED=False вимикає сторінку навіть при наявному наборі", bs.aktsii_pick(prods) == [])
bs.SALE_WEEK_ENABLED = True


# ── 7. контраст ──
def lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


chk(f"контраст: білий на #D93A3A (кнопка, бейдж) {round(ratio('#FFFFFF', '#D93A3A'), 2)}; #7A1313 на #FFF1EE (підпис «Акція до») {round(ratio('#7A1313', '#FFF1EE'), 2)}; #D93A3A на білому (нова ціна) {round(ratio('#D93A3A', '#FFFFFF'), 2)} — усі ≥ 4.5", all(ratio(*x) >= 4.5 for x in (("#FFFFFF", "#D93A3A"), ("#7A1313", "#FFF1EE"), ("#D93A3A", "#FFFFFF"))))

# ── 6. браузер: відлік, стилі ──
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    chk("playwright доступний", False)
    sync_playwright = None
if sync_playwright:
    real_now = datetime.now(timezone.utc).astimezone(KY)
    bs.SALE_ACTIVE = dict(SALE, until=real_now + timedelta(days=1, hours=2, minutes=30))
    bs.write_aktsii(prods)
    bs.write_product(p_sale, related=None, cat_slug={}, similar=[])
    cats_c = {}
    for p in prods:
        cats_c.setdefault(p["category"], []).append(p)
    cl_c = sorted(cats_c, key=lambda c: -len(cats_c[c]))
    bs.write_catalog("Каталог іграшок", prods, cl_c, {c: f"c{i}" for i, c in enumerate(cl_c)}, "catalog.html", active=None)
    (out / "index.json").write_text(json.dumps(bs.index_entries(prods), ensure_ascii=False), encoding="utf-8")
    shutil.copytree(HERE / "site" / "assets", out / "assets")
    os.chdir(out)

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for w in (1280, 390):
            ctx = br.new_context(viewport={"width": w, "height": 900}, reduced_motion="reduce")
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.route("**/googletagmanager.com/**", lambda r: r.abort())
            pg.route("**/stock.json*", lambda r: r.abort())
            pg.goto(f"{base}/aktsii.html", wait_until="load")
            pg.wait_for_timeout(500)
            v = pg.evaluate("(()=>{const l=String(document.querySelector('.sale-left')); const pr=document.querySelector('.card .pr-sale'); const old=document.querySelector('.card .pr-old'); const bd=document.querySelector('.card .sale-badge'); return [l, getComputedStyle(pr).color, getComputedStyle(old).textDecorationLine, getComputedStyle(bd).backgroundColor, document.documentElement.scrollWidth<=innerWidth, document.querySelectorAll('.sale-list .card').length]})()")
            chk(f"{w}px: на /aktsii немає спільного відліку, нова ціна червона, стара перекреслена (line-through), бейдж червоний, без горизонтального скролу {v}", v[0] == "null" and v[1] == "rgb(217, 58, 58)" and "line-through" in v[2] and v[3] == "rgb(217, 58, 58)" and v[4] and v[5] == 2)
            pg.goto(f"{base}/product-111.html", wait_until="load")
            pg.wait_for_timeout(300)
            chk(f"{w}px: на сторінці товару відлік «До кінця акції: 4 д …» (кінець партії 25.10 23:59, зараз 21.10 12:00 за SITE_SALE_NOW)", pg.evaluate("document.querySelector('.sale-left').textContent").startswith("До кінця акц"))
            pg.evaluate("document.querySelector('.sale-left').setAttribute('data-sale-until', new Date(Date.now()-60000).toISOString()); window.PT_saleTick();")
            chk(f"{w}px: після кінця акції відлік пише «акцію завершено»", pg.evaluate("document.querySelector('.sale-left').textContent") == "акцію завершено")
            chk(f"{w}px: без помилок JS", errs == [])
            # каталог (клієнтський рендер з index.json): акційна ціна ЗІ старою перекресленою і бейджем, як на /aktsii (аудит: каталог показував просто нижчу ціну)
            pg2 = ctx.new_page()
            pg2.route("**/googletagmanager.com/**", lambda r: r.abort())
            pg2.route("**/stock.json*", lambda r: r.abort())
            pg2.goto(f"{base}/catalog.html", wait_until="load")
            pg2.wait_for_timeout(900)
            cv = pg2.evaluate("(()=>{const vis=e=>{const r=e.getBoundingClientRect(); return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'}; const b=[...document.querySelectorAll('.card .sale-badge')].filter(vis); const o=[...document.querySelectorAll('.card s.pr-old')].filter(vis); return [b.length, o.length, b[0]?b[0].textContent:'', o[0]?o[0].textContent:'']})()")
            chk(f"{w}px: каталог: видимі картки акційних товарів мають бейдж «−15%» і перекреслену стару ціну {cv}", cv[0] >= 1 and cv[1] >= 1 and cv[2] == "−15%" and cv[3].endswith("₴"))
            pg2.close()
            ctx.close()
        br.close()

bs.SALE_ACTIVE = None
os.environ.pop("SITE_PICK_DATE", None)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
