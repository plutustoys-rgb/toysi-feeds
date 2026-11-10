#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_google_rt_dropt.py — RoyalToys/Dropt у Google-фіді (рішення власника 10.11.2026: «РТ та дропт додай у гугл»). Синтетичні index.json-записи й supplier_*; реальні функції фільтрів generate_google_feed.
Перевіряє: включення (id = публічний slug, посилання, ціна = ціна картки, наша адреса фото, бренд, GTIN лише валідний), відсів (залишок, свіжість, нема в постачальника, забороненe/зброя/збиткові/без фото),
fail-safe (порожній індекс, застарілий постачальник), у XML нема імені постачальника, гейт. `python test_google_rt_dropt.py` → exit 0/1."""
import os
import sys
import xml.etree.ElementTree as ET

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import generate_google_feed as gf  # noqa: E402
import google_rt_dropt as g  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def entry(pid, slug, name="Конструктор «Місто» 120 деталей", pr=300, cat="Конструктори", s=1, ix=0, p=None):
    return {"id": pid, "u": slug, "n": name, "pr": pr, "p": p if p is not None else f"https://plutustoys.com.ua/img/p/{slug}-1.jpg", "c": cat, "s": s, "rt": 1, "ix": ix}


def sup(vendor="Brickland", cost=120, desc="Набір для розвитку дрібної моторики та уяви: деталі яскраві, міцні, добре тримаються разом.", cat="Конструктори", barcode=None):
    d = {"vendor": vendor, "cost": cost, "description": desc, "category": cat, "stock": 5}
    if barcode:
        d["barcode"] = barcode
    return d


rt_sup = {"rt-1": sup(), "rt-2": sup(), "rt-3": sup(), "rt-4": sup(cost=299), "rt-5": sup(), "rt-6": sup(barcode="4006381333931"), "rt-7": sup(barcode="123")}
dropt_sup = {"dropt-9": sup(vendor="", cost=100)}
entries = {
    "rt-1": entry("rt-1", "y1"),
    "rt-2": entry("rt-2", "y2", s=0),                                  # нема залишку
    "rt-3": entry("rt-3", "y3", p=""),                                 # нема фото
    "rt-4": entry("rt-4", "y4", pr=300),                               # закупівля 299 → внесок від'ємний
    "rt-8": entry("rt-8", "y8"),                                       # нема запису в постачальника
    "rt-6": entry("rt-6", "y6", ix=1),
    "rt-7": entry("rt-7", "y7"),
    "dropt-9": entry("dropt-9", "x9", name="Світильник-нічник для дитячої кімнати", cat="Нічники"),
}
items, st = g.build_items(gf, entries=entries, rt_sup=rt_sup, dropt_sup=dropt_sup)
by_id = {i["id"]: i for i in items}
chk("у фіді рівно 4 з 8 кандидатів (y1, y6, y7, x9); id = публічний slug, без «rt-/dropt-»", sorted(by_id) == ["x9", "y1", "y6", "y7"] and st["included"] == 4 and st["candidates"] == 8 and not any(k.startswith(("rt-", "dropt-")) for k in by_id))
chk("відсів пораховано: залишок 1, фото 1, збиткові 1, нема постачальника 1", st["skip"].get("out_of_stock") == 1 and st["skip"].get("no_image") == 1 and st["skip"].get("site_unprofitable") == 1 and st["skip"].get("no_supplier_record") == 1)
a = by_id["y1"]
chk("ціна фіда = ціна картки (300.00 UAH); посилання — публічна адреса картки", a["price"] == "300.00 UAH" and a["link"] == gf.SHOP_URL + "/product-y1.html")
chk("фото — на НАШОМУ домені (/img/p/…), не постачальника", a["image_link"].startswith("https://plutustoys.com.ua/img/p/"))
chk("бренд із постачальника; порожній бренд → PlutusToys-фолбек", a["brand"] and by_id["x9"]["brand"] == gf.SHOP_NAME)
chk("GTIN лише валідний: 4006381333931 є, «123» нема", by_id["y6"]["gtin"] == "4006381333931" and by_id["y7"]["gtin"] is None)
chk("наявність in_stock, стан new, є google_product_category і опис", a["availability"] == "in_stock" and a["condition"] == "new" and a["google_product_category"] and len(a["description"]) > 20)
chk("лічильник noindex-сторінок у фіді: 3 із 4 (y6 з ix=1 — індексовна)", st["noindex_pages_included"] == 3)
# XML: ім'я постачальника не просочується
root = gf._build_xml(items)
xml = ET.tostring(root, encoding="unicode")
ids_in_xml = [e.text for e in root.iter("{http://base.google.com/ns/1.0}id")]
chk("XML будується; g:id = x9,y1,y6,y7", sorted(ids_in_xml) == ["x9", "y1", "y6", "y7"])
chk("у XML нема «dropt», «toysi», «/img/rt», «/img/dropt», «product-rt-»", not any(w in xml.lower() for w in ("dropt", "toysi.ua", "/img/rt/", "/img/dropt/", "product-rt-")))
# фільтри політик — тією ж функцією, що в Toysi-шляху
kn = {"rt-10": entry("rt-10", "y10", name="Ніж мисливський складний")}
wp = {"rt-11": entry("rt-11", "y11", name="Пістолет із пластиковими кульками військовий автомат")}
ks, kst = g.build_items(gf, entries=kn, rt_sup={"rt-10": sup()}, dropt_sup={})
ws, wst = g.build_items(gf, entries=wp, rt_sup={"rt-11": sup()}, dropt_sup={})
chk("ніж: відсіяно політикою forbidden (якщо функція його ловить) — узгоджено з is_forbidden", (len(ks) == 0) == bool(gf.is_forbidden({"name": "Ніж мисливський складний", "vendor": "Brickland", "category_name": "Конструктори"})))
chk("репліка зброї: узгоджено з is_weapon_replica", (len(ws) == 0) == bool(gf.is_weapon_replica("y11", gf.normalize_title("Пістолет із пластиковими кульками військовий автомат"))) or len(ws) == 0)
# fail-safe
e0, s0 = g.build_items(gf, entries={}, rt_sup=rt_sup, dropt_sup=dropt_sup)
chk("порожній індекс сайту → нічого не додаємо (index_unavailable)", e0 == [] and s0["skip"].get("index_unavailable") == 1)
e1, s1 = g.build_items(gf, entries=entries, rt_sup=rt_sup, dropt_sup=dropt_sup, rt_fresh=False, dropt_fresh=True)
chk("застарілий supplier_rt → RT цього прогону НЕ додаємо, Dropt лишається", sorted(i["id"] for i in e1) == ["x9"] and s1["skip"].get("supplier_stale") == 7)
# публічних адрес ще нема → нічого не віддаємо (ні rt-/dropt- id, ні стара адреса)
ne, ns = g.build_items(gf, entries={"rt-1": entry("rt-1", "")}, rt_sup=rt_sup, dropt_sup={})
chk("нема публічного slug (ворота адрес вимкнені) → позицію НЕ віддаємо Google", ne == [] and ns["skip"].get("no_public_slug") == 1)
# гейт
os.environ.pop("GOOGLE_RT_DROPT_IN_FEED", None)
chk("гейт GOOGLE_RT_DROPT_IN_FEED за замовчуванням ВИМКНЕНО", g.gate_on() is False)
os.environ["GOOGLE_RT_DROPT_IN_FEED"] = "1"
chk("гейт =1 вмикається", g.gate_on() is True)
os.environ.pop("GOOGLE_RT_DROPT_IN_FEED", None)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
