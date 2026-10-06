# -*- coding: utf-8 -*-
"""ALLO вимагає ціни ЦІЛІ без копійок (Аудитор 2026-10-03, п.1): 196 з 453 офферів мали копійки (напр. 82,42). Ціна = ceil(ціна Prom)."""
import sys
import xml.etree.ElementTree as ET
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import generate_allo_feed as g

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

def item(i, price):
    return {"id": str(i), "name": f"Іграшка {i}", "price": price, "stock": 5, "category_id": "10", "category_name": "Різне", "vendor": "TestBrand",
            "vendor_code": str(i), "pictures": [f"https://toysi.ua/p/{i}.jpg"], "description": "Опис", "params": [], "barcode": ""}

cat = {str(i): item(i, 40.0) for i in range(1, 6)}
overrides = {"1": 82.42, "2": 100.0, "3": 100.0000001, "4": 150.01, "5": 99.99}
root = g._build_xml(cat, price_overrides=overrides)
prices = {o.get("id"): o.find("price").text for o in root.find("shop").find("offers")}
print(prices)
chk("82.42 → 83 (ceil)", prices.get("1") == "83")
chk("100.00 → 100 (цілі не зростають)", prices.get("2") == "100")
chk("100.0000001 (float-шум) → 100, не 101", prices.get("3") == "100")
chk("150.01 → 151, 99.99 → 100", prices.get("4") == "151" and prices.get("5") == "100")
chk("жодної ціни з крапкою/копійками у фіді", all("." not in p for p in prices.values()) and len(prices) == 5)
chk("відхилення від ціни Prom < 1 грн", all(-0.01 <= int(prices[k]) - overrides[k] < 1.0001 for k in prices))
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ ALLO: ціни цілі — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
