#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_feed_adult_exclusion.py — доросла тематика «18+» не йде у фіди Google/Meta/Bing (звіт Merchant Center 15.10: «Restricted adult content» для 235167, 289261…); 6 мм «кульок» (страйкбол) теж поза фідом.
Інваріанти: (1) is_adult_product: «18+» у назві (в дужках, з комою, в кінці) → True; «від 12-18+ міс», «18+ місяців», «XZ-H18 +», «118+», «для дорослих і дітей» → False; (2) build_feed_items виключає такі позиції
й веде лічильник adult_excluded, звичайні лишаються; (3) is_weapon_replica ловить «гумових кульок 6 мм» (раніше пропускав: «кульок» не містить «кульк»). `python test_feed_adult_exclusion.py` → exit 0/1."""
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "site"))

import generate_google_feed as gg  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


for t, exp in {
    'Настільна гра "Крок до ліжка" 18+ (укр)': True, "Чекова книжка бажань: 18+ (укр)": True, 'Настільна гра "Дівочі розмови", укр (18+)': True,
    "Карткова гра 18+, розважальна": True, "Еротична гра для пар": True,
    "Уцінка. Тир батар. XZ-H18 + (36шт / 2) світло": False, "Серветки вологі для дорослих і дітей": False, "Брязкальце від 12-18+ міс": False, "Брязкальце 18+ місяців": False,
    "Конструктор 118+ деталей": False, "Плюшевий ведмідь": False, "": False, None: False,
}.items():
    chk(f"is_adult_product({t!r}) = {exp}", gg.is_adult_product(t) is exp)

chk("is_weapon_replica: «гумових кульок 6 мм» (страйкбол-кульки) → True; «Кульки для дитячої зброї, 6 мм» → True; звичайна кулька без 6 мм → False",
    gg.is_weapon_replica("222960", "Набір гумових кульок 6 мм (1000 шт)") and gg.is_weapon_replica("261750", "Кульки для дитячої зброї, 300 кульок, пластик, 6 мм") and not gg.is_weapon_replica("1", "М'ячик гумовий кулька 6 см"))


def item(pid, name, price=200.0):
    return {"id": pid, "name": name, "vendor": "Test", "price": price, "stock": 5, "category_name": "Розважальні", "category_id": "1",
            "pictures": ["https://toysi.ua/p/enl_%s.jpg" % pid], "description": "Опис товару достатньої довжини для фіда.", "barcode": ""}


cat = {"1": item("1", 'Настільна гра "Крок до ліжка" 18+ (укр)'), "2": item("2", "Плюшевий ведмідь великий"), "3": item("3", "Брязкальце від 12-18+ міс")}
items, st = gg.build_feed_items(cat, {}, {}, {}, None, None)
ids = {i["pid"] for i in items}
chk("build_feed_items: гра 18+ виключена, ведмідь і брязкальце (12-18+ міс) лишились, лічильник adult_excluded=1", ids == {"2", "3"} and st.get("adult_excluded") == 1)
chk("included у статистиці = кількість позицій", st["included"] == len(items))

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
