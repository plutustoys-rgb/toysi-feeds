#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_google_category_map.py — відповідність категорій Toysi → Google (19.10.2026, запит SEO п.4, рішення власника «робіть категорії гугл») і product_type «Головна > Категорія».
Інваріанти: (1) однозначні категорії без ключових слів («Картини за номерами», «Ляльки + лялькові набори»…) отримують точний ID; (2) ключові правила мають пріоритет (не зламано); (3) двозначні/небезпечні категорії (Тварини і птахи, Брелоки, Рюкзаки, Сумочки та гаманці, Маскарадні костюми, Книги…) ЛИШАЮТЬСЯ на запасній 1239;
(4) апострофи ’/ʼ нормалізуються («Дерев’яні вкладиші»); (5) усі ID відповідної мапи — валідні в таксономії Google і НЕ з Apparel/Luggage/Media/Sporting (вимагають gender/age_group/ISBN); (6) product_type = «Головна > <категорія>» у XML і відсутній, якщо категорії нема; (7) build_feed_items проводить category_name у XML.
`python test_google_category_map.py` → exit 0/1."""
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "site"))
import generate_google_feed as gg  # noqa: E402
import verify_google_category_ids as vg  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


G = gg.google_product_category
chk("«Картини за номерами» → 4778; «Ляльки + лялькові набори» → 1255; «Набори для навчання» → 1262; «Ведмеді» → 1259", G("Картини за номерами") == "4778" and G("Ляльки + лялькові набори") == "1255" and G("Набори для навчання") == "1262" and G("Ведмеді") == "1259")
chk("регістр і пробіли не заважають", G("  БРЯЗКАЛЬЦЯ ") == "1244" and G("Залізниці і треки") == "5152")
chk("апострофи: «Дерев’яні вкладиші» (U+2019) і «Дерев'яні вкладиші» → 6725", G("Дерев’яні вкладиші") == "6725" and G("Дерев'яні вкладиші") == "6725" and G("Деревʼяні вкладиші") == "6725")
chk("пріоритет ключових правил: «Пазли» → 3867, «Конструктори» → 3805, «Машинки» → 3551 (не зламано мапою)", G("Пазли") == "3867" and G("Конструктори") == "3805" and G("Машинки металеві") == "3551")
for n in ("Тварини і птахи", "Розважальні", "Брелоки", "Рюкзаки", "Сумочки та гаманці", "Маскарадні костюми", "Прикраси", "Книги для малят", "Енциклопедії", "Подушки", "Круги і жилети", "Скейти і пеніборди", "Невідома категорія", ""):
    chk(f"двозначна/небезпечна або невідома «{n}» → запасна 1239", G(n) == gg.GOOGLE_CATEGORY_FALLBACK)
tax = vg.load_taxonomy()
ids = set(gg._CATEGORY_NAME_MAP.values())
chk("усі ID мапи валідні в таксономії Google", all(i in tax for i in ids))
chk("жоден ID мапи не з Apparel/Luggage/Media/Sporting Goods/Mature (вимагають інших атрибутів)", not any(tax[i].startswith(("Apparel", "Luggage", "Media", "Sporting", "Mature")) for i in ids))
chk("ключі мапи — нижній регістр із прямим апострофом (інакше не збігаються з нормалізацією)", all(k == k.lower() and "’" not in k and "ʼ" not in k for k in gg._CATEGORY_NAME_MAP))


def item(pid, cat):
    return {"id": pid, "name": f"Товар {pid}", "vendor": "Test", "price": 200.0, "stock": 5, "category_name": cat, "category_id": "1",
            "pictures": ["https://toysi.ua/p/enl_%s.jpg" % pid], "description": "Опис товару достатньої довжини для фіда.", "barcode": ""}


items, _ = gg.build_feed_items({"1": item("1", "Картини за номерами"), "2": item("2", "Тварини і птахи")}, {}, {}, {}, None, None)
by = {i["pid"]: i for i in items}
chk("build_feed_items: google_product_category за мапою (4778) і запасна (1239) для двозначної", by["1"]["google_product_category"] == "4778" and by["2"]["google_product_category"] == "1239")
xml = gg._build_xml(items)
NS = "{http://base.google.com/ns/1.0}"
pt = {e.findtext(NS + "id"): e.findtext(NS + "product_type") for e in xml.iter("item")}
chk("XML: product_type = «Головна > Картини за номерами» / «Головна > Тварини і птахи»", pt == {"1": "Головна > Картини за номерами", "2": "Головна > Тварини і птахи"})
no_cat, _ = gg.build_feed_items({"3": item("3", "")}, {}, {}, {}, None, None)
chk("без категорії product_type у XML не пишеться", all(e.find(NS + "product_type") is None for e in gg._build_xml(no_cat).iter("item")))
# --- custom_label_0: цінова смуга (запит SEO 18.10) ---
chk("price_band: межі lt150 / 150_300 / 300_500 / ge500 (149.99 | 150 | 299.99 | 300 | 499.99 | 500)", [gg.price_band(x) for x in (0.0, 149.99, 150.0, 299.99, 300.0, 499.99, 500.0, 9999.0)] == ["lt150", "lt150", "150_300", "150_300", "300_500", "300_500", "ge500", "ge500"])
bands, _ = gg.build_feed_items({"10": {**item("10", "Тварини і птахи"), "price": 200.0}}, {}, {}, {}, None, None)
chk("build_feed_items кладе price_band у позицію; XML має custom_label_0 на КОЖНІЙ позиції", all(i.get("price_band") for i in bands) and all(e.findtext(NS + "custom_label_0") in ("lt150", "150_300", "300_500", "ge500") for e in gg._build_xml(items + bands).iter("item")))
chk("значення custom_label_0 у XML збігається з ціною позиції", all(e.findtext(NS + "custom_label_0") == gg.price_band(float(e.findtext(NS + "price").split()[0])) for e in gg._build_xml(items).iter("item")))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
