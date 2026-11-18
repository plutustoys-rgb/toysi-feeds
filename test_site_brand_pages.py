#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_brand_pages.py — 18.11.2026 (SEO 17.11, ТЗ «сторінки брендів KIDDISVIT», рішення власника «Так, усе»).
(1) shipped-тексти site/brand_pages.json (12 сторінок) проходять завантажувач, заборонені слова/теги відсікаються; site/porady.json має 2 нові статті;
(2) правила добору брендів (LOL: 5 «так» і 5 «ні»; Funko, Baby Born, Bratz, Squishmallows, Rainbow High; підсторінки Funko за шляхом KIDDISVIT і лише Funko);
(3) ворота SITE_BRAND_PAGES_LIVE: вимкнено → жодної сторінки, жодного посилання у футері; увімкнено → сторінки (≥12 товарів у наявності), хаб, футер, sitemap, ItemList/FAQPage/Breadcrumb, статті лінкують на бренди; сторінка з <12 товарів не будується;
повторна збірка з вимкненими воротами прибирає застарілі brend-*.html."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
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
texts = bs._load_brand_texts(str(SITE / "brand_pages.json"))
want = {s for s, _p, _f in bs.BRAND_PAGES}
chk(f"site/brand_pages.json: усі {len(want)} сторінок брендів проходять завантажувач (є h1, intro, ≥3 питання; без заборонених слів)", set(texts) == want)
chk("тексти: жодного «оригінал/офіційний/ліцензійний/дистриб'ютор», імені постачальника, «<» чи «>»", not any(bs._BRAND_BAD_TEXT_RX.search(" ".join([v["h1"], v["intro"]] + [q + " " + a for q, a in v["faq"]])) for v in texts.values()))
# підсторінка Funko приймає 1 питання, головна сторінка — ні (мін. 3)
one = tmp / "one_faq.json"
one.write_text(json.dumps({"brend-funko-pop-anime": {"h1": "Funko аніме", "intro": "Фігурки героїв аніме.", "faq": [{"q": "q", "a": "a"}]},
                           "brend-funko-pop": {"h1": "Funko", "intro": "Фігурки.", "faq": [{"q": "q", "a": "a"}]}}, ensure_ascii=False), encoding="utf-8")
_one = bs._load_brand_texts(str(one))
chk("підсторінка Funko з 1 питанням — приймається; головна сторінка бренду з 1 питанням — пропущена", "brend-funko-pop-anime" in _one and "brend-funko-pop" not in _one)
bad = tmp / "bad.json"
bad.write_text(json.dumps({"brend-bratz": {"h1": "Ляльки Bratz", "intro": "Це оригінальна лялька.", "faq": [{"q": f"q{i}", "a": f"a{i}"} for i in range(3)]},
                           "brend-baby-born": {"h1": "Ляльки Baby Born", "intro": "Ляльки від KIDDISVIT.", "faq": [{"q": f"q{i}", "a": f"a{i}"} for i in range(3)]},
                           "brend-lol-surprise": {"h1": "LOL", "intro": "Ляльки.", "faq": [{"q": "q", "a": "a"}]},
                           "brend-funko-pop": {"h1": "Funko", "intro": "Фігурки, офіційні версії.", "faq": [{"q": f"q{i}", "a": f"a{i}"} for i in range(3)]},
                           "brend-squishmallows": {"h1": "Squishmallows", "intro": "М'які іграшки.", "faq": [{"q": f"q{i}", "a": f"a{i}"} for i in range(3)]}}, ensure_ascii=False), encoding="utf-8")
err = io.StringIO()
with contextlib.redirect_stderr(err):
    got = bs._load_brand_texts(str(bad))
chk("кривий/заборонений запис пропускається ГОЛОСНО: «оригінальна» (оригінал…), ім'я постачальника, <3 питань, «офіційні» — відсіяні; чистий — лишається", set(got) == {"brend-squishmallows"} and err.getvalue().count("запис пропущено") == 4)
por = bs._load_porady(str(SITE / "porady.json"))
chk("site/porady.json: статті porady-funko-pop-bitty-pop-klipsa і porady-baby-born-za-vikom завантажуються (≥5 розділів, 4 питання)", all(s in por and len(por[s]["sections"]) >= 5 and len(por[s]["faq"]) == 4 for s in ("porady-funko-pop-bitty-pop-klipsa", "porady-baby-born-za-vikom")))

# ── (2) правила добору ──
rules = {s: fn for s, _p, fn in bs.BRAND_PAGES}
nm = lambda n, v="", path="": {"name": n, "vendor": v, "src_path": path}  # noqa: E731
LOL_YES = ["Лялька L.O.L. Surprise! Dance", "LOL Surprise OMG Fashion", "L. O. L. SURPRISE мініатюра", "ЛОЛ Сюрприз ляльки", "Набір L.O.L. SURPRISE! Tweens"]
LOL_NO = ["Lollipop іграшка льодяник", "Trolls LOL game", "Lolita сукня", "Doll set Lol-pop", "Мобільна гра lol"]
chk(f"LOL: 5 «так» ({sum(rules['brend-lol-surprise'](nm(n)) for n in LOL_YES)}/5) і 5 «ні» ({sum(not rules['brend-lol-surprise'](nm(n)) for n in LOL_NO)}/5)", all(rules["brend-lol-surprise"](nm(n)) for n in LOL_YES) and not any(rules["brend-lol-surprise"](nm(n)) for n in LOL_NO))
LOL_YES2 = ["L.O.L Surprise! OMG", "LOLSurprise Dolls", "lol-surprise tweens", "ЛОЛ-Сюрприз Лялька", "Лялька Л.О.Л. Танці"]
chk("LOL (аудит #1706): «L.O.L» без кінцевої крапки, «LOLSurprise», «lol-surprise», «ЛОЛ-Сюрприз», «Л.О.Л.» — ловляться", all(rules["brend-lol-surprise"](nm(n)) for n in LOL_YES2))
chk("Baby-Born/Беби Борн/Rainbow-High/Сквішмеллоу ловляться; «Rainbow Highway» і «братця» — ні", rules["brend-baby-born"](nm("Baby-Born лялька")) and rules["brend-baby-born"](nm("Беби Борн")) and rules["brend-rainbow-high"](nm("Rainbow-High Ruby"))
    and rules["brend-squishmallows"](nm("Сквішмеллоу кіт")) and not rules["brend-rainbow-high"](nm("Rainbow Highway")) and not rules["brend-bratz"](nm("братця Івасик")) and rules["brend-bratz"](nm("Лялька Братц")))
chk("ляльки-сторінки не беруть пазли/картини за номерами/розмальовки (H1 «Ляльки …»): Rainbow High у категорії «Пазли» — ні, у «Ляльки» — так", not rules["brend-rainbow-high"]({"name": "Пазл Rainbow High", "vendor": "", "category": "Пазли і вкладиші"}) and rules["brend-rainbow-high"]({"name": "Rainbow High лялька", "vendor": "", "category": "Ляльки + лялькові набори"}))
chk("Funko: бренд Funko у vendor або в назві; «Funko Pop», «Фанко»; «Funkoland»/«Sunko» — ні", rules["brend-funko-pop"](nm("Фігурка Pop", "Funko")) and rules["brend-funko-pop"](nm("Funko Pop! Marvel")) and rules["brend-funko-pop"](nm("Фанко поп")) and not rules["brend-funko-pop"](nm("Funkoland набір")) and not rules["brend-funko-pop"](nm("Sunko іграшка")))
chk("Baby Born / Bratz / Squishmallows / Rainbow High — без регістру; «Bratzillaz» — не Bratz",
    rules["brend-baby-born"](nm("Лялька BABY born 43 см")) and rules["brend-bratz"](nm("Лялька BRATZ Yasmin")) and not rules["brend-bratz"](nm("Bratzillaz Jade")) and rules["brend-squishmallows"](nm("Squishmallows Cam 30 см"))
    and rules["brend-rainbow-high"](nm("Rainbow High Violet")) and not rules["brend-rainbow-high"](nm("Rainbow Dash")))
chk("підсторінка Funko: потрібен шлях KIDDISVIT І бренд Funko (Squishmallows під «Funko > Герої ігор» — НЕ в підсторінці «ігри»)",
    rules["brend-funko-pop-igry"](nm("Funko Pop Mario", "Funko", "Funko > Герої ігор")) and not rules["brend-funko-pop-igry"](nm("Squishmallows Cam", "Squishmallows", "Funko > Герої ігор")) and not rules["brend-funko-pop-igry"](nm("Funko Pop Mario", "Funko", ""))
    and rules["brend-funko-pop-marvel"](nm("Funko Pop Hulk", "Funko", "Хлопчикам > Ліцензійні герої > Marvel")))

# підсторінки HP і Marvel: шлях АБО назва (SEO 18.11 15:31)
chk("HP: шлях «Funko > Гаррі Поттер» АБО назва (Harry Potter / Гоґвортс); не-Funko — ні",
    rules["brend-funko-pop-harry-potter"](nm("Funko Pop Dementor", "Funko", "Funko > Герої фільмів та серіалів")) is False
    and rules["brend-funko-pop-harry-potter"](nm("Фігурка Funko Pop Harry Potter Dementor", "Funko", "Funko > Герої фільмів та серіалів"))
    and rules["brend-funko-pop-harry-potter"](nm("Funko Bitty Pop Гоґвортс", "Funko", "")) and rules["brend-funko-pop-harry-potter"](nm("Funko Pop Снейп", "Funko", "Funko > Гаррі Поттер"))
    and not rules["brend-funko-pop-harry-potter"](nm("Harry Potter пазл", "Ravensburger", "Пазли")))
chk("Marvel: шлях «…Marvel» АБО назва (Танос, Шибайголов, Hulk, Тор); «Віктор» і «Імператор» — НІ (тор лише з лівою межею); не-Funko — ні",
    rules["brend-funko-pop-marvel"](nm("Funko Pop Танос", "Funko", "Бренди > Funko")) and rules["brend-funko-pop-marvel"](nm("Funko Pop Шибайголов", "Funko", "Іграшки для колекціонування"))
    and rules["brend-funko-pop-marvel"](nm("Funko Pop Тор Рагнарок", "Funko", "")) and rules["brend-funko-pop-marvel"](nm("Funko Hulk", "Funko", "Хлопчикам > Ліцензійні герої > Marvel"))
    and not rules["brend-funko-pop-marvel"](nm("Funko Pop Аркейн – Віктор", "Funko", "")) and not rules["brend-funko-pop-marvel"](nm("Funko Pop Ворота Балдура – Імператор", "Funko", ""))
    and not rules["brend-funko-pop-marvel"](nm("Marvel пазл", "Ravensburger", "Пазли")))

# ── (3) збірка ──
now = datetime.now(timezone.utc)
bs.LIQPAY_LIVE = True
bs.SITE_KV_LIVE = True
bs.BRAND_TEXTS_FILE = str(SITE / "brand_pages.json")
bs.PORADY_FILE = str(SITE / "porady.json")


def toysi(pid, name, cat="Фігурки героїв", stock=5, vendor="T"):
    return {"id": str(pid), "name": name, "vendor": vendor, "price": 200 + pid % 200, "stock": stock, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/p/{pid}"}


catalog = {str(i): toysi(i, f"Іграшка {i}", "Машинки" if i % 2 else "Конструктори") for i in range(1, 41)}
for i in range(101, 113):
    catalog[str(i)] = toysi(i, f"Фігурка Funko Pop Герой {i}", vendor="Funko")
catalog["120"] = toysi(120, "Фігурка Funko Pop Вичерпаний", stock=0, vendor="Funko")
for i in range(201, 213):
    catalog[str(i)] = toysi(i, f"Лялька Baby Born Лілі {i}", "Пупси")
for i in range(301, 312):   # лише 11 → сторінка Bratz НЕ будується
    catalog[str(i)] = toysi(i, f"Лялька Bratz Ясмін {i}", "Ляльки + лялькові набори")
for i in range(401, 413):
    catalog[str(i)] = toysi(i, f"М'яка іграшка Squishmallows Кіт {i}", "М'які іграшки")
for i in range(501, 513):
    catalog[str(i)] = toysi(i, f"Лялька Rainbow High Віолет {i}", "Ляльки + лялькові набори")
for i in range(601, 613):
    catalog[str(i)] = toysi(i, f"Лялька L.O.L. Surprise! Танці {i}", "LOL")
catalog["650"] = toysi(650, "Lollipop льодяник набір", "Інша творчість")
for i in range(701, 706):   # Rainbow High-пазли: у H1 «Ляльки …» їх бути не має
    catalog[str(i)] = toysi(i, f"Пазл Rainbow High Пазл {i}", "Пазли і вкладиші")
kv_items = {}
for i in range(1, 14):   # 13 kv-карток Funko зі шляхом «Funko > Герої фільмів та серіалів» → підсторінка «фільми й серіали»
    k = f"kv-{900 + i}"
    kv_items[k] = {"name": f"Фігурка Funko Pop Кіногерой {i}", "description": "Колекційна фігурка з вінілу, висота 10 см. Підходить для колекціонування й гри.", "vendor": "Funko", "vendor_code": f"KV-{i}",
                   "barcode": "4823099900015", "category": "Герої фільмів та серіалів", "category_path": ["Funko", "Герої фільмів та серіалів"], "source_category_path": ["Funko", "Герої фільмів та серіалів"],
                   "images": [f"https://plutustoys.com.ua/img/kv/{k}-1.jpg"], "price": 959, "cost": 600.5, "stock": 7, "index": False}
(tmp / "kv.json").write_text(json.dumps({"updated": now.isoformat(), "catalog_n": len(kv_items), "items": kv_items}, ensure_ascii=False), encoding="utf-8")
(tmp / "kvm.json").write_text(json.dumps({"paths": {"Funko > Герої фільмів та серіалів": "Фігурки героїв"}, "brands": {}}, ensure_ascii=False), encoding="utf-8")
bs.SUPPLIER_KV_FILE, bs.KV_CATEGORY_MAP_FILE = str(tmp / "kv.json"), str(tmp / "kvm.json")
bs.SUPPLIER_RT_FILE = str(tmp / "no_rt.json")
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")


def build(live):
    bs.BRAND_PAGES_LIVE = live
    out = Path(tempfile.mkdtemp()) if live is None else OUTD
    bs.OUT = str(out)
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        bs.build()
    return out, err.getvalue()


OUTD = Path(tempfile.mkdtemp())
out, err = build(False)
chk("ворота вимкнені: жодної brend-*.html і brendy.html; у футері нема «Бренди»; статей про бренди теж нема (йдуть разом зі сторінками брендів)",
    not list(out.glob("brend-*.html")) and not (out / "brendy.html").exists() and 'href="brendy.html"' not in (out / "index.html").read_text(encoding="utf-8")
    and not (out / "porady-funko-pop-bitty-pop-klipsa.html").exists() and not (out / "porady-baby-born-za-vikom.html").exists())
out, err = build(True)
built = {q.stem for q in out.glob("brend-*.html")}
chk(f"ворота відкриті: збудовано {sorted(built)}", built == {"brend-funko-pop", "brend-baby-born", "brend-squishmallows", "brend-rainbow-high", "brend-lol-surprise", "brend-funko-pop-filmy-serialy"})
chk("Bratz (лише 11 товарів < 12) — НЕ будується, голосне попередження; підсторінки Funko без товарів — теж", "brend-bratz — лише 11 товарів" in err and not (out / "brend-bratz.html").exists())


def pg(f):
    q = out / f
    return q.read_text(encoding="utf-8") if q.exists() else ""


h = pg("brend-baby-born.html")
chk("сторінка Baby Born: H1 з файлу, 12 карток, лише Baby Born, ItemList + FAQPage + BreadcrumbList, canonical на себе, не noindex",
    "<h1 class=\"page\">Ляльки Baby Born</h1>" in h and h.count('class="card') == 12 and "Лялька Baby Born" in h and "М'яка іграшка Squishmallows" not in h and '"@type": "ItemList"' in h and '"@type": "FAQPage"' in h
    and '"@type": "BreadcrumbList"' in h and 'rel="canonical" href="https://plutustoys.com.ua/brend-baby-born.html"' in h and "noindex" not in h.split("</head>")[0])
hf = pg("brend-funko-pop.html")
chk("головна Funko: Toysi (12) + kv (13) = 25 карток; вичерпаний Funko Toysi 120 і Squishmallows — немає; kv-картка з публічною адресою product-k90X.html (не kv-)",
    hf.count('class="card') == 25 and "Вичерпаний" not in hf and "product-k901.html" in hf and "product-kv-" not in hf)
hs = pg("brend-funko-pop-filmy-serialy.html")
chk("підсторінка «фільми й серіали»: 13 kv-карток (лише шлях KIDDISVIT + Funko), хлібні крихти ведуть через головну сторінку Funko і хаб брендів", hs.count('class="card') == 13 and 'href="brend-funko-pop.html"' in hs and 'href="brendy.html"' in hs)
hub = pg("brendy.html")
chk("хаб: усі 6 побудованих сторінок, підсторінка вкладена під Funko; Bratz (не збудовано) — немає; ItemList", all(f'href="{s}.html"' in hub for s in built) and "brend-bratz" not in hub and '"@type": "ItemList"' in hub)
idx_h = pg("index.html")
chk("футер: посилання «Бренди» → brendy.html на головній і на сторінці бренду", 'href="brendy.html">Бренди<' in idx_h and 'href="brendy.html">Бренди<' in h)
sm = "".join(q.read_text(encoding="utf-8") for q in out.glob("sitemap-*.xml"))
chk("sitemap: кожна збудована сторінка й хаб — рівно раз; Bratz нема", all(sm.count(f"/{s}.html") == 1 for s in built | {"brendy"}) and "brend-bratz" not in sm)
art = pg("porady-funko-pop-bitty-pop-klipsa.html")
art2 = pg("porady-baby-born-za-vikom.html")
chk("статті: porady-funko-pop… лінкує на головну Funko і підсторінку; porady-baby-born… — на Baby Born; є Article + FAQPage; непобудований бренд (Bratz) у статті не лінкується",
    'href="brend-funko-pop.html"' in art and 'href="brend-funko-pop-filmy-serialy.html"' in art and 'href="brend-funko-pop-anime.html"' not in art and 'href="brend-baby-born.html"' in art2 and '"@type": "Article"' in art and '"@type": "FAQPage"' in art2)
chk("сторінка Funko лінкує на свою статтю; Baby Born — на свою", 'href="porady-funko-pop-bitty-pop-klipsa.html"' in hf and 'href="porady-baby-born-za-vikom.html"' in h)
bad_words = re.compile(r"оригінал|офіційн|ліцензійн|дистриб|kiddisvit|кіддісвіт", re.I)
chk("жодна сторінка брендів/хаб/статті не містить «оригінал/офіційний/ліцензійний/дистриб'ютор/KIDDISVIT»", not any(bad_words.search(pg(f"{s}.html").split("<footer")[0]) for s in list(built) + ["brendy", "porady-funko-pop-bitty-pop-klipsa", "porady-baby-born-za-vikom"]))
chk("Rainbow High: пазли (категорія «Пазли») НЕ в добірці ляльок; лише 12 ляльок", "Пазл Rainbow High" not in pg("brend-rainbow-high.html") and pg("brend-rainbow-high.html").count('class="card') == 12)
chk("закупівля kv (600.5) не просочилась у сторінки брендів", not any("600.5" in pg(f"{s}.html") for s in built))
# аудит #1706 (🟡1): нема тексту головної сторінки Funko → підсторінка не будується, футер/хаб/статті узгоджені з фактично записаним, без 404-посилань
texts_all = json.loads((SITE / "brand_pages.json").read_text(encoding="utf-8"))
texts_cut = {k: v for k, v in texts_all.items() if k != "brend-funko-pop"}
(tmp / "bp_cut.json").write_text(json.dumps(texts_cut, ensure_ascii=False), encoding="utf-8")
bs.BRAND_TEXTS_FILE = str(tmp / "bp_cut.json")
out, err = build(True)
built2 = {q.stem for q in out.glob("brend-*.html")}
art_f = pg("porady-funko-pop-bitty-pop-klipsa.html")
chk(f"без тексту Funko: збудовано {sorted(built2)}; підсторінка Funko НЕ записана (голосне попередження), стаття про Funko НЕ збудована, жодного href на не записану сторінку",
    "brend-funko-pop" not in built2 and "brend-funko-pop-filmy-serialy" not in built2 and "головна сторінка brend-funko-pop не збудована" in err and art_f == "" and pg("porady-baby-born-za-vikom.html") != ""
    and all(f"{h_}.html" in {q.name for q in out.glob("*.html")} for h_ in re.findall(r'href="(brend-[a-z-]+)\.html"', pg("brendy.html") + pg("index.html"))))
# немає жодного тексту → хаб і футер не будуються
(tmp / "bp_none.json").write_text("{}", encoding="utf-8")
bs.BRAND_TEXTS_FILE = str(tmp / "bp_none.json")
out, err = build(True)
chk("жодного тексту брендів: нема хаба, нема «Бренди» у футері, нема статей про бренди", not (out / "brendy.html").exists() and 'href="brendy.html"' not in pg("index.html") and pg("porady-baby-born-za-vikom.html") == "")
# мало товарів Baby Born (<12) → стаття про Baby Born НЕ публікується (її тексти «у каталозі є 26/28/…» були б неправдиві)
bs.BRAND_TEXTS_FILE = str(SITE / "brand_pages.json")
for i in range(201, 207):
    catalog.pop(str(i))
out, err = build(True)
chk("Baby Born лише 6 товарів (<12): сторінки нема, статті про Baby Born теж нема; стаття про Funko є", not (out / "brend-baby-born.html").exists() and pg("porady-baby-born-za-vikom.html") == "" and pg("porady-funko-pop-bitty-pop-klipsa.html") != "")
# повторна збірка з вимкненими воротами в ту саму теку прибирає застарілі сторінки
out, err = build(False)
chk("повторна збірка з вимкненими воротами прибрала всі brend-*.html і brendy.html", not list(out.glob("brend-*.html")) and not (out / "brendy.html").exists())
print("\n" + ("ALL OK" if not F else f"FAIL: {len(F)}"))
sys.exit(1 if F else 0)
