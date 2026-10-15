#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_internal_links.py — SEO-замовлення 12.10.2026, внутрішня перелінковка (повний build() на синтетичному каталозі; мережа не потрібна):
  А. слот тексту картки (site/product_texts.json): esc(), абзаци за порожнім рядком, позиція в HTML (після блоку ціни/доставки й опису, ПЕРЕД характеристиками Toysi,
     перед блоками «Схожі»), нема id — нема блоку; лоадер ГОЛОСНИЙ (BOM читається, cp1251/битий JSON/не-словник/не-рядки/нечислові id/довгі тексти/RecursionError);
     id без картки → попередження наприкінці build();
  Б. «Схожі товари»: до 6, ТА Ж категорія, лише в наявності, найближчі за ціною (тай-брейк id), без самої картки, без «Уцінки»/«Уценки», детермінізм, OOS-сторінка, швидкість;
  В. «Схожі категорії»: лише існуючі сторінки, без самої категорії й дублів, ≤6, лише 1-ша сторінка, нема файлу/категорії — нема блоку, голосний лоадер;
  Г. пагінація: з 1-ї сторінки до будь-якої ≤3 кліків (42 сторінки «Картини за номерами» — на реально збудованих файлах; а також 7/8/100/1170…); вікно ±2, перша/остання,
     поточна — не посилання, ≤7 сторінок — усі номери; rel prev/next і canonical НЕ змінені."""
import contextlib
import io
import json
import os
import random
import re
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID", "SITE_BUILD_LIMIT"):
    os.environ.pop(k, None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


SPECS = "<br><b>Бренд:</b> Тест<br><b>Вік:</b> 3+"


def item(pid, name, category, price, stock=5, specs=False):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": stock, "category_name": category, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "barcode": "",
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів." + (SPECS if specs else "")}


CARS, DOLLS, BIG, SOLO, DUO = "Машинки", "Ляльки", "Картини за номерами", "Одинак", "Дует"
catalog = {}


def add(pid, name, cat, price, **kw):
    catalog[str(pid)] = item(pid, name, cat, price, **kw)


# Машинки: ціни підібрано так, щоб перевірити відстань, рівні ціни в одному й різних боках (тай-брейк за id) і саму картку
add(500, "Машинка-центр", CARS, 200, specs=True)       # сторінка під перевіркою: текст + характеристики
add(19, "Машинка рівна ціна", CARS, 200)               # diff 0
add(13, "Машинка 195", CARS, 195)                       # diff 5
add(14, "Машинка 205", CARS, 205)                       # diff 5
add(10, "Машинка 190 а", CARS, 190)                     # diff 10 (ліворуч, id 10)
add(11, "Машинка 190 б", CARS, 190)                     # diff 10 (ліворуч, id 11)
add(12, "Машинка 210", CARS, 210)                       # diff 10 (праворуч, id 12) → 6-й за (diff,id) серед diff=10 є 12, а не 15…
add(15, "Машинка 150", CARS, 150)
add(16, "Машинка 260", CARS, 260)
add(17, "Машинка 100", CARS, 100)
add(18, "Машинка 400", CARS, 400)
add(600, "Машинка закінчилась", CARS, 198, stock=0)     # OOS: сторінка жива, але не кандидат у «Схожі»
add(601, "Ще одна без залишку", CARS, 200, stock=0)
for i in range(4):                                       # Ляльки: дешеві (≥60 ₴) — для крос-селу «Додайте до замовлення» на Машинках; і однакова ціна 200 з Машинками (не мають змішуватись)
    add(30 + i, f"Лялька {i}", DOLLS, 80 + i)
add(40, "Лялька 200", DOLLS, 200)
add(41, "Лялька 201", DOLLS, 201)
add(700, "Одинока", SOLO, 120)                           # єдина в категорії → «Схожих» нема
add(710, "Дует а", DUO, 120)
add(711, "Дует б", DUO, 500)                             # у Дуету лише ще один → блок із 1 картки
for i in range(10):
    add(800 + i, f"Уцінка. Товар {i}", "Уцінка", 100 + i)
for i in range(4):
    add(820 + i, f"Уценка. Товар {i}", "Уценка", 100 + i)
add(900, "Уцінка без залишку", "Уцінка", 100, stock=0)
for i in range(1000):                                    # 1000 / 24 = 42 сторінки
    add(2000 + i, f"Картина номер {i}", BIG, 100 + (i % 50) * 3)
for c in range(8):                                       # 8 категорій по 2 товари — щоб перевірити ліміт «Схожих категорій» (≤6)
    for j in range(2):
        add(5000 + c * 10 + j, f"Набір {c}-{j}", f"Набір{c}", 90 + j)


def build_with(files=None, tag=""):
    """повний build() у новій теці; files — {атрибут bs: вміст файлу}; повертає (тека, stderr)"""
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    for attr, content in (files or {}).items():
        fp = out / (attr.strip("_").lower() + tag + ".json")
        if isinstance(content, bytes):
            fp.write_bytes(content)
        else:
            fp.write_text(content, encoding="utf-8")
        setattr(bs, attr, str(fp))
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        bs.build()
    return out, err.getvalue()


bs.tp.fetch_toysi_catalog = lambda: catalog
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.price_of = lambda it: int(it["price"])                # детерміновані ціни (реальна формула — competitor_pricing, тестується окремо)
bs.LIMIT = 0
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None

slug_cars, slug_dolls, slug_big = bs.slugify(CARS), bs.slugify(DOLLS), bs.slugify(BIG)
TEXT_500 = 'Перший абзац <b>з тегом</b> & амперсандом, "лапки".\n\nДругий абзац про машинку.\r\n\r\n   \n\nТретій абзац.'
texts = {"500": TEXT_500, "14": "Текст без характеристик.", "600": "Текст для сторінки без залишку.", "19": "   ", "20": 5, "9999": "Картки з таким id нема", "abc": "нечисловий id", "40": "x" * 901}
related = {"description": "ігнорується", "related": {
    slug_cars: [slug_dolls, slug_cars, "nonexistent", slug_dolls, slug_big],
    slug_dolls: [],
    "ghost-key": [slug_cars],
    slug_big: [slug_cars, "nabir0", "nabir1", "nabir2", "nabir3", "nabir4", "nabir5", "nabir6", "nabir7"],
    "nabir0": ["nabir1", "nabir2", "nabir3", "nabir4", "nabir5", "nabir6", "nabir7", "nabir1"],
}}
out, err = build_with({"_PRODUCT_TEXTS_FILE": json.dumps(texts, ensure_ascii=False), "_CATEGORY_RELATED_FILE": json.dumps(related, ensure_ascii=False)})


def rd(name):
    return (out / name).read_text(encoding="utf-8")


def sections(h):
    """{назва секції: [id товарів]} для всіх <section class="related">"""
    return {t: re.findall(r'class="card" href="product-(\d+)\.html"', body)
            for t, body in re.findall(r'<section class="related"><h2>(.*?)</h2>(.*?)</section>', h, re.S)}


# ═══ А. Текст картки ═══
h500 = rd("product-500.html")
pt = re.findall(r'<div class="prod-text">(.*?)</div>', h500, re.S)
chk("А: блок prod-text на картці з текстом — рівно один, 3 абзаци (порожні/пробільні пропущено)", len(pt) == 1 and pt[0].count("<p>") == 3)
chk("А: HTML у тексті екранується (<b> → &lt;b&gt;, & → &amp;, лапки → &quot;), літерального <b>з тегом нема",
    "<p>Перший абзац &lt;b&gt;з тегом&lt;/b&gt; &amp; амперсандом, &quot;лапки&quot;.</p>" in h500 and "<b>з тегом</b>" not in h500)
chk("А: абзаци за порожнім рядком (\\n\\n і \\r\\n\\r\\n)", "<p>Другий абзац про машинку.</p>" in h500 and "<p>Третій абзац.</p>" in h500)
i_price, i_badges, i_deliv = h500.index('class="price-row"'), h500.index('class="trust-badges"'), h500.index('class="delivery"')
i_desc, i_text = h500.index('<div class="desc">'), h500.index('<div class="prod-text">')
i_specs, i_li = h500.index('<div class="desc specs">'), h500.index("<li><b>Бренд:</b>")
i_sec = h500.index('<section class="related">')
i_buybar = h500.index('<div class="buybar">')
chk("А: порядок: ціна → бейджі оплати → доставка → «Опис» → ТЕКСТ → характеристики Toysi → «Схожі»/крос-сел → закріплена панель «У кошик»",
    i_price < i_badges < i_deliv < i_desc < i_text < i_specs < i_li < i_sec < i_buybar)
chk("А: текст нижче блоку ціни/оплати/доставки (перший екран мобільного не виштовхнуто) і кнопка «У кошик» (data-add) є в закріпленій панелі",
    i_text > i_deliv > i_price and "data-add" in h500[i_buybar:])
chk("А: у «Опис» лишився опис Toysi, характеристики — окремим блоком після тексту", "<ul>" not in h500[i_desc:i_text] and h500.count("<li>") == 2)
h19 = rd("product-19.html")
chk("А: порожнє/пробільне значення → блоку нема", "prod-text" not in h19)
h13 = rd("product-13.html")
chk("А: нема id у файлі → блоку нема; розмітка опису як була (один блок desc)", "prod-text" not in h13 and 'class="desc specs"' not in h13 and h13.count('<div class="desc">') == 1)
h40 = rd("product-40.html")
chk("А: довгий текст (901 символ) усе одно показано", "prod-text" in h40)
h600 = rd("product-600.html")
chk("А: OOS-сторінка теж показує текст (SEO-цінність) і лишається OutOfStock", "prod-text" in h600 and "Текст для сторінки без залишку." in h600 and "schema.org/OutOfStock" in h600)
h14 = rd("product-14.html")
chk("А: без характеристик Toysi: текст просто під описом (один блок desc, без desc specs), перед «Схожими»",
    h14.index('<div class="desc">') < h14.index('<div class="prod-text">') < h14.index('<section class="related">') and 'class="desc specs"' not in h14)
chk("А: JSON-LD картки не містить тексту SEO (розмітку не чіпано)", "Другий абзац" not in re.search(r'<script type="application/ld\+json">(.*?)</script>', h500, re.S).group(1))
chk("А: product_texts: нечисловий id, не-рядок і пробільний — відкинуто голосно/мовчки; id без картки → попередження в build()",
    "abc" in err and "id не з цифр" in err and "значення НЕ рядок" in err and "'20'" in err and "9999" in err and "id без картки" in err)
chk("А: довгий текст → попередження про довжину", "довший за 900" in err and "'40'" in err)
chk("А: <b> у тексті → попередження (екранується як текст)", "є < або >" in err and "'500'" in err)
chk("А: id з картки (500) НЕ в списку «id без картки»", "id без картки" in err and "'500'" not in err.split("id без картки")[1].split("\n")[0])

import contextlib as _cl  # noqa: E402


def load_texts(content, name, binary=False):
    f = out / name
    if content is not None:
        (f.write_bytes if binary else f.write_text)(content, **({} if binary else {"encoding": "utf-8"}))
    bs._PRODUCT_TEXTS_FILE = str(f)
    e = io.StringIO()
    with _cl.redirect_stderr(e):
        r = bs._load_product_texts()
    return r, e.getvalue()


r, e = load_texts(None, "nope_t.json")
chk("А-лоадер: нема файлу → порожньо БЕЗ шуму", r == {} and e == "")
r, e = load_texts("{not json", "bad_t.json")
chk("А-лоадер: битий JSON → порожньо, ГОЛОСНО", r == {} and "product_texts.json" in e and "невалідний JSON" in e)
r, e = load_texts(b"\xef\xbb\xbf" + json.dumps({"11529": "Текст"}, ensure_ascii=False).encode("utf-8"), "bom_t.json", binary=True)
chk("А-лоадер: BOM (Блокнот) читається", r == {"11529": "Текст"} and e == "")
(out / "cp1251_t.json").write_bytes('{"1": "Текст"}'.encode("cp1251"))
bs._PRODUCT_TEXTS_FILE = str(out / "cp1251_t.json")
_e = io.StringIO()
with _cl.redirect_stderr(_e):
    r = bs._load_product_texts()
chk("А-лоадер: cp1251 → порожньо, ГОЛОСНО", r == {} and "НЕ прочитано" in _e.getvalue())
r, e = load_texts('["a"]', "list_t.json")
chk("А-лоадер: корінь-список → ГОЛОСНО", r == {} and "очікується об'єкт" in e)
r, e = load_texts(json.dumps({"1": "ok", "2": 5, "3": None, "4": ["x"], "5": "  ", "x1": "id", "٣": "арабська цифра"}, ensure_ascii=False), "mixed_t.json")
chk("А-лоадер: не-рядки й нечислові id (вкл. юнікод-цифри) відкинуто ГОЛОСНО; лишається лише валідне", r == {"1": "ok"} and "значення НЕ рядок" in e and "id не з цифр" in e and "x1" in e)
r, e = load_texts("[" * 100000, "deep_t.json")
chk("А-лоадер: глибоко вкладений JSON → ГОЛОСНО, без винятку (RecursionError)", r == {} and "product_texts.json" in e)
r, e = load_texts("", "empty_t.json")
chk("А-лоадер: порожній файл → ГОЛОСНО", r == {} and "product_texts.json" in e)

shipped = json.loads((HERE / "site" / "product_texts.json").read_text(encoding="utf-8-sig"))
chk("А-дані: site/product_texts.json у репо: 177 записів, id — цифри, значення — непорожні рядки ≤900 без < >",
    len(shipped) == 177 and all(re.fullmatch(r"[0-9]+", k) and isinstance(v, str) and v.strip() and len(v) <= 900 and "<" not in v and ">" not in v for k, v in shipped.items()))

# ═══ Б. Схожі товари ═══
S = sections(h500)
sim = S.get("Схожі товари", [])
chk("Б: на картці є «Додайте до замовлення» (існуючий крос-сел НЕ чіпано) і «Схожі товари»", "Додайте до замовлення" in S and "Схожі товари" in S)
chk("Б: перший блок — крос-сел, «Схожі товари» нижче", list(S).index("Додайте до замовлення") < list(S).index("Схожі товари"))
chk("Б: крос-сел лишився з ІНШИХ категорій (не Машинки) і не порожній", S["Додайте до замовлення"] and all(catalog[i]["category_name"] != CARS for i in S["Додайте до замовлення"]))
chk("Б: рівно 6 найближчих за ціною в порядку (відстань, id): 19(0) → 13,14(5) → 10,11,12(10)", sim == ["19", "13", "14", "10", "11", "12"])
chk("Б: серед схожих нема самої картки, товарів без залишку (600/601), чужих категорій (Ляльки 40/41 мають ту ж ціну 200)",
    "500" not in sim and not {"600", "601", "40", "41"} & set(sim))
chk("Б: lazy-зображення у плитках «Схожих»", re.search(r'<section class="related"><h2>Схожі товари</h2>.*?<img [^>]*loading="lazy"', h500, re.S) is not None)
chk("Б: категорія з однією карткою (Одинак) — блоку «Схожі товари» нема", "Схожі товари" not in rd("product-700.html"))
chk("Б: категорія з двома — у «Схожих» лише інша картка", sections(rd("product-710.html")).get("Схожі товари") == ["711"])
chk("Б: «Уцінка» і «Уценка» — блоку нема (в наявності й без залишку)", all("Схожі товари" not in rd(f"product-{i}.html") for i in (800, 805, 820, 823, 900)))
so = sections(h600)
chk("Б: OOS-сторінка (ціна 198): «Схожі товари в наявності» = наявні тієї ж категорії за (відстань, id): 19(2), 500(2), 13(3), 14(7), 10,11(8); без OOS 600/601; без крос-селу",
    so.get("Схожі товари в наявності") == ["19", "500", "13", "14", "10", "11"] and "Додайте до замовлення" not in h600)
chk("Б: sitemap/canonical/JSON-LD не змінено (canonical картки на себе)", 'rel="canonical" href="https://plutustoys.com.ua/product-500.html"' in h500 and '"@type": "Product"' in h500)
chk("Б: детермінізм: друга збірка дає ті самі «Схожі»",
    sections(Path(build_with({"_PRODUCT_TEXTS_FILE": json.dumps(texts)})[0] / "product-500.html").read_text(encoding="utf-8"))["Схожі товари"] == sim)

# незалежна перевірка з перебором і перемішаним входом (рівні ціни, 0 залишок, Уцінка)
rnd = random.Random(12)
pool = []
for n in range(600):
    cat = rnd.choice(["А", "Б", "В", "Уцінка", "Уценка товарів"])
    pool.append({"id": str(rnd.randint(1, 99999) + n * 100000), "name": f"n{n}", "price": rnd.randint(50, 75), "category": cat, "stock": rnd.choice([0, 3, 3, 3])})
for n in range(40):
    pool.append({"id": f"x{n}", "name": f"nn{n}", "price": rnd.randint(50, 75), "category": "А", "stock": 4})   # нечислові id — детерміновано після числових


def brute(p, k=6):
    if "уцінк" in p["category"].lower() or "уценк" in p["category"].lower():
        return []
    c = [q for q in pool if q["category"] == p["category"] and q["stock"] > 0 and q["id"] != p["id"]]
    c.sort(key=lambda q: (abs(q["price"] - p["price"]), bs._tie_key(q, p)))
    return [q["id"] for q in c[:k]]


bad = 0
for shuffle_seed in (1, 2):
    shuffled = pool[:]
    random.Random(shuffle_seed).shuffle(shuffled)
    idx = bs.build_similar_index(shuffled)
    for p in pool:
        if bs.similar_products(idx, p) is None or [q["id"] for q in bs.similar_products(idx, p)] != brute(p):
            bad += 1
chk(f"Б: двовказівниковий пошук = перебір (600+ товарів, багато рівних цін, нечислові id, 0 залишку, Уцінка; 2 різні порядки входу) — розбіжностей: {bad}", bad == 0)
chk("Б: числові id порівнюються за значенням (9 < 10), а не рядком", bs._id_key("9") < bs._id_key("10") < bs._id_key("a1"))

big = [{"id": str(i), "name": "n", "price": (i * 7919) % 4000, "category": f"К{i % 60}", "stock": 1} for i in range(30000)]
t0 = time.time()
idx = bs.build_similar_index(big)
tot = sum(len(bs.similar_products(idx, p)) for p in big)
dt = time.time() - t0
chk(f"Б: 30 000 товарів / 60 категорій — «Схожі» для всіх за {dt:.1f} с (не O(n²))", dt < 20 and tot == 30000 * 6)

# ═══ В. Схожі категорії ═══
hc = rd(f"category-{slug_cars}.html")
rc = re.search(r'<nav class="relcats" aria-label="Схожі категорії">(.*?)</nav>', hc, re.S)
links = re.findall(r'<a href="(category-[a-z0-9-]+\.html)">(.*?)</a>', rc.group(1)) if rc else []
chk("В: блок relcats на 1-й сторінці категорії: лише існуючі, без самої категорії й дублів, у порядку файлу", links == [(f"category-{slug_dolls}.html", DOLLS), (f"category-{slug_big}.html", BIG)])
chk("В: порожній список (Ляльки) → блоку нема", "relcats" not in rd(f"category-{slug_dolls}.html"))
chk("В: категорія, якої нема у файлі (Одинак) → блоку нема", "relcats" not in rd(f"category-{bs.slugify(SOLO)}.html"))
hb1, hb2 = rd(f"category-{slug_big}.html"), rd(f"category-{slug_big}_2.html")
n_rel = len(re.findall(r'<a href="category-[a-z0-9-]+\.html">', re.search(r'<nav class="relcats".*?</nav>', hb1, re.S).group(0)))
chk("В: максимум 6 посилань (у файлі 9, усі існують)", n_rel == 6)
chk("В: на пагінації (стор.2) блоку нема — не дублюється", "relcats" not in hb2)
chk("В: catalog.html і головна без relcats; «Популярних категорій» на головній більше нема (власник 15.10)", "relcats" not in rd("catalog.html") and "relcats" not in rd("index.html") and 'aria-label="Популярні категорії"' not in rd("index.html"))
n0 = re.search(r'<nav class="relcats".*?</nav>', rd("category-nabir0.html"), re.S).group(0)
chk("В: дубль у файлі (nabir1 двічі) — одне посилання", n0.count('href="category-nabir1.html"') == 1 and "category-nabir0.html" not in n0)
chk("В: попередження: ключ без сторінки (ghost-key) і ціль без сторінки (nonexistent)", "ghost-key" in err and "nonexistent" in err and "category_related.json" in err)
chk("В: сторінка категорії не отримала зайвого H2-стану: H1/title/canonical не змінено", f'<h1 class="page cat-h1">{BIG}</h1>' in hb1 and f'rel="canonical" href="https://plutustoys.com.ua/category-{slug_big}.html"' in hb1)


def load_rel(content, name, binary=False):
    f = out / name
    if content is not None:
        (f.write_bytes if binary else f.write_text)(content, **({} if binary else {"encoding": "utf-8"}))
    bs._CATEGORY_RELATED_FILE = str(f)
    e = io.StringIO()
    with _cl.redirect_stderr(e):
        r = bs._load_category_related()
    return r, e.getvalue()


r, e = load_rel(None, "nope_r.json")
chk("В-лоадер: нема файлу → порожньо без шуму", r == {} and e == "")
r, e = load_rel("{oops", "bad_r.json")
chk("В-лоадер: битий JSON → ГОЛОСНО", r == {} and "category_related.json" in e and "невалідний JSON" in e)
r, e = load_rel(b"\xef\xbb\xbf" + json.dumps({"related": {"a": ["b"]}}).encode("utf-8"), "bom_r.json", binary=True)
chk("В-лоадер: BOM читається", r == {"a": ["b"]} and e == "")
r, e = load_rel('["a"]', "list_r.json")
chk("В-лоадер: корінь не словник → ГОЛОСНО", r == {} and "очікується" in e)
r, e = load_rel('{"related": ["a"]}', "norel_r.json")
chk("В-лоадер: related не словник → ГОЛОСНО", r == {} and "очікується" in e)
r, e = load_rel(json.dumps({"related": {"a": ["b", 5, "C D", "c"], "Bad Key": ["x"], "n": "str", "ok": []}}), "mixed_r.json")
chk("В-лоадер: нерядкові/некоректні slug-и й ключі відкинуто ГОЛОСНО, коректні лишаються", r == {"a": ["b", "c"]} and "відкинуто" in e and "Bad Key" in e)
(out / "cp1251_r.json").write_bytes('{"related": {"a": ["б"]}}'.encode("cp1251"))
bs._CATEGORY_RELATED_FILE = str(out / "cp1251_r.json")
_e = io.StringIO()
with _cl.redirect_stderr(_e):
    r = bs._load_category_related()
chk("В-лоадер: cp1251 → ГОЛОСНО", r == {} and "НЕ прочитано" in _e.getvalue())
r, e = load_rel("[" * 100000, "deep_r.json")
chk("В-лоадер: глибоко вкладений JSON → ГОЛОСНО, без винятку", r == {} and "category_related.json" in e)
shipped_r = json.loads((HERE / "site" / "category_related.json").read_text(encoding="utf-8-sig"))["related"]
chk("В-дані: site/category_related.json у репо: 107 ключів, ≤6 цілей, slug-и [a-z0-9-]+, без самопосилань",
    len(shipped_r) == 107 and all(re.fullmatch(r"[a-z0-9-]+", k) and isinstance(v, list) and 0 < len(v) <= 6 and k not in v and all(re.fullmatch(r"[a-z0-9-]+", x) for x in v) for k, v in shipped_r.items()))

# ═══ Г. Пагінація ═══
FN = "category-x.html"


def pager_graph(n):
    """{сторінка: множина сторінок, на які веде її пейджер}; перевіряє форму розмітки"""
    g = {}
    for k in range(1, n + 1):
        h = bs._pager(FN, k, n)
        hrefs = re.findall(r'<a class="pg(?: nav)?" href="([^"]+)"', h)
        nums = set()
        for hr in hrefs:
            m = re.fullmatch(r"category-x(?:_(\d+))?\.html", hr)
            nums.add(int(m.group(1)) if m and m.group(1) else 1)
        g[k] = nums
    return g


def max_clicks(g, n):
    dist, q = {1: 0}, deque([1])
    while q:
        u = q.popleft()
        for v in g[u]:
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return (max(dist.values()), len(dist) == n)


for n in (2, 3, 5, 7, 8, 9, 10, 12, 20, 24, 42, 43, 64, 100, 125, 126, 200, 500, 1000, 1170, 2000, 5000):
    mc, allr = max_clicks(pager_graph(n), n)
    chk(f"Г: {n} стор.: з 1-ї до будь-якої {mc} кліків (≤3), досяжні всі", mc <= 3 and allr)

# реальні файли 42-сторінкової категорії «Картини за номерами»
files = {k: rd(f"category-{slug_big}.html" if k == 1 else f"category-{slug_big}_{k}.html") for k in range(1, 43)}
chk("Г: 42 файли сторінок «Картини за номерами» збудовано, 43-ї нема", not (out / f"category-{slug_big}_43.html").exists())
g = {}
for k, h in files.items():
    pg = re.search(r'<nav class="pager".*?</nav>', h, re.S).group(0)
    g[k] = {int((re.fullmatch(rf"category-{slug_big}(?:_(\d+))?\.html", hr).group(1)) or 1) for hr in re.findall(r'<a class="pg(?: nav)?" href="([^"]+)"', pg)}
mc, allr = max_clicks(g, 42)
chk(f"Г: на РЕАЛЬНИХ збудованих сторінках (42): з 1-ї до будь-якої ≤3 кліків (макс {mc}), досяжні всі", mc <= 3 and allr)
def href(i):
    return f'href="category-{slug_big}{"" if i == 1 else "_" + str(i)}.html"'


for k in (1, 2, 3, 21, 22, 41, 42):
    pg = re.search(r'<nav class="pager".*?</nav>', files[k], re.S).group(0)
    chk(f"Г: стор.{k}: поточна — <span class=\"pg cur\">, а не посилання; перша й остання є; вікно ±2 є",
        f'<span class="pg cur">{k}</span>' in pg and href(k) not in pg and (k == 1 or href(1) in pg) and (k == 42 or href(42) in pg)
        and all(href(i) in pg for i in range(max(1, k - 2), min(42, k + 2) + 1) if i != k))
chk("Г: «← Назад» ведуть на k-1, «Далі →» на k+1; на 1-й нема «Назад», на 42-й нема «Далі»",
    'class="pg nav" href="category-%s_21.html">← Назад' % slug_big in files[22] and 'class="pg nav" href="category-%s_23.html">Далі →' % slug_big in files[22]
    and "← Назад" not in files[1] and "Далі →" not in files[42] and 'class="pg nav" href="category-%s.html">← Назад' % slug_big in files[2])
# rel prev/next/canonical без змін
ok_rel = True
for k, h in files.items():
    base = f"category-{slug_big}" + ("" if k == 1 else f"_{k}") + ".html"
    prev_ = f"category-{slug_big}" + ("" if k - 1 == 1 else f"_{k - 1}") + ".html"
    next_ = f"category-{slug_big}_{k + 1}.html"
    ok_rel &= (re.findall(r'<link rel="prev" href="([^"]+)">', h) == ([prev_] if k > 1 else []))
    ok_rel &= (re.findall(r'<link rel="next" href="([^"]+)">', h) == ([next_] if k < 42 else []))
    ok_rel &= (re.findall(r'<link rel="canonical" href="([^"]+)">', h) == [f"https://plutustoys.com.ua/{base}"])
chk("Г: rel prev/next і canonical на всіх 42 сторінках рівно як були", ok_rel)
cat_all = rd("catalog.html")
chk("Г: у каталозі (catalog.html) пейджер є і rel=next на catalog_2.html", 'class="pager"' in cat_all and '<link rel="next" href="catalog_2.html">' in cat_all)
for n in (2, 5, 7):
    ok = all(all(f'>{i}</' in bs._pager(FN, k, n) for i in range(1, n + 1)) for k in range(1, n + 1))
    chk(f"Г: {n} стор. (≤7): показано ВСІ номери на кожній сторінці", ok)
chk("Г: одна сторінка — пейджера нема", bs._pager(FN, 1, 1) == "" and bs._pager(FN, 1, 0) == "")
def seq(h):
    toks = re.findall(r'<span class="pg gap">…</span>|<span class="pg cur">(\d+)</span>|<a class="pg" href="[^"]+">(\d+)</a>', h)
    return ["…" if not (a_ or b_) else int(a_ or b_) for a_, b_ in toks]


ok = True
for n in (8, 9, 42, 100):
    for k in range(1, n + 1):
        t = seq(bs._pager(FN, k, n))
        nums = [x for x in t if x != "…"]
        ok &= nums == sorted(set(nums)) and nums[0] == 1 and nums[-1] == n and k in nums
        for i, x in enumerate(t):
            if x == "…":
                ok &= 0 < i < len(t) - 1 and t[i + 1] - t[i - 1] > 1
            elif i and t[i - 1] != "…":
                ok &= x - t[i - 1] == 1
chk("Г: розмітка: номери зростають без повторів, «…» стоїть лише між несуміжними, між суміжними — без «…» (8/9/42/100 стор., усі k)", ok)
for n in (42, 1170):
    sz = max(len(bs._pager_pages(k, n)) for k in range(1, n + 1))
    chk(f"Г: {n} стор.: найбільше номерів у пейджері {sz} (≤45 — розумно для мобільного)", sz <= 45)
chk("Г: вікно ±2 на k=500 з 1170: 498 499 [500] 501 502 і 1, 1170", {498, 499, 500, 501, 502, 1, 1170} <= bs._pager_pages(500, 1170))


# ═══ Д. Аудит #692: хаби на однакових цінах і одинокий сурогат ═══
serie = [{"id": str(1000 + i), "name": f"s{i}", "price": 94, "category": "Серія", "stock": 3} for i in range(195)]      # «Пазли G-Toys»: 195 карток з ОДНАКОВОЮ ціною
idx_s = bs.build_similar_index(serie)
indeg = {}
for p_ in serie:
    for q_ in bs.similar_products(idx_s, p_):
        indeg[q_["id"]] = indeg.get(q_["id"], 0) + 1
chk(f"Д: серія з 195 карток на однаковій ціні: вхідних «схожих» максимум {max(indeg.values())} (було 196 у п'яти товарів), без хабів — циклічно від id картки", max(indeg.values()) <= 8 and len(indeg) == 195)
chk("Д: кожна картка серії отримує рівно 6 «схожих», без себе, детерміновано (дві збірки індексу однакові)", all(len(bs.similar_products(idx_s, p_)) == 6 and p_["id"] not in [q_["id"] for q_ in bs.similar_products(idx_s, p_)] for p_ in serie)
    and [q["id"] for q in bs.similar_products(idx_s, serie[7])] == [q["id"] for q in bs.similar_products(bs.build_similar_index(serie), serie[7])])
chk("Д: «схожі» картки id 1007 — наступні за id (1008…1013), а не 1000…1005 для всіх", [q["id"] for q in bs.similar_products(idx_s, serie[7])] == ["1008", "1009", "1010", "1011", "1012", "1013"])
chk("Д: остання картка серії огортається по колу (1194 → 1000…1005)", [q["id"] for q in bs.similar_products(idx_s, serie[-1])] == ["1000", "1001", "1002", "1003", "1004", "1005"])
o_s, e_s = build_with({"_PRODUCT_TEXTS_FILE": r'{"5": "abc \ud800 def", "6": "нормальний текст", "7": "з NUL \u0000 всередині"}'})
chk("Д: одинокий сурогат і NUL у product_texts.json: збірка НЕ падає (sitemap записано), значення відкинуто голосно", "сурогат" in e_s and (o_s / "sitemap.xml").exists())

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ Внутрішня перелінковка: текст картки / схожі товари / схожі категорії / пагінація — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
