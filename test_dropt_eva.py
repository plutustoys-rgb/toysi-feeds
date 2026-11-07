#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dropt_eva.py — товари Dropt у EVA-фіді (07.11.2026). Офлайн. Інваріанти: (1) classify: правила по НАЗВІ → валідний лист EVA, виключення й сумнівні → None (не вгадуємо); (2) extract_size бере розмір товару з назви/опису і
не плутає ємність/роздільність; (3) build_offer_fields: відсів (залишок, ціна, фото, опис, БЕЗ розміру, бренд); (4) append: емісія, унікальні назви, липка категорія, заглушка для зниклих; (5) БРАМА за замовчуванням закрита
(навіть з EVA_DROPT_IN_FEED=1, поки EVA_ORDERS_SUPPORTED=False) і generate_eva_feed._maybe_add_dropt_offers без неї не змінює дерево; (6) supplier_fresh."""
import importlib
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
import generate_eva_feed as ge  # noqa: E402
import rt_eva  # noqa: E402
import dropt_eva as de  # noqa: E402
from forbidden_products import is_forbidden  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
de.LISTED_FILE = tmp / "listed.json"
valid = rt_eva.valid_eva_ids()

# (1) classify
cases = {
    "Дитячий надувний басейн JR8083 305х305х76 см": "118503",
    "Гірлянда штора на вікно 212 LED 3х2м Теплий білий": "112356",
    "Квадрокоптер QY66-X05 Літаючий дрон з WiFi камерою": "105752",
    "М'яка плюшева іграшка Єдиноріг 40 см": "101231",
    "Powerbank – брелок для ключів Power Pod": "119658",
    "Спальний мішок spal-3": "121260",
    "Ігровий трек для кішок 4 рівні з м'ячами": "104012",
    "Інтерактивна іграшка «Пташка» для котів з USB": "104012",
    "Гумова іграшка для собак на присосці М'ячик": "104026",
    "Дитячий шкільний рюкзак для дівчаток з єдинорогом": "106607",
}
bad = {k: (de.classify(k) or (None,))[0] for k, v in cases.items() if (de.classify(k) or (None,))[0] != v}
chk("classify: 10 типових назв → очікуваний лист EVA " + str(bad), not bad)
chk("classify: усі листки правил — валідні id EVA (довідник/мапа)", all((i in valid) for _p, i, _n in de.RULES if i))
nones = ["Тренажер еспандер для плавання банджі Гумка для басейну", "Пляжна підстилка - намет з козирком", "Магніт на телескопічному подовжувачі", "Новорічний нічник Свічка зі сніговиком",
         "Дитячий ігровий стіл-конструктор 10302", "Бездротові навушники i18 TWS", "Портативна Bluetooth колонка PartyBox", "Роутер R103 4G/5G", "Екшн-камера Q7 4K Wi-Fi", "Рюкзак для перенесення домашніх тварин CosmoPet"]
wrong = [n for n in nones if de.classify(n) is not None]
chk("classify: виключення й товари БЕЗ листа EVA (навушники, колонки, роутер, екшн-камера, помилкові влучання) → None " + str(wrong), not wrong)
chk("classify: порожня/None назва → None, не виняток", de.classify("") is None and de.classify(None) is None)

# (2) extract_size
sz = {"Гамак 240х150 см": "240х150 см", "Басейн Ø220 см": "Ø220 см", "Пакет 50×40×125 см": "50×40×125 см", "Шнур довжина 3 м": "довжина 3 м", "Спальник 190*100 см": "190*100 см"}
chk("extract_size: розмір товару з назви (х/×/*, Ø, «довжина N м»)", all(de.extract_size(k) == v for k, v in sz.items()))
chk("extract_size: 4K / 18650 / 20000 мАг / 50 Вт / 100% — НЕ розмір", all(de.extract_size(t) is None for t in ["Колонка 4K 18650 20000 мАг", "Лампа 50 Вт 100%", "Акумулятор 4V 4Ah", "Дрон 1080P WiFi"]))
chk("extract_size: з опису, якщо в назві нема; HTML ігнорується", de.extract_size("Намет", "<p>Розмір: 140х100 см, легкий</p>") == "140х100 см")

# аудит #1117: хибні влучання, виміряні на реальному каталозі, та хибні «розміри»
fp = ["Туристичний намет АВТОМАТ 2-місний", "Пляжний намет", "Зонт-намет рибальський", "Дитяча складна москітна сітка-намет для ліжечка", "Новорічний LED-нічник W890C", "Бездротові навушники i19 TWS з кейсом PowerBank",
      "Бездротовий караоке мікрофон + Powerbank", "Надувний шезлонг DL-486 175×74×29", "Шезлонг розкладний 66×200", "Дитяче плюшеве крісло X-636", "Басейн для купання домашніх тварин YXZ-702", "Дитячий басейн для кульок 1,2 м",
      "Зарядний пристрій LiitoKala Lii-PD4", "Танцювальна плюшева квітка з Bluetooth-колонкою та FM-радіо"]
chk("classify (аудит #1117): 14 реальних хибних влучань тепер без листа " + str([n for n in fp if de.classify(n)]), not [n for n in fp if de.classify(n)])
chk("classify: «Дитячий манеж-намет» → Манежі; «Гірлянда на батарейках» → гірлянди; «Дитячий ігровий намет» → Намети",
    de.classify("Дитячий манеж-намет")[0] == "116256" and de.classify("Гірлянда світлодіодна на батарейках 10 м")[0] == "112356" and de.classify("Дитячий ігровий намет Замок принцеси")[0] == "105920")
chk("extract_size (аудит #1117): роздільність, 2 х 18650, 3x3 Вт, переріз 2х1,5 мм, 5 м/с — НЕ розмір",
    all(de.extract_size(t) is None for t in ["Камера 1920x1080 Full HD", "Планшет 2 х 18650", "Лампа 3x3 Вт", "Кабель 2х1,5 мм", "Дрон 5 м/с", "Приставка 1280*720", "Привід 4х4"]))
chk("extract_size: 3 виміри без одиниці та 2 виміри з одиницею — розмір", de.extract_size("Пакет 50×40×125") == "50×40×125" and de.extract_size("Намет 240х150 см") == "240х150 см" and de.extract_size("Гірлянда 3х2м") == "3х2м")
import time as _t  # noqa: E402
_t0 = _t.time()
de.extract_size(("1x" * 20000) + " 1,2" * 20000 + " довжина " * 5000)
chk("extract_size: патологічний довгий текст — без катастрофічного backtracking (<2 с)", _t.time() - _t0 < 2)

# (3) build_offer_fields
DESC = "Чудовий товар для дому та відпочинку, зручний і міцний. Безпечні матеріали, яскравий дизайн."


def item(**kw):
    b = {"name": "Дитячий надувний басейн Test 305х305х76 см", "description": DESC, "vendor": "TestBrand", "vendor_code": "59298", "category": "Туризм, відпочинок і спорт / Літній відпочинок",
         "images": ["https://dropt.in.ua/image/x-1000x1000.jpg"], "price": 900, "cost": 600, "stock": 3}
    b.update(kw)
    return b


EVA = ("118503", "Басейни")
f, why = de.build_offer_fields("dropt-59298", item(), EVA, ge, is_forbidden)
chk("build_offer_fields: нормальний товар → поля з розміром у параметрах " + str(why), f is not None and f["price"] == 900 and f["category"] == "118503" and f["params"][0][1].startswith("305х305") and de.validate_offer(f, valid, 255) == "")
res = {k: de.build_offer_fields("dropt-9", v, EVA, ge, is_forbidden)[1] for k, v in {
    "stock": item(stock=1), "price": item(price=610), "no_pictures": item(images=[]), "short_desc": item(description="коротко"), "no_vendor": item(vendor=""),
    "no_size": item(name="Дитячий надувний басейн Test")}.items()}
chk("відсів: залишок<2, ціна<cost×1.3, без фото, короткий опис, без бренду, БЕЗ розміру → причини " + str(res), res == {"stock": "stock", "price": "price", "no_pictures": "no_pictures", "short_desc": "short_desc", "no_vendor": "no_vendor", "no_size": "no_size"})
chk("build_offer_fields: id не dropt- → відмова id", de.build_offer_fields("rt-1", item(), EVA, ge, is_forbidden)[1] == "id")
chk("validate_offer: невалідна категорія / id / ціна → відмова", de.validate_offer({**f, "category": "000"}, valid, 255) == "category" and de.validate_offer({**f, "id": "x-1"}, valid, 255) == "id" and de.validate_offer({**f, "price": 0}, valid, 255) == "price/stock")


# (4) append
def base_root():
    yml = ET.Element("yml_catalog")
    shop = ET.SubElement(yml, "shop")
    ET.SubElement(shop, "categories")
    offers = ET.SubElement(shop, "offers")
    o = ET.SubElement(offers, "offer", id="100", available="true")
    ET.SubElement(o, "name_ua").text = "Дитячий надувний басейн Test 305х305х76 см"      # збігається з назвою Dropt → артикул
    return yml


sup = {"dropt-59298": item(), "dropt-2": item(name="Гамак підвісний Test 240х150 см", vendor_code="2"), "dropt-3": item(name="Басейн без розміру", vendor_code="3"),
       "dropt-4": item(name="Бездротові навушники i18 TWS 10 см", vendor_code="4")}
root = base_root()
st = de.append_dropt_offers(root, ge, sup)
offers = {o.get("id"): o for o in root.find("shop").find("offers").findall("offer")}
chk("append: емітовано лише dropt-59298 (є правило+розмір); dropt-2 (нема правила), dropt-3 (нема розміру), dropt-4 (навушники — нема листа) — ні " + str(st), st.get("emitted") == 1 and set(offers) == {"100", "dropt-59298"})
chk("append: назва збіглася з іншим offer'ом → дописано артикул; категорія задекларована", offers["dropt-59298"].findtext("name_ua").endswith("(59298)") and root.find("shop").find("categories").find("category[@id='118503']") is not None)
chk("append: статистика відсіву видима (skip_no_rule/skip_no_size) " + str(st), st.get("skip_no_rule") == 2 and st.get("skip_no_size") == 1)
# заглушка для зниклих (listed-файл збережено попереднім append)
root3 = base_root()
st3 = de.append_dropt_offers(root3, ge, {"dropt-59298": item(stock=1)})            # зник (залишок 1) → заглушка available=false, та сама категорія
o3 = {o.get("id"): o for o in root3.find("shop").find("offers").findall("offer")}
chk("зниклий листований товар → заглушка available=false, stock 0, та сама категорія " + str(st3), st3.get("deactivated") == 1 and o3["dropt-59298"].get("available") == "false" and o3["dropt-59298"].findtext("categoryId") == "118503" and o3["dropt-59298"].findtext("stock_quantity") == "0")

chk("append: порожній файл постачальника не деактивує листовані (захист)", de.append_dropt_offers(base_root(), ge, {}) == {"skip_empty_supplier": 1})

# (5) брама
os.environ.pop("EVA_DROPT_IN_FEED", None)
chk("брама: без EVA_DROPT_IN_FEED закрита", de.gate_on() is False)
os.environ["EVA_DROPT_IN_FEED"] = "1"
chk("брама: навіть з EVA_DROPT_IN_FEED=1 закрита, доки EVA_ORDERS_SUPPORTED=False (EVA-замовлення Dropt ще не виконуються)", de.EVA_ORDERS_SUPPORTED is False and de.gate_on() is False)
r0 = base_root()
chk("generate_eva_feed._maybe_add_dropt_offers: брама закрита → те саме дерево, жодних змін", ge._maybe_add_dropt_offers(r0, {}) is r0 and len(r0.find("shop").find("offers")) == 1)
de.EVA_ORDERS_SUPPORTED = True
de.supplier_fresh = lambda *a, **k: True
de.load_supplier = lambda: {"dropt-59298": item(name="Унікальний басейн Dropt 305х305х76 см")}
de.LISTED_FILE = tmp / "listed2.json"
r1 = base_root()
out = ge._maybe_add_dropt_offers(r1, {})
ids = [o.get("id") for o in out.find("shop").find("offers").findall("offer")]
chk("хук з відкритою брамою: працює на КОПІЇ (оригінал не змінено), Dropt-offer додано " + str(ids), out is not r1 and ids == ["100", "dropt-59298"] and len(r1.find("shop").find("offers")) == 1)
de.supplier_fresh = lambda *a, **k: False
r2 = base_root()
chk("хук: файл постачальника застарів → Dropt не додано, дерево без змін", ge._maybe_add_dropt_offers(r2, {}) is r2)
de.supplier_fresh = lambda *a, **k: True


def boom(*a, **k):
    raise RuntimeError("boom")


de.append_dropt_offers = boom
r3 = base_root()
chk("хук: збій модуля не ламає фід — повертає початкове дерево", ge._maybe_add_dropt_offers(r3, {}) is r3)
os.environ.pop("EVA_DROPT_IN_FEED", None)

# (6) supplier_fresh
importlib.reload(de)          # скинути підміни
fresh = tmp / "s.json"
de.supplier_path = lambda: fresh
fresh.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(), "items": {}}), encoding="utf-8")
chk("supplier_fresh: свіжий файл → True", de.supplier_fresh() is True)
fresh.write_text(json.dumps({"updated": (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat(), "items": {}}), encoding="utf-8")
chk("supplier_fresh: файл старший за 3 год → False", de.supplier_fresh() is False)
fresh.write_text("{не json", encoding="utf-8")
chk("supplier_fresh: битий файл → False", de.supplier_fresh() is False)
chk("після reload брама знову закрита за замовчуванням", de.EVA_ORDERS_SUPPORTED is False)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
