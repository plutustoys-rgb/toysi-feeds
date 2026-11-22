#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_prom.py — товари RoyalToys у Prom-фіді (22.11.2026, рішення власника «РТ тільки без дублів з Toysi і лише якщо в Toysi цього товару немає в наявності»). Офлайн, синтетичні дані, без мережі/Telegram.
Інваріанти: (1) брама PROM_RT_IN_FEED вимкнена → фід БІТОВО той самий (з хуком і без); (2) Toysi-частина при увімкненій брамі ідентична, RT лише дописуються; (3) у Prom беремо лише UNIQUE/UNIQUE_BRAND — DUP і GRAY ні;
(4) двійник Toysi у наявності (живий каталог) → товар не береться, двійник без залишку → береться; (5) ціна = канонічна decide_price_for_platform, мінімум 60 ₴, залишок ≥3; (6) категорія — лише відома Prom-id (не заглушка 29), калібрування з перевіркою на відкладеній вибірці;
(7) застарілий постачальник/відбір, усічений каталог, збій → RT не додаються (оригінал); (8) ємність: лише вільні слоти; (9) rt- безпечні для prom_catalog_sync/репрайсера; (10) Prom-замовлення з rt- не йде в Toysi і дає позначку в сповіщенні."""
import copy
import json
import os
import random
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
for _k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "PROM_RT_IN_FEED", "PROM_RT_MAX"):
    os.environ.pop(_k, None)
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
sys.path.insert(0, str(Path(__file__).parent))
import competitor_pricing as cp  # noqa: E402
import generate_prom_feed as gp  # noqa: E402
import generate_prom_feed_top as gpt  # noqa: E402
import rt_dup_finder as rd  # noqa: E402
import rt_prom  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
rt_prom.SEL_FILE = tmp / "sel.json"
rt_prom.SUPPLIER_FILE = tmp / "supplier_rt.json"
rt_prom.DUP_FILE = tmp / "rt_dup_map.json"
rt_prom.OVERRIDES_FILE = tmp / "overrides.json"
rt_prom.MIN_CATALOG_N = 10                  # синтетичний каталог малий; гард «усічений каталог» перевіряємо окремо, повертаючи справжню константу

# ───────────────────────── синтетичний каталог Toysi з відомими Prom-категоріями ─────────────────────────
CATS = {   # Toysi-категорія → (Prom id, слова категорії)
    "Тест-Машини": ("2606", ["тачка", "болід", "кабріолет", "вантажівка", "самоскид", "бульдозер", "екскаватор", "мотоцикл", "трактор", "автобус"]),
    "Тест-Лялі": ("2605", ["лялька", "пупс", "принцеса", "русалка", "фея", "мереживо", "сукня", "косичка", "капелюшок", "візочок"]),
    "Тест-Пазли": ("180613", ["пазл", "мозаїка", "головоломка", "лабіринт", "кросворд", "танграм", "ребус", "картина", "елемент", "рамка"]),
    "Тест-Блоки": ("2614", ["конструктор", "цеглинка", "блок", "замок", "деталь", "кубик", "платформа", "модуль", "каркас", "балка"]),
}
MODS = ["яскравий", "чарівний", "веселий", "класичний", "музичний", "святковий", "казковий", "міцний", "легкий", "зручний", "дзвінкий", "добрий", "гарний", "цікавий", "новий"]
VENDORS = ["Альфатой", "Бетатой", "Гаматой"]
rnd = random.Random(7)


def make_name(cat):
    return " ".join(rnd.sample(CATS[cat][1], 3) + rnd.sample(MODS, 1)).capitalize()


toysi, cache = {}, {}
tid = 1000
for cat, (pid, _w) in CATS.items():
    for i in range(130):
        tid += 1
        toysi[str(tid)] = {"id": str(tid), "name": make_name(cat), "vendor": rnd.choice(VENDORS), "stock": 5, "price": "100", "pictures": ["https://toysi.example/p.jpg"], "category_id": cat[-3:], "category_name": cat,
                           "vendor_code": str(tid), "params": [], "description": "x"}
        if i % 10 != 9:                      # 10% без відомої Prom-категорії; ще частина з заглушкою 29
            cache[str(tid)] = {"category_id": int(pid) if i % 17 else 29, "category_caption": "Кап " + pid}
chk("синтетичний каталог: 520 Toysi, мітки Prom-категорій у кеші", len(toysi) == 520 and len(cache) > 400)
lab_stub = rt_prom.toysi_prom_label(next(t for k, t in toysi.items() if cache.get(k, {}).get("category_id") == 29 and t["category_name"] == "Тест-Машини"), cache)
chk("toysi_prom_label: заглушка 29 у кеші = «невідомо» (None), якщо курована мапа теж нічого не дає", lab_stub is None)
chk("toysi_prom_label: курована мапа за назвою категорії Toysi (Ляльки → 2605) без кешу", rt_prom.toysi_prom_label({"id": "1", "name": "x", "category_name": "Ляльки"}, {})[0] == "2605")
chk("toysi_prom_label: виключена категорія (велосипеди) → EXCL", rt_prom.toysi_prom_label({"id": "1", "name": "x", "category_name": "Велосипеди"}, {})[0] == "EXCL")

# ───────────────────────── RT-товари ─────────────────────────
DESC = "Чудова іграшка для дітей, розвиває дрібну моторику та уяву. Безпечні матеріали, яскраві кольори."


def rt_item(name, cat="RT-кат", vendor="РТвендор", cost=120, stock=6, **kw):
    base = {"name": name, "description": DESC, "vendor": vendor, "vendor_code": "RTX-1", "category": cat, "images": ["https://plutustoys.com.ua/img/rt/rt-1-1.jpg"], "price": str(int(cost * 1.5)), "cost": float(cost), "stock": stock,
            "dims_cm": [20.0, 10.0, 5.0], "weight_kg": 0.5}
    base.update(kw)
    return base


sup = {}
n = 0
for cat in CATS:
    for i in range(14):                          # по 14 унікальних RT на категорію (бренд, якого нема в Toysi → UNIQUE)
        n += 1
        sup[f"rt-{n}"] = rt_item(make_name(cat), cat="RT " + cat)
dup_src = toysi["1001"]
sup["rt-9001"] = rt_item(dup_src["name"], cat="RT Тест-Машини", vendor=dup_src["vendor"], vendor_code="DUP-1")           # точна копія Toysi у наявності → DUP
sup["rt-9002"] = rt_item("Тест оригінальна річ непорівнянна", cat="RT Різне", vendor=dup_src["vendor"], vendor_code="UB-1")   # бренд Toysi, але назва інша → UNIQUE_BRAND
oos_id = "1002"
toysi[oos_id]["stock"] = 0
toysi[oos_id]["name"] = "Тачка болід Зорепад Мерехтлива"      # унікальна назва: інакше випадковий схожий сусід робить RT «GRAY» (артефакт вузького синтетичного словника)
sup["rt-9003"] = rt_item(toysi[oos_id]["name"], cat="RT Тест-Машини", vendor=toysi[oos_id]["vendor"], vendor_code="OOS-1", dup_of=oos_id)   # двійник Toysi БЕЗ залишку → береться
t3 = toysi["1003"]
words = t3["name"].split()
sup["rt-9004"] = rt_item(" ".join(words[:3]) + " ЦІКАВИЙ", cat="RT Тест-Машини", vendor=t3["vendor"], vendor_code="GR-1")        # 3 з 4 слів збігаються → сумнівний двійник (GRAY) або DUP
cls, m = __import__("rt_eva").classify_dups({k[3:]: v for k, v in sup.items()}, toysi, list(sup), rd)
chk("класифікація: rt-9001 → DUP, rt-9002 → UNIQUE_BRAND, rt-9003 (двійник OOS) → не DUP/GRAY, унікальні RT бренду без перетину → UNIQUE",
    cls["rt-9001"] == "DUP" and cls["rt-9002"] == "UNIQUE_BRAND" and cls["rt-9003"] in rt_prom.INCLUDE_CLASSES and cls["rt-1"] == "UNIQUE")
chk("rt-9004 (3 з 4 слів) — GRAY або DUP, у Prom НЕ береться", cls["rt-9004"] in ("GRAY", "DUP") and cls["rt-9004"] not in rt_prom.INCLUDE_CLASSES)

# ───────────────────────── відбір: kNN-калібрування з перевіркою на відкладеній вибірці ─────────────────────────
sel = rt_prom.build_selection(toysi, sup, cache, overrides={})
st = sel["stats"]
cal = st["calibration"]
print("  stats:", json.dumps(st, ensure_ascii=False))
chk("калібрування: пороги знайдені, точність на половині A ≥0.95 і на ВІДКЛАДЕНІЙ половині B ≥0.93, n_b ≥30", cal["thresholds"] is not None and cal["prec_cal_half"] >= 0.95 and cal["prec_holdout"] >= 0.93 and cal["n_holdout"] >= 30)
chk("відбір: DUP і GRAY у вибірку не потрапили; UNIQUE/UNIQUE_BRAND з категорією — так", "rt-9001" not in sel["items"] and "rt-9004" not in sel["items"] and "rt-1" in sel["items"] and "rt-9003" in sel["items"])
right = {sid: CATS[sup[sid]["category"][3:]][0] for sid in sel["items"] if sup[sid]["category"][3:] in CATS}
acc = sum(1 for sid, pid in right.items() if sel["items"][sid]["prom_id"] == pid) / max(1, len(right))
chk(f"категорії Prom для RT збігаються з істиною синтетики ({acc:.3f} ≥ 0.95) і всі id — відомі (не 29)", acc >= 0.95 and all(v["prom_id"] in sel["valid_ids"] and v["prom_id"] != "29" for v in sel["items"].values()))
chk("правила категорій RT побудовано (≥1) і RT-категорія «Різне» без правила → товар без категорії НЕ береться (rt-9002 відсутній або має категорію з вето, але не вгадується)",
    len(sel["rules"]) >= 1 and sel["stats"]["dropped"].get("no_category", 0) >= 1 and "rt-9002" not in sel["items"])
# ручна таблиця: override рятує категорію, null виключає, невідомий/заглушковий id ігнорується
ov = {"RT Різне": {"prom_id": "2606", "prom_name": "Машинки"}, "RT Тест-Блоки": None, "RT Тест-Пазли": {"prom_id": "29"}}
sel_ov = rt_prom.build_selection(toysi, sup, cache, overrides=ov)
chk("override: «RT Різне» → 2606 (src=override); null → усі RT Тест-Блоки виключено; id 29 (заглушка) ігнорується й потрапляє в bad_overrides",
    sel_ov["items"].get("rt-9002", {}).get("prom_id") == "2606" and sel_ov["items"]["rt-9002"]["src"] == "override"
    and not [s for s in sel_ov["items"] if sup[s]["category"] == "RT Тест-Блоки"] and "RT Тест-Пазли" in sel_ov["stats"]["bad_overrides"])
# липка категорія: попередній відбір диктує id, поки він валідний
prev = {"items": {"rt-1": {"prom_id": "2605", "prom_name": "", "toysi_cat": "Тест-Лялі"}}}
sel_st = rt_prom.build_selection(toysi, sup, cache, overrides={}, prev_sel=prev)
chk("липка категорія: rt-1 лишається в 2605 (src=sticky), хоча kNN дав би іншу", sel_st["items"]["rt-1"]["prom_id"] == "2605" and sel_st["items"]["rt-1"]["src"] == "sticky")
# відкладена вибірка не підтверджує → авто-категорій немає: шумні мітки (випадкові Prom-id)
noisy = {k: {"category_id": rnd.choice([2606, 2605, 180613, 2614]), "category_caption": "n"} for k in toysi}
sel_noisy = rt_prom.build_selection(toysi, sup, noisy, overrides={})
chk("шумні мітки Toysi → калібрування не проходить: thresholds=None, причина вказана, авто-категорій 0 (RT без ручної таблиці не беремо)",
    sel_noisy["stats"]["calibration"]["thresholds"] is None and sel_noisy["stats"]["calibration"]["reason"] and sel_noisy["stats"]["in_feed_candidates"] == 0)
sel_few = rt_prom.build_selection(toysi, sup, {}, overrides={})
chk("кеш Prom-категорій порожній (і мапа нічого не дає) → авто-категорій 0, причина «замало навчальних товарів»", sel_few["stats"]["in_feed_candidates"] == 0 and "замало" in sel_few["stats"]["calibration"]["reason"])

# ───────────────────────── емісія ─────────────────────────
valid = set(sel["valid_ids"])
SI = {"prom_id": "2606", "prom_name": "", "toysi_cat": "Тест-Машини", "cls": "UNIQUE", "src": "rule"}
live = {k: dict(v) for k, v in toysi.items()}
f, why = rt_prom.build_offer_fields("rt-1", rt_item("Тестова іграшка Модель 1"), SI, live, {})
canon = cp.decide_price_for_platform(120.0, None, "prom", "Тест-Машини", prom_category_id=2606)["price"]
chk("build_offer_fields: нормальний товар → ціна = decide_price_for_platform(cost,None,'prom',…) (канон), id=vendorCode=rt-1, категорія 2606, ≥1 param", f is not None and f["price"] == canon and f["id"] == f["vendor_code"] == "rt-1" and f["category"] == "2606" and f["params"] and rt_prom.validate_offer(f, valid | {"2606"}) == "")
chk("ціна RT на Prom = cost×1.75 для без-конкурентної гілки (PROM-канон), а не site_price (cost×1.5) із supplier_rt.price", abs(f["price"] - max(120 * cp.NO_COMPETITOR_MULT, cp.decide_price_for_platform(120.0, None, "prom", "Тест-Машини", prom_category_id=2606)["floor"])) < 0.01 and f["price"] != 180)
bad = {"stock": rt_item("Тест А Б В", stock=2), "cost": rt_item("Тест А Б В", cost=10), "no_pictures": rt_item("Тест А Б В", images=[]), "short_desc": rt_item("Тест А Б В", description="коротко"),
       "no_dims": rt_item("Тест А Б В", dims_cm=[0, 0, 0], weight_kg=0), "kv_shadow": rt_item("Тест А Б В", kv_dup_of="kv-5"), "policy": rt_item("Пістолет водяний Тест"), "russian_language": rt_item("Гра (рос) Тест"),
       "supplier_name_leak": rt_item("Тест А Б В", description="Купуйте у RoyalToys найкращі іграшки для дітей, якісний товар і гарна упаковка, подарунок.")}
res = {k: rt_prom.build_offer_fields("rt-9", v, SI, live, {})[1] for k, v in bad.items()}
print("  відсів:", res)
chk("відсіювання: залишок 2<3, cost<20, без фото, короткий опис, без габаритів, kv-«тінь», політика (пістолет), рос-мовна назва, ім'я постачальника в описі",
    res["stock"] == "stock" and res["cost"] == "cost" and res["no_pictures"] == "no_pictures" and res["short_desc"] == "short_desc" and res["no_dims"] == "no_dims" and res["kv_shadow"] == "kv_shadow"
    and res["policy"] == "policy" and res["russian_language"] == "russian_language" and res["supplier_name_leak"] in ("short_desc", "supplier_name_leak"))
cheap_cost = 20.0
dec_cheap = cp.decide_price_for_platform(cheap_cost, None, "prom", "Тест-Машини", prom_category_id=2606)["price"]
chk(f"мінімум ціни: товар із ціною Prom {dec_cheap:.2f} < {rt_prom.MIN_PROM_PRICE:g} ₴ → «cheap» (на 10 ₴ плати за замовлення = {cp.PROM_ORDER_FEE_UAH:g}/{rt_prom.MIN_PROM_PRICE:g} виручки); товар за ≥60 — проходить",
    dec_cheap < rt_prom.MIN_PROM_PRICE and rt_prom.build_offer_fields("rt-9", rt_item("Тест А Б В", cost=cheap_cost), SI, live, {})[1] == "cheap" and cp.PROM_ORDER_FEE_UAH == 10.0)
# двійник Toysi у наявності / без залишку (живий каталог)
r_tw = rt_item("Тест А Б В", dup_of="1001")
chk("двійник Toysi (dup_of) у наявності за живим каталогом → toysi_twin_in_stock; той самий товар, коли двійник OOS (stock=0) → береться",
    rt_prom.build_offer_fields("rt-9", r_tw, SI, live, {})[1] == "toysi_twin_in_stock" and rt_prom.build_offer_fields("rt-9", dict(r_tw, dup_of=oos_id), SI, live, {})[0] is not None)
chk("двійник із rt_dup_map.json (не з supplier_rt) теж враховується; двійник зник із каталогу → береться; нерозбірливий залишок двійника → НЕ беремо",
    rt_prom.build_offer_fields("rt-9", rt_item("Тест А Б В"), SI, live, {"rt-9": "1001"})[1] == "toysi_twin_in_stock" and rt_prom.build_offer_fields("rt-9", rt_item("Тест А Б В"), SI, live, {"rt-9": "999999"})[0] is not None
    and rt_prom.build_offer_fields("rt-9", r_tw, SI, dict(live, **{"1001": dict(live["1001"], stock="abc")}), {})[1] == "toysi_twin_in_stock")
chk("validate_offer: id/категорія 29/невідома/ціна<60 → відмова", rt_prom.validate_offer(dict(f, id="x-1"), valid | {"2606"}) == "id" and rt_prom.validate_offer(dict(f, category="29"), valid | {"29", "2606"}) == "category"
    and rt_prom.validate_offer(dict(f, category="123"), valid) == "category" and rt_prom.validate_offer(dict(f, price=59.99), valid | {"2606"}) == "price/stock")


def base_root(n_toysi=5):
    yml = ET.Element("yml_catalog", date="2026-11-22 10:00")
    shop = ET.SubElement(yml, "shop")
    ET.SubElement(shop, "categories")
    offers = ET.SubElement(shop, "offers")
    for i in range(n_toysi):
        o = ET.SubElement(offers, "offer", id=str(500 + i), available="true")
        ET.SubElement(o, "name_ua").text = f"Toysi товар {i}"
    return yml


def as_text(root):
    return ET.tostring(root, encoding="unicode")


sel_ok = {"built_at": datetime.now().isoformat(timespec="seconds"), "rt_n": 4, "valid_ids": ["2606"],
          "items": {"rt-1": dict(SI), "rt-2": dict(SI, cls="UNIQUE_BRAND"), "rt-3": dict(SI, cls="GRAY"), "rt-4": dict(SI, cls="DUP"), "rt-5": dict(SI)}}
sup_e = {"rt-1": rt_item("Перша іграшка Модель 1", vendor_code="A-1"), "rt-2": rt_item("Друга іграшка Модель 2", cost=200), "rt-3": rt_item("Третя іграшка Модель 3"), "rt-4": rt_item("Четверта іграшка Модель 4"),
         "rt-5": rt_item("П'ята іграшка Модель 5", dup_of="1001")}
root = base_root(5)
toysi_before = as_text(root)
stt = rt_prom.append_rt_offers(root, live, sel_ok, sup_e, total_cap=6000)
ids = {o.get("id"): o for o in root.find("shop").find("offers")}
chk("append: емітовано rt-1 і rt-2 (UNIQUE/UNIQUE_BRAND); rt-3 (GRAY), rt-4 (DUP) — НІ; rt-5 (двійник у наявності) — НІ", stt.get("emitted") == 2 and "rt-1" in ids and "rt-2" in ids and "rt-3" not in ids and "rt-4" not in ids and "rt-5" not in ids and stt.get("skip_toysi_twin_in_stock") == 1)
o1 = ids["rt-1"]
chk("offer: id=vendorCode=rt-1, available=true, price .2f, quantity_in_stock, categoryId, picture, vendor, description/description_ua, name/name_ua (укр.), params, keywords",
    o1.findtext("vendorCode") == "rt-1" and o1.get("available") == "true" and o1.findtext("price") == f"{canon:.2f}" and o1.findtext("quantity_in_stock") == "6" and o1.findtext("categoryId") == "2606" and o1.find("picture") is not None
    and o1.findtext("vendor") == "РТвендор" and len(o1.findtext("description_ua")) >= 30 and o1.findtext("name") == o1.findtext("name_ua") == "Перша іграшка Модель 1" and o1.findall("param") and o1.findtext("keywords_ua"))
chk("Toysi-частина дерева НЕ змінилась: перші 5 offer'ів (текст) ідентичні, RT лише дописані в кінець", as_text(root).startswith(toysi_before[:toysi_before.index("</offers>")]) and list(ids)[:5] == ["500", "501", "502", "503", "504"] and list(ids)[5:] == ["rt-1", "rt-2"])
# ємність
root2 = base_root(5)
sel_many = dict(sel_ok, items={f"rt-{i}": dict(SI) for i in range(1, 6)})
sup_many = {f"rt-{i}": rt_item(f"Іграшка номер {i} Тест", cost=100 + 20 * i) for i in range(1, 6)}
s2 = rt_prom.append_rt_offers(root2, live, sel_many, sup_many, total_cap=7, prev_ids={"rt-1"})
got2 = [o.get("id") for o in root2.find("shop").find("offers")][5:]
chk("ємність: cap 7, Toysi 5 → лише 2 вільні слоти; спершу вже присутній у фіді (rt-1), далі найприбутковіший (rt-5); решта skip_no_free_slot=3", got2 == ["rt-1", "rt-5"] and s2["skip_no_free_slot"] == 3 and s2["free_slots"] == 2)
s3 = rt_prom.append_rt_offers(base_root(8), live, sel_many, sup_many, total_cap=7)
chk("Toysi займають усі слоти (8 ≥ cap 7) → RT 0 (RT ніколи не витісняють Toysi)", s3.get("emitted", 0) == 0 and s3["free_slots"] == 0)
os.environ["PROM_RT_MAX"] = "1"
s4 = rt_prom.append_rt_offers(base_root(5), live, sel_many, sup_many, total_cap=6000)
os.environ.pop("PROM_RT_MAX")
chk("PROM_RT_MAX=1 — додатковий поверх-обмежувач (поетапна розкатка)", s4["emitted"] == 1)

# ───────────────────────── брама, свіжість, збої ─────────────────────────
def write_sup(age_h=0.0, items=None):
    rt_prom.SUPPLIER_FILE.write_text(json.dumps({"updated": (datetime.now().astimezone() - timedelta(hours=age_h)).isoformat(), "items": items or sup_e}), encoding="utf-8")


write_sup()
rt_prom._atomic_write(rt_prom.SEL_FILE, sel_ok)
big = {str(i): {} for i in range(50)}            # «повний» каталог для гарду MIN_CATALOG_N=10; двійників у ньому немає
big.update(live)
r0 = base_root()
chk("брама PROM_RT_IN_FEED вимкнена → maybe_add_rt_offers повертає той самий root (фід не змінюється)", rt_prom.maybe_add_rt_offers(r0, big, {}, 6000) is r0)
os.environ["PROM_RT_IN_FEED"] = "1"
try:
    r1 = base_root()
    out1 = rt_prom.maybe_add_rt_offers(r1, big, {}, 6000)
    chk("брама ввімкнена, дані свіжі → повертається КОПІЯ з RT, оригінал не змінено", out1 is not r1 and len(out1.find("shop").find("offers")) > 5 and len(r1.find("shop").find("offers")) == 5)
    write_sup(age_h=9)
    r2 = base_root()
    chk("supplier_rt.json старший за 6 год → RT не додаємо (root той самий)", rt_prom.maybe_add_rt_offers(r2, big, {}, 6000) is r2 and not rt_prom.supplier_fresh())
    write_sup()
    rt_prom._atomic_write(rt_prom.SEL_FILE, dict(sel_ok, built_at=(datetime.now() - timedelta(hours=40)).isoformat(timespec="seconds")))
    orig_build = rt_prom.build_selection
    rt_prom.build_selection = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    r3 = base_root()
    chk("відбір застарілий (>36 год) і перебудова впала → RT не додаємо (root той самий)", rt_prom.maybe_add_rt_offers(r3, big, {}, 6000) is r3)
    rt_prom.build_selection = orig_build
    rt_prom._atomic_write(rt_prom.SEL_FILE, sel_ok)
    rt_prom.MIN_CATALOG_N = 25_000
    r4 = base_root()
    chk("каталог Toysi усічений (<25 000) → RT не додаємо (інакше «двійника в наявності немає» було б хибним)", rt_prom.maybe_add_rt_offers(r4, big, {}, 6000) is r4)
    rt_prom.MIN_CATALOG_N = 10
    orig_app = rt_prom.append_rt_offers

    def boom(*a, **k):
        a[0].find("shop").find("offers").append(ET.Element("offer", id="rt-bad"))      # частково змінює копію
        raise RuntimeError("boom")
    rt_prom.append_rt_offers = boom
    r5 = base_root()
    out5 = rt_prom.maybe_add_rt_offers(r5, big, {}, 6000)
    chk("збій під час додавання → повертається ОРИГІНАЛ (жодного напівдоданого offer, Toysi цілий)", out5 is r5 and not [o for o in r5.find("shop").find("offers") if o.get("id") == "rt-bad"])
    rt_prom.append_rt_offers = orig_app
finally:
    os.environ.pop("PROM_RT_IN_FEED", None)
    rt_prom.append_rt_offers = orig_app
write_sup()
rt_prom.SUPPLIER_FILE.write_text("{битий json", encoding="utf-8")
chk("supplier_rt.json битий → supplier_fresh False, load_supplier {}", not rt_prom.supplier_fresh() and rt_prom.load_supplier() == {})
write_sup()
# ensure_selection: гарди як у rt_eva
old_sel = {"built_at": (datetime.now() - timedelta(hours=20)).isoformat(timespec="seconds"), "rt_n": 1, "items": {}, "stats": {"in_feed_candidates": 1000}}
rt_prom._atomic_write(rt_prom.SEL_FILE, old_sel)
rt_prom.MIN_CATALOG_N = 25_000
chk("ensure_selection: усічений каталог (<25000) → перебудови немає, лишається свіжий старий відбір", rt_prom.ensure_selection({"1": {}}, {}) == old_sel)
bigcat = {str(i): {} for i in range(25_001)}
try:
    rt_prom.build_selection = lambda ty, sup_, cache_, **k: {"built_at": datetime.now().isoformat(timespec="seconds"), "rt_n": 1, "items": {}, "stats": {"in_feed_candidates": 300}}
    chk("ensure_selection: новий відбір підозріло менший (300 проти 1000, <70%) → відкинуто", rt_prom.ensure_selection(bigcat, {}) == old_sel)
    rt_prom.build_selection = lambda ty, sup_, cache_, **k: {"built_at": datetime.now().isoformat(timespec="seconds"), "rt_n": 1, "items": {}, "stats": {"in_feed_candidates": 900}}
    chk("ensure_selection: нормальний новий відбір (900 проти 1000) приймається й записується", rt_prom.ensure_selection(bigcat, {})["stats"]["in_feed_candidates"] == 900 and rt_prom.load_selection()["stats"]["in_feed_candidates"] == 900)
finally:
    rt_prom.build_selection = orig_build
    rt_prom.MIN_CATALOG_N = 10
chk("selection_fresh: свіжий → True; >STALE_H → False; без дати → False", rt_prom.selection_fresh(sel_ok) and not rt_prom.selection_fresh({"built_at": (datetime.now() - timedelta(hours=rt_prom.STALE_H + 1)).isoformat()}) and not rt_prom.selection_fresh({}))

# ───────────────────────── фід Toysi: бітова незмінність + Toysi-частина ідентична ─────────────────────────
gp.fetch_russian_text = lambda: {}


class _FixedDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 22, 12, 0, 0)


gp.datetime = _FixedDT
mini = {str(100 + i): {"id": str(100 + i), "name": f"Тестовий товар {i}", "vendor": "Альфатой", "stock": 4, "price": "150", "pictures": ["https://toysi.example/p.jpg"], "category_id": "9", "category_name": "Конструктори",
                       "vendor_code": str(100 + i), "params": [("Колір", "Червоний")], "description": "Опис товару для тесту фіду. Безпечні матеріали.", "country": "Україна", "barcode": "123"} for i in range(6)}
fa, fb, fc = tmp / "a.xml", tmp / "b.xml", tmp / "c.xml"
gp.generate_feed(output_file=str(fa), catalog=mini, full_catalog=mini)
gp.generate_feed(output_file=str(fb), catalog=mini, full_catalog=mini, post_build=None)
chk("generate_feed: post_build=None (брама вимкнена) → файл БІТОВО той самий, що без параметра", fa.read_bytes() == fb.read_bytes() and len(fa.read_bytes()) > 1000)
os.environ.pop("PROM_RT_IN_FEED", None)
chk("_rt_post_build: брама вимкнена → None (generate_top_feed викликає generate_feed без хука)", gpt._rt_post_build(mini, {}) is None)
os.environ["PROM_RT_IN_FEED"] = "1"
try:
    hook = gpt._rt_post_build(big, {})
    chk("_rt_post_build: брама ввімкнена → хук", callable(hook))
    rt_prom._atomic_write(rt_prom.SEL_FILE, sel_ok)
    write_sup()
    gp.generate_feed(output_file=str(fc), catalog=mini, full_catalog=mini, post_build=lambda root: rt_prom.maybe_add_rt_offers(root, big, {}, 6000))
finally:
    os.environ.pop("PROM_RT_IN_FEED", None)
ta, tc = fa.read_text(encoding="utf-8"), fc.read_text(encoding="utf-8")
ra, rc = ET.fromstring(ta.split("\n", 1)[1].replace("<![CDATA[", "").replace("]]>", "")), ET.fromstring(tc.split("\n", 1)[1].replace("<![CDATA[", "").replace("]]>", ""))
oa, oc = ra.find("shop").find("offers").findall("offer"), rc.find("shop").find("offers").findall("offer")
chk("з увімкненою бронею: Toysi-offer'и (число, порядок, зміст) ідентичні, RT дописано після них (rt-1, rt-2)", len(oa) == 6 and [ET.tostring(x).strip() for x in oc[:6]] == [ET.tostring(x).strip() for x in oa] and [x.get("id") for x in oc[6:]] == ["rt-1", "rt-2"])
chk("<categories> і решта shop незмінні; description/description_ua RT загорнуті в CDATA як у Toysi", ET.tostring(ra.find("shop").find("categories")) == ET.tostring(rc.find("shop").find("categories")) and tc.count("<![CDATA[") == ta.count("<![CDATA[") + 4)
# збій хука → файл як без хука
fd = tmp / "d.xml"
gp.generate_feed(output_file=str(fd), catalog=mini, full_catalog=mini, post_build=lambda root: (_ for _ in ()).throw(RuntimeError("boom")))
chk("post_build кидає виняток → generate_feed пише Toysi-фід без змін (бітово як без хука)", fd.read_bytes() == fa.read_bytes())

# generate_top_feed: хук підключається лише за брамою; у журнал штовхнутих йдуть лише Toysi-id (rt- туди не потрапляють)
import prom_pushed_ledger  # noqa: E402
cap = {}
orig = (gpt.fetch_toysi_catalog, gpt.select_top_items, gpt.generate_feed, prom_pushed_ledger.record_pushed, gpt.load_fresh_prom_price_overrides, gpt.load_fresh_prom_competitor_prices)
try:
    import prom_competitor_pricer as pcp
    import seo_content_db
    orig_cache, orig_seo = pcp._load_prom_category_cache, seo_content_db.load_approved_prom_overrides
    pcp._load_prom_category_cache = lambda: {}
    seo_content_db.load_approved_prom_overrides = lambda: {}
    gpt.fetch_toysi_catalog = lambda: mini
    gpt.select_top_items = lambda c: dict(c)
    gpt.generate_feed = lambda **kw: cap.update(kw)
    prom_pushed_ledger.record_pushed = lambda ids: cap.__setitem__("ledger", sorted(ids)) or 0
    gpt.load_fresh_prom_price_overrides = lambda: {}
    gpt.load_fresh_prom_competitor_prices = lambda: {}
    gpt.generate_top_feed(str(tmp / "x.xml"))
    chk("generate_top_feed, брама вимкнена: post_build=None; ledger = лише Toysi-id", cap.get("post_build") is None and cap["ledger"] == sorted(mini))
    os.environ["PROM_RT_IN_FEED"] = "1"
    cap.clear()
    gpt.generate_top_feed(str(tmp / "x.xml"))
    chk("generate_top_feed, брама ввімкнена: post_build підключено; ledger усе одно лише Toysi-id (rt- не записуються → prom_catalog_sync їх не чіпає)", callable(cap.get("post_build")) and cap["ledger"] == sorted(mini))
finally:
    os.environ.pop("PROM_RT_IN_FEED", None)
    (gpt.fetch_toysi_catalog, gpt.select_top_items, gpt.generate_feed, prom_pushed_ledger.record_pushed, gpt.load_fresh_prom_price_overrides, gpt.load_fresh_prom_competitor_prices) = orig
    pcp._load_prom_category_cache, seo_content_db.load_approved_prom_overrides = orig_cache, orig_seo
# членство: rt- у поточному фіді не потрапляють у Toysi-membership select_top_items
feed_with_rt = tmp / "feed_with_rt.xml"
feed_with_rt.write_text('<offer id="100" available="true"/><offer id="rt-1" available="true"/>', encoding="utf-8")
gpt.FEED_PATH = feed_with_rt
mem = gpt.load_feed_membership()
chk("load_feed_membership бачить rt-1, але select_top_items перетинає з eligible (Toysi) → rt- не стають «членами» набору Toysi", "rt-1" in mem and ("rt-1" not in (mem & set(mini))))
chk("load_prev_rt_ids: з поточного фіду бере лише rt-id", rt_prom.load_prev_rt_ids(feed_with_rt) == {"rt-1"})

# ───────────────────────── rt- безпечні для prom_catalog_sync / репрайсера ─────────────────────────
try:
    import prom_catalog_sync as pcs
    prom_products = {"rt-1": {"status": "published", "presence": "available", "name": "RT"}, "100": {"status": "published", "presence": "available", "name": "T"}, "777": {"status": "published", "name": "вручну власник"}}
    stale = pcs.find_stale_external_ids(prom_products, desired_ids={"101"}, toysi_ids={"100", "101"})
    chk("prom_catalog_sync.find_stale_external_ids: rt-1 (нема в каталозі Toysi) і ручний 777 НЕ позначаються застарілими; застарілий Toysi-товар 100 — так", stale == ["100"])
    pcs.load_ledger = lambda: {"100", "101"}                       # rt- у журнал не пишуться → кандидатів-«зниклих з Toysi» серед rt- нема
    called = {}
    pcs.fetch_prom_products_by_external_ids = lambda ids: called.setdefault("ids", set(ids)) and ({}, set())
    pcs._save_ledger_checked = lambda c: None
    pcs._load_ledger_checked = lambda: set()
    to_delist, to_prune = pcs.find_stale_via_ledger({"101"}, {"101"} | {"100"}, prom_products)
    chk("prom_catalog_sync.find_stale_via_ledger: кандидати лише з журналу (rt- там нема) → rt-1 не звіряється й не гаситься", "rt-1" not in called.get("ids", set()) and "rt-1" not in to_delist)
except ImportError as e:
    chk(f"prom_catalog_sync імпортується ({e})", False)
import prom_competitor_pricer as pcp2  # noqa: E402
ghost, giveup_stats = pcp2._ghost_check_candidates({}, {"100": {}}, {"rt-1", "100"}, {"rt-1": "t", "100": "t", "rt-2": "t"})[:2]
chk("prom_competitor_pricer._ghost_check_candidates: ітерує лише top_catalog (Toysi) — rt-2 (колись жив, зник) НЕ стає «привидом»", "rt-2" not in ghost and "rt-1" not in ghost)

# ───────────────────────── Prom-замовлення з rt- ─────────────────────────
import orders_watcher as ow  # noqa: E402
import order_router as orr  # noqa: E402
prom_order = {"id": 424242, "status": "pending", "payment_option": {"name": "Пром-оплата"}, "payment_data": {"status": "paid"}, "client_first_name": "Тест", "client_last_name": "Тестович", "phone": "0500000000",
              "delivery_address": "Київ, відділення 5", "delivery_provider_data": {}, "products": [{"sku": "rt-123", "external_id": "rt-123", "name": "RT іграшка", "quantity": "1", "price": "199,00"}]}
conv = ow._convert_prom_order(prom_order)
chk("orders_watcher._convert_prom_order: Prom-замовлення з rt-позицією → toysi_code «rt-123» (sku/external_id = id оффера у фіді)", conv["items"][0]["toysi_code"] == "rt-123")
reached = []
orr._ensure_site_prepaid_receipt = lambda conn, order, tm: True
for fn in ("_check_prom_not_cancelled", "_check_eva_not_cancelled", "_check_rozetka_not_cancelled"):
    setattr(orr, fn, lambda conn, order: True)
orr.fetch_toysi_catalog = lambda: reached.append("fetch") or {}
orr.send_throttled_alert = lambda *a, **k: None
orr.route_order(None, {"internal_order_id": "prom_424242", "platform": "prom", "items": conv["items"]})
chk("order_router.route_order(platform=prom, rt-позиція): повертається ДО звернення до Toysi (каталог не тягнемо, нічого не передаємо)", reached == [])
import orders_db  # noqa: E402
notes = []
import telegram_notify  # noqa: E402
orig_n = telegram_notify.notify_new_order
telegram_notify.notify_new_order = lambda *a, **k: notes.append((a, k))
orig_ow = (ow.init_db, ow.fetch_new_orders_prom, ow.fetch_new_orders_rozetka, ow.fetch_new_orders_eva, ow.get_connection, ow.insert_order)


class _Ctx:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


try:
    ow.init_db = lambda: None
    ow.fetch_new_orders_rozetka = lambda: []
    ow.fetch_new_orders_eva = lambda: []
    ow.get_connection = lambda: _Ctx()
    ow.insert_order = lambda conn, order: True
    ow.fetch_new_orders_prom = lambda: [conv]
    ow.poll_once()
    plain = dict(conv, order_id="1", items=[{"toysi_code": "123456", "name": "Звичайний", "qty": 1, "price": 100.0}])
    ow.fetch_new_orders_prom = lambda: [plain]
    ow.poll_once()
finally:
    telegram_notify.notify_new_order = orig_n
    (ow.init_db, ow.fetch_new_orders_prom, ow.fetch_new_orders_rozetka, ow.fetch_new_orders_eva, ow.get_connection, ow.insert_order) = orig_ow
chk("poll_once: нове Prom-замовлення з rt- → сповіщення «Нове замовлення» з позначкою ручного виконання; звичайне Prom-замовлення — без позначки",
    len(notes) == 2 and "rt-" in notes[0][1].get("note", "") and "ВРУЧНУ" in notes[0][1]["note"] and notes[1][1].get("note", "") == "")
chk("orders_db.get_orders_ready_to_forward виключає rt- позиції на рівні SQL (Prom+RT не потрапляє в авто-форвард)", "NOT LIKE '%\"toysi_code\": \"rt-%'" in Path(orders_db.__file__).read_text(encoding="utf-8"))

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
