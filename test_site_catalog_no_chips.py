#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_catalog_no_chips.py — 14.10.2026 (власник): у повному каталозі (catalog.html і його сторінки) рядок чипів-категорій прибрано — є панель сортування/фільтрів із вибором категорії.
На сторінках КАТЕГОРІЙ чипи лишаються (навігація між категоріями)."""
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


IMG = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
prods = [{"id": str(2000 + i), "name": f"Іграшка «Л{i}»", "category": f"Категорія{i % 3}", "price": 200 + i, "contribution": 100, "stock": 5, "photo": IMG, "desc": ""} for i in range(60)]
cats = {}
for p in prods:
    cats.setdefault(p["category"], []).append(p)
cl = sorted(cats, key=lambda c: -len(cats[c]))
cs = {c: f"c{i}" for i, c in enumerate(cl)}
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
w1 = bs.write_catalog("Каталог іграшок", prods, cl, cs, "catalog.html", active=None)
w2 = bs.write_catalog(f"Каталог • {cl[0]}", cats[cl[0]], cl, cs, f"category-{cs[cl[0]]}.html", active=cl[0])
cat = (out / "catalog.html").read_text(encoding="utf-8")
cat2 = (out / sorted(w1)[1]).read_text(encoding="utf-8")
catg = (out / f"category-{cs[cl[0]]}.html").read_text(encoding="utf-8")
chk("catalog.html: рядка чипів-категорій (<div class=chips>) НЕМАЄ", 'class="chips' not in cat.split("<footer")[0].split('<h1 class="page"')[0])
chk("друга сторінка каталогу — теж без рядка чипів", 'class="chips' not in cat2.split("<footer")[0].split('<h1 class="page"')[0])
chk("панель сортування/фільтрів каталогу з випадним списком «Категорія» (#cf-cat) на місці", 'id="cf-cat"' in cat and 'id="cf-sort"' in cat)
chk("сторінка категорії: чипи-категорії ЛИШИЛИСЬ (навігація)", 'class="chips' in catg and 'class="chip active"' in catg)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
