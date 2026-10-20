#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_russian_language.py — 20.10.2026 (власник: «російськомовні прибери»): російськомовні товари (Toysi і RoyalToys) на сайті НЕ показуємо.
Лише мовний маркер у назві («рус», «рос», «рос.», «російською…»); двомовні («рос. і укр. мовами») і «змістові» назви («Російські народні казки») лишаються.
(1) детектор; (2) повна збірка: картки з маркером не потрапляють ні в index.json, ні в каталог, ні на сторінки; двомовні й звичайні — потрапляють."""
import contextlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_PICK_DATE"] = "2026-10-10"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402
import site_photo_probe  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


CASES = [
    ('Гра з гудзичками "Шнуруем-комбінуємо", рус', True),
    ('Настільна гра "44 кота. Кет Бенд" (рус)', True),
    ("Інтерактивна іграшка Слонік рос", True),
    ("Настільна гра Irreality 30861 (рос.)", True),
    ("Книга Час Майстерів 150110 рус.", True),
    ("Дитячий сортер Автобус Bambi 7511 озвучення російською мовою", True),
    ("Іграшка з російськомовним озвученням", True),
    ("Музична іграшка, рос. звучення", True),
    ('Настільна гра "Лото" MKC0202 правила гри рос. і укр. мовою', False),
    ("Гра (укр./рос.) для дітей", False),
    ("Книжка Російські народні казки", False),
    ("Конструктор Українські казки", False),
    ("Іграшка-прибиральник Росомаха", False),
    ("Пазл Російська матрьошка", False),
    ("Набір для творчості Руслан", False),
]
bad = [(n, w) for n, w in CASES if bs.is_russian_language_item(n) != w]
chk(f"детектор: усі {len(CASES)} випадків (маркери мови — так; двомовні, змістові «російські казки», імена «Руслан», «Росомаха» — ні)", not bad)
if bad:
    print("   помилки:", bad)

site_photo_probe.run = lambda idx: None


def toysi(pid, name, cat="Різне"):
    return {"id": str(pid), "name": name, "vendor": "T", "price": 250 + pid % 100, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/p/{pid}"}


catalog = {str(i): toysi(i, f"Іграшка {i}") for i in range(1, 31)}
catalog["901"] = toysi(901, "Гра настільна Шнуруем (рус)")
catalog["902"] = toysi(902, "Інтерактивна іграшка Слонік рос")
catalog["903"] = toysi(903, 'Настільна гра "Лото" правила гри рос. і укр. мовами')
catalog["904"] = toysi(904, "Книга Російські народні казки")
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
tmp = Path(tempfile.mkdtemp())
bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")
# RoyalToys
bs.LIQPAY_LIVE = True
GOOD = {"name": "Конструктор Місто 120 деталей", "description": "Гарний набір для дітей від 5 років, містить 120 дрібних деталей для творчості.", "vendor": "Ideika", "vendor_code": "RT-1",
        "category": "Конструкторы", "images": ["https://plutustoys.com.ua/img/rt/1-1.jpg"], "price": 480, "cost": 290, "stock": 7, "supplier": "royaltoys"}
items = {"rt-1": GOOD, "rt-2": dict(GOOD, name="Настільна гра Мафія (рус)"), "rt-3": dict(GOOD, name="Гра Збери слово, правила рос. і укр. мовами")}
(tmp / "s.json").write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(), "items": items}, ensure_ascii=False), encoding="utf-8")
(tmp / "m.json").write_text(json.dumps({"Конструкторы": "Різне"}, ensure_ascii=False), encoding="utf-8")
bs.SUPPLIER_RT_FILE, bs.RT_CATEGORY_MAP_FILE = str(tmp / "s.json"), str(tmp / "m.json")
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()
idx = {x["id"] for x in json.loads((out / "index.json").read_text(encoding="utf-8"))}
chk("збірка: російськомовні Toysi (901, 902) і RoyalToys (rt-2) НЕ в index.json; двомовні (903, rt-3), змістові (904) і звичайні — є", not ({"901", "902", "rt-2"} & idx) and {"903", "904", "rt-1", "rt-3", "1"} <= idx)
chk("сторінок російськомовних товарів нема, решта є", not any((out / f"product-{i}.html").exists() for i in ("901", "902", "rt-2")) and all((out / f"product-{i}.html").exists() for i in ("903", "904", "rt-1", "rt-3")))
cat_txt = "".join(q.read_text(encoding="utf-8") for q in out.glob("category-*.html")) + (out / "catalog.html").read_text(encoding="utf-8")
chk("у каталозі/категоріях посилань на російськомовні нема", "product-901" not in cat_txt and "product-902" not in cat_txt and "product-rt-2" not in cat_txt)
sm = "".join(q.read_text(encoding="utf-8") for q in out.glob("sitemap-*.xml"))
chk("у sitemap російськомовних нема", "/product-901.html" not in sm and "/product-902.html" not in sm and "/product-rt-2.html" not in sm)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
