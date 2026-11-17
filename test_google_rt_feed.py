#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_google_rt_feed.py — RoyalToys у рекламних фідах (рішення власника 10.11.2026). Синтетичні index.json-записи й supplier_*; реальні функції фільтрів generate_google_feed.
Після незалежного аудиту #1176: тінь-дублі Toysi (dup) відсіюються; за замовчуванням лише ix=1 (SEO відкрила); фото лише /img/p/ нашого домену; опис очищується від накладеного платежу/посилань/телефонів/імені постачальника;
бренд-витік → PlutusToys; уцінка відсіюється; id у фіді = публічний slug. Брами: нові імена *_RT_IN_FEED і ТИМЧАСОВІ запасні старі *_RT_DROPT_IN_FEED. `python test_google_rt_feed.py` → exit 0/1."""
import os
import sys
import xml.etree.ElementTree as ET

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.pop("FEED_RT_ONLY_IX", None)
os.environ.pop("FEED_RT_DROPT_ONLY_IX", None)
import generate_google_feed as gf  # noqa: E402
import google_rt_feed as g  # noqa: E402

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


def run(entries, rt=None, **kw):
    return g.build_items(gf, entries=entries, rt_sup=rt or {}, **kw)


# ── базові випадки
entries = {
    "rt-1": entry("rt-1", "y1"),
    "rt-2": entry("rt-2", "y2", s=0),                                  # нема залишку
    "rt-3": entry("rt-3", "y3", p=""),                                 # нема фото
    "rt-4": entry("rt-4", "y4", pr=300),                               # закупівля 299 → внесок від'ємний
    "rt-8": entry("rt-8", "y8"),                                       # нема запису в постачальника
    "rt-6": entry("rt-6", "y6"),
    "rt-12": entry("rt-12", "y12", dup=True),                          # тінь дубля Toysi
    "rt-13": entry("rt-13", "y13", ix=0),                              # SEO ще не відкрила
    "rt-14": entry("rt-14", "y14", p="https://royaltoys.ua/img/x.jpg"),  # фото не з нашого /img/p/
    "rt-15": entry("rt-15", "y15", p="https://plutustoys.com.ua/img/rt/rt-15-1.jpg"),   # старий шлях з іменем постачальника
    "rt-16": entry("rt-16", "y16", name="Уцінка Конструктор «Місто»"),
}
rt_sup = {f"rt-{i}": sup() for i in (1, 2, 3, 6, 12, 13, 14, 15, 16)}
rt_sup["rt-4"] = sup(cost=299)
rt_sup["rt-6"] = sup(vendor="", cost=100)
items, st = run(entries, rt_sup)
by_id = {i["id"]: i for i in items}
chk("у фіді рівно 2 (y1, y6); id = публічний slug, без «rt-»", sorted(by_id) == ["y1", "y6"] and st["included"] == 2 and not any(k.startswith("rt-") for k in by_id))
sk = st["skip"]
chk("відсів пораховано: dup 1, not_opened_for_index 1, залишок 1, фото 3 (нема/чуже/старий шлях), збиткові 1, нема постачальника 1, уцінка 1",
    sk.get("shadow_duplicate_of_toysi") == 1 and sk.get("not_opened_for_index") == 1 and sk.get("out_of_stock") == 1 and sk.get("no_image") == 3
    and sk.get("site_unprofitable") == 1 and sk.get("no_supplier_record") == 1 and sk.get("clearance") == 1)
a = by_id["y1"]
chk("ціна фіда = ціна картки (300.00 UAH); посилання — публічна адреса картки", a["price"] == "300.00 UAH" and a["link"] == gf.SHOP_URL + "/product-y1.html")
chk("фото — лише /img/p/ нашого домену", all(i["image_link"].startswith("https://plutustoys.com.ua/img/p/") for i in items))
chk("бренд із постачальника; порожній бренд → PlutusToys", a["brand"] == "Brickland" and by_id["y6"]["brand"] == gf.SHOP_NAME)
chk("GTIN нема (постачальник його не віддає) → identifier_exists=no у XML", all(i["gtin"] is None for i in items))
chk("наявність in_stock, new, категорія Google і опис є", a["availability"] == "in_stock" and a["condition"] == "new" and a["google_product_category"] and len(a["description"]) > 20)
chk("лічильник noindex-сторінок у фіді = 0 (лише ix=1)", st["noindex_pages_included"] == 0)
# FEED_RT_ONLY_IX=0 → віддаємо й закриті
os.environ["FEED_RT_ONLY_IX"] = "0"
it0, st0 = run(entries, rt_sup)
chk("FEED_RT_ONLY_IX=0: з'являється y13 (ix=0), а dup і далі НЕ віддається", "y13" in [i["id"] for i in it0] and "y12" not in [i["id"] for i in it0] and st0["noindex_pages_included"] == 1)
os.environ.pop("FEED_RT_ONLY_IX", None)
# ТИМЧАСОВЕ запасне старе ім'я FEED_RT_DROPT_ONLY_IX (прибрати разом з _env у google_rt_feed після міграції .env)
os.environ["FEED_RT_DROPT_ONLY_IX"] = "0"
chk("запасне старе ім'я FEED_RT_DROPT_ONLY_IX=0 теж віддає закриті (нового нема)", g.only_indexable() is False)
os.environ["FEED_RT_ONLY_IX"] = "1"
chk("нове ім'я має пріоритет над старим (FEED_RT_ONLY_IX=1 при старому =0)", g.only_indexable() is True)
os.environ.pop("FEED_RT_DROPT_ONLY_IX", None)
os.environ.pop("FEED_RT_ONLY_IX", None)
chk("жодного імені → за замовчуванням лише ix=1", g.only_indexable() is True)
# XML
root = gf._build_xml(items)
xml = ET.tostring(root, encoding="unicode")
ids_in_xml = sorted(e.text for e in root.iter("{http://base.google.com/ns/1.0}id"))
chk("XML будується; g:id = y1,y6", ids_in_xml == ["y1", "y6"])
chk("у XML нема «toysi», «royaltoys», «/img/rt/», «product-rt-»", not any(w in xml.lower() for w in ("toysi", "royaltoys", "/img/rt/", "product-rt-")))
# опис: очищення
dirty = ("Чудовий набір для розвитку моторики. Оплата при отриманні на пошті. Телефон +380501234567. Сайт royaltoys.ua. Замовляйте у менеджера. Деталі яскраві та міцні. "
         "Детальніше на https://royaltoys.ua/ua/x/")
it, st2 = run({"rt-1": entry("rt-1", "y1")}, {"rt-1": sup(desc=dirty)})
d = it[0]["description"]
chk("опис очищено: нема накладеного платежу, телефону, URL, імені постачальника; корисні речення лишились", it and "Чудовий набір" in d and "Деталі яскраві" in d and not any(w in d.lower() for w in ("оплата при", "+380", "http", "royaltoys", "менеджер")))
it, _ = run({"rt-1": entry("rt-1", "y1")}, {"rt-1": sup(desc="Замовляйте у менеджера. Накладений платіж доступний.")})
chk("опис повністю із заборонених речень → назва товару", it and it[0]["description"].startswith("Конструктор"))
it, _ = run({"rt-1": entry("rt-1", "y1")}, {"rt-1": sup(vendor="Royaltoys")})
chk("бренд «Royaltoys» (витік імені постачальника) → PlutusToys", it and it[0]["brand"] == gf.SHOP_NAME and "royaltoys" not in ET.tostring(gf._build_xml(it), encoding="unicode").lower())
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
e0, s0 = run({}, rt_sup)
chk("порожній індекс сайту → нічого (index_unavailable)", e0 == [] and s0["skip"].get("index_unavailable") == 1)
e1, s1 = run(entries, rt_sup, rt_fresh=False)
chk("застарілий supplier_rt → RT не додаємо", e1 == [] and s1["skip"].get("supplier_stale") == len(entries))
ne, ns = run({"rt-1": entry("rt-1", "")}, rt_sup)
chk("нема публічного slug → позицію НЕ віддаємо", ne == [] and ns["skip"].get("no_public_slug") == 1)
# Meta і Bing: ті самі позиції будуються їхніми _build_xml, без імені постачальника
import generate_meta_feed as gm  # noqa: E402
import generate_bing_feed as gb  # noqa: E402
for nm, mod in (("Meta", gm), ("Bing", gb)):
    xm = ET.tostring(mod._build_xml(items), encoding="unicode")
    chk(f"{nm}: XML будується з позицій RT; нема «toysi», «royaltoys», «/img/rt/», «product-rt-»; є y1, y6",
        "y1" in xm and "y6" in xm and not any(w in xm.lower() for w in ("toysi", "royaltoys", "/img/rt/", "product-rt-")))
# extend_items: брама на кожен фід окремо, збій не валить
base = [{"id": "t1"}]
for k in ("GOOGLE", "META", "BING"):
    os.environ.pop(f"{k}_RT_IN_FEED", None)
    os.environ.pop(f"{k}_RT_DROPT_IN_FEED", None)
chk("extend_items: усі брами вимкнено → items без змін (Google/Meta/Bing)", all(g.extend_items(list(base), gf, f) == base for f in ("google", "meta", "bing")))
os.environ["META_RT_IN_FEED"] = "1"
chk("брама Meta вмикається окремо, Google лишається вимкненою", g.gate_on("meta") and not g.gate_on("google") and not g.gate_on("bing"))
_orig = g.build_items
g.build_items = lambda gf_, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
chk("extend_items: збій build_items НЕ валить фід — items без змін", g.extend_items(list(base), gf, "meta") == base)
g.build_items = lambda gf_, **kw: ([{"id": "x1"}], {"candidates": 1, "included": 1})
chk("extend_items: брама =1 → позиції додано", g.extend_items(list(base), gf, "meta") == base + [{"id": "x1"}])
g.build_items = _orig
os.environ.pop("META_RT_IN_FEED", None)
# гейти: нове ім'я, ТИМЧАСОВЕ запасне старе ім'я (прибрати після міграції .env на VPS) і «жодного → вимкнено»
for k in ("GOOGLE", "META", "BING"):
    os.environ.pop(f"{k}_RT_IN_FEED", None)
    os.environ.pop(f"{k}_RT_DROPT_IN_FEED", None)
chk("гейт GOOGLE_RT_IN_FEED за замовчуванням ВИМКНЕНО (жодного з імен нема)", g.gate_on() is False and g.gate_on("meta") is False and g.gate_on("bing") is False)
os.environ["GOOGLE_RT_IN_FEED"] = "1"
chk("нове ім'я GOOGLE_RT_IN_FEED=1 вмикає RT-фід", g.gate_on() is True)
os.environ.pop("GOOGLE_RT_IN_FEED", None)
os.environ["GOOGLE_RT_DROPT_IN_FEED"] = "1"
chk("ТИМЧАСОВО: старе ім'я GOOGLE_RT_DROPT_IN_FEED=1 (вже в .env VPS) теж вмикає RT-фід, коли нового нема", g.gate_on() is True and g.gate_on("meta") is False)
os.environ["GOOGLE_RT_IN_FEED"] = "0"
chk("нове ім'я має пріоритет: GOOGLE_RT_IN_FEED=0 вимикає навіть при старому =1", g.gate_on() is False)
os.environ.pop("GOOGLE_RT_IN_FEED", None)
os.environ.pop("GOOGLE_RT_DROPT_IN_FEED", None)
for k in ("META", "BING"):
    os.environ[f"{k}_RT_DROPT_IN_FEED"] = "1"
    chk(f"ТИМЧАСОВО: старе {k}_RT_DROPT_IN_FEED=1 вмикає RT-фід {k}", g.gate_on(k.lower()) is True)
    os.environ.pop(f"{k}_RT_DROPT_IN_FEED", None)
    os.environ[f"{k}_RT_IN_FEED"] = "1"
    chk(f"нове {k}_RT_IN_FEED=1 вмикає RT-фід {k}", g.gate_on(k.lower()) is True)
    os.environ.pop(f"{k}_RT_IN_FEED", None)
# ── російський Google-фід: переклади RT (власник 10.11: «переклад робіть»)
import json as _json  # noqa: E402
import tempfile as _tf  # noqa: E402
from pathlib import Path as _P  # noqa: E402
long_desc = ("Перше речення про набір. " * 30).strip()
tt, dd = g.ru_source({"title": " Конструктор «Місто» ", "description": long_desc})
chk("ru_source: назва обрізана по пробілах, опис ≤ 450 і закінчується межею речення", tt == "Конструктор «Місто»" and 120 < len(dd) <= g.RU_DESC_MAX and dd.endswith("."))
it_list, _ = g.build_items(gf, entries={"rt-1": entry("rt-1", "y1")}, rt_sup={"rt-1": sup()})
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
f_ru.write_text(_json.dumps({"version": 1, "items": {"rt-1": dict(GOOD, name="Набор Royaltoys", description="Описание товара.")}}), encoding="utf-8")
rt3, st3 = g.merge_ru_texts({}, [it], path=f_ru)
chk("переклад з іменем постачальника → непридатний", "rt-1" not in rt3 and st3["bad"] == 1)
rt4, st4 = g.merge_ru_texts({}, [it], path=_P(_tf.mkdtemp()) / "nema.json")
chk("нема файлу перекладів → нічого не додано (missing=1), без винятку", rt4 == {} and st4["missing"] == 1)
f_ru.write_text(_json.dumps({"version": 1, "items": {"rt-1": GOOD}}), encoding="utf-8")
rt5, _ = g.merge_ru_texts({}, [it], path=f_ru)
ru_out, ru_stat = gf.build_ru_items([it], rt5)
chk("build_ru_items: позиція RT з перекладом потрапляє в ru-фід із російською назвою; id і ціна ті самі", ru_stat["ru_included"] == 1 and "Город" in ru_out[0]["title"] and ru_out[0]["id"] == "y1" and ru_out[0]["price"] == it["price"])
exp = g.export_for_translation(gf, entries={"rt-1": entry("rt-1", "y1"), "rt-2": entry("rt-2", "y2")}, rt_sup={"rt-1": sup(), "rt-2": sup()})
chk("export_for_translation: обидві позиції без актуального перекладу, є src і назва", {e["pid"] for e in exp} == {"rt-1", "rt-2"} and all(e["src"] and e["title"] for e in exp))
os.environ.pop("GOOGLE_RU_RT_IN_FEED", None)
os.environ.pop("GOOGLE_RU_RT_DROPT_IN_FEED", None)
chk("брама ru GOOGLE_RU_RT_IN_FEED за замовчуванням ВИМКНЕНО (жодного з імен нема)", g.gate_ru_on() is False)
os.environ["GOOGLE_RU_RT_IN_FEED"] = "1"
chk("нове ім'я GOOGLE_RU_RT_IN_FEED=1 вмикає ru-фід RT", g.gate_ru_on() is True)
os.environ.pop("GOOGLE_RU_RT_IN_FEED", None)
os.environ["GOOGLE_RU_RT_DROPT_IN_FEED"] = "1"
chk("ТИМЧАСОВО: старе GOOGLE_RU_RT_DROPT_IN_FEED=1 (вже в .env VPS) теж вмикає, коли нового нема", g.gate_ru_on() is True)
os.environ["GOOGLE_RU_RT_IN_FEED"] = "0"
chk("нове ім'я має пріоритет над старим (=0 вимикає)", g.gate_ru_on() is False)
os.environ.pop("GOOGLE_RU_RT_IN_FEED", None)
os.environ.pop("GOOGLE_RU_RT_DROPT_IN_FEED", None)
# реальний файл перекладів: лише rt-, без записів іншого постачальника
_real = g._load_ru_file()
chk("rt_ru_text.json читається, усі ключі — rt-", len(_real) > 1000 and all(k.startswith("rt-") for k in _real))
# ── KIDDISVIT (kv-, 17.11.2026): той самий конвеєр, власні брами, окремі джерела
def kv_entry(pid, slug, **kw):
    e = entry(pid, slug, **kw)
    e["sup"] = "kv"
    return e


KV_GOOD_DESC = "Фігурка героя з відомого серіалу: добре промальована, міцна, пасує до колекції й ігор."
kv_entries = {
    "kv-1": kv_entry("kv-1", "k1", name="Фігурка Funko POP! Гаррі", pr=1050, cat="Фігурки героїв"),
    "kv-2": kv_entry("kv-2", "k2", s=0),
    "kv-3": kv_entry("kv-3", "k3", ix=0),
    "kv-4": kv_entry("kv-4", "k4", dup=True),
    "kv-5": kv_entry("kv-5", "k5", p="https://plutustoys.com.ua/img/kv/kv-5-1.jpg"),
    "kv-6": kv_entry("kv-6", "k6", name="Каталка з ручкою Мишко", pr=900),
    "kv-7": kv_entry("kv-7", "k7", pr=500),
    "rt-1": entry("rt-1", "y1"),                                        # rt- у kv-запуску не береться
    "kvu-9": kv_entry("kvu-9", "k9"),                                   # «Уцінка» (kvu-) у фіди не йде
}
kv_sup = {"kv-1": {"vendor": "Funko", "cost": 700.02, "description": KV_GOOD_DESC, "category": "Герої фільмів та серіалів", "barcode": "8930007515264"},
          "kv-2": sup(), "kv-3": sup(), "kv-4": sup(), "kv-5": sup(), "kv-6": {"vendor": "KIDDI SMART", "cost": 600, "description": KV_GOOD_DESC, "category": "Каталки"},
          "kv-7": {"vendor": "Funko", "cost": 495, "description": KV_GOOD_DESC, "category": "Фігурки"}, "kvu-9": sup(), "rt-1": sup()}
kitems, kst = g.build_items(gf, entries=kv_entries, rt_sup=kv_sup, kind="kv")
kby = {i["id"]: i for i in kitems}
chk("kv-запуск: у фіді k1 і k6 (id = публічний slug k<id>, без «kv-»); rt-1 і kvu-9 не беруться", sorted(kby) == ["k1", "k6"] and kst["included_kv"] == 2 and kst["included_rt"] == 0 and kst["kind"] == "kv")
ksk = kst["skip"]
chk("kv відсів: залишок 1, не відкрито для індексу 1, тінь 1, фото 1 (старий шлях /img/kv/), збиткові 1 (k7)", ksk.get("out_of_stock") == 1 and ksk.get("not_opened_for_index") == 1 and ksk.get("shadow_duplicate_of_toysi") == 1 and ksk.get("no_image") == 1 and ksk.get("site_unprofitable") == 1)
k1 = kby["k1"]
chk("kv: ціна фіда = ціна картки 1050.00 UAH, посилка /product-k1.html, бренд Funko, валідний GTIN, опис постачальника", k1["price"] == "1050.00 UAH" and k1["link"] == gf.SHOP_URL + "/product-k1.html" and k1["brand"] == "Funko" and k1["gtin"] == "8930007515264" and KV_GOOD_DESC[:30] in k1["description"])
chk("kv: «Каталка з ручкою» не відсіюється хибним правилом не-іграшок (бренд KIDDI SMART лишається)", kby["k6"]["brand"] == "KIDDI SMART")
chk("rt-запуск не бере kv-: RT-запуск на змішаному index → лише y1", [i["id"] for i in g.build_items(gf, entries=kv_entries, rt_sup=kv_sup, kind="rt")[0]] == ["y1"])
# витік імені постачальника KIDDISVIT
leak_e = {"kv-1": kv_entry("kv-1", "k1", name="Фігурка від KIDDISVIT", pr=1050)}
chk("ім'я KIDDISVIT у назві → позиція не віддається (supplier_name_leak)", g.build_items(gf, entries=leak_e, rt_sup={"kv-1": kv_sup["kv-1"]}, kind="kv")[1]["skip"].get("supplier_name_leak") == 1)
dsc = g.scrub_description("Класна фігурка. Замовляйте на сайті kiddisvit.ua тепер. Є ще й набір.")
chk("scrub_description: речення з доменом KIDDISVIT вирізається", "kiddisvit" not in dsc.lower() and "Класна фігурка." in dsc)
chk("leaks: «kv-»/«kvu-» як id у значеннях ловиться, звичайні слова — ні", g.leaks("id kv-5") and g.leaks("kvu-12") and not g.leaks("Kiddi Smart набір"))
chk("supplier_stale для kv: джерело не свіже → жодної позиції, відсів supplier_stale", g.build_items(gf, entries=kv_entries, rt_sup=kv_sup, rt_fresh=False, kind="kv")[1]["skip"].get("supplier_stale", 0) >= 1)
# брами
os.environ.pop("META_KV_IN_FEED", None)
chk("брама kv вимкнена за замовчуванням і НЕ залежить від RT-брами", g.gate_on("meta", "kv") is False)
os.environ["META_RT_IN_FEED"] = "1"
chk("META_RT_IN_FEED=1 не вмикає kv", g.gate_on("meta", "kv") is False and g.gate_on("meta", "rt") is True)
os.environ["META_KV_IN_FEED"] = "1"
chk("META_KV_IN_FEED=1 вмикає kv окремо", g.gate_on("meta", "kv") is True)
calls = []
g.build_items = lambda gf_, **kw: (calls.append(kw.get("kind")) or ([{"id": "x_" + kw.get("kind", "rt")}], {"candidates": 1, "included": 1, "kind": kw.get("kind", "rt")}))
r = g.extend_items(list(base), gf, "meta")
chk("extend_items: обидві брами =1 → і RT, і KIDDISVIT додано (кожен своїм викликом)", calls == ["rt", "kv"] and r == base + [{"id": "x_rt"}, {"id": "x_kv"}])
os.environ.pop("META_RT_IN_FEED", None)
calls.clear()
r2 = g.extend_items(list(base), gf, "meta")
chk("лише KIDDISVIT-брама → додано лише kv", calls == ["kv"] and r2 == base + [{"id": "x_kv"}])


def _boom(gf_, **kw):
    if kw.get("kind") == "rt":
        raise RuntimeError("rt boom")
    return [{"id": "ok_kv"}], {"candidates": 1, "included": 1, "kind": "kv"}


os.environ["META_RT_IN_FEED"] = "1"
g.build_items = _boom
chk("збій RT НЕ забирає KIDDISVIT (і навпаки): kv-позиції додано попри виняток RT", g.extend_items(list(base), gf, "meta") == base + [{"id": "ok_kv"}])
g.build_items = _orig
os.environ.pop("META_RT_IN_FEED", None)
os.environ.pop("META_KV_IN_FEED", None)
chk("supplier_path: kv → supplier_kv.json, rt → supplier_rt.json; перевизначення SUPPLIER_KV_FILE", g.supplier_path("kv").name == "supplier_kv.json" and g.supplier_path("rt").name == "supplier_rt.json")
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
