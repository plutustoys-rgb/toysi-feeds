# -*- coding: utf-8 -*-
"""Регрес фільтра «товар має картку на сайті» (generate_google_feed.load_site_product_ids / build_feed_items site_ids):
посилання Google/Meta/Bing ведуть на старі Prom-URL → 301 на /product-<id>.html; без картки це /catalog.html
(невідповідність цільової сторінки в Merchant Center). Fail-open: нема/малий індекс → фільтр вимкнено."""
import json, os, sys, tempfile
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import generate_google_feed as gg

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

def with_index(content):
    d = Path(tempfile.mkdtemp())
    if content is not None:
        (d / "index.json").write_text(content, encoding="utf-8")
    os.environ["SITE_DIR"] = str(d)
    return gg.load_site_product_ids()

big = json.dumps([{"id": str(i), "n": "x", "pr": 100} for i in range(1500)])
ids = with_index(big)
chk("великий індекс → множина id", ids is not None and len(ids) == 1500 and "7" in ids)
chk("нема index.json → None (fail-open)", with_index(None) is None)
chk("биттий JSON → None (fail-open)", with_index("{not json") is None)
chk("підозріло малий індекс (<1000) → None (fail-open)", with_index(json.dumps([{"id": "1"}])) is None)

# build_feed_items: товар поза site_ids відсікається ще до розрахунків, у stats.not_on_site
cat = {str(i): {"id": str(i), "name": "Тест", "price": 100} for i in range(100)}
items, stats = gg.build_feed_items(cat, {}, {}, {}, None, {str(i) for i in range(96)})   # відсікає 4 з 100 (4%)
chk("товари не з site_ids → not_on_site=4 (4% каталогу)", stats["not_on_site"] == 4)
items, stats = gg.build_feed_items(cat, {}, {}, {}, None, None)
chk("site_ids=None → фільтр не застосовується (not_on_site=0)", stats["not_on_site"] == 0)
# страховка (аудит #618): фільтр, що відсікав би >25% каталогу, вимикається — це ознака обрізаного індексу
items, stats = gg.build_feed_items(cat, {}, {}, {}, None, {"0", "1"})   # відсікав би 98%
chk("фільтр на 98% каталогу вимикається (not_on_site=0)", stats["not_on_site"] == 0)
items, stats = gg.build_feed_items(cat, {}, {}, {}, None, {str(i) for i in range(70)})   # 30% > 25%
chk("фільтр на 30% каталогу вимикається (межа 25%)", stats["not_on_site"] == 0)
items, stats = gg.build_feed_items(cat, {}, {}, {}, None, {str(i) for i in range(80)})   # рівно 20% ≤ 25%
chk("фільтр на 20% каталогу лишається (not_on_site=20)", stats["not_on_site"] == 20)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ фільтр «є на сайті» — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
