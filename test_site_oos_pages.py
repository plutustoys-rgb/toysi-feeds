#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_oos_pages.py — 11.10.2026 (рішення Консультанта за Search Console: 7 605 з 8 960 непроіндексованих адрес — «404», це товари, що зникли зі складу
Toysi, бо build_site.py викидав їх зі збірки). Інваріанти:
  • товар БЕЗ залишку (stock=0), що має назву+фото і не заборонений/не UNO, отримує ЖИВУ сторінку product-<id>.html: «Немає в наявності»,
    schema.org/OutOfStock, кнопка вимкнена (нема data-add), блок «зараз немає в наявності» з посиланням на категорію (або на каталог, якщо в категорії
    нічого нема в наявності), title «— немає в наявності», description починається «Немає в наявності.»;
  • такі сторінки лишаються в sitemap (priority 0.3) і НЕ видаляються кроком прибирання застарілих;
  • у каталог, категорії, головну, крос-сел і index.json (пошук, фіди, ціни для замовлення) вони НЕ потрапляють;
  • без фото / заборонене / UNO без залишку — як і раніше без сторінки.
Самодостатній: build() запускається на синтетичному каталозі в тимчасовій теці, мережа не потрібна."""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID", "SITE_BUILD_LIMIT"):
    os.environ.pop(k, None)
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def item(pid, name, category, stock=5, pics=True, vendor="Test", price=300.0):
    return {"id": str(pid), "name": name, "vendor": vendor, "price": price, "stock": stock, "category_name": category, "category_id": "10",
            "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"] if pics else [], "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.",
            "barcode": ""}


catalog = {}
for i in range(1, 41):                                         # 40 у наявності у двох категоріях
    catalog[str(i)] = item(i, f"Іграшка номер {i}", "Машинки" if i % 2 else "Ляльки", stock=3 + i % 4, price=250.0 + i)
catalog["501"] = item(501, "Машинка закінчилась", "Машинки", stock=0)
catalog["502"] = item(502, "Лялька закінчилась", "Ляльки", stock="0")
catalog["503"] = item(503, "Тільки без залишку в категорії", "Порожня категорія", stock=0)           # у категорії нічого нема в наявності
catalog["504"] = item(504, "Без фото і без залишку", "Машинки", stock=0, pics=False)
catalog["505"] = item(505, "Сувенірний меч без залишку", "Мечі, ножі та шаблі", stock=0)             # forbidden_products
catalog["506"] = item(506, "Карткова гра UNO Kids", "Машинки", stock=0, vendor="MiC")                  # ТМ UNO
catalog["507"] = item(507, "Назва <b>&\"</b> зі спецсимволами", "Машинки", stock=0)

out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.tp.fetch_toysi_catalog = lambda: catalog
bs.LIMIT = 0
import site_photo_probe  # noqa: E402
site_photo_probe.run = lambda idx: None                        # без мережі
(out / "product-9999999.html").write_text("stale", encoding="utf-8")        # застаріла сторінка (товару вже нема в каталозі) — має зникнути
(out / "product-501.html").write_text("old oos page from earlier build", encoding="utf-8")   # раніше написана OOS-сторінка — переписується, не видаляється
bs.build()

# ── сторінки
for pid in ("501", "502", "503", "507"):
    chk(f"сторінка товару без залишку {pid} існує", (out / f"product-{pid}.html").exists())
for pid, why in (("504", "без фото"), ("505", "заборонений"), ("506", "UNO")):
    chk(f"{pid} ({why}, без залишку) сторінки НЕ має", not (out / f"product-{pid}.html").exists())
chk("застаріла сторінка неіснуючого товару прибрана", not (out / "product-9999999.html").exists())
chk("сторінка 501 переписана свіжою збіркою (не «old oos…»)", "old oos page" not in (out / "product-501.html").read_text(encoding="utf-8"))

h = (out / "product-501.html").read_text(encoding="utf-8")
chk("OOS: JSON-LD availability = OutOfStock (не InStock)", "schema.org/OutOfStock" in h and "schema.org/InStock" not in h)
chk("OOS: «Немає в наявності» в блоці ціни", 'class="avail oos">Немає в наявності' in h)
chk("OOS: кнопка вимкнена і без data-add (покласти в кошик не можна)", '<button class="btn" disabled>Немає в наявності</button>' in h and "data-add" not in h)
chk("OOS: блок oos-note з посиланням на категорію (є товари в наявності)", 'class="oos-note"' in h and 'Подивитися схожі в категорії «Машинки»' in h and 'href="category-' in h)
chk("OOS: title «— немає в наявності»", re.search(r"<title>[^<]*немає в наявності \| PlutusToys</title>", h) is not None)
chk("OOS: meta description починається «Немає в наявності.»", re.search(r'<meta name="description" content="Немає в наявності\.', h) is not None)
chk("OOS: canonical на себе і індексується (немає noindex)", 'rel="canonical" href="https://plutustoys.com.ua/product-501.html"' in h and "noindex" not in h)
chk("OOS: без блоку крос-селу «Додайте до замовлення»", "Додайте до замовлення" not in h)
h503 = (out / "product-503.html").read_text(encoding="utf-8")
chk("OOS у категорії без наявних: посилання веде на каталог, а не на неіснуючу категорію", 'href="catalog.html">Подивитися схожі в каталозі' in h503)
h507 = (out / "product-507.html").read_text(encoding="utf-8")
chk("спецсимволи в назві екрановані (немає літерального <b> у тілі й JSON-LD)", "<b>&\"</b>" not in h507 and "\\u003cb>" in h507)
h1 = (out / "product-1.html").read_text(encoding="utf-8")
chk("товар у наявності: InStock, кнопка активна, title «купити»", "schema.org/InStock" in h1 and "data-add" in h1 and "— купити | PlutusToys" in h1 and 'class="oos-note"' not in h1)

# ── index.json / каталог / головна / sitemap
idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
ids = {str(p["id"]) for p in idx}
chk("index.json (пошук, ціни замовлень, фіди) містить лише товари в наявності: 40, без 50x", len(ids) == 40 and not any(i.startswith("50") for i in ids))
listing = "".join(p.read_text(encoding="utf-8") for p in out.glob("catalog*.html")) + "".join(p.read_text(encoding="utf-8") for p in out.glob("category-*.html")) \
    + (out / "index.html").read_text(encoding="utf-8")
chk("у каталозі/категоріях/головній немає посилань на товари без залишку", not re.search(r"product-50[1-7]\.html", listing))
chk("немає сторінки категорії для категорії, де все закінчилось", not (out / "category-porozhnia-kategoriia.html").exists() and not list(out.glob("category-*porozh*")))
for p in out.glob("product-*.html"):
    if p.name.startswith("product-1.html"):
        pass
rel_leak = [p.name for p in out.glob("product-*.html") if re.search(r"product-50[1-7]\.html", p.read_text(encoding="utf-8")) and p.name not in ("product-501.html", "product-502.html", "product-503.html", "product-507.html")]
chk("крос-сел на сторінках у наявності не пропонує товар без залишку", not rel_leak)
sm = (out / "sitemap.xml").read_text(encoding="utf-8")
chk("sitemap: OOS-сторінки є з priority 0.3", all(f"/product-{i}.html</loc>" in sm for i in ("501", "502", "503", "507"))
    and re.search(r"product-501\.html</loc>[^\n]*<priority>0\.3</priority>", sm) is not None)
chk("sitemap: товари в наявності лишились з priority 0.6", re.search(r"product-1\.html</loc>[^\n]*<priority>0\.6</priority>", sm) is not None)
chk("sitemap: немає сторінок без фото/заборонених/UNO", not re.search(r"product-50[456]\.html", sm))

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ OOS-сторінки — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
