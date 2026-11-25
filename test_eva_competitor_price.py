# -*- coding: utf-8 -*-
"""test_eva_competitor_price.py — репрайсер EVA «мінімум конкурента − 3 ₴» (рішення власника 25.11). Офлайн. Інваріанти: без воріт/без файлу/старий файл → стара формула; конкурент вищий за формулу → не піднімаємо;
нижчий → max(конкурент−3, покривна підлога); підлога не порушується; промо-SKU не чіпаємо; битий файл безпечний."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, ".")
import generate_eva_feed as g
from competitor_pricing import real_toysi_cost

fails = 0


def chk(name, ok):
    global fails
    print(("[OK ] " if ok else "[FAIL] ") + name)
    if not ok:
        fails += 1


def item(pid="900001", price=300.0):
    return {"id": pid, "price": price, "vendor": "TestBrand", "category_name": "Іграшки", "is_import": True, "name": "Тест"}


def use_file(prices, age_days=0, raw=None):
    tmp = Path(tempfile.mkdtemp()) / "eva_competitor_prices.json"
    if raw is not None:
        tmp.write_text(raw, encoding="utf-8")
    elif prices is not None:
        tmp.write_text(json.dumps({"at": (datetime.now() - timedelta(days=age_days)).isoformat(timespec="seconds"), "prices": prices}), encoding="utf-8")
    g.EVA_COMPETITOR_FILE = tmp
    g._eva_competitor_cache = None


os.environ.pop("EVA_COMPETITOR_PRICE", None)
it = item()
base = g._eva_price(it)
cost = real_toysi_cost(dict(it))
floor = round(cost * (1 + g.EVA_LOSS_FLOOR_MARGIN) / (1 - g.EVA_COMMISSION_EST), 2)
chk("база: формула дає ціну > підлоги (тестовий товар придатний)", base and base > floor)

use_file({"900001": round(base * 0.8, 2)})
chk("ворота ВИМКНЕНО → ціна як за формулою (файл ігнорується)", g._eva_price(it) == base)

os.environ["EVA_COMPETITOR_PRICE"] = "1"
use_file({"900001": round(base * 0.8, 2)})
exp = round(max(round(base * 0.8 - 3, 2), floor), 2)
chk("ворота ON, конкурент дешевший → max(конкурент−3, підлога)", g._eva_price(it) == exp and exp < base)

use_file({"900001": round(base * 1.3, 2)})
chk("конкурент ДОРОЖЧИЙ за формулу → ціну НЕ піднімаємо (cap = формула)", g._eva_price(it) == base)

use_file({"900001": round(floor * 0.5, 2)})
chk("конкурент нижчий за підлогу → ціна = покривна підлога (не збиткова)", g._eva_price(it) == floor)

use_file({"900001": round(base * 0.8, 2)}, age_days=4)
chk("файл старший за 3 доби → ігнорується (стара формула)", g._eva_price(it) == base)

use_file(None, raw="{не json")
chk("битий файл → безпечно, стара формула", g._eva_price(it) == base)

use_file({"777": 10.0})
chk("товар без запису в файлі → стара формула", g._eva_price(it) == base)

use_file({"900001": round(base * 0.8, 2)})
g.EVA_BIRTHDAY_PROMO_SKUS = set(g.EVA_BIRTHDAY_PROMO_SKUS) | {"900001"}
promo = g._eva_price(it)
g.EVA_BIRTHDAY_PROMO_SKUS = set(g.EVA_BIRTHDAY_PROMO_SKUS) - {"900001"}
chk("промо-SKU не чіпаємо репрайсером", promo > base)

use_file({"900001": "abc", "900002": -5})
chk("нечислове/від'ємне значення конкурента ігнорується", g._eva_price(it) == base)

print("\nALL OK" if not fails else "\nFAILS: %d" % fails)
sys.exit(1 if fails else 0)
