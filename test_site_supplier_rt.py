#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_supplier_rt.py — 19.10.2026 (Код: пілот RoyalToys у ЗАГАЛЬНОМУ каталозі; власник: «продумайте вимкнення накладеного платежа» для цих товарів).
(1) site/supplier_rt.json + rt_category_map.json зливаються в каталог (id «rt-…», ціна сайту й опт з файлу, фото https, укр. опис без «накладений/при отриманні»); невідома категорія/нема фото/застарілий файл/нема онлайн-оплати → не публікуємо, голосно;
(2) повна збірка: product-rt-<id>.html, index.json (pr = ціна з файлу), у категорії/sitemap; (3) браузер: у кошику з rt-позицією «накладений платіж» неактивний, обрана картка онлайн, пояснення; без rt — як було; змішаний кошик — окремі посилки."""
import contextlib
import functools
import http.server
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
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


DEFAULT_RT_FILE = bs.SUPPLIER_RT_FILE   # шлях за замовчуванням ДО підміни в тесті
site_photo_probe.run = lambda idx: None
tmp = Path(tempfile.mkdtemp())
now = datetime.now(timezone.utc)
GOOD = {"name": "Конструктор «Місто» 120 дет.", "description": "Гарний набір. Оплата при отриманні на відділенні. Підходить для дітей від 5 років, містить 120 дрібних деталей. Накладений платіж можливий.", "vendor": "Ideika",
        "vendor_code": "RT-77", "category": "Конструкторы", "images": ["https://plutustoys.com.ua/img/rt/101-1.jpg"], "price": 480, "cost": 290, "stock": 7, "supplier": "royaltoys"}


def write_files(items, updated=None, cmap=None):
    (tmp / "s.json").write_text(json.dumps({"updated": (updated or now).isoformat(), "items": items}, ensure_ascii=False), encoding="utf-8")
    (tmp / "m.json").write_text(json.dumps(cmap if cmap is not None else {"Конструкторы": "Конструктори RT"}, ensure_ascii=False), encoding="utf-8")
    bs.SUPPLIER_RT_FILE, bs.RT_CATEGORY_MAP_FILE = str(tmp / "s.json"), str(tmp / "m.json")


def load(items, **kw):
    write_files(items, **kw)
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        res = bs._load_supplier_rt()
    return res, err.getvalue()


# опт (cost) не має віддаватись публічно (Код 19.10): файл ПОЗА DocumentRoot (site/), не в git, Apache його ще й забороняє
site_dir = (HERE / "site").resolve()
rt_path = Path(DEFAULT_RT_FILE).resolve()
chk("supplier_rt.json за замовчуванням лежить у корені репо, а НЕ в site/ (DocumentRoot)", rt_path == (HERE / "supplier_rt.json").resolve() and site_dir not in rt_path.parents)
gi = (HERE / ".gitignore").read_text(encoding="utf-8")
chk("supplier_rt.json і site/img/rt/ у .gitignore", re.search(r"^/supplier_rt\.json\s*$", gi, re.M) is not None and re.search(r"^/site/img/rt/\s*$", gi, re.M) is not None)
apa = (HERE / "deploy" / "activate_site_apache.sh").read_text(encoding="utf-8")
chk("Apache забороняє віддавати supplier_rt.json навіть якщо його покладуть у site/", re.search(r'<FilesMatch "\^supplier_rt\\.json\$">\s*Require all denied', apa) is not None)
js = (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8")
chk("замовлення з rt-позицією явно йде з payment_method=prepaid (app.js), навіть якщо радіо не обране", re.search(r'some\(function\(k\)\{ return /\^rt-/\.test\(k\); \}\)\) payment="prepaid"', js) is not None)
bs.LIQPAY_LIVE = True
r, e = load({"rt-101": GOOD})
it = r.get("rt-101", {})
chk("валідна картка зливається: id rt-101, категорія з мапи, фото https, ціна сайту й опт із файлу, залишок", it.get("category_name") == "Конструктори RT" and it.get("site_price") == 480 and it.get("rt_cost") == 290 and it.get("stock") == 7 and it.get("pictures") == GOOD["images"] and e == "")
chk("price_of бере ціну з файлу (без 1.5× від Toysi)", bs.price_of(it) == 480)
chk("опис без речень про накладений платіж/оплату при отриманні, решта на місці", "отриман" not in it["description"] and "накладен" not in it["description"].lower() and "Гарний набір." in it["description"] and "від 5 років" in it["description"])
r, e = load({"rt-1": dict(GOOD, category="Невідома"), "rt-2": dict(GOOD, images=[]), "rt-3": dict(GOOD, price=0), "bad id": GOOD, "rt-4": dict(GOOD, name=" ")})
chk("невідома категорія/нема фото/ціна 0/поганий id/нема назви — НЕ публікуються, у stderr є пояснення", r == {} and "supplier_rt.json: не опубліковано" in e and "category" in e)
r, e = load({"rt-101": GOOD}, updated=now - timedelta(days=4))
chk("файл старіший за 3 доби → порожньо, голосно (ціни/залишки не свіжі)", r == {} and "застарів" in e)
bs.LIQPAY_LIVE = False
r, e = load({"rt-101": GOOD})
chk("без ввімкненої онлайн-оплати картки не публікуємо (лише передоплата)", r == {} and "онлайн-оплата не ввімкнена" in e)
bs.LIQPAY_LIVE = True
bs.SUPPLIER_RT_FILE = str(tmp / "nope.json")
chk("файлу нема → порожньо, без шуму", bs._load_supplier_rt() == {})

# (2) повна збірка
write_files({"rt-101": GOOD, "rt-102": dict(GOOD, name="Набір кубиків дерев'яних 50 шт", vendor_code="RT-78", price=350, cost=210, stock=0, images=["https://plutustoys.com.ua/img/rt/102-1.jpg"]), "rt-103": dict(GOOD, name="Набір фігурок 12 шт", index=False, vendor_code="RT-79"), "rt-104": dict(GOOD, name="Брязкальце зі звуком", description="Коротко.")})


def toysi(pid, name, cat):
    return {"id": str(pid), "name": name, "vendor": "T", "price": 200 + pid % 200, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/p/{pid}"}


catalog = {str(i): toysi(i, f"Іграшка {i}", "Конструктори RT" if i % 2 else "Машинки") for i in range(1, 41)}
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()
idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
row = next((x for x in idx if x["id"] == "rt-101"), None)
chk("index.json: rt-101 з ціною сайту 480 (pr); rt-102 (залишок 0) НЕ в індексі", row is not None and row["pr"] == 480 and not any(x["id"] == "rt-102" for x in idx))
pg = out / "product-rt-101.html"
h = pg.read_text(encoding="utf-8") if pg.exists() else ""
chk("сторінка product-rt-101.html є, ціна 480, фото з нашого сервера, постачальника покупець не бачить", pg.exists() and "480" in h and "img/rt/101-1.jpg" in h and "royaltoys" not in h.lower())
p2 = out / "product-rt-102.html"
chk("сторінка товару без залишку rt-102: є (OutOfStock), без оплати при отриманні", p2.exists() and "OutOfStock" in p2.read_text(encoding="utf-8") and "при отриманні" not in p2.read_text(encoding="utf-8").split("<footer")[0].lower())
cat_files = [p for p in out.glob("category-*.html") if "rt-101" in p.read_text(encoding="utf-8")]
chk("картка потрапила в сторінку категорії «Конструктори RT»", len(cat_files) >= 1)
sm = "".join(p.read_text(encoding="utf-8") for p in out.glob("sitemap-*.xml"))
chk("у sitemap є product-rt-101.html рівно раз", sm.count("/product-rt-101.html") == 1)
smrt = (out / "sitemap-rt.xml").read_text(encoding="utf-8") if (out / "sitemap-rt.xml").exists() else ""
smp = (out / "sitemap-products.xml").read_text(encoding="utf-8")
chk("sitemap-rt.xml: лише індексовані rt (101); хвиля не відкрита (103: index=false) і тонкий опис (104) — НЕ в sitemap; у products/priority rt нема", "/product-rt-101.html" in smrt and "/product-rt-103.html" not in smrt and "/product-rt-104.html" not in smrt and "product-rt-" not in smp and "product-rt-" not in (out / "sitemap-priority.xml").read_text(encoding="utf-8"))
h103 = (out / "product-rt-103.html").read_text(encoding="utf-8")
h104 = (out / "product-rt-104.html").read_text(encoding="utf-8")
chk("rt-103 (index=false) і rt-104 (опис <50 знаків): <meta robots noindex,follow>; rt-101 — без noindex", 'content="noindex,follow"' in h103 and 'content="noindex,follow"' in h104 and 'noindex' not in h.split("</head>")[0])
home = (out / "index.html").read_text(encoding="utf-8")
chk("rt-картки НЕ потрапляють на головну (каруселі, добірки) і в кошикові add-on", "product-rt-" not in home and "product-rt-" not in (out / "cart.html").read_text(encoding="utf-8"))
ix = {x["id"]: x for x in idx}
chk("index.json: rt-запис має rt=1 і ix (1 — індексується, 0 — ні) для фідів; у звичайного товару цих полів нема", ix["rt-101"].get("rt") == 1 and ix["rt-101"].get("ix") == 1 and ix["rt-103"].get("ix") == 0 and ix["rt-104"].get("ix") == 0 and "rt" not in ix["5"] and "ix" not in ix["5"])
cat_html = [q.read_text(encoding="utf-8") for q in out.glob("category-*.html") if "product-rt-101" in q.read_text(encoding="utf-8")]
order = re.findall(r'"url": "https://plutustoys.com.ua/product-([^"]+)\.html"', cat_html[0]) if cat_html else []
chk("у категорії rt-картки стоять ПІСЛЯ Toysi (rec_order, ItemList)", "rt-101" in order and all(not x.startswith("rt-") for x in order[:order.index("rt-101")]) and order.index("rt-101") >= 1)

# (3) браузер: кошик
cart_html = (out / "cart.html").read_text(encoding="utf-8")
chk("розмітка кошика: є блок пояснення #rt-pay-note, прихований за замовчуванням", re.search(r'id="rt-pay-note" hidden', cart_html) is not None)
shutil.copytree(HERE / "site" / "assets", out / "assets", dirs_exist_ok=True)
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
from playwright.sync_api import sync_playwright  # noqa: E402

STATE = "(()=>{const c=document.querySelector('input[name=payment][value=cod]'), p=document.querySelector('input[name=payment][value=prepaid]'), n=document.getElementById('rt-pay-note'); return {cod_disabled:c?c.disabled:null, cod_checked:c?c.checked:null, pre_checked:p?p.checked:null, note_hidden:n.hidden, note:n.textContent, off:c.parentNode.classList.contains('is-off')}})()"
RTI = {"id": "rt-101", "name": "Конструктор", "price": 480, "photo": "", "qty": 1}
PLI = {"id": "5", "name": "T", "price": 300, "photo": "", "qty": 1}
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for label, cart, want in (("з rt-позицією", {"rt-101": RTI}, "rt"), ("без rt (звичайний кошик)", {"5": PLI}, "plain"), ("змішаний", {"rt-101": RTI, "5": PLI}, "mixed")):
        pg = br.new_page(viewport={"width": 390, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.add_init_script(f"localStorage.setItem('pt_cart_v1', JSON.stringify({json.dumps(cart)}))")
        pg.goto(f"{base}/cart.html", wait_until="load")
        pg.wait_for_timeout(400)
        s = pg.evaluate(STATE)
        if want == "plain":
            chk(f"кошик {label}: накладений платіж активний, пояснення нема", s["cod_disabled"] is False and s["note_hidden"] is True and s["off"] is False)
        else:
            chk(f"кошик {label}: накладений платіж неактивний, обрана картка онлайн, пояснення видиме", s["cod_disabled"] is True and s["cod_checked"] is False and s["pre_checked"] is True and s["note_hidden"] is False and "передоплат" in s["note"] and s["off"] is True)
            chk(f"кошик {label}: згадка про окремі посилки лише у змішаному", ("окремими посилками" in s["note"]) == (want == "mixed"))
        chk(f"кошик {label}: без помилок JS", errs == [])
        pg.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
