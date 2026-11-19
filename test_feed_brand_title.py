#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_feed_brand_title.py — марка виробника в g:title фідів Google/Meta/Bing (SEO 19.11.2026) + GS1-перевірка GTIN.
Інваріанти: (1) vendor з include → «Марка Назва», вже є в назві (без регістру/пробілів) → без дубля; (2) vendor не з include, порожній, MIC/Bambi → без марки;
(3) ліцензійна марка (збіг з ліцензійним переліком) без білого списку — НЕ додається, з білим — додається; чорний список — ніколи;
(4) FEED_BRAND_IN_TITLE=0 вимикає; нема файла переліку → без змін; (5) ru-фід бере те саме рішення (title_brand);
(6) is_valid_gtin: хибна контрольна цифра відхиляється, правильна проходить; (7) google_rt_feed додає марку так само і НЕ змінює хеш перекладів RT; (8) типовий режим: гейт ліцензій вимкнено → ліцензійна марка додається. `python test_feed_brand_title.py` → exit 0/1."""
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "site"))
import feed_brand_title as fb  # noqa: E402
import generate_google_feed as gg  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


os.environ.pop("FEED_BRAND_IN_TITLE", None)
inc, exc = fb._load()
chk("перелік завантажено: 78 include, MIC/Bambi в exclude, їх нема в include", len(inc) == 78 and {"MIC", "Bambi"} <= exc and not ({"MIC", "Bambi", "MiC"} & inc))

B = lambda v, **kw: fb.brand_for_title(v, vendor_key=gg.vendor_key, **kw)
chk("vendor з include → марка", B("Strateg") == "Strateg" and B("Ideyka") == "Ideyka")
chk("точний збіг із регістром: «strateg» ≠ «Strateg» → без марки", B("strateg") == "")
chk("рідкісний vendor, не з include → без марки", B("Незнайомий Виробник") == "")
chk("порожній/None vendor → без марки", B("") == "" and B(None) == "")
chk("MIC/Bambi/МІС не додаються", B("MIC") == "" and B("Bambi") == "" and B("МІС") == "")

chk("«Назва» → «Марка Назва»", fb.with_brand("Маленький дослідник", "Strateg") == "Strateg Маленький дослідник")
chk("марка вже в назві (інший регістр) → без дубля", fb.with_brand("Набір STRATEG Дослідник", "Strateg") == "Набір STRATEG Дослідник")
chk("«Danko toys» ≈ «Dankotoys» у назві → без дубля", fb.with_brand("Dankotoys Конструктор", "Danko toys") == "Dankotoys Конструктор")
chk("порожня марка/порожня назва → без змін", fb.with_brand("Назва", "") == "Назва" and fb.with_brand("", "Strateg") == "")

# ліцензійні фільтри
rx = re.compile(r"\bhasbro\b", re.I)
chk("гейт УВІМКНЕНО: марка Hasbro збігається з переліком, vendor НЕ в білому списку → НЕ додається", B("Hasbro", licensed_rx=rx, licensed_gate=True, licensed_wl=frozenset()) == "")
chk("гейт УВІМКНЕНО: той самий vendor у білому списку → додається", B("Hasbro", licensed_rx=rx, licensed_gate=True, licensed_wl=frozenset({gg.vendor_key("Hasbro")})) == "Hasbro")
chk("гейт ВИМКНЕНО (типово, рішення власника 30.10): ліцензійна марка додається, як і сам товар у фіді (аудит #1750, дефект 2)", B("Hasbro", licensed_rx=rx, licensed_gate=False, licensed_wl=frozenset()) == "Hasbro")
chk("vendor у чорному списку → ніколи, ні при гейті, ні без", B("Hasbro", licensed_rx=rx, licensed_gate=True, licensed_wl=frozenset({"hasbro"}), licensed_bl=frozenset({"hasbro"})) == ""
    and B("Hasbro", licensed_rx=rx, licensed_gate=False, licensed_bl=frozenset({"hasbro"})) == "")
chk("марка, якої нема в ліцензійному переліку → додається й при гейті без білого списку", B("Strateg", licensed_rx=rx, licensed_gate=True, licensed_wl=frozenset()) == "Strateg")

# вимкнення і відсутній файл
os.environ["FEED_BRAND_IN_TITLE"] = "0"
chk("FEED_BRAND_IN_TITLE=0 вимикає", B("Strateg") == "")
os.environ.pop("FEED_BRAND_IN_TITLE")
_orig = fb.BRANDS_FILE
fb._load.cache_clear()
fb.BRANDS_FILE = Path(__file__).parent / "_no_such_brands_file.json"
chk("нема файла переліку → без марки, без винятку", B("Strateg") == "")
fb.BRANDS_FILE = _orig
fb._load.cache_clear()
chk("файл відновлено → знову працює", B("Strateg") == "Strateg")

# build_feed_items: інтеграція
def item(pid, name, vendor):
    return {"id": pid, "name": name, "vendor": vendor, "price": 200.0, "stock": 5, "category_name": "Машинки", "category_id": "1",
            "pictures": ["https://toysi.ua/p/enl_%s.jpg" % pid], "description": "Опис товару достатньої довжини для фіда.", "barcode": ""}


cat = {"7001": item("7001", "Маленький дослідник", "Strateg"),
       "7002": item("7002", "Машинка металева", "Невідомий"),
       "7003": item("7003", "Strateg Конструктор", "Strateg")}
items, st = gg.build_feed_items(cat, {}, {}, {}, None, None)
by = {i["pid"]: i for i in items}
chk("у build_feed_items: g:title = «Strateg Маленький дослідник», g:brand без змін", by["7001"]["title"] == "Strateg Маленький дослідник" and by["7001"]["brand"] == "Strateg")
chk("vendor не з include → назва без змін", by["7002"]["title"] == "Машинка металева" and by["7002"]["title_brand"] == "")
chk("марка вже в назві → без дубля", by["7003"]["title"] == "Strateg Конструктор")
chk("title_brand — службове, у XML не пишеться", "title_brand" not in gg.ET.tostring(gg._build_xml(items), encoding="unicode"))

ru, _ = gg.build_ru_items(items, {"7001": {"name": "Маленький исследователь", "description": "Описание"}})
chk("ru-фід: те саме рішення про марку («Strateg Маленький исследователь»)", ru and ru[0]["title"] == "Strateg Маленький исследователь")

# довгі назви: ліміт 150 лишається
long_name = "Дуже довга назва товару " * 12
it_long, _ = gg.build_feed_items({"7009": item("7009", long_name, "Strateg")}, {}, {}, {}, None, None)
chk("назва з маркою не перевищує 150 символів", it_long and len(it_long[0]["title"]) <= 150 and it_long[0]["title"].startswith("Strateg "))

# build_feed_items у ТИПОВОМУ режимі (гейт ліцензій вимкнено, чорний список увімкнено): Funko з include додається (аудит #1750, дефект 2)
os.environ.pop("FEED_EXCLUDE_LICENSED", None)
cat_f = {"7101": item("7101", "Фігурка колекційна", "Funko")}
itf, _ = gg.build_feed_items(cat_f, {}, {}, {}, None, None)
chk("типовий режим: vendor Funko (ліцензійна марка) → назва «Funko Фігурка колекційна»", itf and itf[0]["title"] == "Funko Фігурка колекційна")

# RT/KV: google_rt_feed додає марку так само; хеш перекладу RT від назви БЕЗ марки (аудит #1750, дефект 1)
import google_rt_feed as g  # noqa: E402
GOOD = "Набір для розвитку дрібної моторики та уяви: деталі яскраві, міцні, добре тримаються разом."
def rt_entry(pid, slug, name):
    return {"id": pid, "u": slug, "n": name, "pr": 300, "p": f"https://plutustoys.com.ua/img/p/{slug}-1.jpg", "c": "Конструктори", "s": 1, "rt": 1, "ix": 1}
def rt_sup(vendor):
    return {"vendor": vendor, "cost": 120, "description": GOOD, "category": "Конструктори", "stock": 5}
rt_on, _ = g.build_items(gg, entries={"rt-1": rt_entry("rt-1", "y1", "Конструктор «Місто» 120 деталей")}, rt_sup={"rt-1": rt_sup("Strateg")})
rt_off, _ = g.build_items(gg, entries={"rt-1": rt_entry("rt-1", "y1", "Конструктор «Місто» 120 деталей")}, rt_sup={"rt-1": rt_sup("Невідомий")})
chk("google_rt_feed: vendor з include → «Strateg Конструктор «Місто» 120 деталей»", rt_on and rt_on[0]["title"] == "Strateg Конструктор «Місто» 120 деталей" and rt_on[0]["title_brand"] == "Strateg")
chk("google_rt_feed: vendor не з include → назва без змін, title_brand порожній", rt_off and rt_off[0]["title"] == "Конструктор «Місто» 120 деталей" and rt_off[0]["title_brand"] == "")
chk("хеш перекладу RT однаковий з маркою й без: марка не інвалідує збережені переклади", rt_on and rt_off and g.src_hash(*g.ru_source(rt_on[0])) == g.src_hash(*g.ru_source(rt_off[0])))
chk("перекладачу йде назва БЕЗ марки (export/ru_source)", rt_on and g.ru_source(rt_on[0])[0] == "Конструктор «Місто» 120 деталей")
_saved_ru = {"rt-1": {"src": g.src_hash(*g.ru_source(rt_off[0])), "name": "Конструктор Город 120 деталей", "description": "Набор для развития мелкой моторики и воображения, детали яркие и прочные."}}
_orig_ru = g._load_ru_file
g._load_ru_file = lambda path=None: _saved_ru
mr, mst = g.merge_ru_texts({}, rt_on)
g._load_ru_file = _orig_ru
chk("збережений переклад (збережений ДО марки) лишається актуальним, не stale, після додавання марки", mst.get("used") == 1 and mst.get("stale") == 0 and "rt-1" in mr)
ru_rt, _ = gg.build_ru_items(rt_on, mr)
chk("ru-фід RT: «Strateg Конструктор Город 120 деталей» — марка додається в ru-назву", ru_rt and ru_rt[0]["title"] == "Strateg Конструктор Город 120 деталей")

# лоадер переліку: BOM і необ'єктний JSON не валять фід
import tempfile  # noqa: E402
_tmp = Path(tempfile.mkdtemp())
(_tmp / "bom.json").write_bytes(b"\xef\xbb\xbf" + '{"include": ["Strateg"], "exclude": []}'.encode("utf-8"))
(_tmp / "arr.json").write_text("[]", encoding="utf-8")
_orig = fb.BRANDS_FILE
fb._load.cache_clear(); fb.BRANDS_FILE = _tmp / "bom.json"
chk("файл переліку з BOM читається", fb._load()[0] == frozenset({"Strateg"}))
fb._load.cache_clear(); fb.BRANDS_FILE = _tmp / "arr.json"
chk("JSON не-об'єкт → порожній перелік без винятку", fb._load() == (frozenset(), frozenset()))
fb.BRANDS_FILE = _orig; fb._load.cache_clear()

# GTIN
chk("GTIN-13 із правильною контрольною цифрою проходить (4006381333931)", gg.is_valid_gtin("4006381333931") is True)
chk("той самий із хибною контрольною цифрою (…932) відхиляється", gg.is_valid_gtin("4006381333932") is False)
chk("GTIN-8 правильний (96385074) проходить, хибний (96385075) — ні", gg.is_valid_gtin("96385074") is True and gg.is_valid_gtin("96385075") is False)
chk("не цифри / не та довжина / порожнє → False", gg.is_valid_gtin("40063813339ab") is False and gg.is_valid_gtin("12345") is False and gg.is_valid_gtin("") is False and gg.is_valid_gtin(None) is False)
chk("реальний kv-штрихкод зі зразка (6900001191681) проходить", gg.is_valid_gtin("6900001191681") is True)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
