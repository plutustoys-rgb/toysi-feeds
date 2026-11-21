#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_card_collections.py — 19.11.2026 (SEO 18.11 18:13): блок «Добірки» на картці товару — до 3 внутрішніх посилань на ЗБУДОВАНІ сторінки,
чиє правило добору картка задовольняє (бюджет podarunok-do-N → вік podarunok-na-N → бренд brend-* → адвент-календарі); анкор = H1; тіні/уцінка/немає в наявності — без блоку;
нема сторінки — нема посилання. Правила беремо з планів збірки; тут — юніт-перевірки _set_collection_rules/_collection_links і наскрізна збірка."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_PICK_DATE"] = "2026-10-10"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402
import site_photo_probe  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


# ── (1) юніт: правила й вибір посилань ──
plan_g = [("podarunok-do-100-hrn", "Подарунок до 100", "До 100", {"pmin": 0, "pmax": 100}, []),
          ("podarunok-do-300-hrn", "Подарунок до 300", "До 300", {"pmin": 0, "pmax": 300}, []),
          ("podarunok-na-mykolaia", "На Миколая", "На Миколая", {"pmin": 100, "pmax": 700, "bands": 6}, []),
          ("podarunok-na-3-roky", "Подарунок на 3 роки", "На 3 роки", {"age": (1, 3)}, []),
          ("podarunok-na-2-roky", "Подарунок на 2 роки", "На 2 роки", {"age": (1, 2)}, [])]
plan_b = [("brend-funko-pop", "Фігурки Funko Pop!", None, [], {}), ("brend-funko-pop-anime", "Funko аніме", "brend-funko-pop", [], {})]
plan_s = [("advent-kalendari", "Адвент-календарі для дітей", "t", bs.SEL_PARENT_GIFT, [], {}), ("zoshyty-u-kosu-liniiu", "Зошити", "t", "zoshyty", [], {})]
bs._set_collection_rules(plan_g, plan_b, plan_s)
R = bs.COLLECTION_RULES
chk("правила: бюджетні лише без bands/ny (Миколай не бюджет), за зростанням pmax; вікові — вужчий діапазон першим; бренд — лише головні; добір — лише з батьком «хаб подарунків»",
    [x[2] for x in R["budget"]] == ["podarunok-do-100-hrn", "podarunok-do-300-hrn"] and [x[2] for x in R["age"]] == ["podarunok-na-2-roky", "podarunok-na-3-roky"]
    and [x[0] for x in R["brand"]] == ["brend-funko-pop"] and [x[0] for x in R["sel"]] == ["advent-kalendari", "zoshyty-u-kosu-liniiu"])


def prod(**kw):
    p = {"id": "1", "name": "Фігурка Funko Pop! Герой", "vendor": "Funko", "price": 250, "stock": 3, "photo": "x.jpg", "category": "Фігурки", "contribution": 100.0,
         "desc": "Від 2 років. Гарна фігурка", "rt": False}
    p.update(kw)
    return p


p1 = prod()
L = bs._collection_links(p1)
chk(f"картка 250 ₴, від 2 років, Funko → 3 посилання у порядку бюджет → вік → бренд: {[u for u, _a in L]}",
    [u for u, _a in L] == ["podarunok-do-300-hrn.html", "podarunok-na-2-roky.html", "brend-funko-pop.html"] and L[0][1] == "Подарунок до 300")
chk("картка «18+» (відомий вік > GIFT_MAX_AGE) НЕ отримує бюджетне посилання (бюджетні сторінки її відсікають), вікове — не з нашого діапазону", [u for u, _a in bs._collection_links(prod(price=250, desc="Від 18 років", vendor="T", name="Настільна гра"))] == [])
chk("тіні, уцінка, немає в наявності, ціна 0 — без блоку", all(bs._collection_links(prod(**kw)) == [] for kw in ({"shadow": True}, {"name": "Уцінка. Funko Pop", "price": 100}, {"stock": 0}, {"price": 0})))
chk("ціна більша за найбільший бюджет → без бюджетного посилання; вік невідомий → без вікового", [u for u, _a in bs._collection_links(prod(price=900, desc="Фігурка"))] == ["brend-funko-pop.html"])
chk("RT-картка: лише бренд/добір (подарункові сторінки — тільки Toysi)", [u for u, _a in bs._collection_links(prod(rt=True))] == ["brend-funko-pop.html"])
adv = prod(name="Адвент-календар конструктор 24 віконця", vendor="T", desc="", price=900)
chk("адвент: картка з «адвент» у назві → advent-kalendari (і лише якщо сторінка в плані)", [u for u, _a in bs._collection_links(adv)] == ["advent-kalendari.html"])
chk("добір не лише з батьком «хаб подарунків» (SEO 21.11): зошит у косу лінію → zoshyty-u-kosu-liniiu (батько — категорія)", [u for u, _a in bs._collection_links(prod(name="Зошит у косу лінію 12 арк", vendor="T", desc="", price=900))] == ["zoshyty-u-kosu-liniiu.html"])
cap_items = [{"id": str(i)} for i in range(bs.SELECTION_MAX_ITEMS)]
bs._set_collection_rules([], [], [("zoshyty-u-kosu-liniiu", "Зошити", "t", "zoshyty", cap_items, {})])
chk("добір обрізано до SELECTION_MAX_ITEMS: лінкуємо лише картку, що реально на сторінці (id 5), не ту, що лише за правилом (id 9999)",
    [u for u, _a in bs._collection_links(prod(id="5", name="Зошит у косу лінію", vendor="T", desc="", price=900))] == ["zoshyty-u-kosu-liniiu.html"]
    and bs._collection_links(prod(id="9999", name="Зошит у косу лінію", vendor="T", desc="", price=900)) == [])
chk("noindex-картка добору не лінкується (SEO: лише без noindex)", bs._collection_links(prod(id="5", name="Зошит у косу лінію", vendor="T", desc="", price=900, noindex=True)) == [])
bs._set_collection_rules([], [], [])
chk("нема збудованих сторінок — нема посилань", bs._collection_links(p1) == [] and bs._collections_html(p1) == "")

# ── (2) наскрізна збірка ──
site_photo_probe.run = lambda idx: None
tmp = Path(tempfile.mkdtemp())
bs.LIQPAY_LIVE = True
bs.SUPPLIER_KV_FILE = str(tmp / "no_kv.json")
bs.SUPPLIER_RT_FILE = str(tmp / "no_rt.json")
bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")
bs.SELECTION_TEXTS_FILE = str(tmp / "selection_pages.json")
(tmp / "selection_pages.json").write_text(json.dumps({"advent-kalendari": {"intro": "Адвент-календарі для дітей: конструктори з віконцями.", "faq": [{"q": f"q{i}", "a": f"a{i}"} for i in range(3)]}}, ensure_ascii=False), encoding="utf-8")


def toysi(pid, name, cat, price, desc="Опис товару достатньої довжини для картки сайту, понад сорок символів.", stock=5):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": stock, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": desc, "params": {}, "url": f"https://toysi.ua/p/{pid}", "rt_cost": 10}


cat = {}
for i in range(1, 40):
    cat[str(i)] = toysi(i, f"Машинка {i}", "Машинки", 50 + i, desc="Від 3 років. Опис товару достатньої довжини для картки сайту, понад сорок символів.")
for i in range(700, 714):
    cat[str(i)] = toysi(i, f"Адвент-календар конструктор {i}, 24 віконця", "Конструктори", 120 + i % 7)
bs.tp.fetch_toysi_catalog = lambda: dict(cat)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.GIFT_MIN_ITEMS = 1
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
err = io.StringIO()
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
    bs.build()
built_gift = sorted(q.stem for q in out.glob("podarunok-*.html"))
pp = out / "product-5.html"
h = pp.read_text(encoding="utf-8") if pp.exists() else ""
blk = re.search(r'<nav class="gift-links card-collections".*?</nav>', h, re.S)
links = re.findall(r'<li><a href="([^"]+)">([^<]*)</a>', blk.group(0)) if blk else []
print("   збудовано сторінок подарунків:", built_gift[:8], "| посилання на картці 5:", links)
chk("на картці є блок «Добірки» (≤3 посилання), усі ведуть на реально збудовані сторінки, анкор = H1 сторінки",
    bool(blk) and 1 <= len(links) <= 3 and all((out / u).exists() for u, _a in links) and all(f'<h1 class="page">{a}</h1>' in (out / u).read_text(encoding="utf-8") for u, a in links))
adv_h = (out / "product-705.html").read_text(encoding="utf-8")
chk("картка адвент-календаря містить посилання на advent-kalendari.html (збудовано)", 'href="advent-kalendari.html">Адвент-календарі для дітей</a>' in adv_h and (out / "advent-kalendari.html").exists())
chk("блок не лишається порожнім: ні «<ul></ul>», ні заголовка без посилань на жодній картці", not any("card-collections" in q.read_text(encoding="utf-8") and "<ul></ul>" in q.read_text(encoding="utf-8") for q in list(out.glob("product-*.html"))[:300]))
print("\n" + ("ALL OK" if not F else f"FAIL: {len(F)}"))
sys.exit(1 if F else 0)
