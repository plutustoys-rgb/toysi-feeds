#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_kv_shadows.py — 18.11.2026 (Код: kv_dup_map/rt_kv_dup_map, SEO 17.11 17:31: canonical БЕЗ ланцюжків — завжди на кінцеву ЖИВУ картку; рішення власника: показувати KIDDISVIT, RoyalToys — тінь).
Тіні в два боки: (1) kv- (dup_of → Toysi) і rt- (dup_of → Toysi) — тінь, поки Toysi в наявності (пріоритет Toysi); (2) rt- (kv_dup_of → kv-<id>) — тінь kv, поки kv у наявності й сама не тінь;
ланцюжок rt → kv → Toysi стискається до Toysi; ціль вичерпана/відсутня/сміттєва → тінь не ставимо."""
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
bs.LIQPAY_LIVE = True
bs.SITE_KV_LIVE = True


def kvi(k, **kw):
    d = {"name": f"Фігурка колекційна «Герой {k}» 10 см", "description": "Колекційна фігурка з вінілу, висота 10 см. Підходить для колекціонування й гри.", "vendor": "Funko", "vendor_code": f"KV-{k}",
         "barcode": "4823099900015", "category": "Герої фільмів та серіалів", "category_path": ["Funko", "Герої фільмів та серіалів"],
         "images": [f"https://plutustoys.com.ua/img/kv/kv-{k}-1.jpg"], "price": 959, "cost": 600.5, "stock": 7, "index": True}
    d.update(kw)
    return d


def rti(k, **kw):
    d = {"name": f"Конструктор «Місто {k}» 120 дет.", "description": "Гарний набір для дітей від 5 років, з великою кількістю деталей.", "vendor": "R", "vendor_code": f"RT-{k}", "category": "Конструкторы",
         "images": [f"https://plutustoys.com.ua/img/rt/rt-{k}-1.jpg"], "price": 480, "cost": 290, "stock": 7, "index": True}
    d.update(kw)
    return d


kv_items = {
    "kv-601": kvi(601, dup_of="5"),            # kv — тінь Toysi 5 (Toysi в наявності)
    "kv-602": kvi(602, dup_of="901"),          # Toysi 901 вичерпаний → kv звичайна
    "kv-603": kvi(603),                        # звичайна kv (ціль для rt)
    "kv-604": kvi(604, dup_of="6"),            # kv — тінь Toysi 6 (ціль для rt → ланцюжок)
    "kv-605": kvi(605, stock=0),               # вичерпана kv (ціль для rt, не публікується в каталозі)
}
rt_items = {
    "rt-701": rti(701, kv_dup_of="kv-603"),                 # → тінь kv-603: canonical product-k603.html
    "rt-702": rti(702, kv_dup_of="kv-604"),                 # kv-604 сама тінь Toysi 6 → canonical одразу product-6.html (без ланцюжка)
    "rt-703": rti(703, kv_dup_of="kv-999"),                 # цілі нема → звичайна
    "rt-704": rti(704, dup_of="7", kv_dup_of="kv-603"),     # і Toysi, і kv → пріоритет Toysi: product-7.html
    "rt-705": rti(705, kv_dup_of="kv-605"),                 # ціль вичерпана → звичайна
    "rt-706": rti(706, kv_dup_of="rt-12"),                  # сміттєве значення → ігнорується
    "rt-707": rti(707, dup_of="8"),                         # старе поведінка: тінь Toysi 8
}
(tmp / "kv.json").write_text(json.dumps({"updated": now.isoformat(), "catalog_n": len(kv_items), "items": kv_items}, ensure_ascii=False), encoding="utf-8")
(tmp / "kvm.json").write_text(json.dumps({"paths": {"Funko > Герої фільмів та серіалів": "Фігурки героїв"}, "brands": {}}, ensure_ascii=False), encoding="utf-8")
(tmp / "rt.json").write_text(json.dumps({"updated": now.isoformat(), "items": rt_items}, ensure_ascii=False), encoding="utf-8")
(tmp / "rtm.json").write_text(json.dumps({"Конструкторы": "Фігурки героїв"}, ensure_ascii=False), encoding="utf-8")
bs.SUPPLIER_KV_FILE, bs.KV_CATEGORY_MAP_FILE = str(tmp / "kv.json"), str(tmp / "kvm.json")
bs.SUPPLIER_RT_FILE, bs.RT_CATEGORY_MAP_FILE = str(tmp / "rt.json"), str(tmp / "rtm.json")


def toysi(pid, name, cat):
    return {"id": str(pid), "name": name, "vendor": "T", "price": 200 + pid % 200, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/p/{pid}"}


catalog = {str(i): toysi(i, f"Іграшка {i}", "Фігурки героїв" if i % 2 else "Машинки") for i in range(1, 41)}
catalog["901"] = toysi(901, "Вичерпаний товар Toysi", "Фігурки героїв")
catalog["901"]["stock"] = 0
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.CATEGORY_GROUPS_FILE = str(tmp / "none.json")
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
err = io.StringIO()
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
    bs.build()


def page(fn):
    q = out / fn
    return q.read_text(encoding="utf-8") if q.exists() else ""


def canon(fn):
    m = re.search(r'<link rel="canonical" href="https://plutustoys\.com\.ua/([^"]+)"', page(fn))
    return m.group(1) if m else None


def noindex(fn):
    return 'content="noindex,follow"' in page(fn)


idx = {x["id"]: x for x in json.loads((out / "index.json").read_text(encoding="utf-8"))}
irt = {x["id"] for x in json.loads((out / "index-rt.json").read_text(encoding="utf-8"))}
sm = "".join(q.read_text(encoding="utf-8") for q in out.glob("sitemap-*.xml"))
chk("kv-601 (dup_of Toysi 5, Toysi в наявності) — тінь: canonical product-5.html, noindex, купити не можна", canon("product-k601.html") == "product-5.html" and noindex("product-k601.html") and "data-add=" not in page("product-k601.html").split("<footer")[0])
chk("kv-602 (Toysi 901 вичерпаний) — звичайна kv: canonical на себе, у index-rt.json, у sitemap-k.xml", canon("product-k602.html") == "product-k602.html" and "kv-602" in irt and "/product-k602.html" in sm)
chk("kv-603 — звичайна, ціль для rt", canon("product-k603.html") == "product-k603.html" and "kv-603" in irt)
chk("kv-604 (dup_of Toysi 6) — тінь Toysi 6", canon("product-k604.html") == "product-6.html")
chk("rt-701 → kv-603: тінь KIDDISVIT, canonical product-k603.html (публічна адреса), noindex", canon("product-rt-701.html" if not bs.PUBLIC_IDS_LIVE else "product-y701.html") == "product-k603.html")
chk("rt-702 → kv-604 (сама тінь Toysi 6): canonical одразу product-6.html — без ланцюжка rt → kv → Toysi", canon("product-rt-702.html") == "product-6.html")
def twin_ok(fn, href):
    body = page(fn).split("<footer")[0]
    return "data-add=" not in body and f'href="{href}">Перейти до товару' in body


chk("тіні (rt→kv, ланцюжок rt→kv→Toysi, rt→Toysi, kv→Toysi): нема data-add/ціни, кнопка «Перейти до товару» веде на кінцеву картку",
    twin_ok("product-rt-701.html" if not bs.PUBLIC_IDS_LIVE else "product-y701.html", "product-k603.html") and twin_ok("product-rt-702.html", "product-6.html")
    and twin_ok("product-rt-704.html", "product-7.html") and twin_ok("product-rt-707.html", "product-8.html") and twin_ok("product-k601.html", "product-5.html") and twin_ok("product-k604.html", "product-6.html"))
chk("rt-703 (ціль kv-999 нема) — звичайна: canonical на себе, не тінь, в index-rt.json", canon("product-rt-703.html") == "product-rt-703.html" and "rt-703" in irt)
chk("rt-704 (і dup_of Toysi 7, і kv_dup_of) — пріоритет Toysi: canonical product-7.html", canon("product-rt-704.html") == "product-7.html")
chk("rt-705 (kv-605 вичерпана) і rt-706 (kv_dup_of сміттєве) — звичайні", canon("product-rt-705.html") == "product-rt-705.html" and canon("product-rt-706.html") == "product-rt-706.html" and {"rt-705", "rt-706"} <= irt)
chk("rt-707 (dup_of Toysi 8) — як і раніше тінь Toysi: canonical product-8.html", canon("product-rt-707.html") == "product-8.html")
shadows = ["kv-601", "kv-604", "rt-701", "rt-702", "rt-704", "rt-707"]
chk("усі тіні: у повному index.json з dup=1, НЕ в index-rt.json/index-t.json, НЕ в sitemap", all(idx[s].get("dup") == 1 and s not in irt for s in shadows)
    and not any(re.search(rf"/product-({re.escape(bs.public_slug(s))})\.html", sm) for s in shadows))
chk("жодна тінь не показана в категорії/каталозі (немає посилань на їхні сторінки)", not any(f"product-{bs.public_slug(s)}.html" in "".join(q.read_text(encoding="utf-8") for q in list(out.glob("category-*.html")) + [out / "catalog.html"]) for s in shadows))
chk("у логу збірки: «Дублі: 6 карток-тіней… RT→KIDDISVIT 2»", "6 карток-тіней" in err.getvalue() and "RT→KIDDISVIT 2" in err.getvalue())
print("\n" + ("ALL OK" if not F else f"FAIL: {len(F)}"))
sys.exit(1 if F else 0)
