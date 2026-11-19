#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_coloring_pages.py — 18.11.2026 (SEO: «безкоштовні розмальовки з Плутусом», рішення власника «Так, робимо»; аудит #1707).
Хаб /rozmalovky.html + добірки /rozmalovky-novorichni|-dlia-malyukiv|-dlia-ditei.html. Аркуші (PNG+PDF) — site/colorings/<id>.png|.pdf, перелік і alt — site/rozmalovky.json, тексти — site/rozmalovky_texts.json.
Сторінка будується лише з текстом І ≥3 аркушів із обома файлами (PNG — справжній, розмір береться з заголовка); число «N аркушів» у тексті = фактична кількість; хаб — лише коли збудовано ВСІ три добірки;
футер/категорії розмальовок/sitemap узгоджені з фактично записаним; застарілі сторінки прибираються; title/alt аркушів без обіцянок розвитку/чужих марок/імені постачальника."""
import contextlib
import io
import json
import os
import re
import struct
import sys
import tempfile
import zlib
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


def png(w=1240, h=1754):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00" * 4)) + chunk(b"IEND", b"")


# ── (1) shipped-тексти ──
_man, texts = bs._load_coloring(str(tmp / "no_manifest.json"), str(SITE / "rozmalovky_texts.json"))
chk("site/rozmalovky_texts.json: 4 тексти (хаб + 3 добірки) проходять завантажувач (h1, intro, ≥3 питання; без обіцянок розвитку/користі, чужих марок, <>, імені постачальника)", set(texts) == {"rozmalovky", *bs.COLORING_SUBPAGES})
GOOD3 = [{"q": f"q{i}", "a": f"a{i}"} for i in range(3)]
bad = tmp / "bad_texts.json"
bad.write_text(json.dumps({"rozmalovky-novorichni": {"h1": "H", "intro": "Розмальовка розвиває моторику.", "faq": GOOD3}, "rozmalovky-dlia-ditei": {"h1": "H", "intro": "Ок.", "faq": [{"q": "q", "a": "a"}]},
                           "rozmalovky-dlia-malyukiv": {"h1": "H", "intro": "Малюнки для друку.", "faq": GOOD3}, "rozmalovky": {"h1": "H", "intro": "Малюнки на тему Disney.", "faq": GOOD3}}, ensure_ascii=False), encoding="utf-8")
err = io.StringIO()
with contextlib.redirect_stderr(err):
    _m, got = bs._load_coloring(str(tmp / "no_manifest.json"), str(bad))
chk("заборонене слово («розвиває», «Disney») і <3 питань — запис пропущено ГОЛОСНО; чистий лишається", set(got) == {"rozmalovky-dlia-malyukiv"} and err.getvalue().count("запис пропущено") == 3)
for w, ok_ in (("розвиток моторики", True), ("на користь дитині", True), ("тренує дрібну моторику", True), ("навчить малювати", True), ("Peppa Pig", True), ("Kiddi-Svit", True), ("рoзвиває", True), ("Плутус біля ялинки", False), ("Сніговик з подарунками", False)):
    chk(f"заборонене/дозволене в тексті: «{w}» → {'відсікається' if ok_ else 'дозволено'}", bs._coloring_bad(w) is ok_)

# ── (2) збірка ──
OUTD = Path(tempfile.mkdtemp())
cdir = OUTD / bs.COLORING_DIR
cdir.mkdir(parents=True)


def sheet_files(sid, w=1240, h=1754):
    (cdir / f"{sid}.png").write_bytes(png(w, h))
    (cdir / f"{sid}.pdf").write_bytes(b"%PDF-1.4\n")


def mk_manifest(counts):
    man = {}
    for slug, n in counts.items():
        lst = []
        for i in range(1, n + 1):
            sid = f"plutus-{slug[11:]}-{i}"
            sheet_files(sid)
            lst.append({"id": sid, "title": f"Аркуш {i}: {slug}", "alt": f"Розмальовка: Плутус, сюжет {i} ({slug})"})
        man[slug] = {"sheets": lst}
    return man


SHEETS = {"rozmalovky-novorichni": 8, "rozmalovky-dlia-malyukiv": 6, "rozmalovky-dlia-ditei": 6}   # як у шипнутих текстах («8 аркушів» / «6» / «6»)
man = mk_manifest(SHEETS)
man["rozmalovky-dlia-ditei"]["sheets"].append({"id": "plutus-bez-faylu", "title": "Без файлу", "alt": "Розмальовка без файлів"})   # файлів нема → пропускається ГОЛОСНО
man["rozmalovky-novorichni"]["sheets"].append({"id": "Bad_ID!", "title": "x", "alt": "y"})   # некоректний id
man["rozmalovky-novorichni"]["sheets"].append({"id": "plutus-novorichni-1", "title": "дубль", "alt": "дубль"})   # дубль id → пропускається
man["rozmalovky-dlia-malyukiv"]["sheets"].append({"id": "plutus-dlia-malyukiv-x", "title": "Розвиває моторику", "alt": "Розмальовка, що розвиває"})   # заборонене слово → пропускається
sheet_files("plutus-dlia-malyukiv-x")
(cdir / "plutus-not-png.png").write_bytes(b"not a png at all, just text bytes here")
(cdir / "plutus-not-png.pdf").write_bytes(b"%PDF")
man["rozmalovky-dlia-malyukiv"]["sheets"].append({"id": "plutus-not-png", "title": "Не PNG", "alt": "Файл не PNG"})
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
for i in range(401, 404):
    catalog[str(i)] = toysi(i, f"Книжка-малювалка «Казка {i}»", "Розмальовки та розпис")
catalog["301"] = toysi(301, "Розмальовка вичерпана", "Класичні розмальовки", stock=0)
catalog["302"] = toysi(302, "Уцінка. Розмальовка з дефектом", "Класичні розмальовки")
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
chk(f"збудовано {sorted(built)}: хаб + 3 добірки", built == {"rozmalovky", "rozmalovky-novorichni", "rozmalovky-dlia-malyukiv", "rozmalovky-dlia-ditei"})
chk("голосні попередження: аркуш без файлів, некоректний id, дубль id, заборонене слово в title/alt, не-PNG", "plutus-bez-faylu" in err and "аркуш без коректних id" in err and "повторюється" in err and "заборонене слово" in err and "не є коректним PNG" in err)
h = pg("rozmalovky-novorichni.html")
chk("новорічна: H1, 8 аркушів: <img alt> + «Завантажити PDF» (download, type=pdf), перший без lazy; без форм; ImageObject ×8 з реальним розміром 1240×1754, FAQPage, BreadcrumbList; canonical на себе; не noindex",
    "Новорічні розмальовки для дітей" in h and h.count('class="coloring-sheet"') == 8 and h.count('class="btn coloring-dl"') == 8 and h.count('<img src="colorings/plutus-novorichni-') == 8 and 'alt="Розмальовка: Плутус, сюжет 1 (rozmalovky-novorichni)"' in h
    and h.count('"@type": "ImageObject"') == 8 and '"width": 1240' in h and '"height": 1754' in h and h.count('"acquireLicensePage": "https://plutustoys.com.ua/rozmalovky.html#umovy"') == 8 and h.count('"license": "https://plutustoys.com.ua/rozmalovky.html#umovy"') == 8 and h.count('"creditText": "PlutusToys"') == 8 and h.count('"copyrightNotice": "© PlutusToys"') == 8 and 'id="umovy"' not in h and '"@type": "FAQPage"' in h and '"@type": "BreadcrumbList"' in h
    and 'rel="canonical" href="https://plutustoys.com.ua/rozmalovky-novorichni.html"' in h and "noindex" not in h.split("</head>")[0] and "<form" not in h.split("<footer")[0].split("</header>")[-1]
    and 'href="colorings/plutus-novorichni-1.pdf" type="application/pdf" download' in h and re.search(r'plutus-novorichni-1\.png"[^>]*height="438"[^>]*decoding="async"', h) and 'plutus-novorichni-2.png"' in h and re.search(r'plutus-novorichni-2\.png"[^>]*loading="lazy"', h))
chk("блок «Розмальовки-книжки й набори»: живі товари з категорій розмальовок (4–12; без вичерпаного й уцінки), посилання на хаб; 3 категорії + «Розмальовки та розпис»", "Розмальовки-книжки й набори" in h and 4 <= h.count('class="card') <= 12 and "вичерпана" not in h and "з дефектом" not in h and 'href="rozmalovky.html"' in h and "Казка" in h)
hub = pg("rozmalovky.html")
chk("хаб: посилання на 3 добірки, усі 20 прев'ю без дублів", all(f'href="{s}.html"' in hub for s in bs.COLORING_SUBPAGES) and hub.count('class="coloring-sheet"') == 20)
idx_h = pg("index.html")
chk("футер: «Розмальовки для друку» → rozmalovky.html на головній і на сторінці добірки", 'href="rozmalovky.html">Розмальовки для друку<' in idx_h and 'href="rozmalovky.html">Розмальовки для друку<' in h)
chk("сторінки категорій розмальовок (Класичні, Водні, «Розмальовки та розпис») лінкують на хаб (notice); Машинки — ні",
    all('href="rozmalovky.html">завантажити й роздрукувати' in pg(f"category-{s}.html") for s in ("klasychni-rozmalovky", "vodni-rozmalovky", "rozmalovky-ta-rozpys") if (OUTD / f"category-{s}.html").exists())
    and sum(1 for s in ("klasychni-rozmalovky", "vodni-rozmalovky", "rozmalovky-ta-rozpys") if (OUTD / f"category-{s}.html").exists()) >= 2 and "завантажити й роздрукувати" not in pg("category-mashynky.html"))
chk("хаб: блок «Умови використання» з id=umovy (ціль license/acquireLicensePage); текст за ТЗ; лише на хабі", hub.count('id="umovy"') == 1 and "Розмальовки з Плутусом можна безкоштовно завантажити й роздрукувати для домашнього використання. Персонаж Плутус — PlutusToys." in hub)
sm = "".join(q.read_text(encoding="utf-8") for q in OUTD.glob("sitemap-*.xml"))
chk("sitemap: хаб і 3 добірки — рівно раз", all(sm.count(f"/{s}.html") == 1 for s in ("rozmalovky", *bs.COLORING_SUBPAGES)))
chk("og:image = перший аркуш; жодного KIDDISVIT/kv-/rt- на сторінках розмальовок", 'colorings/plutus-novorichni-1.png' in h.split("</head>")[0] and not re.search(r"kiddisvit|kv-\d|rt-\d", h + hub, re.I))

# ── (3) інваріанти узгодженості ──
# «старі» тексти (до 18.11: з числами аркушів і згадками «трьох добірок», без standalone) — фікстура; шипнуті тексти тепер standalone
legacy = json.loads((SITE / "rozmalovky_texts.json").read_text(encoding="utf-8"))
for k_ in legacy:
    legacy[k_].pop("standalone", None)
legacy["rozmalovky-novorichni"]["intro"] = "Новорічні розмальовки з Плутусом — 8 аркушів зі святковими сюжетами."
legacy["rozmalovky-dlia-malyukiv"]["intro"] = "Розмальовки для малюків 3–5 років — 6 аркушів з Плутусом. Порівняно з добіркою для малюків, у добірці для дітей 6–9 років більше деталей."
legacy["rozmalovky"]["intro"] = "Безкоштовні розмальовки з Плутусом зібрані в три добірки: новорічні, для малюків і для дітей."
(tmp / "texts_legacy.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
bs.COLORING_TEXTS_FILE = str(tmp / "texts_legacy.json")
# (а) число в тексті ≠ фактична кількість → добірка не будується (а отже й хаб)
SHEETS2 = dict(SHEETS, **{"rozmalovky-novorichni": 5})
man2 = mk_manifest(SHEETS2)
(tmp / "man.json").write_text(json.dumps(man2, ensure_ascii=False), encoding="utf-8")
err = build()
chk("у тексті «8 аркушів», а аркушів 5 → новорічна НЕ будується, хаб теж (потрібні всі три); футер без посилання; голосно", not list(OUTD.glob("rozmalovky*.html")) and 'href="rozmalovky.html"' not in pg("index.html") and "у тексті «8 аркуш" in err)
# (б) добірка «для дітей» має лише 2 аркуші → без хаба нічого
man3 = mk_manifest(dict(SHEETS, **{"rozmalovky-dlia-ditei": 2}))
(tmp / "man.json").write_text(json.dumps(man3, ensure_ascii=False), encoding="utf-8")
err = build()
chk("«для дітей» лише 2 аркуші (<3), тексти БЕЗ прапорця standalone (згадують інші добірки/число) → ні хаба, ні добірок; голосно", not list(OUTD.glob("rozmalovky*.html")) and "rozmalovky-dlia-ditei — лише 2 аркушів" in err and "не позначені standalone" in err)
# (б2) SEO 18.11 варіант «а»: тексти standalone → хаб з тими добірками, що є; новорічна не чекає інших
alone = json.loads((SITE / "rozmalovky_texts.json").read_text(encoding="utf-8"))
for k_ in alone:   # чисті standalone-тексти: без чисел і без згадок інших добірок
    alone[k_]["intro"] = "Розмальовки з Плутусом — власним персонажем магазину PlutusToys. Аркуші можна безкоштовно завантажити у PDF і роздрукувати вдома без реєстрації."
    alone[k_]["faq"] = alone[k_]["faq"][:2] + [{"q": "Скільки це коштує?", "a": "Нічого: аркуші безкоштовні."}]
    alone[k_]["standalone"] = True
(tmp / "texts_alone.json").write_text(json.dumps(alone, ensure_ascii=False), encoding="utf-8")
bs.COLORING_TEXTS_FILE = str(tmp / "texts_alone.json")
err = build()
built_b = {q_.stem for q_ in OUTD.glob("rozmalovky*.html")}
chk(f"standalone-тексти: «для дітей» (2 аркуші) не збудована, а хаб + новорічна + малюки — так: {sorted(built_b)}; хаб лінкує лише на збудовані", built_b == {"rozmalovky", "rozmalovky-novorichni", "rozmalovky-dlia-malyukiv"} and 'href="rozmalovky-dlia-ditei.html"' not in pg("rozmalovky.html") and 'href="rozmalovky.html">Розмальовки для друку<' in pg("index.html"))
# лише новорічна (SMM поки здав тільки її)
(tmp / "man.json").write_text(json.dumps(mk_manifest({"rozmalovky-novorichni": 8}), ensure_ascii=False), encoding="utf-8")
err = build()
built_c = {q_.stem for q_ in OUTD.glob("rozmalovky*.html")}
chk(f"SMM здав лише новорічні (8): збудовано {sorted(built_c)}; хаб показує 8 прев'ю без згадок відсутніх добірок; без шуму про відсутні добірки", built_c == {"rozmalovky", "rozmalovky-novorichni"} and pg("rozmalovky.html").count('class="coloring-sheet"') == 8 and "лише 0 аркушів" not in err)
bs.COLORING_TEXTS_FILE = str(tmp / "texts_legacy.json")
err = build()
chk("ті самі 8 новорічних, але тексти БЕЗ standalone (старі: «три добірки») → не будуємо (інакше хаб бреше)", not list(OUTD.glob("rozmalovky*.html")) and "не позначені standalone" in err)
# (б3) standalone — не лише декларація (аудит #1711): текст «три добірки» / назва відсутньої добірки / число хаба ≠ аркушів / не-bool прапорець → не будуємо
def build_with(mod, cnt):
    d_ = json.loads((tmp / "texts_alone.json").read_text(encoding="utf-8"))
    mod(d_)
    (tmp / "texts_x.json").write_text(json.dumps(d_, ensure_ascii=False), encoding="utf-8")
    (tmp / "man.json").write_text(json.dumps(mk_manifest(cnt), ensure_ascii=False), encoding="utf-8")
    bs.COLORING_TEXTS_FILE = str(tmp / "texts_x.json")
    e_ = build()
    return {q_.stem for q_ in OUTD.glob("rozmalovky*.html")}, e_
ONLY1 = {"rozmalovky-novorichni": 8}
b_, e_ = build_with(lambda d: d["rozmalovky"].update(intro="Безкоштовні розмальовки — зібрані в три добірки для друку вдома."), ONLY1)
chk("standalone-хаб згадує «три добірки», а є одна → не будуємо", not b_ and "згадують інші добірки" in e_)
b_, e_ = build_with(lambda d: d["rozmalovky-novorichni"].update(intro="Новорічні розмальовки. Для менших дітей є розмальовки для малюків 3–5 років — окрема добірка."), ONLY1)
chk("standalone-текст називає відсутню добірку за назвою → не будуємо", not b_ and "згадують інші добірки" in e_)
b_, e_ = build_with(lambda d: d["rozmalovky"].update(intro="У хабі 5 аркушів для друку."), ONLY1)
chk("число аркушів у тексті ХАБА (5) ≠ фактичних (8) → не будуємо; голосно", not b_ and "хаб — у тексті «5 аркуш" in e_)
b_, e_ = build_with(lambda d: d["rozmalovky-novorichni"].update(faq=d["rozmalovky-novorichni"]["faq"] + [{"q": "Скільки аркушів?", "a": "Усього 6 аркушів."}]), ONLY1)
chk("число аркушів у FAQ добірки (6) ≠ 8 → добірка не будується", not b_ and "6 аркуш" in e_)
for bad_ in ("true", 1, None):
    b_, e_ = build_with(lambda d, v=bad_: d["rozmalovky"].update(standalone=v), ONLY1)
    chk(f"standalone={bad_!r} (не булевий true) → не будуємо", not b_)
b_, e_ = build_with(lambda d: d["rozmalovky-novorichni"].pop("standalone"), ONLY1)
chk("прапорець лише на хабі, а на добірці нема → не будуємо", not b_)
b_, e_ = build_with(lambda d: None, ONLY1)
hp = pg("rozmalovky.html")
chk("одна добірка: на її сторінці нема порожнього блоку «Інші розмальовки»; на хабі є лінк на неї", b_ == {"rozmalovky", "rozmalovky-novorichni"} and "Інші розмальовки</h2>" not in pg("rozmalovky-novorichni.html") and "<ul></ul>" not in pg("rozmalovky-novorichni.html") and 'href="rozmalovky-novorichni.html"' in hp)
shared = mk_manifest({"rozmalovky-novorichni": 8, "rozmalovky-dlia-malyukiv": 6})
shared["rozmalovky-dlia-malyukiv"]["sheets"][0] = dict(shared["rozmalovky-novorichni"]["sheets"][0])
(tmp / "man.json").write_text(json.dumps(shared, ensure_ascii=False), encoding="utf-8")
build()
chk("спільний аркуш у двох добірках → у хабі 13 прев'ю (дубль id пропущено), а не 14", pg("rozmalovky.html").count('class="coloring-sheet"') == 13)
bs.COLORING_TEXTS_FILE = str(SITE / "rozmalovky_texts.json")
# ПРОДАКШН-СТАН 18.11: шипнуті тексти (standalone) + лише 5 новорічних аркушів SMM → хаб + новорічна
bs.COLORING_TEXTS_FILE = str(SITE / "rozmalovky_texts.json")
(tmp / "man.json").write_text(json.dumps(mk_manifest({"rozmalovky-novorichni": 5}), ensure_ascii=False), encoding="utf-8")
err = build()
built_p = {q_.stem for q_ in OUTD.glob("rozmalovky*.html")}
chk(f"шипнуті тексти + 5 новорічних аркушів → збудовано {sorted(built_p)}; без попереджень «coloring»; хаб без згадок відсутніх добірок", built_p == {"rozmalovky", "rozmalovky-novorichni"} and "coloring" not in err and not re.search(r"малюк|6–9|три добірки", pg("rozmalovky.html").split('<h1 class="page">')[1].split('<div class="coloring-grid">')[0] + pg("rozmalovky.html").split('<nav class="gift-links"')[1].split("</nav>")[0], re.I))
# (в) хаб без тексту, а добірки з текстом → нічого (інакше футер вів би на неіснуючий хаб)
(tmp / "man.json").write_text(json.dumps(mk_manifest(SHEETS), ensure_ascii=False), encoding="utf-8")
no_hub = {k: v for k, v in json.loads((SITE / "rozmalovky_texts.json").read_text(encoding="utf-8")).items() if k != "rozmalovky"}
(tmp / "texts_nohub.json").write_text(json.dumps(no_hub, ensure_ascii=False), encoding="utf-8")
bs.COLORING_TEXTS_FILE = str(tmp / "texts_nohub.json")
err = build()
chk("нема тексту хаба → жодної сторінки, у футері нема посилання (нема 404)", not list(OUTD.glob("rozmalovky*.html")) and 'href="rozmalovky.html"' not in pg("index.html") and "без тексту" in err)
# (г) без маніфесту (стан зараз) → тихо: нічого не будуємо і не шумимо
bs.COLORING_TEXTS_FILE = str(SITE / "rozmalovky_texts.json")
bs.COLORING_MANIFEST_FILE = str(tmp / "absent.json")
err = build()
chk("маніфесту нема (аркушів ще нема): нічого не будується, у журналі збірки жодних «coloring»-попереджень", not list(OUTD.glob("rozmalovky*.html")) and "coloring" not in err)
# (д) стара збірка з сторінками → аркуші зникли → застарілі сторінки прибрано І алерт (сезонна сторінка не зникає мовчки)
import telegram_notify  # noqa: E402
ALERTS = []
telegram_notify.send_throttled_alert = lambda *a, **k: ALERTS.append(a)
bs.COLORING_MANIFEST_FILE = str(tmp / "man.json")
(tmp / "man.json").write_text(json.dumps(mk_manifest(SHEETS), ensure_ascii=False), encoding="utf-8")
build()
assert (OUTD / "rozmalovky.html").exists()
chk("збірка зі сторінками розмальовок — алерту «зникли» нема", not ALERTS)
for q in cdir.glob("*"):
    q.unlink()
build()
chk("аркушів не стало → застарілі rozmalovky*.html прибрано, посилання у футері зникло", not list(OUTD.glob("rozmalovky*.html")) and 'href="rozmalovky.html"' not in pg("index.html"))
chk("і АЛЕРТ site_coloring_vanished (раніше живі сторінки зникли); повторна збірка без сторінок — алерту вже нема (файлу хаба нема)", len(ALERTS) == 1 and ALERTS[0][0] == "site_coloring_vanished")
n_before = len(ALERTS)
build()
chk("повторна збірка без розмальовок не дзвонить знову", len(ALERTS) == n_before)
# (е) часткове зникнення (аудит #1711): хаб лишається, а добірка, що була жива, зникла → теж алерт (з назвою файлу)
bs.COLORING_TEXTS_FILE = str(tmp / "texts_alone.json")
(tmp / "man.json").write_text(json.dumps(mk_manifest({"rozmalovky-novorichni": 8, "rozmalovky-dlia-malyukiv": 6}), ensure_ascii=False), encoding="utf-8")
build()
n0 = len(ALERTS)
assert (OUTD / "rozmalovky-dlia-malyukiv.html").exists()
(tmp / "man.json").write_text(json.dumps(mk_manifest({"rozmalovky-novorichni": 8}), ensure_ascii=False), encoding="utf-8")
build()
chk("добірка «малюки» зникла, хаб і новорічна лишились → алерт із назвою rozmalovky-dlia-malyukiv.html", len(ALERTS) == n0 + 1 and "rozmalovky-dlia-malyukiv.html" in ALERTS[-1][1] and not (OUTD / "rozmalovky-dlia-malyukiv.html").exists() and (OUTD / "rozmalovky.html").exists())
print("\n" + ("ALL OK" if not F else f"FAIL: {len(F)}"))
sys.exit(1 if F else 0)
