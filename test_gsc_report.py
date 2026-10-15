#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gsc_report.py — щоденний знімок Search Console (gsc_report.py, запит SEO 15.10). Інваріанти: (1) query_page за ОДНУ добу з відставанням 3 дні, dimensions=query,page, пагінація startRow;
(2) daily за 90 днів по даті, відсортовано; (3) sitemaps.list → рядок на кожен тип вмісту; (4) URL Inspection для переданого переліку, один збійний URL не валить решту, усі збійні → збій частини;
(5) API вимкнено (403 SERVICE_DISABLED) → код 0, файлів нема, без алерту; (6) збій однієї частини не скасовує інших, алерт; (7) нічого не пишеться, крім CSV; ключ/токени не друкуються. `python test_gsc_report.py` → exit 0/1."""
import csv
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
import gsc_report as g  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


class R:
    def __init__(self, data, code=200):
        self._d, self.status_code = data, code

    def json(self):
        return self._d


class Sess:
    def __init__(self, mode="ok", inspect_fail=()):
        self.mode, self.calls, self.inspect_fail = mode, [], set(inspect_fail)

    def post(self, url, json=None, timeout=0):
        self.calls.append((url, json))
        if self.mode == "disabled":
            return R({"error": {"code": 403, "errors": [{"reason": "accessNotConfigured"}], "details": [{"reason": "SERVICE_DISABLED"}], "message": "API has not been used"}}, 403)
        if "urlInspection" in url:
            if json["inspectionUrl"] in self.inspect_fail or self.mode == "inspect_dead":
                return R({"error": {"code": 429, "message": "quota"}}, 429)
            return R({"inspectionResult": {"indexStatusResult": {"verdict": "PASS", "coverageState": "Submitted and indexed", "indexingState": "INDEXING_ALLOWED", "lastCrawlTime": "2026-10-14T05:00:00Z", "googleCanonical": json["inspectionUrl"], "pageFetchState": "SUCCESSFUL", "robotsTxtState": "ALLOWED"}}})
        if "searchAnalytics" in url:
            if self.mode == "qp_fail" and json["dimensions"] == ["query", "page"]:
                return R({"error": {"code": 500, "message": "boom"}}, 500)
            if json["dimensions"] == ["date"]:
                return R({"rows": [{"keys": ["2026-10-02"], "clicks": 2, "impressions": 50, "ctr": 0.04, "position": 12.3}, {"keys": ["2026-10-01"], "clicks": 1, "impressions": 40, "ctr": 0.025, "position": 14.1}]})
            return R({"rows": [{"keys": ["лялька", "https://plutustoys.com.ua/product-1.html"], "clicks": 1, "impressions": 10, "ctr": 0.1, "position": 5.123}]})
        return R({}, 404)

    def get(self, url, params=None, timeout=0):
        self.calls.append((url, None))
        if self.mode == "disabled":
            return self.post(url)
        if url.endswith("/sitemaps"):
            return R({"sitemap": [{"path": "https://plutustoys.com.ua/sitemap-products.xml", "lastSubmitted": "2026-10-15T01:00:00Z", "lastDownloaded": "2026-10-15T05:00:00Z", "isPending": False, "errors": "0", "warnings": "0", "contents": [{"type": "web", "submitted": "9000", "indexed": "0"}]}]})
        return R({"siteEntry": [{"siteUrl": "https://plutustoys.com.ua/", "permissionLevel": "siteRestrictedUser"}]})


NOW = datetime(2026, 10, 15, 6, 40)
URLS = ["https://plutustoys.com.ua/", "https://plutustoys.com.ua/product-1.html", "https://plutustoys.com.ua/catalog.html"]
g.priority_urls = lambda n=100, sitemap_url="": URLS     # без мережі

d = Path(tempfile.mkdtemp())
s = Sess()
rc = g.run(s, d, NOW)
rows = list(csv.DictReader(open(d / "gsc_query_page_2026-10-15.csv", encoding="utf-8-sig")))
chk("run: код 0, 4 файли записано", rc == 0 and len(list(d.glob("*.csv"))) == 4 and not list(d.glob("*.tmp")))
chk("query_page: доба = сьогодні−3 (12.10), запит dimensions query,page, startRow 0", rows and rows[0]["date"] == "2026-10-12" and any(c[1] and c[1].get("dimensions") == ["query", "page"] and c[1]["startDate"] == c[1]["endDate"] == "2026-10-12" and c[1]["startRow"] == 0 for c in s.calls))
chk("query_page: кирилиця в запиті збережена, position округлено до 2 знаків", rows[0]["query"] == "лялька" and rows[0]["position"] == "5.12")
daily = list(csv.DictReader(open(d / "gsc_daily_2026-10-15.csv", encoding="utf-8-sig")))
dc = [c for c in s.calls if c[1] and c[1].get("dimensions") == ["date"]][0][1]
chk("daily: вікно 90 днів до сьогодні−3 (14.07 … 12.10), відсортовано за датою", dc["startDate"] == "2026-07-15" and dc["endDate"] == "2026-10-12" and [r["date"] for r in daily] == ["2026-10-01", "2026-10-02"])
sm = list(csv.DictReader(open(d / "gsc_sitemaps_2026-10-15.csv", encoding="utf-8-sig")))
chk("sitemaps: path, submitted (deprecated-поле indexed не збираємо)", sm and sm[0]["submitted"] == "9000" and "indexed" not in sm[0] and sm[0]["path"].endswith("sitemap-products.xml"))
ins = list(csv.DictReader(open(d / "gsc_inspect_2026-10-15.csv", encoding="utf-8-sig")))
chk("inspect: рядок на кожен URL, coverageState і lastCrawlTime", len(ins) == 3 and ins[0]["coverageState"] == "Submitted and indexed" and ins[0]["lastCrawlTime"].startswith("2026-10-14"))
chk("sitemaps.list — GET (без тіла), searchAnalytics і inspect — POST-запити на читання, інших методів клієнт не має (Sess.post/get)", [c for c in s.calls if "sitemaps" in c[0]][0][1] is None and len([c for c in s.calls if "urlInspection" in c[0]]) == 3)

d2 = Path(tempfile.mkdtemp())
rc = g.run(Sess("disabled"), d2, NOW)
chk("API вимкнено: код 0, файлів нема", rc == 0 and not list(d2.glob("*")))

alerts = []
g._alert = lambda k, t: alerts.append(k)
d3 = Path(tempfile.mkdtemp())
rc = g.run(Sess("qp_fail"), d3, NOW)
chk("збій query_page: решта частин записана, код 0, алерт gsc_report_part", rc == 0 and not (d3 / "gsc_query_page_2026-10-15.csv").exists() and (d3 / "gsc_daily_2026-10-15.csv").exists() and (d3 / "gsc_inspect_2026-10-15.csv").exists() and "gsc_report_part" in alerts)
alerts.clear()
d4 = Path(tempfile.mkdtemp())
rc = g.run(Sess("ok", inspect_fail={URLS[1]}), d4, NOW)
ins4 = list(csv.DictReader(open(d4 / "gsc_inspect_2026-10-15.csv", encoding="utf-8-sig")))
chk("один збійний URL не валить inspect: у файлі 3 рядки, збійний позначено ERROR, алерту нема", rc == 0 and len(ins4) == 3 and ins4[1]["verdict"].startswith("ERROR") and not alerts)
d5 = Path(tempfile.mkdtemp())
rc = g.run(Sess("inspect_dead"), d5, NOW)
chk("усі URL inspect збійні → частина inspect не записана, інші так, алерт", rc == 0 and not (d5 / "gsc_inspect_2026-10-15.csv").exists() and (d5 / "gsc_daily_2026-10-15.csv").exists() and "gsc_report_part" in alerts)
d7 = Path(tempfile.mkdtemp())
_clock = iter([0, 0, 1000, 1000, 1000, 1000, 1000])
_orig = g.time.monotonic
g.time.monotonic = lambda: next(_clock)
try:
    _p, _n = g.part_inspect(Sess(), NOW, d7, urls=URLS, budget_sec=400)
finally:
    g.time.monotonic = _orig
chk("бюджет часу inspect: після вичерпання зупиняється, записує вже перевірене (1 з 3 URL)", _n == 1 and len(list(csv.DictReader(open(d7 / "gsc_inspect_2026-10-15.csv", encoding="utf-8-sig")))) == 1)
alerts.clear()
_r = Sess("inspect_dead")
rr = g.run(_r, Path(tempfile.mkdtemp()), NOW)
chk("усі inspect збійні (429): частина провалена, алерт gsc_report_part (текст першої помилки йде в алерт через failed)", rr == 0 and "gsc_report_part" in alerts)
g.priority_urls = lambda n=100, sitemap_url="": []
alerts.clear()
d6 = Path(tempfile.mkdtemp())
rc = g.run(Sess(), d6, NOW)
chk("порожній priority-sitemap: inspect збій+алерт, три інші частини записані", rc == 0 and not (d6 / "gsc_inspect_2026-10-15.csv").exists() and len(list(d6.glob("*.csv"))) == 3 and "gsc_report_part" in alerts)
chk("--check: властивість є → 0; API вимкнено → 2", g.check(Sess()) == 0 and g.check(Sess("disabled")) == 2)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
