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
chk("«Арбалети і луки» (239852, 277619, 264057, 297048) — pid-страховка, рішення власника 22.10; «Азартні» в списку НЕМАЄ (лишаються у фідах)", all(p in gg.FEED_EXCLUDED_PRODUCT_IDS for p in ("239852", "277619", "264057", "297048"))
    and not any(p in gg.FEED_EXCLUDED_PRODUCT_IDS for p in ("141716", "289284", "19250", "132858", "257679", "257680", "139013", "126642", "205520")))
cat2 = {p: item(p, "Лук зі стрілами") for p in ("239852", "277619", "264057", "297048", "141716")}
for p in cat2:
    cat2[p]["category_name"] = "Інша назва категорії"        # навіть якщо категорію перейменують — pid-виняток тримає
items2, st2 = gg.build_feed_items(cat2, {}, {}, {}, None, None)
chk("при ІНШІЙ назві категорії 4 pid арбалетів усе одно виключені, «Азартний» 141716 лишається в фіді", {i["pid"] for i in items2} == {"141716"} and st2.get("excluded_product") == 4)
# 289264 «До Дна!»: на коробці «алкогольна гра 18+» (SEO 25.10), у назві мітки нема — фільтр за назвою його не бачить, тому точковий pid
t289264 = 'Настільна гра "До Дна!" (укр)'
chk("назва «До Дна!» чиста для is_adult_product (саме тому потрібен pid-виняток)", gg.is_adult_product(t289264) is False)
cat3 = {"289264": item("289264", t289264), "289265": item("289265", "Настільна гра «Дитяче лото»")}
items3, st3 = gg.build_feed_items(cat3, {}, {}, {}, None, None)
chk("289264 виключено з фідів (Google/Meta/Bing), сусідній 289265 лишився, excluded_product = 1", {i["pid"] for i in items3} == {"289265"} and st3.get("excluded_product") == 1)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
