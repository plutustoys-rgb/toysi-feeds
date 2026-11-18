#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_coloring_pages.py — 18.11.2026 (SEO: «безкоштовні розмальовки з Плутусом», рішення власника «Так, робимо»).
Хаб /rozmalovky.html + добірки /rozmalovky-novorichni|-dlia-malyukiv|-dlia-ditei.html. Аркуші (PNG+PDF) — site/colorings/<id>.png|.pdf, перелік і alt — site/rozmalovky.json, тексти — site/rozmalovky_texts.json.
Сторінка будується лише з текстом І ≥3 аркушів із обома файлами; хаб — коли є хоч одна добірка; футер/категорії розмальовок/sitemap узгоджені з фактично записаним; застарілі сторінки прибираються."""
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


site_photo_probe.run = lambda idx: None
SITE = HERE / "site"
tmp = Path(tempfile.mkdtemp())

# ── (1) shipped-тексти ──
_man, texts = bs._load_coloring(str(tmp / "no_manifest.json"), str(SITE / "rozmalovky_texts.json"))
chk("site/rozmalovky_texts.json: 4 тексти (хаб + 3 добірки) проходять завантажувач (h1, intro, ≥3 питання; без «розвиває/корисно», <>, імені постачальника)", set(texts) == {"rozmalovky", *bs.COLORING_SUBPAGES})
bad = tmp / "bad_texts.json"
bad.write_text(json.dumps({"rozmalovky-novorichni": {"h1": "H", "intro": "Розмальовка розвиває моторику.", "faq": [{"q": f"q{i}", "a": f"a{i}"} for i in range(3)]},
                           "rozmalovky-dlia-ditei": {"h1": "H", "intro": "Ок.", "faq": [{"q": "q", "a": "a"}]},
                           "rozmalovky-dlia-malyukiv": {"h1": "H", "intro": "Малюнки для друку.", "faq": [{"q": f"q{i}", "a": f"a{i}"} for i in range(3)]}}, ensure_ascii=False), encoding="utf-8")
err = io.StringIO()
with contextlib.redirect_stderr(err):
    _m, got = bs._load_coloring(str(tmp / "no_manifest.json"), str(bad))
chk("заборонене слово («розвиває») і <3 питань — запис пропущено ГОЛОСНО; чистий лишається", set(got) == {"rozmalovky-dlia-malyukiv"} and err.getvalue().count("запис пропущено") == 2)

# ── (2) збірка ──
OUTD = Path(tempfile.mkdtemp())
cdir = OUTD / bs.COLORING_DIR
cdir.mkdir(parents=True)


def sheet_files(sid):
    (cdir / f"{sid}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (cdir / f"{sid}.pdf").write_bytes(b"%PDF-1.4\n")


man = {}
SHEETS = {"rozmalovky-novorichni": 4, "rozmalovky-dlia-malyukiv": 3, "rozmalovky-dlia-ditei": 2}
for slug, n in SHEETS.items():
    lst = []
    for i in range(1, n + 1):
        sid = f"plutus-{slug[11:]}-{i}"
        sheet_files(sid)
        lst.append({"id": sid, "title": f"Аркуш {i}: {slug}", "alt": f"Розмальовка: Плутус, сюжет {i} ({slug})"})
    man[slug] = {"sheets": lst}
man["rozmalovky-dlia-ditei"]["sheets"].append({"id": "plutus-bez-faylu", "title": "Без файлу", "alt": "Розмальовка без файлів"})   # файлів нема → пропускається ГОЛОСНО
man["rozmalovky-novorichni"]["sheets"].append({"id": "Bad_ID!", "title": "x", "alt": "y"})   # некоректний id → пропускається
(tmp / "man.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
bs.COLORING_MANIFEST_FILE = str(tmp / "man.json")
bs.COLORING_TEXTS_FILE = str(SITE / "rozmalovky_texts.json")


def toysi(pid, name, cat, stock=5):
    return {"id": str(pid), "name": name, "vendor": "T", "price": 120 + pid % 100, "stock": stock, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/p/{pid}"}


catalog = {str(i): toysi(i, f"Іграшка {i}", "Машинки" if i % 2 else "Конструктори") for i in range(1, 41)}
for i in range(101, 111):
    catalog[str(i)] = toysi(i, f"Розмальовка «Тварини {i}» А4 {i} стор.", "Класичні розмальовки")
for i in range(201, 204):
    catalog[str(i)] = toysi(i, f"Водна розмальовка «Море {i}»", "Водні розмальовки")
catalog["301"] = toysi(301, "Розмальовка вичерпана", "Класичні розмальовки", stock=0)
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")
bs.SUPPLIER_RT_FILE = str(tmp / "no_rt.json")
bs.SUPPLIER_KV_FILE = str(tmp / "no_kv.json")
bs.OUT = str(OUTD)


def build():
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        bs.build()
    return err.getvalue()


def pg(f):
    q = OUTD / f
    return q.read_text(encoding="utf-8") if q.exists() else ""


err = build()
built = {q.stem for q in OUTD.glob("rozmalovky*.html")}
chk(f"збудовано {sorted(built)}: хаб + новорічні (4) + малюки (3); «для дітей» (лише 2 аркуші із файлами < 3) — НІ", built == {"rozmalovky", "rozmalovky-novorichni", "rozmalovky-dlia-malyukiv"})
chk("голосні попередження: аркуш без файлів, некоректний id, «лише 2 аркушів із файлами (< 3)»", "plutus-bez-faylu" in err and "аркуш без коректних id" in err and "rozmalovky-dlia-ditei — лише 2 аркушів" in err)
h = pg("rozmalovky-novorichni.html")
chk("новорічна: H1 з файлу, 4 аркуші: <img alt=…> + «Завантажити PDF» (download) на кожен; без форм/полів пошти; ImageObject ×4, FAQPage, BreadcrumbList; canonical на себе; не noindex",
    "Новорічні розмальовки для дітей" in h and h.count('class="coloring-sheet"') == 4 and h.count('class="btn coloring-dl"') == 4 and h.count('<img src="colorings/plutus-novorichni-') == 4 and 'alt="Розмальовка: Плутус, сюжет 1 (rozmalovky-novorichni)"' in h
    and h.count('"@type": "ImageObject"') == 4 and '"@type": "FAQPage"' in h and '"@type": "BreadcrumbList"' in h and 'rel="canonical" href="https://plutustoys.com.ua/rozmalovky-novorichni.html"' in h
    and "noindex" not in h.split("</head>")[0] and "<form" not in h.split("<footer")[0].split("</header>")[-1] and 'href="colorings/plutus-novorichni-1.pdf" download' in h)
chk("блок «Розмальовки-книжки й набори»: живі товари з категорій розмальовок (≤12, без вичерпаного), посилання на хаб", "Розмальовки-книжки й набори" in h and h.count('class="card') <= 12 and h.count('class="card') >= 4 and "вичерпана" not in h and 'href="rozmalovky.html"' in h)
hub = pg("rozmalovky.html")
chk("хаб: посилання на 2 збудовані добірки (не на «для дітей»), усі 7 прев'ю", 'href="rozmalovky-novorichni.html"' in hub and 'href="rozmalovky-dlia-malyukiv.html"' in hub and "rozmalovky-dlia-ditei" not in hub and hub.count('class="coloring-sheet"') == 7)
idx_h = pg("index.html")
chk("футер: «Розмальовки для друку» → rozmalovky.html на головній і на сторінці добірки", 'href="rozmalovky.html">Розмальовки для друку<' in idx_h and 'href="rozmalovky.html">Розмальовки для друку<' in h)
cats = [q for q in OUTD.glob("category-*.html") if "Тварини 101" in q.read_text(encoding="utf-8")]
chk("сторінка категорії «Класичні розмальовки» лінкує на хаб (notice)", bool(cats) and any('href="rozmalovky.html">завантажити й роздрукувати' in q.read_text(encoding="utf-8") for q in cats))
chk("інші категорії (Машинки) notice-у не мають", not any("завантажити й роздрукувати" in q.read_text(encoding="utf-8") for q in OUTD.glob("category-*.html") if "Іграшка 1<" in q.read_text(encoding="utf-8") and "Тварини 101" not in q.read_text(encoding="utf-8")))
sm = "".join(q.read_text(encoding="utf-8") for q in OUTD.glob("sitemap-*.xml"))
chk("sitemap: хаб і 2 добірки — рівно раз; «для дітей» немає; сторінки індексуються (не noindex)", all(sm.count(f"/{s}.html") == 1 for s in ("rozmalovky", "rozmalovky-novorichni", "rozmalovky-dlia-malyukiv")) and "rozmalovky-dlia-ditei" not in sm)
chk("og:image = перший аркуш сторінки; жодного KIDDISVIT/kv-/внутрішніх id на сторінках розмальовок", 'colorings/plutus-novorichni-1.png' in h.split("</head>")[0] and not re.search(r"kiddisvit|kv-\d|rt-\d", h + hub, re.I))
# приберемо аркуші → повторна збірка прибирає застарілі сторінки й посилання
for q in cdir.glob("*"):
    q.unlink()
err = build()
chk("аркушів нема → жодної сторінки розмальовок (застарілі прибрано), у футері нема посилання", not list(OUTD.glob("rozmalovky*.html")) and 'href="rozmalovky.html"' not in pg("index.html"))
print("\n" + ("ALL OK" if not F else f"FAIL: {len(F)}"))
sys.exit(1 if F else 0)
