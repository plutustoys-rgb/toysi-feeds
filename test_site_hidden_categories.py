#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_hidden_categories.py — 22.10.2026 (SEO; рішення власника «приберай, якщо зайве»): категорії «Азартні» (звичайні настільні ігри — Google читав як азартні) і «Арбалети і луки» (іграшкова зброя):
(1) сторінка категорії ЖИВА, але meta robots noindex,follow; (2) нема в sitemap-categories / sitemap-priority; (3) нема в хабі /categories.html, групах/сайдбарі, чипах, «Схожих категоріях», у списку категорій
фільтра (data-hide) і в cat-urls; (4) товари «Азартні» лишаються звичайними (в sitemap-products, індексуються); товари «Арбалети і луки» — noindex і поза sitemap-products; сторінки товарів живі.
Решта категорій не зачеплена. Використовує реальний site/category_groups.json (обидві категорії там є)."""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
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
DESC = "Опис товару достатньої довжини для картки сайту, понад сорок символів."


def toysi(pid, name, cat, price=300):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": DESC, "params": {}, "url": "x"}


catalog = {}
n = 1
for cat, cnt in (("Азартні", 4), ("Арбалети і луки", 3), ("Ляльки", 8), ("Машинки", 8), ("Настільні ігри", 6)):
    for i in range(cnt):
        catalog[str(n)] = toysi(n, f"{cat} товар {i}", cat, price=200 + n)
        n += 1
IDS = {c: [k for k, v in catalog.items() if v["category_name"] == c] for c in ("Азартні", "Арбалети і луки", "Ляльки")}
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()


def read(name):
    return (out / name).read_text(encoding="utf-8")


az, ar, lyal = bs.slugify("Азартні"), bs.slugify("Арбалети і луки"), bs.slugify("Ляльки")
# (1) сторінки живі й noindex
for slug, label in ((az, "Азартні"), (ar, "Арбалети і луки")):
    h = read(f"category-{slug}.html")
    chk(f"«{label}»: сторінка категорії жива, meta robots noindex,follow", 'name="robots" content="noindex,follow"' in h and "<h1" in h)
chk("«Ляльки» (звичайна категорія): без noindex", "noindex" not in read(f"category-{lyal}.html"))
# (2) sitemap
smc = read("sitemap-categories.xml") if (out / "sitemap-categories.xml").exists() else ""
smp = read("sitemap-priority.xml") if (out / "sitemap-priority.xml").exists() else ""
chk("sitemap-categories і sitemap-priority: «Азартні» й «Арбалети і луки» немає", all(f"category-{x}.html" not in smc + smp for x in (az, ar)))
chk("звичайна категорія (Ляльки) у sitemap-categories на місці", f"category-{lyal}.html" in smc + smp)
# (3) навігація
hub = read("categories.html")
chk("хаб /categories.html: обох категорій нема, звичайні є", all(f"category-{x}.html" not in hub for x in (az, ar)) and f"category-{lyal}.html" in hub)
cat = read("catalog.html")
chk("catalog.html (сайдбар груп, cat-urls, чипи): посилань на обидві категорії нема", all(f"category-{x}.html" not in cat for x in (az, ar)))
chk("фільтр категорій: data-hide містить обидві назви (браузер не покаже їх у списку)", 'data-hide=\'["Азартні", "Арбалети і луки"]\'' in cat)
for slug in (lyal, bs.slugify("Машинки")):
    h = read(f"category-{slug}.html")
    chk(f"сторінка категорії {slug}: чипи/«Схожі»/cat-urls без прихованих", all(f"category-{x}.html" not in h for x in (az, ar)))
groups_pages = "".join(p.read_text(encoding="utf-8") for p in out.glob("group-*.html"))
chk("сторінки груп: прихованих категорій у сайдбарі нема", all(f"category-{x}.html" not in groups_pages for x in (az, ar)))
# (4) товари
smprod = read("sitemap-products.xml") if (out / "sitemap-products.xml").exists() else ""
small = smprod + smp + (read("sitemap-oos.xml") if (out / "sitemap-oos.xml").exists() else "")
az_ok = all((out / f"product-{i}.html").exists() and "noindex" not in read(f"product-{i}.html") and f"product-{i}.html" in small for i in IDS["Азартні"])
chk("товари «Азартні»: сторінки живі, індексуються, лишились у sitemap (звичайні ігри)", az_ok)
ar_ok = all((out / f"product-{i}.html").exists() and 'name="robots" content="noindex,follow"' in read(f"product-{i}.html") and f"product-{i}.html" not in small for i in IDS["Арбалети і луки"])
chk("товари «Арбалети і луки»: сторінки живі, noindex,follow, поза sitemap", ar_ok)
ly_ok = all("noindex" not in read(f"product-{i}.html") and f"product-{i}.html" in small for i in IDS["Ляльки"])
chk("товари звичайної категорії не зачеплені (індексуються, в sitemap)", ly_ok)
idx = json.loads(read("index.json"))
chk("index.json (пошук/ціни для замовлень): товари прихованих категорій на місці — купувати можна", all(any(x["id"] == i for x in idx) for i in IDS["Азартні"] + IDS["Арбалети і луки"]))
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n_ in F:
        print("  -", n_)
    sys.exit(1)
print("ALL OK")
