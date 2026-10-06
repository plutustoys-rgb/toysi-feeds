# -*- coding: utf-8 -*-
"""Google/Bing: товар без валідного GTIN отримує <g:identifier_exists>no</g:identifier_exists> (Аудитор 2026-10-03, п.8); з GTIN — без тега."""
import sys
import xml.etree.ElementTree as ET
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import generate_google_feed as gg
import generate_bing_feed as gb

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

NS = "{http://base.google.com/ns/1.0}"
def mk(i, gtin):
    return {"id": str(i), "title": f"Іграшка {i}", "description": "Опис", "link": f"https://plutustoys.com.ua/product-{i}.html",
            "image_link": "https://toysi.ua/p/1.jpg", "price": "100.00 UAH", "availability": "in_stock", "condition": "new",
            "brand": "X", "gtin": gtin, "google_product_category": "1"}
for name, mod in (("Google", gg), ("Bing", gb)):
    root = mod._build_xml([mk(1, None), mk(2, "4820000000012")])
    items = root.find("channel").findall("item")
    chk(f"{name}: без GTIN → identifier_exists=no", items[0].findtext(NS + "identifier_exists") == "no" and items[0].find(NS + "gtin") is None)
    chk(f"{name}: з GTIN → є gtin, identifier_exists немає", items[1].findtext(NS + "gtin") == "4820000000012" and items[1].find(NS + "identifier_exists") is None)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ identifier_exists — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
