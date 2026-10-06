# -*- coding: utf-8 -*-
"""Регрес (2026-10-06): Google/Meta/Bing-фід має ту саму ЦІНУ й веде на ту саму СТОРІНКУ, що й власний сайт.
До цього ціна була Prom-ова, а 301 вів на сайт з іншою ціною: з 2042 порівняних позицій збігалось лише 17 →
«розбіжність ціни» в Merchant Center. Також: ТМ «UNO» (претензія Mattel) не потрапляє ні на сайт, ні у фід."""
import sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent / "site"))
import competitor_pricing as cp
import generate_google_feed as gg
import trademark_filter as tf
import build_site as bs

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

def item(pid, name="Плюшевий ведмідь", vendor="Test", price=200.0):
    return {"id": pid, "name": name, "vendor": vendor, "price": price, "stock": 5, "category_name": "М'які іграшки",
            "pictures": ["https://toysi.ua/p/enl_%s.jpg" % pid], "description": "Опис товару достатньої довжини для фіда.", "barcode": ""}

it = item("1001")
expected = cp.site_retail_price(it)
chk("єдине джерело: build_site.price_of == competitor_pricing.site_retail_price", bs.price_of(it) == expected)
chk("ціна сайту = ceil(знижена Toysi × 1.5)", expected == __import__("math").ceil(cp.toysi_discounted_price(it) * 1.5))

cat = {"1001": it}
items, stats = gg.build_feed_items(cat, {}, {}, {"1001": 1.0}, {"1001": 1.0}, None)   # «Prom-override» 1.0 ігнорується
chk("у фіді рівно 1 товар без Prom-посилань (links порожній)", len(items) == 1 and stats["included"] == 1)
chk("ціна фіда == ціна сайту (override Prom ігнорується)", abs(float(items[0]["price"].split()[0]) - expected) < 0.005)
chk("посилання — рідна картка сайту /product-<id>.html", items[0]["link"] == "https://plutustoys.com.ua/product-1001.html")
chk("у посиланні немає Prom-структури", "/ua/p" not in items[0]["link"] and "prom" not in items[0]["link"].lower())

# підлога: ціна покриває собівартість Toysi З пакуванням («Збірка») і дає ≥3% — для будь-якої ціни, мінімальної суми нема
import math
bad = []
for p in (3, 8, 10, 15, 20, 25, 30, 33, 40, 60, 100, 250, 1000, 5000):
    x = item("9" + str(p), price=float(p))
    cost = cp.real_toysi_cost(x); price = cp.site_retail_price(x)
    if price < cost * 1.03 - 1e-9 or price < math.ceil(cp.toysi_discounted_price(x) * 1.5):
        bad.append((p, price, round(cost, 2)))
chk("ціна сайту ≥ собівартість(+пакування)×1.03 і ≥ ×1.5 для будь-якої ціни (14 точок)", not bad)
x = item("9991", price=10.0)
chk("дешевий товар: підлога піднімає ціну вище ×1.5", cp.site_retail_price(x) > math.ceil(cp.toysi_discounted_price(x) * 1.5))
x = item("9992", price=500.0)
chk("звичайний товар: ціна лишається ×1.5 (підлога не діє)", cp.site_retail_price(x) == math.ceil(cp.toysi_discounted_price(x) * 1.5))

# ціна ІЗ КАРТКИ (index.json): фід бере pr рівно як на сторінці, навіть якщо формула дала б інше (дрейф каталогу між збірками)
items, stats = gg.build_feed_items({"1001": it}, {}, {}, {}, None, {"1001": expected + 7})
chk("ціна фіда = pr з індексу (збіг за побудовою), а не перерахунок", abs(float(items[0]["price"].split()[0]) - (expected + 7)) < 0.005 and stats["price_from_card"] == 1)
items, stats = gg.build_feed_items({"1001": it}, {}, {}, {}, None, None)
chk("без індексу — формула сайту (price_from_formula=1)", stats["price_from_formula"] == 1 and abs(float(items[0]["price"].split()[0]) - expected) < 0.005)
# ТМ UNO
chk("UNO (vendor ≠ Mattel) заблоковано", tf.is_uno_trademark_blocked("Карткова гра UNO Kids", "MiC"))
chk("кирилиця «УНО» заблокована", tf.is_uno_trademark_blocked('Гра "УНО"', "Strateg"))
chk("Mattel UNO дозволено", not tf.is_uno_trademark_blocked("UNO Mattel", "Mattel"))
chk("«КапиУНО»/«УНА» цілим словом не чіпаємо", not tf.is_uno_trademark_blocked("Іграшка КапиУНО", "X") and not tf.is_uno_trademark_blocked("УНА Семейная", "X"))
items, stats = gg.build_feed_items({"1002": item("1002", name="Карткова гра UNO Kids", vendor="MiC")}, {}, {}, {}, None, None)
chk("UNO не потрапляє у фід (trademark_blocked=1)", stats["trademark_blocked"] == 1 and not items)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ ціна/посилання фіда = сайт, UNO відсічено — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
