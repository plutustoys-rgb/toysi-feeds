#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_eva.py — товари RoyalToys у EVA-фіді (21.10.2026). Офлайн, синтетичні дані. Інваріанти: (1) брама EVA_RT_IN_FEED вимкнена → фід НЕ змінюється; (2) offer RT емітується ЛИШЕ з усіма обов'язковими полями (фото https, опис ≥30, ≥2 param, валідна категорія EVA);
зіпсований/заборонений/без залишку/занизька ціна — НЕ емітується; (3) назва, що збігається з Toysi-офером, отримує артикул; (4) колись листований rt-id, що випав, → мінімальний available=false; (5) збій додавання → Toysi-фід лишається як був; (6) застарілий відбір → RT не додаються."""
import copy
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
sys.path.insert(0, str(Path(__file__).parent))
import generate_eva_feed as ge  # noqa: E402
import rt_eva  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
rt_eva.LISTED_FILE = tmp / "listed.json"
rt_eva.SEL_FILE = tmp / "sel.json"
valid = rt_eva.valid_eva_ids()
chk("valid_eva_ids: довідник EVA (БТК_id) + співставлені id (сотні листків)", len(valid) > 300 and "105311" in valid)
EV = "105311"      # «Пазли…» — валідний листок

DESC = "Чудова іграшка для дітей, розвиває дрібну моторику та уяву. Безпечні матеріали, яскраві кольори."


def item(**kw):
    base = {"name": "Іграшка Тест Модель 1", "description": DESC, "vendor": "TestBrand", "vendor_code": "TB-1", "category": "Тестова категорія", "images": ["https://plutustoys.com.ua/img/rt/rt-1-1.jpg"],
            "price": "300", "cost": "200.0", "stock": "5", "dims_cm": "[20.0, 10.0, 5.0]", "weight_kg": "0.5"}
    base.update(kw)
    return base


from forbidden_products import is_forbidden  # noqa: E402
SEL = {"eva_id": EV, "eva_name": "Пазли", "cls": "UNIQUE", "src": "rule"}
f, why = rt_eva.build_offer_fields("rt-1", item(), SEL, ge, is_forbidden)
chk("build_offer_fields: нормальний товар → поля (ціна=supplier price, категорія EVA, ≥2 param, розміри)", f is not None and f["price"] == 300 and f["category"] == EV and len(f["params"]) >= 2 and rt_eva.validate_offer(f, valid, 255) == "")
bad = {"stock": item(stock="1"), "price": item(price="210"), "no_pictures": item(images=[]), "short_desc": item(description="коротко"), "no_vendor": item(vendor="")}
res = {k: rt_eva.build_offer_fields("rt-9", v, SEL, ge, is_forbidden)[1] for k, v in bad.items()}
chk("відсіювання: залишок ≤1, ціна < cost×1.3, без фото, короткий опис, без бренду → причини stock/price/no_pictures/short_desc/no_vendor", res == {"stock": "stock", "price": "price", "no_pictures": "no_pictures", "short_desc": "short_desc", "no_vendor": "no_vendor"})
chk("validate_offer: невалідна категорія / id / ціна → відмова", rt_eva.validate_offer({**f, "category": "000"}, valid, 255) == "category" and rt_eva.validate_offer({**f, "id": "x-1"}, valid, 255) == "id" and rt_eva.validate_offer({**f, "price": 0}, valid, 255) == "price/stock")


def base_root():
    yml = ET.Element("yml_catalog")
    shop = ET.SubElement(yml, "shop")
    ET.SubElement(shop, "categories")
    offers = ET.SubElement(shop, "offers")
    o = ET.SubElement(offers, "offer", id="100", available="true")
    ET.SubElement(o, "name_ua").text = "Іграшка Тест Модель 1"        # збігається з назвою RT → має отримати артикул
    return yml


sup = {"rt-1": item(), "rt-2": item(name="Інша іграшка Модель 2", vendor_code="TB-2"), "rt-3": item(stock="0", name="Третя іграшка Модель 3")}
sel = {"built_at": datetime.now().isoformat(timespec="seconds"), "rt_n": 3, "valid_ids": [EV], "items": {"rt-1": dict(SEL), "rt-2": dict(SEL, cls="GRAY"), "rt-3": dict(SEL), "rt-4": dict(SEL, cls="DUP")}}
root = base_root()
st = rt_eva.append_rt_offers(root, ge, sel, sup)
offers = root.find("shop").find("offers").findall("offer")
ids = {o.get("id"): o for o in offers}
chk("append_rt_offers: емітовано rt-1 і rt-2 (GRAY теж); rt-3 (залишок 0) і rt-4 (DUP, нема в supplier) — ні; категорія EVA задекларована", st.get("emitted") == 2 and "rt-1" in ids and "rt-2" in ids and "rt-3" not in ids and root.find("shop").find("categories").find(f"category[@id='{EV}']") is not None)
n1 = ids["rt-1"].findtext("name_ua")
chk("назва, що збіглась з Toysi-офером, отримала артикул (унікальність назв)", n1 != "Іграшка Тест Модель 1" and "TB-1" in n1)
o1 = ids["rt-1"]
chk("offer: price/stock/categoryId/picture/vendor/description_ua/≥2 param", o1.findtext("price") == "300.00" and o1.findtext("stock_quantity") == "5" and o1.findtext("categoryId") == EV and o1.find("picture") is not None
    and o1.findtext("vendor") and len(o1.findtext("description_ua")) >= 30 and len(o1.findall("param")) >= 2)
rt_eva.LISTED_FILE.write_text(json.dumps({"listed": ["rt-1", "rt-2", "rt-3"]}), encoding="utf-8")
root2 = base_root()
st2 = rt_eva.append_rt_offers(root2, ge, sel, sup)
o3 = [o for o in root2.find("shop").find("offers") if o.get("id") == "rt-3"]
chk("колись листований rt-3 (випав: залишок 0) → мінімальний offer available=false, stock 0", st2.get("deactivated") == 1 and o3 and o3[0].get("available") == "false" and o3[0].findtext("stock_quantity") == "0")

# категорія поза відомо-прийнятими (не в valid_ids і не задекларована Toysi-частиною) → offer НЕ емітується
sel_bad = {**sel, "items": {"rt-1": dict(SEL, eva_id="105999")}}
root3 = base_root()
st3 = rt_eva.append_rt_offers(root3, ge, sel_bad, sup)
chk("категорія EVA поза відомо-прийнятими → offer не емітується (invalid_category), фід не отримує невідомих id", st3.get("emitted", 0) == 0 and st3.get("invalid_category") == 1)
# брама і збої
os.environ.pop("EVA_RT_IN_FEED", None)
r0 = base_root()
chk("брама EVA_RT_IN_FEED вимкнена → _maybe_add_rt_offers повертає той самий root (фід не змінюється)", ge._maybe_add_rt_offers(r0, {}) is r0)
os.environ["EVA_RT_IN_FEED"] = "1"
orig_ens, orig_app = rt_eva.ensure_selection, rt_eva.append_rt_offers
try:
    rt_eva.ensure_selection = lambda ty, me, rebuild_after_h=12.0: None
    r1 = base_root()
    chk("відбору нема/застарілий → root без змін", ge._maybe_add_rt_offers(r1, {}) is r1)
    rt_eva.ensure_selection = lambda ty, me, rebuild_after_h=12.0: sel

    def boom(*a, **k):
        a[0].find("shop").find("offers").append(ET.Element("offer", id="rt-bad"))      # частково змінює копію
        raise RuntimeError("boom")
    rt_eva.append_rt_offers = boom
    r2 = base_root()
    out = ge._maybe_add_rt_offers(r2, {})
    chk("збій під час додавання → повертається ОРИГІНАЛ (Toysi-фід цілий, жодного напівдоданого offer)", out is r2 and not [o for o in r2.find("shop").find("offers") if o.get("id") == "rt-bad"])
finally:
    rt_eva.ensure_selection, rt_eva.append_rt_offers = orig_ens, orig_app
    os.environ.pop("EVA_RT_IN_FEED", None)
old = {"built_at": (datetime.now() - timedelta(hours=rt_eva.STALE_H + 1)).isoformat(timespec="seconds")}
chk("selection_fresh: свіжий → True; старший за STALE_H → False; без дати → False", rt_eva.selection_fresh(sel) and not rt_eva.selection_fresh(old) and not rt_eva.selection_fresh({}))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
