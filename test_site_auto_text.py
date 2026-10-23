#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_auto_text.py — автотекст картки САЙТУ з атрибутів Toysi (site_auto_text.py, 23.10.2026; запит SEO + слово власника).

ІНВАРІАНТИ:
  - словник визначень = 25 ключів SEO (seo_site_definitions.json); службові ключі «_…» пропускаються; кривий файл → {} (без винятку);
  - текст = «Категорія: …» + визначення (лише зі словника) + Виробник/Країна/Розмір (лише реальні значення); БЕЗ користі/оцінок/closers/generic marketplace-генератора;
  - категорія без визначення й без жодного факту → '' (не вигадуємо); країна з чорного списку не друкується; вибір варіанта детермінований за sku;
  - marketplace-генератор (seo_content_generator.CATEGORY_BENEFIT) НЕ змінено — фіди маркетплейсів не зачеплені;
  - build_site: прапорець SITE_AUTO_TEXTS_ENABLED вимкнено за замовчуванням → у збірці автотекстів нема; увімкнено → prod-text на картках Toysi з визначенням/фактами, ручний product_texts.json має пріоритет,
    rt- картки без автотексту, HTML екранується.
Без мережі.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID", "SITE_BUILD_LIMIT"):
    os.environ.pop(k, None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import site_auto_text as sat  # noqa: E402
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


defs = sat.load_definitions()
chk("словник SEO: рівно 25 ключів, службові «_…» не потрапили, кожне значення — непорожній список рядків", len(defs) == 25 and not any(k.startswith("_") for k in defs) and all(isinstance(v, list) and v and all(isinstance(s, str) and s for s in v) for v in defs.values()))
chk("ключі словника — нижній регістр", all(k == k.lower() for k in defs))
tmp = Path(tempfile.mkdtemp())
(tmp / "bad.json").write_text("{не json", encoding="utf-8")
(tmp / "arr.json").write_text("[1,2]", encoding="utf-8")
(tmp / "mix.json").write_text(json.dumps({"_x": "пояснення", "пенал": ["Пенал — це футляр."], "кривий": "рядок", "порожній": [], "числа": [1, 2]}, ensure_ascii=False), encoding="utf-8")
chk("кривий/неіснуючий/не-об'єктний файл → {} без винятку", sat.load_definitions(tmp / "bad.json") == {} and sat.load_definitions(tmp / "nope.json") == {} and sat.load_definitions(tmp / "arr.json") == {})
chk("змішаний файл: лише валідні ключі (службові, не-списки, порожні, нерядкові відкинуто)", sat.load_definitions(tmp / "mix.json") == {"пенал": ["Пенал — це футляр."]})

ITEM = {"id": "100", "category_name": "Спецтехніка", "vendor": "Автопром", "country": "Китай",
        "params": [["Довжина без упаковки (см)", "20"], ["Ширина без упаковки (см)", "10"], ["Висота без упаковки (см)", "8"]]}
t = sat.site_text(ITEM, defs)
chk("повний текст: категорія, визначення, виробник, країна, розмір — одним абзацом", t == "Категорія: Спецтехніка. Спецтехніка — це моделі машин спеціального призначення. Виробник — Автопром. Країна походження — Китай. Розмір: 20×10×8 см.")
bad_words = ("розвива", "якісн", "чудов", "гарний", "підійде", "подарунок", "дарує", "радість", "найкращ")
chk("у тексті НЕМАЄ користі/оцінок/closers marketplace-генератора", not any(w in t.lower() for w in bad_words))
chk("категорія без визначення, але з фактами → «Категорія» + факти, без вигаданого речення", sat.site_text({"id": "1", "category_name": "Розважальні", "vendor": "Бренд"}, defs) == "Категорія: Розважальні. Виробник — Бренд.")
chk("категорія без визначення й без фактів → '' (не вигадуємо)", sat.site_text({"id": "2", "category_name": "Розважальні"}, defs) == "" and sat.site_text({"id": "3"}, defs) == "")
chk("визначення без жодного факту → текст «Категорія + визначення» (визначення — достатній факт)", sat.site_text({"id": "4", "category_name": "Пенали"}, {"пенал": ["Пенал — це футляр."]}, verified=None) == "Категорія: Пенали. Пенал — це футляр.")
chk("країна з чорного списку не друкується; решта фактів лишається", "Країна" not in sat.site_text({"id": "5", "category_name": "Х", "vendor": "В", "country": "Росія"}, defs) and "Виробник — В." in sat.site_text({"id": "5", "category_name": "Х", "vendor": "В", "country": "Росія"}, defs))
chk("розмір лише за ВСІМА трьома значеннями", "Розмір" not in sat.site_text({"id": "6", "category_name": "Х", "vendor": "В", "params": [["Довжина без упаковки (см)", "5"]]}, defs))
chk("кривий params (не пари) не ламає", sat.site_text({"id": "7", "category_name": "Х", "vendor": "В", "params": ["a", 5, None, ["k"]]}, defs) == "Категорія: Х. Виробник — В.")
chk("вибір варіанта детермінований за sku (двічі той самий) і різні sku можуть дати різні", sat.definition_for("метале", "10", {"метале": ["A.", "B.", "C."]}) == sat.definition_for("метале", "10", {"метале": ["A.", "B.", "C."]})
    and len({sat.definition_for("метале", str(i), {"метале": ["A.", "B.", "C."]}) for i in range(40)}) > 1)

# ── змішані категорії, білий список, розмір
MIXED = "Пенали та папки"
chk("змішана категорія: визначення того ключа, що є в НАЗВІ товару («Папка» → папка, не пенал)", "Папка — це обкладинка" in sat.site_text({"id": "10", "name": "Папка з кнопкою А5", "category_name": MIXED}, defs) and "Пенал — це футляр" not in sat.site_text({"id": "10", "name": "Папка з кнопкою А5", "category_name": MIXED}, defs))
chk("змішана категорія: ключ є в назві → пенал", "Пенал — це футляр" in sat.site_text({"id": "11", "name": "Пенал учнівський", "category_name": MIXED}, defs))
chk("змішана категорія: жоден ключ не в назві → без визначення, лише факти (не визначення іншого товару)", sat.site_text({"id": "12", "name": "Тренувальні трусики", "category_name": "Клейонки, килимки і підгузники", "vendor": "Mega Zayka"}, defs) == "Категорія: Клейонки, килимки і підгузники. Виробник — Mega Zayka.")
for cat_name, bad in (("Металеві конструктори", "Металева модель"), ("Художні книги", "для малят"), ("Магнітні конструктори", "Магніт —"), ("Плакати інтерактивні", "Інтерактивна іграшка")):
    out = sat.site_text({"id": "13", "name": "Товар", "category_name": cat_name, "vendor": "В"}, defs)
    chk(f"категорія «{cat_name}» поза списком звірених → без визначення «{bad}»", bad not in out and out == f"Категорія: {cat_name}. Виробник — В.")
chk("білий список: усі 21 назва — нижній регістр, і кожній відповідає хоча б один ключ словника", all(c == c.lower() for c in sat.VERIFIED_CATEGORIES) and len(sat.VERIFIED_CATEGORIES) == 21 and all(any(k in c for k in defs) for c in sat.VERIFIED_CATEGORIES))
chk("розмір: є «без упаковки» → «Розмір:»", sat._dimensions([["Довжина без упаковки (см)", "1"], ["Ширина без упаковки (см)", "2"], ["Висота без упаковки (см)", "3"]]) == "Розмір: 1×2×3 см.")
chk("розмір: лише «в упаковці» → «Розмір упаковки:» (не видається за розмір товару)", sat._dimensions([["Довжина в упаковці (см)", "1"], ["Ширина в упаковці (см)", "2"], ["Висота в упаковці (см)", "3"]]) == "Розмір упаковки: 1×2×3 см.")
chk("розмір: набори не змішуються (довжина без упаковки + ширина/висота в упаковці → пусто)", sat._dimensions([["Довжина без упаковки (см)", "1"], ["Ширина в упаковці (см)", "2"], ["Висота в упаковці (см)", "3"]]) == "")

# ── marketplace-генератор не зачеплений
import seo_content_generator as scg  # noqa: E402
chk("seo_content_generator.CATEGORY_BENEFIT не змінено цим PR: 24 ключі, жодного ключа зі словника сайту, якого раніше не було («метале», «брелок», «прорізувач»)", len(scg.CATEGORY_BENEFIT) == 24 and not any(k in scg.CATEGORY_BENEFIT for k in ("метале", "брелок", "прорізувач", "парасол")))

# ── build_site: прапорець, пріоритет, rt, екранування
chk("SITE_AUTO_TEXTS_ENABLED за замовчуванням ВИМКНЕНО", bs.SITE_AUTO_TEXTS_ENABLED is False)


def item(pid, name, category, price, **kw):
    d = {"id": str(pid), "name": name, "vendor": "Автопром", "price": price, "stock": 5, "category_name": category, "category_id": "10", "country": "Китай",
         "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "barcode": "", "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів."}
    d.update(kw)
    return d


catalog = {
    "500": item(500, "Спецмашина", "Спецтехніка", 200),
    "501": item(501, "Розважалка", "Розважальні", 150, vendor="", country=""),             # без визначення й без фактів → без тексту
    "502": item(502, "Ручний текст", "Спецтехніка", 160),                                  # ручний product_texts.json — пріоритет
    "503": item(503, "Тег", "Брелоки", 90, vendor="A<b>&B", country="Китай"),              # екранування вмісту
}
bs.tp.fetch_toysi_catalog = lambda: catalog
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.price_of = lambda it: int(it["price"])
bs.LIMIT = 0
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None


def build(enabled):
    out = Path(tempfile.mkdtemp())
    bs.OUT = str(out)
    mf = out / "pt.json"
    mf.write_text(json.dumps({"502": "Ручний текст картки."}, ensure_ascii=False), encoding="utf-8")
    bs._PRODUCT_TEXTS_FILE = str(mf)
    bs.SITE_AUTO_TEXTS_ENABLED = enabled
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        bs.build()
    return out


def page(out, pid):
    return (out / f"product-{pid}.html").read_text(encoding="utf-8")


o_off = build(False)
chk("прапорець ВИМКНЕНО: на картках нема автотексту (жодного prod-text крім ручного 502)", all('class="prod-text"' not in page(o_off, p) for p in (500, 501, 503)) and 'class="prod-text"' in page(o_off, 502))
o_on = build(True)
p500 = page(o_on, 500)
chk("прапорець УВІМКНЕНО: картка з визначенням має prod-text зі словниковим визначенням і фактами", 'class="prod-text"' in p500 and "Спецтехніка — це моделі машин спеціального призначення." in p500 and "Виробник — Автопром." in p500 and "Країна походження — Китай." in p500)
chk("картка без визначення й без фактів — без prod-text", 'class="prod-text"' not in page(o_on, 501))
p502 = page(o_on, 502)
chk("ручний product_texts.json має ПРІОРИТЕТ: є ручний текст, автотексту нема", "Ручний текст картки." in p502 and "моделі машин спеціального призначення" not in p502)
p503 = page(o_on, 503)
chk("екранування: A<b>&B → A&lt;b&gt;&amp;B, сирого <b> у prod-text нема", "A&lt;b&gt;&amp;B" in p503 and "A<b>&B" not in p503)
bs.SITE_AUTO_TEXTS_ENABLED = False

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
