#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_kv_eva.py — товари KIDDISVIT у EVA-фіді (18.11.2026). Офлайн, синтетичні дані. Інваріанти:
(1) брама EVA_KV_IN_FEED вимкнена за замовчуванням; (2) «Уцінка» kvu- у EVA не йде; (3) унікальний (нема двійника) → емітується; GRAY → ні;
(4) двійник Toysi/RT у наявності → kv НЕ емітується; двійника в наявності нема → емітується; невідомий двійник (клас DUP без dup_of) → ні;
(5) двійник повернувся → колись листована kv-картка стає available=false (не видаляється), категорія липка; (6) зіпсована мапа RT↔kv → нових не додаємо;
(7) існуючі offer'и дерева (Toysi/RT) не змінюються — kv лише додається; (8) rt_eva.build_selection без нових параметрів поводиться як раніше."""
import inspect
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ.pop("EVA_KV_IN_FEED", None)
sys.path.insert(0, str(Path(__file__).parent))
import generate_eva_feed as ge  # noqa: E402
import rt_eva  # noqa: E402
import kv_eva  # noqa: E402
from forbidden_products import is_forbidden  # noqa: E402,F401

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
kv_eva.LISTED_FILE = tmp / "listed.json"
kv_eva.SEL_FILE = tmp / "sel.json"
kv_eva.SUPPLIER_FILE = tmp / "supplier_kv.json"
kv_eva.RT_KV_MAP_FILE = tmp / "rt_kv_dup_map.json"
kv_eva.OVERRIDES_FILE = tmp / "no_overrides.json"
EV = "105311"
DESC = "Чудова іграшка для дітей, розвиває дрібну моторику та уяву. Безпечні матеріали, яскраві кольори, зручна упаковка для подарунка."


def item(**kw):
    base = {"name": "Фігурка Колекційна Модель 1", "description": DESC, "vendor": "TestBrand", "vendor_code": "KV-1", "category": "Тестова категорія",
            "images": ["https://plutustoys.com.ua/img/kv/kv-1-1.jpg"], "price": "300", "cost": "200.0", "stock": "5", "dims_cm": "[20.0, 10.0, 5.0]", "weight_kg": "0.5"}
    base.update(kw)
    return base


def base_root(toysi_live=(), rt_live=()):
    yml = ET.Element("yml_catalog")
    shop = ET.SubElement(yml, "shop")
    ET.SubElement(shop, "categories")
    offers = ET.SubElement(shop, "offers")
    for i in toysi_live:
        o = ET.SubElement(offers, "offer", id=i, available="true")
        ET.SubElement(o, "name_ua").text = f"Toysi товар {i}"
    for i in rt_live:
        o = ET.SubElement(offers, "offer", id=i, available="true")
        ET.SubElement(o, "name_ua").text = f"RT товар {i}"
    return yml


def sel_of(items):
    return {"built_at": datetime.now().isoformat(timespec="seconds"), "rt_n": len(items), "valid_ids": [EV], "items": items}


S = {"eva_id": EV, "eva_name": "Фігурки", "cls": "UNIQUE", "src": "rule"}
ids_of = lambda root: {o.get("id"): o for o in root.find("shop").find("offers")}  # noqa: E731

# ---- брама / постачальник ----
chk("брама EVA_KV_IN_FEED за замовчуванням вимкнена", not kv_eva.gate_on())
os.environ["EVA_KV_IN_FEED"] = "1"
chk("брама EVA_KV_IN_FEED=1 вмикає", kv_eva.gate_on())
os.environ.pop("EVA_KV_IN_FEED")
kv_eva.SUPPLIER_FILE.write_text(json.dumps({"updated": datetime.now().astimezone().isoformat(), "items": {"kv-1": item(), "kvu-1": item(), "kv-abc": item(), "kv-2": "не dict"}}), encoding="utf-8")
ld = kv_eva.load_supplier()
chk("load_supplier: лише kv-<цифри> (kvu-«Уцінка», нечисловий id і не-dict відсіяні)", list(ld) == ["kv-1"])
chk("supplier_fresh: щойно оновлений → True", kv_eva.supplier_fresh())
kv_eva.SUPPLIER_FILE.write_text(json.dumps({"updated": (datetime.now().astimezone() - timedelta(hours=9)).isoformat(), "items": {"kv-1": item()}}), encoding="utf-8")
chk("supplier_fresh: 9 год тому → False", not kv_eva.supplier_fresh())

# ---- правило відбору ----
kv_eva.RT_KV_MAP_FILE.write_text(json.dumps({"rt-50": "kv-5", "rt-60": "kv-6"}), encoding="utf-8")
sup = {
    "kv-1": item(),                                                                              # унікальний
    "kv-2": item(name="Інша фігурка Модель 2", vendor_code="KV-2"),                              # GRAY
    "kv-3": item(name="Дубль Тойсі Модель 3", vendor_code="KV-3", dup_of="3001"),                # двійник Toysi 3001 є в EVA-дереві (live)
    "kv-4": item(name="Дубль Тойсі Модель 4", vendor_code="KV-4", dup_of="4001"),                # двійник Toysi 4001 нема в дереві й каталог 0 → емітуємо
    "kv-5": item(name="Дубль RT Модель 5", vendor_code="KV-5"),                                  # двійник rt-50 активний → не емітуємо
    "kv-6": item(name="Дубль RT Модель 6", vendor_code="KV-6"),                                  # двійник rt-60 нема → емітуємо
    "kv-7": item(name="Дубль без двійника Модель 7", vendor_code="KV-7"),                        # клас DUP, а двійника не знаємо → не емітуємо
    "kv-8": item(name="Дубль Тойсі Модель 8", vendor_code="KV-8", dup_of="8001"),                # двійник Toysi 8001 нема в дереві, але в каталозі залишок 9 → не емітуємо
}
sel = sel_of({"kv-1": dict(S), "kv-2": dict(S, cls="GRAY"), "kv-3": dict(S, cls="DUP"), "kv-4": dict(S, cls="DUP"), "kv-5": dict(S, cls="DUP"), "kv-6": dict(S, cls="DUP"),
              "kv-7": dict(S, cls="DUP"), "kv-8": dict(S, cls="DUP")})
catalog = {"3001": {"stock": 9}, "4001": {"stock": 0}, "8001": {"stock": 9}}
root = base_root(toysi_live=["3001"], rt_live=["rt-50"])
before = ET.tostring(root)
st = kv_eva.append_kv_offers(root, ge, sel, sup, catalog)
ids = ids_of(root)
chk("унікальний kv-1 емітовано; GRAY kv-2 — ні", "kv-1" in ids and "kv-2" not in ids and st.get("skip_gray") == 1)
chk("двійник Toysi активний у дереві (kv-3) → kv НЕ емітовано", "kv-3" not in ids)
chk("двійник Toysi без наявності (kv-4: у дереві нема, залишок 0) → kv емітовано (дубль «за відсутності у Тойсі»)", "kv-4" in ids)
chk("двійник RT активний (kv-5) → ні; RT-двійника нема в дереві (kv-6) → так", "kv-5" not in ids and "kv-6" in ids)
chk("клас DUP без відомого двійника (kv-7) → НЕ емітовано (наявність перевірити не можемо)", "kv-7" not in ids and st.get("skip_dup_twin_unknown") == 1)
chk("двійник Toysi є в КАТАЛОЗІ з залишком >1, хоч у EVA-дереві його нема (kv-8) → НЕ емітовано (слово власника: наявність у Тойсі)", "kv-8" not in ids)
chk("статистика: emitted 3 = 1 унікальний + 2 заміна дубля", st.get("emitted") == 3 and st.get("emitted_unique") == 1 and st.get("emitted_as_dup_replacement") == 2)
orig = ET.fromstring(before).find("shop").find("offers")
now_offers = root.find("shop").find("offers")
chk("існуючі offer'и дерева (Toysi/RT) не змінились — kv лише додано в кінець", [ET.tostring(o) for o in now_offers][:len(orig)] == [ET.tostring(o) for o in orig])
o1 = ids["kv-1"]
chk("offer kv-1: price/stock/categoryId/picture/vendor/description_ua/≥2 param", o1.findtext("price") == "300.00" and o1.findtext("categoryId") == EV and o1.find("picture") is not None and o1.findtext("vendor")
    and len(o1.findtext("description_ua")) >= 30 and len(o1.findall("param")) >= 2 and o1.get("available") == "true")
chk("kvu- ніколи не емітується навіть якщо влізло у відбір (id не проходить регекс)", kv_eva._validate({"id": "kvu-1", "name": "x", "price": 1, "stock": 1, "category": EV, "pictures": ["https://a"], "vendor": "v", "description": DESC, "params": [("a", "b"), ("c", "d")]}, {EV}, 255) == "id")

# ---- деактивація й липка категорія ----
listed = json.loads(kv_eva.LISTED_FILE.read_text(encoding="utf-8"))["listed"]
chk("стан листованого збережено для емітованих (kv-1, kv-4, kv-6) з категорією", set(listed) == {"kv-1", "kv-4", "kv-6"} and listed["kv-4"]["cat"] == EV)
root2 = base_root(toysi_live=["3001", "4001"], rt_live=["rt-50", "rt-60"])           # двійники kv-4 і kv-6 «повернулись»
sel2 = sel_of({"kv-1": dict(S, eva_id="99120"), "kv-4": dict(S, cls="DUP", eva_id="99120"), "kv-6": dict(S, cls="DUP")})
sel2["valid_ids"] = [EV, "99120"]
st2 = kv_eva.append_kv_offers(root2, ge, sel2, sup, catalog)
ids2 = ids_of(root2)
chk("двійник повернувся → колись листовані kv-4, kv-6 стають available=false, stock 0 (EVA: вимкнення, не видалення)", ids2["kv-4"].get("available") == "false" and ids2["kv-6"].get("available") == "false" and ids2["kv-4"].findtext("stock_quantity") == "0" and st2.get("deactivated") == 2)
chk("заглушка повна: назва, ціна, категорія, бренд, фото, опис", ids2["kv-4"].findtext("categoryId") == EV and ids2["kv-4"].findtext("vendor") == "TestBrand" and ids2["kv-4"].find("picture") is not None and len(ids2["kv-4"].findtext("description_ua")) >= 30)
chk("липка категорія: kv-1 лишився в EV, хоч відбір «перерахував» на 99120", ids2["kv-1"].findtext("categoryId") == EV)
root3 = base_root()
kv_eva.append_kv_offers(root3, ge, sel_of({}), {k: v for k, v in sup.items()}, catalog)
chk("гістерезис: колись листований kv-1 лишається активним, навіть якщо випав із відбору", ids_of(root3).get("kv-1") is not None and ids_of(root3)["kv-1"].get("available") == "true")

# ---- зіпсована мапа RT↔kv ----
kv_eva.LISTED_FILE.unlink()
kv_eva.RT_KV_MAP_FILE.write_text("{ це не json", encoding="utf-8")
root4 = base_root()
st4 = kv_eva.append_kv_offers(root4, ge, sel_of({"kv-1": dict(S)}), {"kv-1": item()}, catalog)
chk("зіпсована rt_kv_dup_map.json → нових kv-карток НЕ додаємо (ризик зайвого дубля)", "kv-1" not in ids_of(root4) and st4.get("abort_rt_kv_map_unreadable") == 1)
kv_eva.RT_KV_MAP_FILE.write_text(json.dumps({"rt-50": "kv-5"}), encoding="utf-8")

# ---- аудит #1705: динамічні двійники за назвою, витік постачальника, порожній каталог, назва заглушки ----
kv_eva.RT_KV_MAP_FILE.write_text("{}", encoding="utf-8")
kv_eva.LISTED_FILE.unlink(missing_ok=True)
rootn = base_root()
off = rootn.find("shop").find("offers")
for oid, nm, av in (("rt-77", "Фігурка Гаррі Pop", "true"), ("555", "Фігурка Герміона Pop", "true"), ("556", "Фігурка Рон Pop", "false")):
    o = ET.SubElement(off, "offer", id=oid, available=av)
    ET.SubElement(o, "name_ua").text = nm
supn = {"kv-21": item(name="Фігурка Гаррі Pop", vendor_code="VC1"), "kv-22": item(name="Фігурка Герміона Pop", vendor_code="VC2"), "kv-23": item(name="Фігурка Рон Pop", vendor_code="VC3"),
        "kv-24": item(name="Фігурка Невіл Pop", vendor_code="VC4")}
seln = sel_of({k: dict(S) for k in supn})
stn = kv_eva.append_kv_offers(rootn, ge, seln, supn, {"1": {"stock": 0}})
idn = ids_of(rootn)
chk("назва збігається з АКТИВНОЮ карткою Toysi/RT (мапи про двійника не знають) → kv НЕ емітовано, суфікса нема", "kv-21" not in idn and "kv-22" not in idn and stn.get("skip_name_matches_live_card") == 2)
chk("назва збігається лише з НЕактивною карткою (Toysi 556 available=false) → емітовано з артикулом; унікальна → як є", "kv-23" in idn and "VC3" in idn["kv-23"].findtext("name_ua") and "kv-24" in idn)
kv_eva.LISTED_FILE.unlink(missing_ok=True)
roote = base_root()
ste = kv_eva.append_kv_offers(roote, ge, sel_of({"kv-1": dict(S)}), {"kv-1": item()}, {})
chk("порожній каталог Toysi (наявність двійників не перевірити) → нових kv не додаємо", "kv-1" not in ids_of(roote) and ste.get("skip_no_catalog") == 1)
kv_eva.LISTED_FILE.unlink(missing_ok=True)
rootl = base_root()
supl = {"kv-11": item(vendor="KIDDISVIT"), "kv-12": item(name="Набір Kiddisvit Pop", vendor_code="X12"), "kv-13": item(description=DESC + " Виробник Kiddisvit.", vendor_code="X13"), "kv-14": item(name="Чиста назва Модель 14", vendor_code="X14")}
stl = kv_eva.append_kv_offers(rootl, ge, sel_of({k: dict(S) for k in supl}), supl, catalog)
idl = ids_of(rootl)
chk("ім'я постачальника у бренді/назві/описі → картка НЕ емітується; чиста — так; у XML нема «kiddisvit»", set(idl) == {"kv-14"} and stl.get("skip_supplier_leak") == 3 and "kiddisvit" not in ET.tostring(rootl, encoding="unicode").lower())
kv_eva.LISTED_FILE.unlink(missing_ok=True)
rootd = base_root(toysi_live=["9001"])
supd = {"kv-31": item(name="Колізійна назва Модель 31", vendor_code="DUP31")}
rootd.find("shop").find("offers").append(ET.fromstring('<offer id="9100" available="true"><name_ua>Колізійна назва Модель 31</name_ua></offer>'))
kv_eva.append_kv_offers(rootd, ge, sel_of({"kv-31": dict(S)}), supd, catalog)
chk("назва колізії з активною карткою → kv-31 не емітовано", "kv-31" not in ids_of(rootd))
kv_eva.LISTED_FILE.write_text(json.dumps({"v": 1, "listed": {"kv-31": {"cat": EV, "catname": "Фігурки", "name": "Колізійна назва Модель 31 (DUP31)", "price": 300, "vendor": "TestBrand", "pics": ["https://plutustoys.com.ua/img/kv/kv-31-1.jpg"], "desc": DESC}}}), encoding="utf-8")
rootz = base_root()
rootz.find("shop").find("offers").append(ET.fromstring('<offer id="9100" available="true"><name_ua>Колізійна назва Модель 31</name_ua></offer>'))
kv_eva.append_kv_offers(rootz, ge, sel_of({"kv-31": dict(S)}), supd, catalog)
z = ids_of(rootz)["kv-31"]
chk("колись листований (з артикулом у назві), двійник з'явився → заглушка available=false зі ТОЮ САМОЮ назвою (id не перейменовується)", z.get("available") == "false" and z.findtext("name_ua") == "Колізійна назва Модель 31 (DUP31)")
kv_eva.LISTED_FILE.unlink(missing_ok=True)
kv_eva.RT_KV_MAP_FILE.write_text(json.dumps({"rt-50": "kv-5"}), encoding="utf-8")

# ---- відбір / гарди ----
sig = inspect.signature(rt_eva.build_selection)
chk("rt_eva.build_selection: нові параметри необов'язкові (None) → поведінка RT не змінена", sig.parameters["overrides"].default is None and sig.parameters["include_classes"].default is None)
seen = {}
orig_bs = rt_eva.build_selection
rt_eva.build_selection = lambda ty, sp, g, overrides=None, include_classes=None: seen.update({"o": overrides, "c": include_classes}) or {"built_at": datetime.now().isoformat(timespec="seconds"), "rt_n": 1, "items": {}, "stats": {"in_feed_candidates": 1}}
kv_eva.build_selection({}, {}, ge)
chk("kv_eva.build_selection передає власні ручні рішення й класи (UNIQUE/UNIQUE_BRAND/DUP, без GRAY)", seen["c"] == ("UNIQUE", "UNIQUE_BRAND", "DUP") and seen["o"] == {})
rt_eva.build_selection = orig_bs
kv_eva.SUPPLIER_FILE.write_text(json.dumps({"updated": datetime.now().astimezone().isoformat(), "items": {"kv-1": item()}}), encoding="utf-8")
old_sel = {"built_at": (datetime.now() - timedelta(hours=20)).isoformat(timespec="seconds"), "rt_n": 1, "items": {}, "stats": {"in_feed_candidates": 1000}}
rt_eva._atomic_write(kv_eva.SEL_FILE, old_sel)
chk("ensure_selection: усічений каталог Toysi → перебудови немає, лишається свіжий старий", kv_eva.ensure_selection({"1": {}}, ge) == old_sel)
orig_kbs = kv_eva.build_selection
kv_eva.build_selection = lambda ty, sp, g: {"built_at": datetime.now().isoformat(timespec="seconds"), "rt_n": 1, "items": {}, "stats": {"in_feed_candidates": 300}}
big = {str(i): {} for i in range(25_001)}
chk("ensure_selection: новий відбір <70% попереднього (300 проти 1000) → відкинуто", kv_eva.ensure_selection(big, ge) == old_sel)
kv_eva.build_selection = orig_kbs

# ---- підключення до генератора ----
rootg = base_root()
chk("_maybe_add_kv_offers: брама вимкнена → дерево те саме (нічого не змінюється)", ge._maybe_add_kv_offers(rootg, {}) is rootg)
os.environ["EVA_KV_IN_FEED"] = "1"
kv_eva.SUPPLIER_FILE.write_text(json.dumps({"updated": (datetime.now().astimezone() - timedelta(hours=9)).isoformat(), "items": {"kv-1": item()}}), encoding="utf-8")
chk("_maybe_add_kv_offers: брама ввімкнена, але supplier застарів → дерево те саме (fail-safe)", ge._maybe_add_kv_offers(rootg, {}) is rootg)
os.environ.pop("EVA_KV_IN_FEED")

# ---- PR прев'ю/TTL ----
chk("TTL перебудови відбору для kv — 4 год (коротше за RT 12): двійник, що з'явився, не висить до півдоби", kv_eva.KV_REBUILD_AFTER_H == 4.0 and inspect.signature(kv_eva.ensure_selection).parameters["rebuild_after_h"].default == 4.0)
kv_eva.LISTED_FILE.write_text(json.dumps({"v": 1, "listed": {"kv-41": {"cat": EV, "catname": "Фігурки", "name": "Фігурка Збережена Модель 41", "price": 300, "vendor": "Збережений бренд", "pics": ["https://plutustoys.com.ua/img/kv/kv-41-1.jpg"], "desc": DESC}}}), encoding="utf-8")
rootv = base_root()
kv_eva.append_kv_offers(rootv, ge, sel_of({}), {"kv-41": item(name="Фігурка Збережена Модель 41", vendor="KIDDISVIT")}, catalog)
rootv2 = base_root()
kv_eva.LISTED_FILE.write_text(json.dumps({"v": 1, "listed": {"kv-41": {"cat": EV, "catname": "Фігурки", "name": "Фігурка Збережена Модель 41", "price": 300, "vendor": "Збережений бренд", "pics": ["https://plutustoys.com.ua/img/kv/kv-41-1.jpg"], "desc": DESC}}}), encoding="utf-8")
sup41 = {"kv-41": item(name="Фігурка Збережена Модель 41", vendor="KIDDISVIT", stock="0")}
kv_eva.append_kv_offers(rootv2, ge, sel_of({}), sup41, catalog)
zz = ids_of(rootv2)["kv-41"]
chk("заглушка деактивації: бренд зі знімка, а не з поточного (зміненого на постачальника) запису; у XML нема «kiddisvit»", zz.get("available") == "false" and zz.findtext("vendor") == "Збережений бренд" and "kiddisvit" not in ET.tostring(rootv2, encoding="unicode").lower())
kv_eva.LISTED_FILE.unlink(missing_ok=True)

# ---- прев'ю: нічого не пише у фід і у справжній стан ----
import parser as _parser  # noqa: E402
kv_eva.SUPPLIER_FILE.write_text(json.dumps({"updated": datetime.now().astimezone().isoformat(), "items": {"kv-51": item(name="Прев'ю Унікальний Модель 51", vendor_code="P51"), "kv-52": item(name="Прев'ю Дубль Модель 52", vendor_code="P52", dup_of="5200")}}), encoding="utf-8")
kv_eva.RT_KV_MAP_FILE.write_text("{}", encoding="utf-8")
xml_in = tmp / "eva_in.xml"
ET.ElementTree(base_root(toysi_live=["1"])).write(xml_in, encoding="utf-8")
xml_before = xml_in.read_bytes()
_orig_fetch, _orig_bs2 = _parser.fetch_toysi_catalog, kv_eva.build_selection
_parser.fetch_toysi_catalog = lambda: {str(i): {"stock": 0} for i in range(25_001)}
kv_eva.build_selection = lambda ty, sp, g: sel_of({"kv-51": dict(S), "kv-52": dict(S, cls="DUP")})
real_listed_before = kv_eva.LISTED_FILE
out_csv = tmp / "prev.csv"
try:
    rc = kv_eva.preview(str(xml_in), str(out_csv))
finally:
    _parser.fetch_toysi_catalog, kv_eva.build_selection = _orig_fetch, _orig_bs2
csv_txt = out_csv.read_text(encoding="utf-8-sig")
chk("прев'ю: CSV містить унікальний kv-51 і дубль kv-52 (двійника в наявності нема); вхідний eva_feed.xml не змінено; справжній стан листованого не створено/не змінено",
    rc == 0 and "kv-51" in csv_txt and "kv-52" in csv_txt and "унікальний" in csv_txt and "дубль" in csv_txt and xml_in.read_bytes() == xml_before and kv_eva.LISTED_FILE == real_listed_before and not kv_eva.LISTED_FILE.exists())

chk("CSV прев'ю: клітинки з формулами (=, +, -, @) екрануються апострофом; звичайний текст і числа як є", kv_eva._csv_safe('=HYPERLINK("x")') == "'=HYPERLINK(\"x\")" and kv_eva._csv_safe("-1") == "'-1" and kv_eva._csv_safe("Pop") == "Pop" and kv_eva._csv_safe(None) == "" and kv_eva._csv_safe(300) == "300")

if F:
    print("\nFAIL:", len(F))
    sys.exit(1)
print("\nOK: усі перевірки пройдені")
