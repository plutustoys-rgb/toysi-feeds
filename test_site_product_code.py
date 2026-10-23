#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_product_code.py — 23.10.2026 (власник: «код товару з власним кодом»): на картці рядок «Код товару: PT-…»; Toysi — PT-<id>, RoyalToys (rt-X) — PT-RX.
Лише показ: id (ключ кошика, замовлень, фідів) і адреса сторінки не міняються; data-add несе старий id."""
import os
import sys
import tempfile
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


chk("Toysi: 176003 -> PT-176003", bs.site_product_code("176003") == "PT-176003")
chk("RoyalToys: rt-A12 -> PT-RA12", bs.site_product_code("rt-A12") == "PT-RA12")
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
base = {"name": "Іграшка", "category": "Кат", "price": 250, "contribution": 100, "stock": 3, "photo": "", "desc": "x"}
t = dict(base, id="9701")
r = dict(base, id="rt-Z9", rt=1)
o = dict(base, id="9702", stock=0)
for x in (t, r, o):
    bs.write_product(x, related=None, cat_slug={}, similar=[])
pt = (out / "product-9701.html").read_text(encoding="utf-8")
pr = (out / "product-rt-Z9.html").read_text(encoding="utf-8")
po = (out / "product-9702.html").read_text(encoding="utf-8")
chk("Toysi: рядок рівно один, код у <b class=\"sku-code\">, кнопка «Копіювати» з data-copy", pt.count('<b class="sku-code">PT-9701</b>') == 1 and pt.count('data-copy="PT-9701"') == 1 and pt.count('class="sku"') == 1 and 'type="button" class="sku-copy"' in pt and 'role="status"' in pt)
chk("RoyalToys: рядок PT-RZ9 і кнопка копіювання", pr.count('<b class="sku-code">PT-RZ9</b>') == 1 and pr.count('data-copy="PT-RZ9"') == 1)
chk("без залишку: код теж є (покупець бачить, який товар)", '<b class="sku-code">PT-9702</b>' in po)
chk("рядок між заголовком і ціною", pt.index('class="prod"') < pt.index('class="sku"') < pt.index('class="price-row"'))
chk("id у кошику не змінено", '"id": "9701"' in pt or '"id":"9701"' in pt or '&quot;id&quot;: &quot;9701&quot;' in pt)
chk("sku у JSON-LD лишається id", '"sku": "9701"' in pt)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
