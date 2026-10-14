#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_google_feed_ru.py — російськомовний фід Google (рішення власника 14.10: «так, для гугла з переходом на наші україномовні сторінки», усі позиції без пілоту).
Інваріанти: (1) та сама множина позицій, що й в українському фіді, мінус ті, де нема російської назви (українську НЕ підставляємо); (2) title/description — російські, link — УКРАЇНСЬКА
сторінка (як в основному), ціна/наявність/бренд/фото/gtin/категорія без змін; (3) у файлі немає собівартості/маржі/внутрішніх полів (pid не пишеться); (4) порожній російський каталог або збій
не пишуть файл і ПРИБИРАЮТЬ застарілий ru-файл (публікація не проштампує старий як свіжий), основний фід не чіпається; (5) XML валідний, запис атомарний (без .tmp).
`python test_google_feed_ru.py` → exit 0/1."""
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import generate_google_feed as gf  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


NS = {"g": "http://base.google.com/ns/1.0"}
items = [
    {"pid": "1", "id": "1", "title": "Конструктор Зоопарк", "description": "Опис українською", "link": "https://plutustoys.com.ua/product-1.html", "image_link": "https://img/1.jpg",
     "price": "250.00 UAH", "availability": "in_stock", "condition": "new", "brand": "Brand A", "gtin": "4006381333931", "google_product_category": "1239"},
    {"pid": "2", "id": "2", "title": "Пазл Місто", "description": "Опис 2", "link": "https://plutustoys.com.ua/product-2.html", "image_link": "https://img/2.jpg",
     "price": "99.00 UAH", "availability": "in_stock", "condition": "new", "brand": "PlutusToys", "gtin": None, "google_product_category": "1239"},
    {"pid": "3", "id": "3", "title": "М'яч", "description": "Опис 3", "link": "https://plutustoys.com.ua/product-3.html", "image_link": "https://img/3.jpg",
     "price": "50.00 UAH", "availability": "in_stock", "condition": "new", "brand": "PlutusToys", "gtin": None, "google_product_category": "1239"},
]
ru = {
    "1": {"name": "Конструктор Зоопарк (рус)", "description": "<p>Описание <b>по-русски</b></p>"},
    "2": {"name": "Пазл Город", "description": ""},          # порожній російський опис → назва
    # "3" відсутній зовсім → позицію пропускаємо
}

out_items, st = gf.build_ru_items(items, ru)
chk("позиція без російської назви пропущена, решта лишилась", [i["id"] for i in out_items] == ["1", "2"] and st["ru_missing_skipped"] == 1)
chk("title російський, description очищений від тегів; порожній опис → назва", out_items[0]["title"] == "Конструктор Зоопарк (рус)" and "Описание" in out_items[0]["description"] and "<" not in out_items[0]["description"] and out_items[1]["description"] == "Пазл Город")
chk("link — українська сторінка, ціна/наявність/бренд/фото/gtin/категорія без змін", all(o["link"] == i["link"] and o["price"] == i["price"] and o["availability"] == i["availability"] and o["brand"] == i["brand"] and o["image_link"] == i["image_link"] and o["gtin"] == i["gtin"] and o["google_product_category"] == i["google_product_category"] for o, i in zip(out_items, items)))
chk("вхідні позиції не мутуються (український фід не псується)", items[0]["title"] == "Конструктор Зоопарк" and items[0]["description"] == "Опис українською")
long_ru = {"1": {"name": "Н" * 400, "description": "Д" * 9000}}
o2, _ = gf.build_ru_items(items[:1], long_ru)
chk("ліміти Google: title ≤150, description ≤5000", len(o2[0]["title"]) == 150 and len(o2[0]["description"]) == 5000)

with tempfile.TemporaryDirectory() as td:
    path = os.path.join(td, "feeds", "google_merchant_feed_ru.xml")
    ok = gf.write_ru_feed(items, path, russian_text=ru)
    chk("write_ru_feed: файл записано, без .tmp", ok and os.path.exists(path) and not os.path.exists(path + ".tmp"))
    root = ET.parse(path).getroot()
    its = root.findall("channel/item")
    chk("XML валідний, 2 позиції, g:id/title/link/price на місці", len(its) == 2 and its[0].find("g:id", NS).text == "1" and its[0].find("title").text == "Конструктор Зоопарк (рус)"
        and its[0].find("link").text.endswith("product-1.html") and its[0].find("g:price", NS).text == "250.00 UAH")
    chk("identifier_exists=no для позиції без gtin, gtin для позиції з gtin", its[1].find("g:identifier_exists", NS).text == "no" and its[0].find("g:gtin", NS).text == "4006381333931")
    raw = open(path, encoding="utf-8").read()
    chk("у файлі нема собівартості/маржі/внутрішніх полів (pid, cost, margin)", not any(w in raw.lower() for w in ("cost", "margin", "<pid", "pid>")))
    chk("опис каналу російською (мова джерела RU)", "Игрушки" in raw)

    # порожній російський каталог: старий файл прибрано, нового нема
    ok = gf.write_ru_feed(items, path, russian_text={})
    chk("порожній російський каталог → False, застарілий ru-файл ПРИБРАНО (публікація не проштампує старий як свіжий)", ok is False and not os.path.exists(path))
    # без жодної позиції з російським текстом
    gf.write_ru_feed(items, path, russian_text=ru)
    ok = gf.write_ru_feed(items, path, russian_text={"999": {"name": "Чужий", "description": ""}})
    chk("російський текст є, але для наших позицій нема → False, файлу нема", ok is False and not os.path.exists(path))
    # збій отримання
    import generate_prom_feed as gp
    orig = gp.fetch_russian_text
    gp.fetch_russian_text = lambda: (_ for _ in ()).throw(RuntimeError("мережа"))
    gf.write_ru_feed(items, path, russian_text=ru)
    ok = gf.write_ru_feed(items, path)
    gp.fetch_russian_text = orig
    chk("збій отримання російського тексту (виняток) → False, без винятку назовні, файлу нема", ok is False and not os.path.exists(path))
    # основний XML будується з тих самих items і не вимагає pid
    root_main = gf._build_xml(items)
    main_raw = ET.tostring(root_main, encoding="unicode")
    chk("основний фід без змін за структурою (немає тегу pid)", "<pid" not in main_raw)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
