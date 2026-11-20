#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_feed_product_size.py — Google-фід: g:size для м'яких іграшок, лише дослівно з назви/опису (SEO 20.11.2026; feed_product_size.py).
ІНВАРІАНТИ: лише м'які іграшки; з назви — рівно один різний розмір; з опису — лише з міткою розміру САМОЇ ІГРАШКИ («в упаковці» ніколи); різні значення → нічого
(порядок чисел не важливий); поза м'якими — нічого; FEED_SOFT_TOY_SIZE=0 вимикає; у XML Google (укр і рос, RT/KV) тег g:size, Meta/Bing — без нього.
`python test_feed_product_size.py` → exit 0/1. Без мережі.
"""
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ.pop("FEED_SOFT_TOY_SIZE", None)
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "site"))

import feed_product_size as fp  # noqa: E402
import generate_google_feed as gg  # noqa: E402
import generate_meta_feed as gm  # noqa: E402
import generate_bing_feed as gb  # noqa: E402

F = []
NS = {"g": "http://base.google.com/ns/1.0"}


def chk(label, ok):
    print(("[OK ] " if ok else "[FAIL] ") + label)
    if not ok:
        F.append(label)


e = fp.extract
# ── назва ──
chk("«… 24 см» → 24 см", e('Мʼяка іграшка "Ведмедик в костюмі котика", 24 см') == "24 см")
chk("«36 х 20 см» (кирилична х) → 36 x 20 см", e("Мʼяка іграшка Змія Сільва, 36 х 20 см") == "36 x 20 см")
chk("«(9 cm)» латиницею → 9 см", e("М'яка іграшка на кліпсі Squishmallows (9 cm)") == "9 см")
chk("діапазон «20-25 см» → 20-25 см", e("М'яка іграшка Зайка 20-25 см") == "20-25 см")
chk("десяткова кома «7,5 см» лишається", e("М'яка іграшка Мишка 7,5 см") == "7,5 см")
chk("«40см» без пробілу", e("Копиця М'яка іграшка Омар (помаранчевий) 40см") == "40 см")
chk("апостроф-гравіс «М`яка» теж м'яка іграшка", e("М`яка іграшка Ведмедик (35 см.)") == "35 см")
chk("тривимірний «18х21х9 см» (латинська x/кирилична х) → 18 x 21 x 9 см", e("М'яка іграшка Цуценя 18х21х9 см") == "18 x 21 x 9 см")
chk("у назві 2 різні розміри («60 см (за стандартом - 85 см)») → нічого, навіть коли в описі є мітка",
    e("М'яка іграшка Ведмедик Teddy, 60 см (за стандартом - 85 см)", "Розмір іграшки: 60 см") is None)
chk("той самий розмір двічі в назві («24 см … 24 см») → один",
    e("М'яка іграшка Зайка 24 см (24 см)") == "24 см")
# ── опис ──
chk("опис: «Розмір іграшки - 26 см» → 26 см", e("М'яка іграшка Півник", "Опис. Розмір іграшки - 26 см. Приємна на дотик.") == "26 см")
chk("опис: «Розмір, зазначений у назві: 40 см» (назва без числа) → 40 см", e("М'яка іграшка Омар", "Розмір, зазначений у назві: 40 см.") == "40 см")
chk("опис: «Габарити без упаковки: 43 x 45 x 12 см» → 43 x 45 x 12 см",
    e("М'яка іграшка Лис", "Габарити в упаковці: 40 x 35 x 45 см Габарити без упаковки: 43 x 45 x 12 см") == "43 x 45 x 12 см")
chk("опис: ЛИШЕ «в упаковці» (габарити коробки) → нічого", e("М'яка іграшка Лис", "Габарити в упаковці: 40 x 35 x 45 см") is None)
chk("опис: різні значення з міткою → нічого", e("М'яка іграшка Лис", "Розмір іграшки: 20 см. Висота іграшки: 30 см.") is None)
chk("опис: те саме в іншому порядку (30х18х13 і 13 x 30 x 18) — НЕ суперечність",
    e("М'яка іграшка Кіт", "Розмір іграшки 30х18х13 см. Без упаковки: 13 x 30 x 18 см") == "30 x 18 x 13 см")
chk("опис: число без мітки («іграшка має 20 см хвіст») → нічого", e("М'яка іграшка Кіт", "Іграшка має хвіст 20 см і м'яке тіло.") is None)
chk("назва має розмір → опис не потрібен і не перебиває", e("М'яка іграшка Кіт 30 см", "Розмір іграшки: 99 см") == "30 см")
# ── не м'які / вимкнення ──
chk("не м'яка іграшка (конструктор 30 см) → нічого", e("Конструктор Місто 30 см", "Розмір іграшки: 30 см", "Конструктори") is None)
chk("категорія «М'які іграшки» робить товар м'яким, навіть коли в назві нема слів", e("Ведмедик Тедді 45 см", "", "М'які іграшки") == "45 см")
chk("порожні/None → нічого, без винятку", e(None) is None and e("", None, None) is None)
chk("російська: «Мягкая игрушка Мишка 30 см» → 30 см", e("Мягкая игрушка Мишка 30 см") == "30 см")
chk("російська: «Размер игрушки: 25 см» в описі", e("Мягкая игрушка Мишка", "Размер игрушки: 25 см.") == "25 см")
os.environ["FEED_SOFT_TOY_SIZE"] = "0"
chk("FEED_SOFT_TOY_SIZE=0 → нічого", e("М'яка іграшка Кіт 30 см") is None)
os.environ.pop("FEED_SOFT_TOY_SIZE", None)


# ── XML ──
def xitem(i, title, desc="Опис.", cat="М'які іграшки"):
    return {"pid": i, "id": i, "title": title, "description": desc, "category_name": cat, "link": f"https://plutustoys.com.ua/p/{i}", "image_link": "https://toysi.ua/p/1.jpg",
            "price": "100.00 UAH", "availability": "in_stock", "condition": "new", "brand": "Test", "gtin": None, "google_product_category": "1259"}


items = [xitem("1", 'М\'яка іграшка "Ведмедик", 24 см'), xitem("2", "М'яка іграшка Кіт", "Габарити в упаковці: 40 x 35 x 45 см"),
         xitem("3", "Конструктор 30 см", cat="Конструктори")]
root = gg._build_xml(items)
sz = {it.find("g:id", NS).text: (it.find("g:size", NS).text if it.find("g:size", NS) is not None else None) for it in root.find("channel").findall("item")}
chk("Google XML: м'яка з розміром → g:size=24 см; без певного розміру → тегу нема; не м'яка → нема", sz == {"1": "24 см", "2": None, "3": None})
ru, _ = gg.build_ru_items([dict(items[0], title_brand="")], {"1": {"name": "Мягкая игрушка Мишка 30 см", "description": "Описание"}})
rroot = gg._build_xml(ru)
chk("ru-фід: розмір із російської назви (RT/KV і Toysi проходять той самий _build_xml)", rroot.find("channel/item/g:size", NS) is not None and rroot.find("channel/item/g:size", NS).text == "30 см")
chk("Meta/Bing _build_xml не торкаємось: у їхньому коді нема feed_product_size",
    "feed_product_size" not in Path(gm.__file__).read_text(encoding="utf-8") and "feed_product_size" not in Path(gb.__file__).read_text(encoding="utf-8"))

print()
if F:
    print(f"FAIL: {len(F)}")
sys.exit(1 if F else 0)
