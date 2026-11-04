#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_reviews.py — відгуки покупців на сайті (03.11.2026; контракт — site/REVIEWS_CONTRACT.md). Повний build() на синтетичному каталозі у тимчасовій теці (мережа не потрібна),
відгуки — ЛИШЕ у тимчасовому файлі тесту (у site/ нічого не кладемо). Перевіряє:
  а) без reviews.json картки байт-у-байт як зі збіркою без файлу (і товар без відгуків у файлі — теж);
  б) з відгуками: блок «Відгуки покупців» видимий (після тексту картки, до «Схожих»), JSON-LD aggregateRating + review чисельно коректні й з тих самих записів;
  в) товар без відгуків — жодного aggregateRating/review;
  г) невалідні записи (rating 0/6/рядок/bool, порожній/короткий текст, не-ISO/неіснуюча дата, задовгий автор, нецифровий ключ) — пропущено з ОДНИМ зведеним попередженням;
  д) XSS: <script>, ", </script> у text/author — екрановано і в HTML, і в JSON-LD;
  е) >10 відгуків → показано 10 найновіших, reviewCount=10;
  ж) review.html: noindex, поза sitemap, Disallow у robots, без аналітики, містить api/review/form і api/review, у змісті нема слів про доставку/гроші;
  з) FAIL-CLOSED: битий JSON, не об'єкт, нема _meta, count не ціле, count ≠ кількості записів, порожній {} → збірка завершується, відгуків/розмітки нема, ГОЛОСНО;
     застарілий/нечитабельний exported_at — лише попередження, відгуки показано."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
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


def item(pid, name, category, price, stock=5):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": stock, "category_name": category, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "barcode": "",
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.<br><b>Бренд:</b> Тест<br><b>Вік:</b> 3+"}


CARS, DOLLS = "Машинки", "Ляльки"
catalog = {}
for i, pr in enumerate((200, 210, 190, 220, 180, 230, 170, 240)):
    catalog[str(101 + i)] = item(101 + i, f"Машинка {i}", CARS, pr)
for i in range(6):
    catalog[str(201 + i)] = item(201 + i, f"Лялька {i}", DOLLS, 80 + i)
catalog["301"] = item(301, "Машинка без залишку", CARS, 205, stock=0)

bs.tp.fetch_toysi_catalog = lambda: catalog
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.price_of = lambda it: int(it["price"])
bs.LIMIT = 0
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None

TMP = Path(tempfile.mkdtemp())
PT_FILE = TMP / "product_texts_fixture.json"
PT_FILE.write_text(json.dumps({"101": "Текст картки для перевірки порядку блоків."}, ensure_ascii=False), encoding="utf-8")
bs._PRODUCT_TEXTS_FILE = str(PT_FILE)
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def payload(entries, count=None, exported_at=NOW, meta=True, ascii=False):
    d = {}
    if meta:
        d["_meta"] = {"exported_at": exported_at, "count": count if count is not None else sum(len(v) if isinstance(v, list) else 1 for v in entries.values())}
    d.update(entries)
    return json.dumps(d, ensure_ascii=ascii)


def build_with(reviews=None):
    """повний build() у новій теці; reviews: None — файлу нема; str/bytes — вміст reviews.json. Повертає (тека, stderr)."""
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    fp = out / "reviews_fixture.json"
    if isinstance(reviews, bytes):
        fp.write_bytes(reviews)
    elif reviews is not None:
        fp.write_text(reviews, encoding="utf-8")
    bs._REVIEWS_FILE = str(fp)
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        bs.build()
    return out, err.getvalue()


def rd(out, name):
    return (out / name).read_text(encoding="utf-8")


def product_ld(h):
    for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', h, re.S):
        d = json.loads(raw)
        if d.get("@type") == "Product":
            return raw, d
    return None, None


PIDS = [f"product-{p}.html" for p in list(catalog)]

# ═══ а) базова збірка без файлу ═══
base, base_err = build_with(None)
chk("а: без reviews.json — збірка тиха щодо відгуків (жодного рядка reviews.json у stderr)", "reviews.json" not in base_err)
chk("а: без reviews.json — на жодній картці нема блоку відгуків і aggregateRating/review",
    all('id="reviews"' not in rd(base, f) and "aggregateRating" not in rd(base, f) and '"review"' not in rd(base, f) for f in PIDS))

# ═══ б/в/г/д/е) збірка з відгуками ═══
XSS_TEXT = 'Чудово! <script>alert(1)</script> "лапки" </script><!-- & кінець'
XSS_AUTHOR = '</script><b>"Х"'
many = [{"rating": 5 if d % 2 else 4, "text": f"Відгук номер {d}", "author": f"Автор {d}", "date": f"2026-10-{d:02d}"} for d in range(1, 13)]   # 12 записів, порядок — від старих (перевіряємо сортування)
entries = {
    "101": [{"rating": 5, "text": "  Дуже гарна машинка, дитина в захваті.  ", "author": "Олена", "date": "2026-10-30"},
            {"rating": 4, "text": "Добре, але коробка пом'ята.\nДругий рядок.", "author": "", "date": "2026-10-29"},
            {"rating": 4, "text": "Нормально", "date": "2026-10-28"}],
    "102": [{"rating": 3, "text": XSS_TEXT, "author": XSS_AUTHOR, "date": "2026-11-01"}],
    "103": many,
    "104": [{"rating": 0, "text": "Нуль зірок", "author": "А", "date": "2026-11-01"},
            {"rating": 6, "text": "Шість зірок", "author": "А", "date": "2026-11-01"},
            {"rating": "5", "text": "Рядок замість числа", "author": "А", "date": "2026-11-01"},
            {"rating": True, "text": "bool замість числа", "author": "А", "date": "2026-11-01"},
            {"rating": 5, "text": "   ", "author": "А", "date": "2026-11-01"},
            {"rating": 5, "text": "ab", "author": "А", "date": "2026-11-01"},
            {"rating": 5, "text": "x" * 1501, "author": "А", "date": "2026-11-01"},
            {"rating": 5, "text": "Не-ISO дата", "author": "А", "date": "10.11.2026"},
            {"rating": 5, "text": "Неіснуюча дата", "author": "А", "date": "2026-13-01"},
            {"rating": 5, "text": "Задовгий автор", "author": "Я" * 41, "date": "2026-11-01"},
            "не об'єкт",
            {"rating": 5, "text": "Дата з майбутнього", "author": "А", "date": "2099-01-01"},
            {"rating": 5, "text": "\u200b\u200b \u200b\ufeff\u2060 \u200c\u200d", "author": "А", "date": "2026-11-01"},
            {"rating": 5, "text": "\u200bab\u200b", "author": "А", "date": "2026-11-01"},
            {"rating": 2, "text": "Єдиний валідний серед поганих", "author": "Петро", "date": "2026-11-02"}],
    "abc": [{"rating": 5, "text": "Нецифровий ключ", "author": "А", "date": "2026-11-01"}],
    "rt-77": [{"rating": 5, "text": "rt-ключ не приймаємо", "author": "А", "date": "2026-11-01"}],
    "00101": [{"rating": 5, "text": "Ведучий нуль у ключі", "author": "А", "date": "2026-11-01"}],
    "105": [{"rating": 4, "text": "Доставка швидка, за 200 грн — нормально", "author": "Марія", "date": "2026-10-20"}],
    "999999": [{"rating": 5, "text": "Товару нема в збірці", "author": "А", "date": "2026-11-01"}],
}
data = json.loads(payload(entries))
data["_service"] = {"будь-що": 1}   # службовий ключ «_…» ігнорується й не рахується в count
out, err = build_with(json.dumps(data, ensure_ascii=False))

h101 = rd(out, "product-101.html")
raw101, ld101 = product_ld(h101)
chk("б: блок «Відгуки покупців» є, з 3 відгуками", 'id="reviews"' in h101 and "Відгуки покупців" in h101 and h101.count('<li class="rv"') == 3)
chk("б: заголовок — середня 4,3 з 5 і «3 відгуки»", "<b>4,3</b> з 5 · 3 відгуки" in h101)
chk("б: зірки символами + числом (★★★★★ 5 з 5; ★★★★☆ 4 з 5)", "★★★★★</span> <span class=\"rv-num\">5 з 5</span>" in h101 and "★★★★☆</span> <span class=\"rv-num\">4 з 5</span>" in h101)
chk("б: порожній/відсутній автор → «Покупець»; текст обрізано від пробілів; \\n → <br>",
    h101.count('<b class="rv-author">Покупець</b>') == 2 and ">Дуже гарна машинка, дитина в захваті.</p>" in h101 and "пом&#x27;ята.<br>Другий рядок." in h101)
i_text, i_rev = h101.index('<div class="prod-text">'), h101.index('id="reviews"')
i_sec, i_buy = h101.index('<section class="related">'), h101.index('<div class="buybar">')
chk("б: порядок: текст картки → відгуки → «Схожі…» → панель «У кошик»", i_text < i_rev < i_sec < i_buy)
ar = (ld101 or {}).get("aggregateRating") or {}
chk("б: JSON-LD aggregateRating: ratingValue \"4.3\", reviewCount \"3\", bestRating \"5\", worstRating \"1\"",
    ar == {"@type": "AggregateRating", "ratingValue": "4.3", "reviewCount": "3", "bestRating": "5", "worstRating": "1"})
rv = (ld101 or {}).get("review") or []
chk("б: JSON-LD review — 3 записи Review з author Person, datePublished, reviewRating (Rating, bestRating 5), reviewBody — ті самі, що на сторінці",
    len(rv) == 3 and all(r["@type"] == "Review" and r["author"]["@type"] == "Person" and r["reviewRating"]["@type"] == "Rating" and r["reviewRating"]["bestRating"] == "5" for r in rv)
    and [r["reviewRating"]["ratingValue"] for r in rv] == ["5", "4", "4"] and [r["datePublished"] for r in rv] == ["2026-10-30", "2026-10-29", "2026-10-28"]
    and rv[0]["reviewBody"] == "Дуже гарна машинка, дитина в захваті." and rv[0]["author"]["name"] == "Олена" and rv[2]["author"]["name"] == "Покупець")
chk("б: без shippingDetails у розмітці", "shippingDetails" not in h101)
chk("б: середня — half-up з 1 знаком (4,4,4,5 → 4.3; 5,4 → 4.5; 1 → 1.0; 5×10 → 5.0)",
    bs._review_avg([{"rating": x} for x in (4, 4, 4, 5)]) == "4.3" and bs._review_avg([{"rating": 5}, {"rating": 4}]) == "4.5"
    and bs._review_avg([{"rating": 1}]) == "1.0" and bs._review_avg([{"rating": 5}] * 10) == "5.0")
chk("б: OOS-картка без відгуків лишається без блоку", 'id="reviews"' not in rd(out, "product-301.html"))

# в) товар без відгуків — сторінка та сама, що в базовій збірці (і жодного aggregateRating/review)
no_rev = [f for f in PIDS if f not in ("product-101.html", "product-102.html", "product-103.html", "product-104.html", "product-105.html")]
chk("в: товари без відгуків — без aggregateRating/review/блоку", all("aggregateRating" not in rd(out, f) and '"review"' not in rd(out, f) and 'id="reviews"' not in rd(out, f) for f in no_rev))
same = [f for f in no_rev if rd(out, f) != rd(base, f)]
chk("а/в: картки без відгуків БАЙТ-У-БАЙТ як у збірці без файлу (крім сітки «Схожих» — її теж не змінено)", not same)
chk("а: службові сторінки (головна/каталог/кошик/подяка) не змінились від наявності reviews.json",
    all(rd(out, f) == rd(base, f) for f in ("index.html", "catalog.html", "cart.html", "thanks.html", "review.html")))

# г) невалідні записи
h104 = rd(out, "product-104.html")
chk("г: з 15 записів 104 показано лише 1 валідний", h104.count('<li class="rv"') == 1 and "Єдиний валідний серед поганих" in h104 and '"reviewCount": "1"' in h104)
for bad in ("Нуль зірок", "Шість зірок", "Рядок замість числа", "bool замість числа", "Не-ISO дата", "Неіснуюча дата", "Задовгий автор", "Дата з майбутнього", "2099"):
    chk(f"г: невалідний запис «{bad}» не показано", bad not in h104)
warn_lines = [ln for ln in err.splitlines() if "reviews.json: пропущено невалідних" in ln]
chk("г: ОДНЕ зведене попередження: 14 невалідних записів (вкл. дату з майбутнього й текст із пробілів нульової ширини) + ключі abc/rt-77/00101",
    len(warn_lines) == 1 and "записів 14" in warn_lines[0] and "'abc'" in warn_lines[0] and "'rt-77'" in warn_lines[0] and "'00101'" in warn_lines[0])
chk("г: ключ із ведучим нулем (00101) не показано ніде", all("Ведучий нуль у ключі" not in rd(out, f) for f in PIDS))
chk("г: текст лише з пробілів нульової ширини / <3 видимих символів — не показано", "\u200bab\u200b" not in h104 and h104.count('<li class="rv"') == 1)
h105 = rd(out, "product-105.html")
money_lines = [ln for ln in err.splitlines() if "у показаних відгуках є гроші/доставка" in ln]
chk("г: гроші/доставка у відгуку — ОДНЕ попередження з id 105, відгук показано (не блокуємо)",
    len(money_lines) == 1 and "'105'" in money_lines[0] and "'101'" not in money_lines[0] and "Доставка швидка, за 200 грн" in h105)
chk("г: нецифровий ключ у вихід не потрапив (немає сторінки, нема тексту)", all("Нецифровий ключ" not in rd(out, f) for f in PIDS))
chk("г: службовий ключ «_service» не рахується й не дає попередження про count", "_meta.count=" not in err and "_service" not in err)

# д) XSS
h102 = rd(out, "product-102.html")
raw102, ld102 = product_ld(h102)
blk = h102[h102.index('id="reviews"'):h102.index("</section>", h102.index('id="reviews"'))]
chk("д: HTML — <script>, </script>, <!--, лапки й & у тексті/авторі екрановано",
    "<script" not in blk and "</script" not in blk and "<!--" not in blk and "<b>&quot;" not in blk
    and "&lt;script&gt;alert(1)&lt;/script&gt; &quot;лапки&quot; &lt;/script&gt;&lt;!-- &amp; кінець" in blk
    and "&lt;/script&gt;&lt;b&gt;&quot;Х&quot;" in blk)
chk("д: JSON-LD — жодного літерального '<' (</script> не виходить), значення відновлюються точно",
    raw102 is not None and "<" not in raw102 and ld102["review"][0]["reviewBody"] == XSS_TEXT.strip() and ld102["review"][0]["author"]["name"] == XSS_AUTHOR)
chk("д: на сторінці рівно стільки </script>, скільки <script (нічого не «вирвалось»)", h102.count("<script") == h102.count("</script>"))

# е) >10
h103 = rd(out, "product-103.html")
raw103, ld103 = product_ld(h103)
shown = re.findall(r'<time class="rv-date" datetime="([0-9-]+)"', h103)
exp_dates = [f"2026-10-{d:02d}" for d in range(12, 2, -1)]
chk("е: з 12 показано 10 найновіших (від нових до старих)", shown == exp_dates)
exp_avg = bs._review_avg([{"rating": 5 if d % 2 else 4} for d in range(3, 13)])
chk(f"е: reviewCount \"10\", ratingValue з показаних 10 ({exp_avg}), review — 10 записів з тими самими датами",
    ld103["aggregateRating"]["reviewCount"] == "10" and ld103["aggregateRating"]["ratingValue"] == exp_avg
    and [r["datePublished"] for r in ld103["review"]] == exp_dates and "10 відгуків" in h103)

# ж) review.html
hr = rd(out, "review.html")
chk("ж: review.html — noindex", '<meta name="robots" content="noindex,follow">' in hr)
chk("ж: review.html — <meta name=\"referrer\" content=\"no-referrer\"> у <head>", '<meta name="referrer" content="no-referrer">' in hr[:hr.index("</head>")])
chk("ж: токен береться ЛИШЕ з фрагмента #t= (location.hash), не з query: URLSearchParams/get(\"t\") нема, location.search — лише у перевірці старого ?t= (→ «недійсне»)",
    'exec(location.hash||"")' in hr and "URLSearchParams" not in hr and '.get("t")' not in hr
    and re.findall(r"location\.search", hr) == ["location.search"] and 'if(/[?&]t=/.test(location.search)){bad();return;}' in hr)
_urls = re.findall(r'(?:href|src|action)="([^"]*)"', hr)
chk("ж: жодне посилання/ресурс сторінки (href/src/action) не містить t= чи #t", _urls and not any(re.search(r"[?&#]t=", u) for u in _urls))
chk("ж: адреса чиститься (replaceState) і в терміналних станах (успіх/помилка), і після помилки POST",
    'function say(s){f.hidden=true;st.textContent=s;st.hidden=false;clean();}' in hr and "btn.textContent=\"Надіслати відгук\";clean();}" in hr)
sm = "".join((out / f).read_text(encoding="utf-8") for f in os.listdir(out) if f.startswith("sitemap") and f.endswith(".xml"))
chk("ж: review.html немає в жодному sitemap", "review.html" not in sm and "sitemap.xml" in os.listdir(out))
chk("ж: robots.txt — Disallow: /review.html", "Disallow: /review.html" in rd(out, "robots.txt"))
chk("ж: містить api/review/form і POST api/review", 'fetch("api/review/form?t="+encodeURIComponent(t),{cache:"no-store",credentials:"omit"})' in hr and 'fetch("api/review",{method:"POST"' in hr)
main = hr[hr.index('<section class="doc review-page"'):hr.index("<footer")]
chk("ж: у змісті сторінки нема слів про доставку/гроші (доставк, гривн/гривен, грн, ₴)", not re.search(r"доставк|гриве?н|грн|₴", main, re.I))
chk("ж: назва товару — textContent, без innerHTML; токен не логується", "pn.textContent=String(nm)" in main and "innerHTML" not in main and "console." not in main)
chk("ж: форма: 5 radio оцінки, textarea maxlength 1500 + лічильник, ім'я maxlength 40, кнопка «Надіслати відгук»",
    main.count('type="radio" name="rating"') == 5 and 'maxlength="1500"' in main and 'id="rv-count"' in main and 'name="author_name" maxlength="40"' in main
    and ">Надіслати відгук</button>" in main)
chk("ж: повідомлення 404/410, «пізніше», успіх, 429; кнопка блокується; t прибирається replaceState після успіху",
    "Посилання недійсне або вже використане" in main and "Спробуйте пізніше" in main and "Дякуємо! Відгук з’явиться після перевірки" in main
    and "r.status===429" in main and "btn.disabled=true" in main and 'history.replaceState(null,"",location.pathname)' in main)
# аналітика: з ID трекерів інші сторінки отримують сніпети, review.html — ні (адреса з токеном не йде в GA4/Pixel)
_ga, _px = bs.GA4_ID, bs.META_PIXEL_ID
bs.GA4_ID, bs.META_PIXEL_ID = "G-TEST123456", "123456789012"
try:
    out_an, _ = build_with(None)
finally:
    bs.GA4_ID, bs.META_PIXEL_ID = _ga, _px
hr_an = rd(out_an, "review.html")
chk("ж: з увімкненою аналітикою: thanks.html має PT_ANALYTICS, review.html — жодного gtag/fbq/PT_ANALYTICS",
    "PT_ANALYTICS" in rd(out_an, "thanks.html") and not re.search(r"PT_ANALYTICS|gtag|fbq|googletagmanager|fbevents", hr_an))

# з) FAIL-CLOSED і лоадер
good = {"101": entries["101"]}
cases = {
    "битий JSON": ('{"_meta": {"count": 3}, "101": [', "невалідний JSON"),
    "не об'єкт": ("[1, 2]", "очікується об'єкт"),
    "нема _meta": (payload(good, meta=False), "нема _meta"),
    "_meta.count не ціле": (json.dumps({"_meta": {"exported_at": NOW, "count": "3"}, **good}, ensure_ascii=False), "нема _meta"),
    "_meta.count дробове": (json.dumps({"_meta": {"exported_at": NOW, "count": 3.0}, **good}, ensure_ascii=False), "нема _meta"),
    "count ≠ записів (обірваний)": (payload(good, count=4), "_meta.count=4, а записів у файлі 3"),
    "порожній {}": ("{}", "нема _meta"),
    "cp1251": ('{"_meta": {"count": 1}, "101": [{"rating": 5, "text": "Текст", "author": "Ія", "date": "2026-11-01"}]}'.encode("cp1251"), "НЕ прочитано"),
}
for name, (content, needle) in cases.items():
    o, e = build_with(content)
    h = rd(o, "product-101.html")
    chk(f"з: {name} → збірка завершилась (sitemap є), картка БЕЗ відгуків і aggregateRating, ГОЛОСНО у stderr",
        (o / "sitemap.xml").exists() and 'id="reviews"' not in h and "aggregateRating" not in h and '"review"' not in h
        and needle in e and "fail-closed" in e and h == rd(base, "product-101.html"))
o, e = build_with(payload({}, count=0))
chk("з: {_meta: count 0} без записів → тихо, відгуків нема", "reviews.json" not in e and 'id="reviews"' not in rd(o, "product-101.html"))


def load(content, now=None):
    fp = TMP / "load_reviews.json"
    fp.write_text(content, encoding="utf-8")
    bs._REVIEWS_FILE = str(fp)
    e = io.StringIO()
    with contextlib.redirect_stderr(e):
        r = bs._load_reviews(now=now)
    return r, e.getvalue()


r, e = load(payload(good, exported_at="2026-11-01T12:00:00Z"), now=datetime(2026, 11, 9, 12, 0, 1, tzinfo=timezone.utc))
chk("з: exported_at старший за 7 діб → лише попередження, відгуки ЗАВАНТАЖЕНО", len(r.get("101", [])) == 3 and "старше 7 діб" in e and "fail-closed" not in e)
r, e = load(payload(good, exported_at="2026-11-03T12:00:00Z"), now=datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc))
chk("з: exported_at 6 діб — тихо", len(r.get("101", [])) == 3 and e == "")
r, e = load(payload(good, exported_at="2026-11-11T12:00:01Z"), now=datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc))
chk("з: exported_at у майбутньому (>1 доба) → лише попередження, відгуки ЗАВАНТАЖЕНО", len(r.get("101", [])) == 3 and "у майбутньому" in e and "fail-closed" not in e)
r, e = load(payload(good, exported_at="2026-11-11T11:00:00Z"), now=datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc))
chk("з: exported_at на <1 добу вперед (розбіжність годинників) — тихо", len(r.get("101", [])) == 3 and e == "")
_fut = {"101": [{"rating": 5, "text": "Сьогоднішній за Києвом", "author": "А", "date": "2026-11-11"}, {"rating": 5, "text": "Завтрашній за Києвом", "author": "А", "date": "2026-11-12"}]}
r, e = load(payload(_fut, exported_at="2026-11-10T22:30:00Z"), now=datetime(2026, 11, 10, 22, 30, 0, tzinfo=timezone.utc))
chk("з: дата відгуку — за київською добою: 11.11 (у Києві вже 00:30 11.11) прийнято, 12.11 (майбутнє) відкинуто",
    [x["text"] for x in r.get("101", [])] == ["Сьогоднішній за Києвом"] and "записів 1" in e)
r, e = load(payload(good, exported_at="вчора"))
chk("з: exported_at нечитабельне → лише попередження, відгуки ЗАВАНТАЖЕНО", len(r.get("101", [])) == 3 and "exported_at нема, нечитабельне" in e and "fail-closed" not in e)
r, e = load(json.dumps({"_meta": {"count": 3}, **good}, ensure_ascii=False))
chk("з: exported_at відсутнє → лише попередження, відгуки ЗАВАНТАЖЕНО", len(r.get("101", [])) == 3 and "exported_at нема" in e)
bs._REVIEWS_FILE = str(TMP / "nope.json")
_e = io.StringIO()
with contextlib.redirect_stderr(_e):
    r = bs._load_reviews()
chk("з: нема файлу → порожньо БЕЗ шуму", r == {} and _e.getvalue() == "")
r, e = load(payload({"101": [{"rating": 5, "text": "abc \ud800 def", "author": "А", "date": "2026-11-01"},
                             {"rating": 5, "text": "з NUL \u0000 всередині", "author": "А", "date": "2026-11-01"},
                             {"rating": 5, "text": "Нормальний текст", "author": "А", "date": "2026-11-01"}]}, ascii=True))
chk("з: одинокий сурогат/NUL у тексті → запис відкинуто (збірка не впаде на записі HTML)", [x["text"] for x in r.get("101", [])] == ["Нормальний текст"] and "записів 2" in e)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ Відгуки: блок / JSON-LD / валідація / XSS / review.html / fail-closed — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
