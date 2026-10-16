#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_demand_featured_auto.py — автоматичний «Популярне зараз» за попитом Google (demand_featured_auto.py, 16.10.2026).
Інваріанти: (1) aggregate: /product-<id>.html — id напряму, старі Prom-URL /ua/p<pid>-… і /p<pid>-… — через own_product_links_cache (перевернутий), чужі URL ігноруються, покази й кліки складаються по id;
(2) select: лише id з index.json, чорний список, max_ids, мінімум показів; порядок: покази ↓, кліки ↓, id ↑; (3) main: пише demand_featured_auto.json у форматі {date, source, ids, categories?, note}, categories беруться з ручного файла SEO; (4) <10 id або збій API → файл НЕ перезаписується, код 1;
(5) порожній index.json → код 1; (6) .tmp не лишається. `python test_demand_featured_auto.py` → exit 0/1."""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import demand_featured_auto as da  # noqa: E402
import gsc_report as gsc  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


rows = [
    {"keys": ["https://plutustoys.com.ua/product-111.html"], "impressions": 100, "clicks": 3},
    {"keys": ["https://plutustoys.com.ua/ua/p9001-lyalka.html"], "impressions": 50, "clicks": 1},
    {"keys": ["https://plutustoys.com.ua/p9001-lyalka.html"], "impressions": 30, "clicks": 2},
    {"keys": ["https://plutustoys.com.ua/ua/p777-nema.html"], "impressions": 999, "clicks": 9},
    {"keys": ["https://plutustoys.com.ua/catalog.html"], "impressions": 5000, "clicks": 50},
    {"keys": []},
]
agg = da.aggregate(rows, {"9001": "222"})
chk("aggregate: product-111 напряму; Prom p9001 (два URL) → toysi 222 (покази 80, кліки 3); невідомий p777 і /catalog.html ігноруються", agg == {"111": [100, 3], "222": [80, 3]})
chk("prom_to_toysi перевертає кеш {toysi: {prom_id}}", da.prom_to_toysi({"222": {"prom_id": 9001}, "x": {}, "y": None}) == {"9001": "222"})
many = {str(i): [1000 - i, 0] for i in range(1, 60)}
rules = {"blacklist_ids": ["3"], "max_ids": 5, "min_impressions": 1}
sel = da.select(many, {str(i) for i in range(1, 60) if i != 2}, rules)
chk("select: поза index (2) і чорний список (3) відсіяно, max_ids=5, порядок за показами", sel == ["1", "4", "5", "6", "7"])
tie = da.select({"10": [5, 1], "9": [5, 2], "8": [5, 2]}, {"8", "9", "10"}, {"blacklist_ids": [], "max_ids": 10, "min_impressions": 1})
chk("тай-брейк: покази рівні → кліки ↓, потім id ↑ (8, 9, 10)", tie == ["8", "9", "10"])
chk("min_impressions відсікає", da.select({"1": [1, 0], "2": [5, 0]}, {"1", "2"}, {"blacklist_ids": [], "max_ids": 10, "min_impressions": 3}) == ["2"])

d = Path(tempfile.mkdtemp())
da.SITE_DIR = d
da.INDEX_FILE = d / "index.json"
da.MANUAL_FILE = d / "demand_featured.json"
da.LINKS_CACHE = d / "links.json"
da.RULES_FILE = d / "rules.json"
(d / "index.json").write_text(json.dumps([{"id": str(i), "pr": 100} for i in range(1, 60)]), encoding="utf-8")
(d / "links.json").write_text(json.dumps({}), encoding="utf-8")
(d / "demand_featured.json").write_text(json.dumps({"date": "2026-10-13", "ids": ["1"], "categories": ["Самокати триколісні"]}, ensure_ascii=False), encoding="utf-8")
(d / "rules.json").write_text(json.dumps({"blacklist_ids": ["5"], "max_ids": 20, "min_impressions": 1}), encoding="utf-8")


class Sess:
    pass


gsc.query_rows = lambda session, start, end, dims, **k: [{"keys": [f"https://plutustoys.com.ua/product-{i}.html"], "impressions": 100 - i, "clicks": 1} for i in range(1, 40)]
out = d / "demand_featured_auto.json"
da.RULES_FILE = d / "rules.json"
rc = da.main(Sess(), datetime(2026, 10, 16, 18, 0), out)
data = json.loads(out.read_text(encoding="utf-8"))
chk("main: код 0, формат {date, source, ids, categories(з ручного файла), note}, 20 id, чорний «5» відсутній", rc == 0 and data["date"] == "2026-10-16" and len(data["ids"]) == 20 and "5" not in data["ids"] and data["categories"] == ["Самокати триколісні"] and "Search Console" in data["source"] and "note" in data)
chk(".tmp не лишився", not list(d.glob("*.tmp")))
before = out.read_text(encoding="utf-8")
gsc.query_rows = lambda *a, **k: [{"keys": ["https://plutustoys.com.ua/product-1.html"], "impressions": 5, "clicks": 0}]
chk("мало даних (<10 id після відбору) → код 1, файл не змінено", da.main(Sess(), datetime(2026, 10, 17), out) == 1 and out.read_text(encoding="utf-8") == before)


def boom(*a, **k):
    raise RuntimeError("403 accessNotConfigured")


gsc.query_rows = boom
chk("збій API → код 1, файл не змінено", da.main(Sess(), datetime(2026, 10, 17), out) == 1 and out.read_text(encoding="utf-8") == before)
(d / "index.json").write_text("[]", encoding="utf-8")
out2 = d / "o2.json"
chk("порожній index.json → код 1, файл не створено", da.main(Sess(), datetime(2026, 10, 17), out2) == 1 and not out2.exists())
chk("нема rules-файла → дефолт (40, порожній чорний список, мін. 1 показ)", da.load_rules(d / "нема.json") == {"blacklist_ids": [], "max_ids": 40, "min_impressions": 1})
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
