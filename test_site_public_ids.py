#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_public_ids.py — 16.11.2026 (ворота SITE_PUBLIC_IDS_LIVE: нейтральні публічні адреси RoyalToys).

Раніше ці перевірки жили в test_site_dropt.py; Dropt прибрано з сайту (рішення власника 16.11.2026), а загальний код нейтральних адрес лишається — тож покриття перенесено сюди на RoyalToys:
(1) public_slug / product_file / site_product_code / public_photo: ворота вимкнені — як було; увімкнені — rt-69616 -> y69616, фото /img/rt/… -> /img/p/y…, Toysi без змін, сміття не перетворюється;
(2) повна збірка з воротами: product-y101.html, canonical, JSON-LD url/sku, код PT-Y101, фото /img/p/y101-1.jpg, поле u в index.json, сторінка категорії посилається на product-y101;
(3) відкат воріт у тому самому каталозі: product-y*.html зникають, є product-rt-101.html;
(4) застарілі sitemap-dropt.xml / sitemap-more.xml (від збірок часів Dropt) прибираються при будь-якому стані воріт."""
import contextlib
import io
import json
import os
import re
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


site_photo_probe.run = lambda idx: None
tmp = Path(tempfile.mkdtemp())
now = datetime.now(timezone.utc)

chk("ворота вимкнені (типово): public_slug/site_product_code/public_photo — як було", (not bs.PUBLIC_IDS_LIVE) and bs.public_slug("rt-69616") == "rt-69616"
    and bs.product_file("rt-69616") == "product-rt-69616.html" and bs.site_product_code("rt-69616") == "PT-R69616" and bs.site_product_code("218158") == "PT-218158"
    and bs.public_photo("rt-69616", "https://plutustoys.com.ua/img/rt/rt-69616-1.jpg") == "https://plutustoys.com.ua/img/rt/rt-69616-1.jpg")
_pi_old = bs.PUBLIC_IDS_LIVE
bs.PUBLIC_IDS_LIVE = True
try:
    chk("ворота: rt-69616 -> y69616, rt-18636s22164 -> y18636s22164; Toysi — як є; сміття не перетворюється",
        bs.public_slug("rt-69616") == "y69616" and bs.public_slug("rt-18636s22164") == "y18636s22164" and bs.public_slug("218158") == "218158"
        and bs.public_slug("rt-") == "rt-" and bs.public_slug("dropt-123") == "dropt-123" and bs.public_slug("zb-5") == "zb-5")
    chk("ворота: адреса/код — без імені постачальника (PT-Y…, product-y…); код зберігає регістр id (PT-Yabc12S3)",
        bs.product_file("rt-69616") == "product-y69616.html" and bs.site_product_code("rt-69616") == "PT-Y69616" and bs.site_product_code("218158") == "PT-218158"
        and bs.site_product_code("rt-abc12S3") == "PT-Yabc12S3")
    chk("ворота: фото нашого сервера переписується на /img/p/<slug>-N.jpg; чуже фото, чужий id, Toysi — без змін",
        bs.public_photo("rt-69616", "https://plutustoys.com.ua/img/rt/rt-69616-1.jpg") == "https://plutustoys.com.ua/img/p/y69616-1.jpg"
        and bs.public_photo("rt-69616", "https://plutustoys.com.ua/img/rt/rt-999-1.jpg") == "https://plutustoys.com.ua/img/rt/rt-999-1.jpg"
        and bs.public_photo("218158", "https://toysi.ua/p/2/enl_218158.jpg") == "https://toysi.ua/p/2/enl_218158.jpg"
        and bs.public_photo("rt-5", "https://plutustoys.com.ua/img/dropt/rt-5-1.jpg") == "https://plutustoys.com.ua/img/dropt/rt-5-1.jpg")

    def toysi(pid, name, cat):
        return {"id": str(pid), "name": name, "vendor": "T", "price": 200 + pid % 200, "stock": 5, "category_name": cat, "category_id": "10",
                "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"], "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.",
                "params": {}, "url": f"https://toysi.ua/p/{pid}"}

    GOOD = {"name": "Конструктор «Місто» 120 дет.", "description": "Набір для складання. Оплата при отриманні на відділенні.", "vendor": "RT", "vendor_code": "RT-77",
            "category": "Конструкторы", "images": ["https://plutustoys.com.ua/img/rt/rt-101-1.jpg"], "price": 480, "cost": 290, "stock": 7, "checked_at": now.isoformat()}
    (tmp / "s.json").write_text(json.dumps({"updated": now.isoformat(), "items": {"rt-101": GOOD}}, ensure_ascii=False), encoding="utf-8")
    (tmp / "m.json").write_text(json.dumps({"Конструкторы": "Конструктори RT"}, ensure_ascii=False), encoding="utf-8")
    bs.SUPPLIER_RT_FILE, bs.RT_CATEGORY_MAP_FILE = str(tmp / "s.json"), str(tmp / "m.json")
    bs.LIQPAY_LIVE = True
    catalog = {str(i): toysi(i, f"Іграшка {i}", "Конструктори RT" if i % 2 else "Машинки") for i in range(1, 41)}
    bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
    bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
    bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")
    out = Path(tempfile.mkdtemp())
    # застарілі sitemap-файли від збірок часів Dropt (до 16.11.2026) мають зникнути за будь-якого стану воріт
    (out / "sitemap-dropt.xml").write_text("<urlset/>", encoding="utf-8")
    (out / "sitemap-more.xml").write_text("<urlset/>", encoding="utf-8")
    bs.OUT = str(out)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bs.build()
    h = (out / "product-y101.html").read_text(encoding="utf-8")
    ld = h.split("application/ld+json", 1)[-1]
    chk("ворота: картка RoyalToys лежить за product-y101.html; product-rt-*.html нема", (out / "product-y101.html").exists() and not any(f.name.startswith("product-rt-") for f in out.iterdir()))
    chk("ворота: canonical, JSON-LD url/sku, код на картці — без «rt-»/«PT-R»", 'rel="canonical" href="https://plutustoys.com.ua/product-y101.html"' in h and '"url": "https://plutustoys.com.ua/product-y101.html"' in ld
        and '"sku": "y101"' in ld and "PT-Y101" in h and "PT-R101" not in h)
    chk("ворота: фото картки /img/p/y101-1.jpg, шляху /img/rt/ на сторінці нема", "img/p/y101-1.jpg" in h and "img/rt/" not in h)
    ix = json.loads((out / "index.json").read_text(encoding="utf-8"))
    ix = {x["id"]: x for x in ix} if isinstance(ix, list) else ix
    chk("ворота: index.json має поле u (публічний slug) для RoyalToys, внутрішній id лишається; для Toysi поля u нема",
        ix["rt-101"].get("u") == "y101" and all("u" not in x for k, x in ix.items() if not k.startswith("rt-")))
    cat = next((v for v in (f.read_text(encoding="utf-8") for f in out.glob("category-*.html")) if "product-y101" in v), "")
    chk("ворота: сторінка категорії посилається на product-y101.html, не на product-rt-101", bool(cat) and "product-rt-101" not in cat)
    chk("застарілі sitemap-dropt.xml / sitemap-more.xml прибрано (ворота ввімкнені)", not (out / "sitemap-dropt.xml").exists() and not (out / "sitemap-more.xml").exists())
    bs.PUBLIC_IDS_LIVE = False
    (out / "sitemap-dropt.xml").write_text("<urlset/>", encoding="utf-8")
    (out / "sitemap-more.xml").write_text("<urlset/>", encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bs.build()   # відкат У ТОМУ САМОМУ каталозі: файли воріт мають зникнути
    chk("відкат воріт: немає product-y*.html, є product-rt-101.html", not any(f.name.startswith("product-y") for f in out.iterdir()) and (out / "product-rt-101.html").exists())
    chk("застарілі sitemap-dropt.xml / sitemap-more.xml прибрано й при вимкнених воротах", not (out / "sitemap-dropt.xml").exists() and not (out / "sitemap-more.xml").exists())
finally:
    bs.PUBLIC_IDS_LIVE = _pi_old
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
