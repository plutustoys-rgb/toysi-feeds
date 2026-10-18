#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_feed_excluded_ids.py — точкові pid, відхилені Merchant Center (FEED_EXCLUDED_PRODUCT_IDS), не йдуть у фіди Google/Meta/Bing, решта лишається; лічильник excluded_product.
Інваріанти: (1) pid зі списку виключений, сусідній товар лишається; (2) лічильник = 1; (3) список не порожній і ключі — рядки. `python test_feed_excluded_ids.py` → exit 0/1."""
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "site"))
import generate_google_feed as gg  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def item(pid, name):
    return {"id": pid, "name": name, "vendor": "Test", "price": 200.0, "stock": 5, "category_name": "Машинки", "category_id": "1",
            "pictures": ["https://toysi.ua/p/enl_%s.jpg" % pid], "description": "Опис товару достатньої довжини для фіда.", "barcode": ""}


chk("список не порожній, ключі рядкові", bool(gg.FEED_EXCLUDED_PRODUCT_IDS) and all(isinstance(k, str) for k in gg.FEED_EXCLUDED_PRODUCT_IDS))
cat = {"283091": item("283091", "Машинка металева Монстр Трак"), "283092": item("283092", "Машинка металева Позашляховик")}
items, st = gg.build_feed_items(cat, {}, {}, {}, None, None)
ids = {i["pid"] for i in items}
chk("283091 виключено, сусідній товар лишився", ids == {"283092"})
chk("лічильник excluded_product = 1", st.get("excluded_product") == 1)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
