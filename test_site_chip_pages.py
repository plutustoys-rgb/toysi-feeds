#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_chip_pages.py — 16.10.2026 (власник: «коли нажимаєш на чіп повинно перекидати на окрему сторінку, як при нажиманні Уцінки. Зі своїми сортувальниками»).
Чип головної = посилання на свою сторінку (podibrka-*.html; Halloween — /helovin.html); сторінка — шаблон «Уцінки»: крупна назва + панель сортування/категорії/ціни (cat-controls + cat-ids), до 72 карток на 3 сторінки,
пагінація як у каталозі; сезонні сторінки (Миколай, До Різдва, Halloween) існують лише у своє вікно; стару сторінку прибирає крок прибирання."""
import json
import os
import re
import sys
import tempfile
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


IMG = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
_n = [0]
W = ["Калина", "Мальви", "Барвінок", "Соняшник", "Лелека", "Журавель", "Ромашка", "Волошка", "Тополя", "Берегиня", "Веселка", "Дніпро", "Карпати", "Чорнобривці", "Мавка", "Лісовик", "Космос", "Зоря", "Мрія", "Казка"]


def P(name, price, contrib, cat, stock=5):
    _n[0] += 1
    return {"id": str(6000 + _n[0]), "name": name, "category": cat, "price": price, "contribution": contrib, "stock": stock, "photo": IMG, "old_price": None}


def many(fmt, n, price, contrib, cat_fmt, start=0):
    return [P(fmt.format(W[(start + i) % 20] + str(start + i)), price, contrib, cat_fmt.format(start + i)) for i in range(n)]


prods = []
prods += many("Набір «{}» для дівчинки", 40, 300, 80, "Подар{}")           # bday: 40 карток із 40 категорій
prods += many("Українська абетка «{}»", 40, 220, 60, "Укр{}")              # ukr
prods += many("Новорічна ялинка «{}»", 40, 250, 60, "Ялинки{}")            # ny (11.25–12.31), xmas (12.1–12.25)
prods += many("Гарбуз декоративний «{}»", 30, 200, 60, "Гарб{}")           # hel (10.1–10.31)
prods += [P(f"Популярна іграшка «{W[i % 20]}{i}»", 300 + i, 90, f"Поп{i}") for i in range(30)]
newp = [P(f"Свіжа іграшка «{W[i % 20]}{i}»", 260 + i, 70, f"Нов{i}") for i in range(30)]   # ТОП Toysi (toysi_top.json): чип «Новинки»
prods += newp
dem = [p["id"] for p in prods[-60:-30]]
prods += [P(f"Звичайна іграшка «{W[i % 20]}{i}»", 300 + i, 150, f"Звич{i}") for i in range(14)]   # основна добірка каруселі (внесок ≥100) — без неї чипів на головній нема
bs._load_demand_featured = lambda today=None: {"ids": dem, "categories": [], "date": None, "source": "test"}
bs.HELOVIN_JSON = str(Path(tempfile.mkdtemp()) / "none.json")
cats = {}
for p in prods:
    cats.setdefault(p["category"], []).append(p)
cl = sorted(cats, key=lambda c: -len(cats[c]))
slug = {c: bs.slugify(c) for c in cats}
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)


def build_day(day):
    os.environ["SITE_PICK_DATE"] = day
    w = set(bs.write_chip_pages(prods, cl, slug)) | set(bs.write_helovin(prods, cl, slug))
    bs.SALE_ACTIVE = None
    bs.write_home(prods, cats, cl, slug)
    return w


TOP = Path(tempfile.mkdtemp()) / "toysi_top.json"
bs.TOYSI_TOP_FILE = str(TOP)
TOP.write_text(json.dumps({"date": "2026-12-10", "fetched_at": "2026-12-10T16:05:00", "lists": {"shipments": [p["id"] for p in newp[:20]], "week": [p["id"] for p in newp[10:]], "yesterday": [], "month": []}}), encoding="utf-8")
w1 = build_day("2026-12-10")
chk(f"10.12: сторінки Українське/Популярне/День народження/Миколай/До Різдва є (24 + решта на стор. 2), Геловіну нема: {sorted(w1)}",
    {"podibrka-ukrainske.html", "podibrka-populiarne.html", "podibrka-den-narodzhennia.html", "podibrka-mykolai-novyi-rik.html", "podibrka-do-rizdva.html"} <= w1 and "helovin.html" not in w1 and "podibrka-ukrainske_2.html" in w1)
u = (out / "podibrka-ukrainske.html").read_text(encoding="utf-8")
ids = re.search(r'<script type="application/json" id="cat-ids">(.*?)</script>', u).group(1)
n_ids = len(ids.strip("[]").split(","))
chk(f"сторінка чипа = шаблон «Уцінки»: cat-bar із крупною назвою, #cat-controls, #cat-ids ({n_ids} товарів ≤ 72), canonical, rel=next на стор.2",
    'class="cat-bar"' in u and '<h1 class="page cat-h1">Українське для дітей</h1>' in u and 'id="cat-controls"' in u and 6 <= n_ids <= bs.CHIP_PAGE_N
    and 'rel="canonical" href="https://plutustoys.com.ua/podibrka-ukrainske.html"' in u and 'rel="next" href="podibrka-ukrainske_2.html"' in u and "<title>Українське для дітей — іграшки | PlutusToys</title>" in u)
u2 = (out / "podibrka-ukrainske_2.html").read_text(encoding="utf-8")
chk("стор. 2: статична (пагінація для краулера), canonical на себе, rel=prev, без панелі", 'id="cat-controls"' not in u2 and 'rel="canonical" href="https://plutustoys.com.ua/podibrka-ukrainske_2.html"' in u2 and 'rel="prev" href="podibrka-ukrainske.html"' in u2)
pop_ids = {x.strip() for x in re.search(r'<script type="application/json" id="cat-ids">(.*?)</script>', (out / "podibrka-populiarne.html").read_text(encoding="utf-8")).group(1).replace('"', "").strip("[]").split(",")}
chk(f"«Популярне зараз»: склад — зі списку попиту (усі {len(pop_ids)} у списку, ≥6)", len(pop_ids) >= 6 and pop_ids <= set(dem))
home = (out / "index.html").read_text(encoding="utf-8")
hrefs = dict(re.findall(r'<a class="chip(?: chip-theme)?" data-set="(\w+)" href="([^"]+)"', home))
chk(f"головна: чипи — посилання на свої сторінки {hrefs}", {"ukr", "pop", "bday", "ny", "xmas"} <= set(hrefs) and all(hrefs[k] == bs.CHIP_PAGE_FILES[k] and (out / hrefs[k]).exists() for k in hrefs) and "hel" not in hrefs)
chk("кожен чип головної має живу сторінку (нема чипів-«сиріт»)", all((out / v).exists() for v in hrefs.values()))
sm = bs.write_sitemap  # sitemap сегменти беруться з paged: файли мають бути в наборі, який віддає write_chip_pages
chk("«Новинки» (ТОП Toysi, власник 16.10): сторінка podibrka-novynky.html є, чип на головній веде на неї", "podibrka-novynky.html" in w1 and hrefs.get("new") == "podibrka-novynky.html" and "Новинки" in home)
nv = (out / "podibrka-novynky.html").read_text(encoding="utf-8")
nv_ids = [x.strip().strip('"') for x in re.search(r'<script type="application/json" id="cat-ids">(.*?)</script>', nv).group(1).strip("[]").split(",")]
chk(f"склад «Новинок»: спершу shipments у порядку Toysi, далі week без повторів ({len(nv_ids)} id, перші збігаються)", nv_ids[:20] == [p["id"] for p in newp[:20]] and len(nv_ids) == len(set(nv_ids)) == 30)
TOP.write_text(json.dumps({"date": "2026-11-01", "lists": {"shipments": [p["id"] for p in newp]}}), encoding="utf-8")
w_old = build_day("2026-12-10")
chk("застарілий toysi_top.json (> 3 днів) — чипа «Новинки» і сторінки нема, решта чипів на місці", "podibrka-novynky.html" not in w_old and 'data-set="new"' not in (out / "index.html").read_text(encoding="utf-8") and "podibrka-ukrainske.html" in w_old)
TOP.unlink()
w_none = build_day("2026-12-10")
chk("нема toysi_top.json — чип «Новинки» прихований без помилки", "podibrka-novynky.html" not in w_none and 'data-set="new"' not in (out / "index.html").read_text(encoding="utf-8"))
TOP.write_text(json.dumps({"date": "2026-12-10", "lists": {"shipments": [p["id"] for p in newp]}}), encoding="utf-8")
w1 = build_day("2026-12-10")
chk("файли сторінок чипів починаються з «podibrka» (префікс у кроці прибирання застарілих сторінок)", all(f.startswith("podibrka") for f in w1))

w2 = build_day("2026-10-20")
chk(f"20.10: Halloween = /helovin.html з панеллю, Миколай/До Різдва нема; Українське/Популярне/День народження лишились: {sorted(w2)}",
    "helovin.html" in w2 and "podibrka-mykolai-novyi-rik.html" not in w2 and "podibrka-do-rizdva.html" not in w2 and {"podibrka-ukrainske.html", "podibrka-populiarne.html", "podibrka-den-narodzhennia.html"} <= w2)
hv = (out / "helovin.html").read_text(encoding="utf-8")
chk("/helovin.html — шаблон з панеллю (cat-controls, крупна назва) і data-list=helovin", 'id="cat-controls"' in hv and '<h1 class="page cat-h1">Halloween</h1>' in hv and 'data-list="helovin"' in hv)
home2 = (out / "index.html").read_text(encoding="utf-8")
chk("головна 20.10: чип «Halloween» → helovin.html", re.search(r'data-set="hel" href="helovin\.html"', home2) is not None)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
