#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_seo_meta.py — SEO-замовлення 2026-10-10, п.2–3 (фінальні шаблони з SEO_CHANNEL.md).
Інваріанти: title товару ≤65 симв. із суфіксом « — купити | PlutusToys» (довга назва ріжеться по слову + «…»);
description ≤160 і НІКОЛИ не «Бренд: …»; категорія/каталог — шаблон + правильна множина («21 052 товари»); пагінація — «— сторінка N»;
BreadcrumbList на картці й категорії (+ видимі крихти); ItemList на категорії; Product.offers має priceValidUntil, shippingDetails,
hasMerchantReturnPolicy, itemCondition (за назвою «Уцінка», не вгадуємо); JSON-LD валідний і без літерального '<'. Самодостатній, мережа не потрібна."""
import importlib
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "site"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID"):
    os.environ.pop(k, None)

import build_site as b  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def ld_blocks(html):
    return [json.loads(m) for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]


# ── title товару
t = b.product_title("Лялька Барбі")
chk("коротка назва: title = назва + суфікс", t == "Лялька Барбі — купити | PlutusToys")
long_name = "Конструктор магнітний великий набір для хлопчиків та дівчаток з 120 деталей у валізі"
t = b.product_title(long_name)
chk("довга назва: title ≤65, закінчується суфіксом, назва обрізана з «…»", len(t) <= 65 and t.endswith(" — купити | PlutusToys") and "…" in t)
chk("довга назва: не обрізано посеред слова", t.split("…")[0].split()[-1] in long_name.split())

# ── description товару
d = b.product_description("Тест", 100, "Бренд: Deddy Bears")
chk("опис «Бренд: …» → шаблон із назвою/ціною, не «Бренд»", d.startswith("Тест — 100 ₴.") and not d.startswith("Бренд"))
d = b.product_description("Тест", 100, "")
chk("порожній опис → шаблон", "Доставка Новою Поштою" in d and len(d) <= 160)
real = "Чудова м'яка іграшка для малюків, виготовлена з безпечних гіпоалергенних матеріалів, приємна на дотик, ідеальний подарунок на день народження."
d = b.product_description("Іграшка", 249, real)
chk("реальний опис → ≤160, містить ціну та доставку", len(d) <= 160 and "Ціна 249 ₴" in d and "Новою Поштою" in d)
chk("реальний опис: починається з тексту опису", d.startswith("Чудова м'яка іграшка"))
chk("опис ≥40 симв. але починається з «Бренд:» → шаблон", b.product_description("Тест", 5, "Бренд: Deddy Bears, країна виробник: Китай, вік 3+").startswith("Тест — 5 ₴."))
d = b.product_description("Х" * 300, 99, "")
chk("надто довга назва → description все одно ≤160", len(d) <= 160)
chk("опис ЗАВЖДИ ≤160 на різних довжинах",
    all(len(b.product_description("Назва " * n, 1234, "Слово " * (n * 7))) <= 160 for n in range(1, 60)))

# ── категорія / каталог
for n, w in ((1, "товар"), (2, "товари"), (4, "товари"), (5, "товарів"), (11, "товарів"), (12, "товарів"), (21, "товар"), (22, "товари"), (25, "товарів"), (111, "товарів")):
    title, desc = b.category_meta("Лялька", n, 99)
    chk(f"множина N={n}: «{n} {w}»", desc.startswith(f"{n} {w} у категорії «Лялька»: ціни від 99 ₴"))
title, desc = b.category_meta(None, 21052, 12)
chk("каталог: «21 052 товари», title за шаблоном", "21 052 товари" in desc and title == "Каталог іграшок — купити в Україні | PlutusToys")
title, desc = b.category_meta("Лялька", 30, 99, page_no=3, pages=5)
chk("пагінація: title «— сторінка N | PlutusToys», description з префіксом", title == "Лялька — сторінка 3 | PlutusToys" and desc.startswith("Сторінка 3 з 5. "))
title, _ = b.category_meta("Лялька", 30, 99)
chk("категорія: title «{Категорія} — купити в Україні | PlutusToys»", title == "Лялька — купити в Україні | PlutusToys")

# ── стан товару за назвою
C = {
    "https://schema.org/NewCondition": ["Лялька Барбі", "Уцінка Лялька м'яка, музична \"Lucky Baby\" - пошкоджена упаковка.",
                                         "Уцінка. Лялька \"Герої в масках: Грег\" - пом'ята упаковка",
                                         "Уцінка. Музичне дитяче піаніно \"Рибка\" - потертості на коробці і віконці"],
    "https://schema.org/DamagedCondition": ["Уцінка. Машина на р / у \"Ford F-350\" (червоний) - Машина подряпана, упаковка пошкоджена",
                                             "Уцінка. Валіза ТехноК, рожевий - тріщина на кришці валізи",
                                             "Уцінка. Шкатулка заводна \"Сердечко\" - балерина зламана",
                                             "Уцінка. Мильні бульбашки - уцінка: неповна баночка з розчином"],
    "https://schema.org/DamagedCondition#2": ["Уценка. Акула на радіокеруванні – не працює", "Уцінка. МУЗИЧНА ІГРАШКА «КАЧЕНЯ» Не ходить. Пошкоджена упаковка.",
                                               "Уцінка. Автомат Не стріляє присосками - пошкоджена упаковка", "Уцінка. Крейда біла - Пошкоджена упаковка та поломана крейда",
                                               "Уцінка. Трансформер «Робокар Поли» Здерта краска, помʼята упаковка", "Уцінка. Електронна гра Не коректно працює електроніка та пошкоджена упаковка",
                                               "Уцінка. Мильні бульбашки «FIGHTER GIANT» Мутні та пошкоджена упаковка", "Уценка. Пупс нема одного ока"],
    "#unsure": ["Уцінка. Блокатор Закінчився термін придатності, брудна упаковка"],
    "https://schema.org/UsedCondition": ["Уцінка. Самокат триколісний (чорно-білий) - вітринний варіант, не товарний вигляд"],
    "": ["Уцінка. Іграшка - щось невідоме", "Уцінка Іграшка без опису"],
}
C["https://schema.org/DamagedCondition"] += C.pop("https://schema.org/DamagedCondition#2")
C[""] += C.pop("#unsure")
for exp, names in C.items():
    for nm in names:
        chk(f"itemCondition[{exp.split('/')[-1] or 'пропущено'}] ← {nm[:55]}", b.item_condition(nm) == exp)

# ── повна генерація картки й категорії
tmp = tempfile.mkdtemp()
b.OUT = tmp
p = {"id": "4242", "name": "Уцінка. Валіза ТехноК, рожевий - тріщина на кришці валізи", "price": 349, "category": "Валізи <b>",
     "photo": "https://example.com/1.jpg", "stock": 3, "desc": "Бренд: Deddy Bears", "contribution": 10.0}
b.write_product(p, related=None, cat_slug={"Валізи <b>": "valizi"})
h = open(os.path.join(tmp, "product-4242.html"), encoding="utf-8").read()
chk("картка: <title> за шаблоном, ≤65", re.search(r"<title>(.*?)</title>", h).group(1).endswith("— купити | PlutusToys") and len(__import__("html").unescape(re.search(r"<title>(.*?)</title>", h).group(1))) <= 65)
m = re.search(r'<meta name="description" content="(.*?)">', h)
chk("картка: description не «Бренд: …», ≤160", m is not None and not m.group(1).startswith("Бренд") and len(__import__("html").unescape(m.group(1))) <= 160)
blocks = ld_blocks(h)
types = [x["@type"] for x in blocks]
chk("картка: є Product і BreadcrumbList", "Product" in types and "BreadcrumbList" in types)
prod = next(x for x in blocks if x["@type"] == "Product")["offers"]
for key in ("priceValidUntil", "shippingDetails", "hasMerchantReturnPolicy", "itemCondition"):
    chk(f"картка: offers.{key}", key in prod)
chk("картка: itemCondition=Damaged (тріщина)", prod["itemCondition"].endswith("DamagedCondition"))
chk("картка: priceValidUntil — дата ISO", re.fullmatch(r"\d{4}-\d{2}-\d{2}", prod["priceValidUntil"]) is not None)
chk("картка: повернення 14 днів, доставка від 65 UAH", prod["hasMerchantReturnPolicy"]["merchantReturnDays"] == 14 and prod["shippingDetails"]["shippingRate"]["value"] == "65")
bc = next(x for x in blocks if x["@type"] == "BreadcrumbList")["itemListElement"]
chk("крихти: Головна → Категорія → Товар, позиції 1..3", [i["position"] for i in bc] == [1, 2, 3] and bc[0]["name"] == "Головна"
    and bc[1]["item"].endswith("/category-valizi.html") and "item" not in bc[2])
chk("видимі крихти в HTML, назва категорії екранована", 'class="crumbs"' in h and "Валізи &lt;b&gt;" in h)
chk("JSON-LD без літерального '<' всередині script", all("<" not in s for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', h, re.S)))

prods = [dict(p, id=str(5000 + i), name=f"Іграшка {i}", price=100 + i, desc="") for i in range(30)]
b.write_catalog("Каталог • Валізи", prods, ["Валізи <b>"], {"Валізи <b>": "valizi"}, "category-valizi.html", active="Валізи <b>")
g1 = open(os.path.join(tmp, "category-valizi.html"), encoding="utf-8").read()
g2 = open(os.path.join(tmp, "category-valizi-2.html"), encoding="utf-8").read() if os.path.exists(os.path.join(tmp, "category-valizi-2.html")) else ""
if not g2:
    names2 = [f for f in os.listdir(tmp) if f.startswith("category-valizi") and f != "category-valizi.html"]
    g2 = open(os.path.join(tmp, names2[0]), encoding="utf-8").read() if names2 else ""
chk("категорія: title/description за шаблоном (30 → «30 товарів», мін. ціна 100)",
    "<title>Валізи &lt;b&gt; — купити в Україні | PlutusToys</title>" in g1 and "30 товарів у категорії" in g1 and "ціни від 100 ₴" in g1)
types1 = [x["@type"] for x in ld_blocks(g1)]
chk("категорія: BreadcrumbList + ItemList", "BreadcrumbList" in types1 and "ItemList" in types1)
il = next(x for x in ld_blocks(g1) if x["@type"] == "ItemList")["itemListElement"]
chk("ItemList: перша сторінка = PER_PAGE елементів, позиції з 1", len(il) == b.PER_PAGE and il[0]["position"] == 1)
chk("друга сторінка: title «— сторінка 2 | PlutusToys», позиції продовжуються",
    "— сторінка 2 | PlutusToys</title>" in g2 and next(x for x in ld_blocks(g2) if x["@type"] == "ItemList")["itemListElement"][0]["position"] == b.PER_PAGE + 1)

# ── п.4: favicon, маленький маскот, згода біля кнопки замовлення
for f in ("favicon.ico", "favicon-32.png", "apple-touch-icon.png", "plutus_mascot_s.png"):
    chk(f"site/assets/{f} існує і непорожній", os.path.getsize(os.path.join(HERE, "site", "assets", f)) > 500)
chk("маленький маскот ≤ 60 КБ (було 299 КБ на кожній сторінці)", os.path.getsize(os.path.join(HERE, "site", "assets", "plutus_mascot_s.png")) <= 60 * 1024)
chk("у <head> є icon/apple-touch-icon", all(x in h for x in ('rel="icon" href="assets/favicon.ico"', 'rel="apple-touch-icon" href="assets/apple-touch-icon.png"')))
chk("картка/сторінки: важкий plutus_mascot.png не підвантажується (лише _s)", 'assets/plutus_mascot.png"' not in h.replace(b.SITE_URL + "/assets/plutus_mascot.png", ""))
b.write_cart()
cart = open(os.path.join(tmp, "cart.html"), encoding="utf-8").read()
chk("кошик: згода з офертою й політикою біля кнопки замовлення", 'href="privacy.html"' in cart and 'href="offer.html"' in cart and cart.index("checkout-submit") < cart.index('class="note consent"'))

# ── доставка: одне число з одного джерела (картка «від 65 ₴» = кошик = JSON-LD)
appjs = open(os.path.join(HERE, "site", "assets", "app.js"), encoding="utf-8").read()
chk("app.js: DELIVERY_HINT == build_site.SHIP_FROM_UAH (кошик не розходиться з карткою)", re.search(r"var DELIVERY_HINT = (\d+);", appjs).group(1) == str(b.SHIP_FROM_UAH))
chk("кошик: «від 65 ₴», без «≈ 70»", "від 65 ₴" in cart and "≈ 70" not in cart and "від \"+PT.DELIVERY_HINT" in appjs)
sh = open(os.path.join(HERE, "deploy", "activate_site_apache.sh"), encoding="utf-8").read()
chk("Apache: заголовки безпеки nosniff/SAMEORIGIN/Referrer-Policy під mod_headers", all(x in sh for x in ('X-Content-Type-Options "nosniff"', 'X-Frame-Options "SAMEORIGIN"', 'Referrer-Policy "strict-origin-when-cross-origin"')) and sh.index("mod_headers.c") < sh.index("X-Content-Type-Options"))

print()
if F:
    print("FAILED:", F)
    sys.exit(1)
print("ALL OK")
