#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_google_rt_dropt.py — RoyalToys/Dropt у рекламних фідах (рішення власника 10.11.2026). Синтетичні index.json-записи й supplier_*; реальні функції фільтрів generate_google_feed.
Після незалежного аудиту #1176: тінь-дублі Toysi (dup) відсіюються; за замовчуванням лише ix=1 (SEO відкрила); фото лише /img/p/ нашого домену; опис очищується від накладеного платежу/посилань/телефонів/імені постачальника;
бренд-витік → PlutusToys; уцінка відсіюється; id у фіді = публічний slug. `python test_google_rt_dropt.py` → exit 0/1."""
import os
import sys
import xml.etree.ElementTree as ET

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.pop("FEED_RT_DROPT_ONLY_IX", None)
import generate_google_feed as gf  # noqa: E402
import google_rt_dropt as g  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


GOOD_DESC = "Набір для розвитку дрібної моторики та уяви: деталі яскраві, міцні, добре тримаються разом."


def entry(pid, slug, name="Конструктор «Місто» 120 деталей", pr=300, cat="Конструктори", s=1, ix=1, p=None, dup=None):
    e = {"id": pid, "u": slug, "n": name, "pr": pr, "p": p if p is not None else f"https://plutustoys.com.ua/img/p/{slug}-1.jpg", "c": cat, "s": s, "rt": 1, "ix": ix}
    if dup:
        e["dup"] = 1
    return e


def sup(vendor="Brickland", cost=120, desc=GOOD_DESC, cat="Конструктори"):
    return {"vendor": vendor, "cost": cost, "description": desc, "category": cat, "stock": 5}


def run(entries, rt=None, dr=None, **kw):
    return g.build_items(gf, entries=entries, rt_sup=rt or {}, dropt_sup=dr or {}, **kw)


# ── базові випадки
entries = {
    "rt-1": entry("rt-1", "y1"),
    "rt-2": entry("rt-2", "y2", s=0),                                  # нема залишку
    "rt-3": entry("rt-3", "y3", p=""),                                 # нема фото
    "rt-4": entry("rt-4", "y4", pr=300),                               # закупівля 299 → внесок від'ємний
    "rt-8": entry("rt-8", "y8"),                                       # нема запису в постачальника
    "rt-6": entry("rt-6", "y6"),
    "dropt-9": entry("dropt-9", "x9", name="Світильник-нічник для дитячої кімнати", cat="Нічники"),
    "rt-12": entry("rt-12", "y12", dup=True),                          # тінь дубля Toysi
    "rt-13": entry("rt-13", "y13", ix=0),                              # SEO ще не відкрила
    "rt-14": entry("rt-14", "y14", p="https://royaltoys.ua/img/x.jpg"),  # фото не з нашого /img/p/
    "rt-15": entry("rt-15", "y15", p="https://plutustoys.com.ua/img/rt/rt-15-1.jpg"),   # старий шлях з іменем постачальника
    "rt-16": entry("rt-16", "y16", name="Уцінка Конструктор «Місто»"),
}
rt_sup = {f"rt-{i}": sup() for i in (1, 2, 3, 6, 12, 13, 14, 15, 16)}
rt_sup["rt-4"] = sup(cost=299)
dropt_sup = {"dropt-9": sup(vendor="", cost=100)}
items, st = run(entries, rt_sup, dropt_sup)
by_id = {i["id"]: i for i in items}
chk("у фіді рівно 3 (y1, y6, x9); id = публічний slug, без «rt-/dropt-»", sorted(by_id) == ["x9", "y1", "y6"] and st["included"] == 3 and not any(k.startswith(("rt-", "dropt-")) for k in by_id))
sk = st["skip"]
chk("відсів пораховано: dup 1, not_opened_for_index 1, залишок 1, фото 3 (нема/чуже/старий шлях), збиткові 1, нема постачальника 1, уцінка 1",
    sk.get("shadow_duplicate_of_toysi") == 1 and sk.get("not_opened_for_index") == 1 and sk.get("out_of_stock") == 1 and sk.get("no_image") == 3
    and sk.get("site_unprofitable") == 1 and sk.get("no_supplier_record") == 1 and sk.get("clearance") == 1)
a = by_id["y1"]
chk("ціна фіда = ціна картки (300.00 UAH); посилання — публічна адреса картки", a["price"] == "300.00 UAH" and a["link"] == gf.SHOP_URL + "/product-y1.html")
chk("фото — лише /img/p/ нашого домену", all(i["image_link"].startswith("https://plutustoys.com.ua/img/p/") for i in items))
chk("бренд із постачальника; порожній бренд → PlutusToys", a["brand"] == "Brickland" and by_id["x9"]["brand"] == gf.SHOP_NAME)
chk("GTIN нема (постачальник його не віддає) → identifier_exists=no у XML", all(i["gtin"] is None for i in items))
chk("наявність in_stock, new, категорія Google і опис є", a["availability"] == "in_stock" and a["condition"] == "new" and a["google_product_category"] and len(a["description"]) > 20)
chk("лічильник noindex-сторінок у фіді = 0 (лише ix=1)", st["noindex_pages_included"] == 0)
# FEED_RT_DROPT_ONLY_IX=0 → віддаємо й закриті
os.environ["FEED_RT_DROPT_ONLY_IX"] = "0"
it0, st0 = run(entries, rt_sup, dropt_sup)
chk("FEED_RT_DROPT_ONLY_IX=0: з'являється y13 (ix=0), а dup і далі НЕ віддається", "y13" in [i["id"] for i in it0] and "y12" not in [i["id"] for i in it0] and st0["noindex_pages_included"] == 1)
os.environ.pop("FEED_RT_DROPT_ONLY_IX", None)
# XML
root = gf._build_xml(items)
xml = ET.tostring(root, encoding="unicode")
ids_in_xml = sorted(e.text for e in root.iter("{http://base.google.com/ns/1.0}id"))
chk("XML будується; g:id = x9,y1,y6", ids_in_xml == ["x9", "y1", "y6"])
chk("у XML нема «dropt», «toysi», «royaltoys», «/img/rt/», «/img/dropt/», «product-rt-»", not any(w in xml.lower() for w in ("dropt", "toysi", "royaltoys", "/img/rt/", "/img/dropt/", "product-rt-")))
# опис: очищення
dirty = ("Чудовий набір для розвитку моторики. Оплата при отриманні на пошті. Телефон +380501234567. Сайт royaltoys.ua. Замовляйте у менеджера. Деталі яскраві та міцні. "
         "Детальніше на https://dropt.in.ua/ua/x/")
it, st2 = run({"rt-1": entry("rt-1", "y1")}, {"rt-1": sup(desc=dirty)})
d = it[0]["description"]
chk("опис очищено: нема накладеного платежу, телефону, URL, імені постачальника; корисні речення лишились", it and "Чудовий набір" in d and "Деталі яскраві" in d and not any(w in d.lower() for w in ("оплата при", "+380", "http", "royaltoys", "dropt", "менеджер")))
it, _ = run({"rt-1": entry("rt-1", "y1")}, {"rt-1": sup(desc="Замовляйте у менеджера. Накладений платіж доступний.")})
chk("опис повністю із заборонених речень → назва товару", it and it[0]["description"].startswith("Конструктор"))
it, _ = run({"rt-1": entry("rt-1", "y1")}, {"rt-1": sup(vendor="Dropt")})
chk("бренд «Dropt» (витік імені постачальника) → PlutusToys", it and it[0]["brand"] == gf.SHOP_NAME and "dropt" not in ET.tostring(gf._build_xml(it), encoding="unicode").lower())
it, st3 = run({"rt-1": entry("rt-1", "y1", name="Набір Royaltoys 120 деталей")}, {"rt-1": sup()})
chk("назва з іменем постачальника → позицію не віддаємо (supplier_name_leak)", it == [] and st3["skip"].get("supplier_name_leak") == 1)
it, _ = run({"rt-1": entry("rt-1", "y1", name="Royal Pony: набір для творчості Sport-серії")}, {"rt-1": sup()})
chk("легальні назви «Royal Pony», «Sport-» (містить «rt-») НЕ приймаємо за витік", len(it) == 1)
# політики — строго за тими ж функціями
for nm in ("Ніж мисливський складний", "Пістолет із пластиковими кульками військовий автомат"):
    ok_forb = bool(gf.is_forbidden({"id": "y11", "name": nm, "vendor": "Brickland", "category_name": "Конструктори"})) or bool(gf.is_weapon_replica("y11", gf.normalize_title(nm)))
    ii, _ = run({"rt-11": entry("rt-11", "y11", name=nm)}, {"rt-11": sup()})
    chk(f"«{nm[:22]}…»: фід узгоджений з is_forbidden/is_weapon_replica (відсіяно ⇔ функції ловлять)", (len(ii) == 0) == ok_forb)
# fail-safe
e0, s0 = run({}, rt_sup, dropt_sup)
chk("порожній індекс сайту → нічого (index_unavailable)", e0 == [] and s0["skip"].get("index_unavailable") == 1)
e1, s1 = run(entries, rt_sup, dropt_sup, rt_fresh=False, dropt_fresh=True)
chk("застарілий supplier_rt → RT не додаємо, Dropt лишається", [i["id"] for i in e1] == ["x9"])
ne, ns = run({"rt-1": entry("rt-1", "")}, rt_sup)
chk("нема публічного slug → позицію НЕ віддаємо", ne == [] and ns["skip"].get("no_public_slug") == 1)
# Meta і Bing: ті самі позиції будуються їхніми _build_xml, без імені постачальника
import generate_meta_feed as gm  # noqa: E402
import generate_bing_feed as gb  # noqa: E402
for nm, mod in (("Meta", gm), ("Bing", gb)):
    xm = ET.tostring(mod._build_xml(items), encoding="unicode")
    chk(f"{nm}: XML будується з позицій RT/Dropt; нема «dropt», «toysi», «royaltoys», «/img/rt/», «product-rt-»; є y1, x9",
        "y1" in xm and "x9" in xm and not any(w in xm.lower() for w in ("dropt", "toysi", "royaltoys", "/img/rt/", "/img/dropt/", "product-rt-")))
# extend_items: брама на кожен фід окремо, збій не валить
base = [{"id": "t1"}]
for k in ("GOOGLE", "META", "BING"):
    os.environ.pop(f"{k}_RT_DROPT_IN_FEED", None)
chk("extend_items: усі брами вимкнено → items без змін (Google/Meta/Bing)", all(g.extend_items(list(base), gf, f) == base for f in ("google", "meta", "bing")))
os.environ["META_RT_DROPT_IN_FEED"] = "1"
chk("брама Meta вмикається окремо, Google лишається вимкненою", g.gate_on("meta") and not g.gate_on("google") and not g.gate_on("bing"))
_orig = g.build_items
g.build_items = lambda gf_: (_ for _ in ()).throw(RuntimeError("boom"))
chk("extend_items: збій build_items НЕ валить фід — items без змін", g.extend_items(list(base), gf, "meta") == base)
g.build_items = lambda gf_: ([{"id": "x1"}], {"candidates": 1, "included": 1})
chk("extend_items: брама =1 → позиції додано", g.extend_items(list(base), gf, "meta") == base + [{"id": "x1"}])
g.build_items = _orig
os.environ.pop("META_RT_DROPT_IN_FEED", None)
# гейт
os.environ.pop("GOOGLE_RT_DROPT_IN_FEED", None)
chk("гейт GOOGLE_RT_DROPT_IN_FEED за замовчуванням ВИМКНЕНО", g.gate_on() is False)
os.environ["GOOGLE_RT_DROPT_IN_FEED"] = "1"
chk("гейт =1 вмикається", g.gate_on() is True)
os.environ.pop("GOOGLE_RT_DROPT_IN_FEED", None)
# ── російський Google-фід: переклади RT/Dropt (власник 10.11: «переклад робіть»)
import json as _json  # noqa: E402
import tempfile as _tf  # noqa: E402
from pathlib import Path as _P  # noqa: E402
long_desc = ("Перше речення про набір. " * 30).strip()
tt, dd = g.ru_source({"title": " Конструктор «Місто» ", "description": long_desc})
chk("ru_source: назва обрізана по пробілах, опис ≤ 450 і закінчується межею речення", tt == "Конструктор «Місто»" and 120 < len(dd) <= g.RU_DESC_MAX and dd.endswith("."))
it_list, _ = g.build_items(gf, entries={"rt-1": entry("rt-1", "y1")}, rt_sup={"rt-1": sup()}, dropt_sup={})
it = it_list[0]
t1, d1 = g.ru_source(it)
h = g.src_hash(t1, d1)
f_ru = _P(_tf.mkdtemp()) / "ru.json"
GOOD = {"src": h, "name": "Конструктор «Город» 120 деталей", "description": "Набор для развития мелкой моторики и воображения."}
f_ru.write_text(_json.dumps({"version": 1, "items": {"rt-1": GOOD}}), encoding="utf-8")
rt0, st0 = g.merge_ru_texts({"123": {"name": "x"}}, [it], path=f_ru)
chk("merge_ru_texts: актуальний переклад додано, Toysi-записи не зачеплено", rt0["rt-1"]["name"].startswith("Конструктор «Город»") and "123" in rt0 and st0["used"] == 1)
f_ru.write_text(_json.dumps({"version": 1, "items": {"rt-1": dict(GOOD, src="deadbeef00000000")}}), encoding="utf-8")
rt1, st1 = g.merge_ru_texts({}, [it], path=f_ru)
chk("застарілий переклад (укр. джерело змінилось) → не використовується", "rt-1" not in rt1 and st1["stale"] == 1)
f_ru.write_text(_json.dumps({"version": 1, "items": {"rt-1": dict(GOOD, name="Конструктор Міста", description="Набір для розвитку.")}}), encoding="utf-8")
rt2, st2 = g.merge_ru_texts({}, [it], path=f_ru)
chk("«переклад» з українськими літерами (і/ї/є) → непридатний, не використовується", "rt-1" not in rt2 and st2["bad"] == 1)
f_ru.write_text(_json.dumps({"version": 1, "items": {"rt-1": dict(GOOD, name="Набор Dropt", description="Описание товара.")}}), encoding="utf-8")
rt3, st3 = g.merge_ru_texts({}, [it], path=f_ru)
chk("переклад з іменем постачальника → непридатний", "rt-1" not in rt3 and st3["bad"] == 1)
rt4, st4 = g.merge_ru_texts({}, [it], path=_P(_tf.mkdtemp()) / "nema.json")
chk("нема файлу перекладів → нічого не додано (missing=1), без винятку", rt4 == {} and st4["missing"] == 1)
f_ru.write_text(_json.dumps({"version": 1, "items": {"rt-1": GOOD}}), encoding="utf-8")
rt5, _ = g.merge_ru_texts({}, [it], path=f_ru)
ru_out, ru_stat = gf.build_ru_items([it], rt5)
chk("build_ru_items: позиція RT з перекладом потрапляє в ru-фід із російською назвою; id і ціна ті самі", ru_stat["ru_included"] == 1 and "Город" in ru_out[0]["title"] and ru_out[0]["id"] == "y1" and ru_out[0]["price"] == it["price"])
exp = g.export_for_translation(gf, entries={"rt-1": entry("rt-1", "y1"), "rt-2": entry("rt-2", "y2")}, rt_sup={"rt-1": sup(), "rt-2": sup()}, dropt_sup={})
chk("export_for_translation: обидві позиції без актуального перекладу, є src і назва", {e["pid"] for e in exp} == {"rt-1", "rt-2"} and all(e["src"] and e["title"] for e in exp))
os.environ.pop("GOOGLE_RU_RT_DROPT_IN_FEED", None)
chk("брама ru GOOGLE_RU_RT_DROPT_IN_FEED за замовчуванням ВИМКНЕНО", g.gate_ru_on() is False)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
