#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_gift_pages.py — 15.11.2026 (SEO, рішення власника «Усі три разом»): сторінки подарунків /podarunok-*.html + хаб /podarunky.html.
Перевіряє: вік з опису (від N років / N+ / N–M років / місяці), правила добору (ціна, вік, ≤2 з категорії, без уцінки/зброї/книжок/дублів основи назви),
«менше 12 товарів — сторінку не будуємо», «нема тексту — не будуємо», розмітку ItemList/FAQPage/BreadcrumbList, видимі питання = розмітка, хаб, sitemap-записи, футер, чип «Миколай і Новий рік».
`python test_site_gift_pages.py` → exit 0/1."""
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
W = ["Калина", "Мальви", "Барвінок", "Соняшник", "Лелека", "Журавель", "Ромашка", "Волошка", "Тополя", "Берегиня", "Веселка", "Зірка", "Ялинка", "Мрія", "Казка", "Промінь", "Хмаринка", "Джміль", "Метелик", "Бузок"]


def P(name, price, cat, desc="", contrib=60, stock=5, old=None):
    _n[0] += 1
    return {"id": str(7000 + _n[0]), "name": name, "category": cat, "price": price, "contribution": contrib, "stock": stock, "photo": IMG, "old_price": old, "desc": desc}


def many(fmt, n, price, cat_fmt, desc="", contrib=60, start=0):
    return [P(fmt.format(W[(start + i) % 20] + str(start + i)), price, cat_fmt.format(start + i), desc, contrib) for i in range(n)]


# ── вік з опису ──
A = bs.gift_min_age
chk("вік: «Рекомендовано для дітей від 3 років» → 3", A("Рекомендовано для дітей від 3 років") == 3.0)
chk("вік: «від 2-х років» → 2", A("Для малюків від 2-х років") == 2.0)
chk("вік: «від 6 міс» → 0,5 року", abs(A("Від 6 міс до 3 років") - 0.5) < 1e-9)
chk("вік: «3+» → 3", A("Позначка 3+ на коробці") == 3.0)
chk("вік: діапазон «5–7 років» → 5", A("Для дітей 5–7 років") == 5.0)
chk("вік: нема → None", A("Просто іграшка без віку") is None)
chk("вік: «Вік: 3 роки» (без «від») → 3", A("Вік: 3 роки") == 3.0 and A("Рекомендований вік 4 роки") is None or A("Вік: 3 роки") == 3.0)
chk("вік: «Час роботи від 2 годин» / «від 5 год» (години) → None", A("Час роботи від 2 годин") is None and A("Заряджається від 5 год") is None)
chk("вік: «Набір 3 + 1 в подарунок» → None; «3+» упритул → 3", A("Набір 3 + 1 в подарунок") is None and A("Для дітей 3+") == 3.0)
chk("вік: «від 1 года» → 1", A("Для малят от 1 года") == 1.0)
chk("вік: HTML і &nbsp; не заважають", A("<p>Вік:&nbsp;від&nbsp;4 років</p>") == 4.0)

prods = []
prods += many("Іграшка вік3 «{}»", 40, 150, "Вік3-{}", "Рекомендовано для дітей від 3 років")
prods += many("Іграшка вік5 «{}»", 40, 250, "Вік5-{}", "Рекомендовано для дітей від 5 років", start=3)
prods += many("Іграшка вік1 «{}»", 40, 90, "Вік1-{}", "Вік: від 12 міс", start=5)
prods += many("Новорічна іграшка «{}»", 40, 320, "Нр{}", "Для малечі від 3 років", start=7)
prods += many("Звичайна іграшка «{}»", 40, 600, "Зв{}", "", start=9)
prods += [P("Автомат військовий іграшковий", 200, "Зброя-X", "від 5 років"), P("Іграшка з наслідком», Уцінка", 120, "Уцінка", "від 3 років", old=300),
          P("Книжка про звірят", 140, "Книги для малят", "від 3 років"), P("Іграшка нема в наявності", 140, "Нема", "від 3 років", stock=0)]
# 3 товари однієї категорії (ліміт 2 на категорію)
prods += [P(n_, 180, "ОднаКатегорія", "від 3 років", contrib=500) for n_ in ("Лялька Аліса", "Машинка Тріо", "Конструктор Місто")]
prods += [P(f"Іграшка 14+ {W[i]}{i}", 90, f"Дорослі{i}", "Для дітей 14+", contrib=400) for i in range(3)]
prods += [P(f"Безкоштовна {W[i]}{i}", 0, f"Нуль{i}", "від 3 років", contrib=400) for i in range(3)]

texts = {s: {"intro": f"Вступ для {s}. Другий абзац.\n\nТретє речення.", "faq": [{"q": f"Питання {i}?", "a": f"Відповідь {i}."} for i in range(3)]} for s, _h, _l, _r in bs.GIFT_PAGES}
tf = Path(tempfile.mkdtemp()) / "gift_pages.json"
tf.write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
lt = bs._load_gift_texts(str(tf))
chk("тексти: 12 slug завантажено, у кожному intro і ≥3 пар q/a", len(lt) == 12 and all(v["intro"] and len(v["faq"]) >= 3 for v in lt.values()))

# ── добір ──
by_slug = {s: r for s, _h, _l, r in bs.GIFT_PAGES}
b100 = bs.gift_pick(prods, by_slug["podarunok-do-100-hrn"])
chk(f"до 100 грн: усі ціни ≤100 ({len(b100)} шт, лише «Вік1»)", b100 and all(p["price"] <= 100 for p in b100))
a3 = bs.gift_pick(prods, by_slug["podarunok-na-3-roky"])
chk(f"3 роки: лише вік у межах 1–3 (без «від 5 років» і без немовлячих міс): {len(a3)} шт", a3 and all(1 <= bs.gift_min_age(p["desc"]) <= 3 for p in a3))
a1 = bs.gift_pick(prods, by_slug["podarunok-na-1-rik"])
chk("1 рік: «від 12 міс» (=1 рік) потрапляє; «від 3 років» — ні", a1 and all(bs.gift_min_age(p["desc"]) <= 1 for p in a1))
allpicked = [p for s in by_slug for p in bs.gift_pick(prods, by_slug[s])]
names = " | ".join(p["name"] for p in allpicked)
chk("виключено: зброя, уцінка, книги, немає в наявності", "Автомат" not in names and "Уцінка" not in names and "Книжка" not in names and "нема в наявності" not in names)
one = [p for p in bs.gift_pick(prods, by_slug["podarunok-na-3-roky"]) if p["category"] == "ОднаКатегорія"]
chk(f"≤2 товари з однієї категорії (товарів 3, у добірці рівно {len(one)})", len(one) == bs.GIFT_PER_CAT)
nm100 = " | ".join(p["name"] for p in bs.gift_pick(prods, by_slug["podarunok-do-100-hrn"]))
chk("на сторінках без вікового правила немає карток «14+» (аудит #1505)", "14+" not in nm100 and "14+" not in " | ".join(p["name"] for p in bs.gift_pick(prods, by_slug["podarunok-na-mykolaia"])))
chk("товар з ціною 0 не потрапляє ніде (аудит #1505)", all("Безкоштовна" not in p["name"] for r in by_slug.values() for p in bs.gift_pick(prods, r)))
chk(f"кожна добірка ≤ {bs.GIFT_SHOW_MAX} товарів", all(len(bs.gift_pick(prods, r)) <= bs.GIFT_SHOW_MAX for r in by_slug.values()))
ny = bs.gift_pick(prods, by_slug["novorichnyi-podarunok-dytyni"])
_ny_rx = re.compile(next(rx for k, _l, _s, rx, _f, _la, _eb in bs.HOME_SEASONAL if k == "ny"), re.I)
_flags = [bool(_ny_rx.search(p["name"])) for p in ny]
chk("новорічна: спершу назви з HOME_SEASONAL «ny» (усі збіги йдуть перед рештою)", ny and _flags[0] and _flags == sorted(_flags, reverse=True))
mk = bs.gift_pick(prods, by_slug["podarunok-na-mykolaia"])
chk("Миколай: ціна 100–700", mk and all(100 <= p["price"] <= 700 for p in mk))

# ── збірка сторінок ──
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
plan = bs.gift_plan(prods, lt)
slugs = [s for s, _h, _l, _r, _i in plan]
chk(f"план: сторінки з ≥{bs.GIFT_MIN_ITEMS} товарів (є «до 100», 1/3/5 років, Миколай, новорічна…): {slugs}", "podarunok-do-100-hrn" in slugs and "podarunok-na-3-roky" in slugs and "podarunok-na-5-rokiv" in slugs and "podarunok-na-1-rik" in slugs)
chk("план: «на 10 років» (нема таких товарів) не будується — менше 12", "podarunok-na-10-rokiv" not in slugs)
urls = bs.write_gift_pages(prods, lt)
files = {u for u, _p, _f in urls}
chk(f"записано: усі сторінки плану + хаб; sitemap-кортежі (файл, пріоритет, частота): {len(files)}", files == {f"{s}.html" for s in slugs} | {"podarunky.html"} and all(len(t) == 3 for t in urls))
chk("GIFT_BUILT = побудовані сторінки (для футера/чипа)", [s for s, _h, _l in bs.GIFT_BUILT] == slugs)
pg = (out / "podarunok-na-3-roky.html").read_text(encoding="utf-8")
chk("H1 = запит, canonical на себе, title містить запит", '<h1 class="page">Подарунок дитині на 3 роки</h1>' in pg and 'rel="canonical" href="https://plutustoys.com.ua/podarunok-na-3-roky.html"' in pg and "<title>Подарунок дитині на 3 роки" in pg)
chk("не noindex", "noindex" not in pg)
chk("вступ: абзаци з тексту", "<p>Вступ для podarunok-na-3-roky. Другий абзац.</p>" in pg and "<p>Третє речення.</p>" in pg)
lds = [json.loads(x.replace("\\u003c", "<")) for x in re.findall(r'<script type="application/ld\+json">(.*?)</script>', pg, re.S)]
types = [d["@type"] for d in lds]
chk(f"розмітка: BreadcrumbList, ItemList, FAQPage ({types})", {"BreadcrumbList", "ItemList", "FAQPage"} <= set(types))
il = next(d for d in lds if d["@type"] == "ItemList")
n_cards = pg.count('<a class="card"')
chk(f"ItemList = видимі картки ({len(il['itemListElement'])} = {n_cards})", len(il["itemListElement"]) == n_cards and n_cards >= bs.GIFT_MIN_ITEMS)
fq = next(d for d in lds if d["@type"] == "FAQPage")
vis = re.findall(r"<h3>(.*?)</h3><p>(.*?)</p>", pg)
chk("FAQPage: питання й відповіді в розмітці = видимі на сторінці", [(q["name"], q["acceptedAnswer"]["text"]) for q in fq["mainEntity"]] == vis and len(vis) == 3)
chk("посилання на сусідні добірки й на хаб", 'href="podarunok-na-5-rokiv.html"' in pg and 'href="podarunky.html"' in pg and 'href="podarunok-na-3-roky.html"' not in pg.split('class="gift-links"')[1])
hub = (out / "podarunky.html").read_text(encoding="utf-8")
chk("хаб: усі побудовані сторінки, H1, canonical, ItemList", all(f'href="{s}.html"' in hub for s in slugs) and "<h1" in hub and 'rel="canonical" href="https://plutustoys.com.ua/podarunky.html"' in hub and '"ItemList"' in hub)
chk("футер сторінки з хабом: «Подарунки дитині»", 'href="podarunky.html">Подарунки дитині</a>' in pg)
bs.GIFT_BUILT[:] = []
chk("без побудованих сторінок футер не має посилання на хаб", "podarunky.html" not in bs.footer())
bs.GIFT_BUILT[:] = [(s, h, lb) for s, h, lb, _r, _i in plan]

# ── порожній/кривий текст і малий каталог ──
chk("нема тексту → сторінок немає", bs.gift_plan(prods, {}) == [] and bs.write_gift_pages(prods, {}) == [] and bs.GIFT_BUILT == [])
bad = Path(tempfile.mkdtemp()) / "gift_pages.json"
bad.write_text(json.dumps({"podarunok-do-100-hrn": {"intro": "x", "faq": [{"q": "a", "a": "b"}]}, "podarunok-do-200-hrn": {"intro": "", "faq": []}, "_note": "x"}), encoding="utf-8")
chk("кривий текст (мало питань / порожній вступ) пропускається голосно", bs._load_gift_texts(str(bad)) == {})
bad2 = Path(tempfile.mkdtemp()) / "gift_pages.json"
bad2.write_text(json.dumps({"podarunok-do-100-hrn": {"intro": "x", "faq": 5}, "podarunok-do-200-hrn": {"intro": "y", "faq": [1, 2, 3]}}), encoding="utf-8")
chk("faq не список / елементи не об'єкти: збірка НЕ падає, запис пропущено (аудит #1505)", bs._load_gift_texts(str(bad2)) == {})
bad3 = Path(tempfile.mkdtemp()) / "gift_pages.json"
bad3.write_text("[1, 2]", encoding="utf-8")
chk("корінь-масив: {} без падіння", bs._load_gift_texts(str(bad3)) == {})
chk("нема файлу → {}", bs._load_gift_texts(str(Path(tempfile.mkdtemp()) / "no.json")) == {})
few = prods[:5]
chk("замало товарів → жодної сторінки і хаба", bs.write_gift_pages(few, lt) == [])

# ── хаб не обіцяє відсутнього ──
hub_txt = (out / "podarunky.html").read_text(encoding="utf-8")
chk("хаб: опис складено з побудованого (є вік і ціна; «від 1 до 10 років» не захардкожено)", "за віком" in hub_txt and "до 100–500 грн" not in hub_txt and "від 1 до 10 років" not in hub_txt)

# ── чип «Миколай і Новий рік» і крок прибирання ──
chk("файли подарунків відомі кроку прибирання застарілих", "podarunky.html" in bs.GIFT_PAGE_FILES and "podarunok-na-3-roky.html" in bs.GIFT_PAGE_FILES)
src = Path(bs.__file__).read_text(encoding="utf-8")
chk("крок прибирання: gift-файли, яких збірка не будує, видаляються окремою гілкою; початок оригінального if лишився (тест gsc_verification)", "elif fn in GIFT_PAGE_FILES and fn not in _gift_files:" in src and "        if (fn.startswith((\"product-\"" in src)
bs.HELOVIN_JSON = str(Path(tempfile.mkdtemp()) / "none.json")
os.environ["SITE_PICK_DATE"] = "2026-12-10"
ny_prods = [P(f"Новорічна ялинка «{W[i % 20]}{i}»", 250, f"Ялинки{i}", "", contrib=70) for i in range(40)] + [P(f"Звичайна іграшка «{W[i % 20]}{i}»", 300 + i, f"Звич{i}", "", contrib=150) for i in range(14)]
ny_cats = {}
for p_ in ny_prods:
    ny_cats.setdefault(p_["category"], []).append(p_)
ny_cl = sorted(ny_cats)
ny_slug = {c: bs.slugify(c) for c in ny_cats}
bs.GIFT_BUILT[:] = [(s, h, lb) for s, h, lb, _r, _i in plan]
w_ny = bs.write_chip_pages(ny_prods, ny_cl, ny_slug)
chip_file = bs.CHIP_PAGE_FILES["ny"]
chk(f"чип «Миколай і Новий рік» ({chip_file}): є посилання на хаб подарунків, коли хаб побудовано", chip_file in w_ny and 'href="podarunky.html"' in (out / chip_file).read_text(encoding="utf-8"))
bs.GIFT_BUILT[:] = []
bs.write_chip_pages(ny_prods, ny_cl, ny_slug)
chk("чип: без хаба посилання нема", 'href="podarunky.html"' not in (out / chip_file).read_text(encoding="utf-8"))

if F:
    print(f"\n❌ FAIL: {len(F)}")
    sys.exit(1)
print("\n✅ Сторінки подарунків — усі перевірки коректні.")
