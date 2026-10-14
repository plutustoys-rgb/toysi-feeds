#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_gmc_status_report.py — щоденний звіт про відхилення Merchant Center (gmc_status_report.py, рішення власника 14.10): лише читання через Merchant API.
Інваріанти: (1) пагінація nextPageToken до кінця, pageSize=1000, жодних записів у API (лише GET); (2) статус: disapproved (disapprovedCountries або severity DISAPPROVED) > approved > pending > unknown;
(3) CSV: рядок на товар, коди й описи причин через « | », мова/мітка/джерело, UTF-8 з BOM для Excel, без .tmp; summary: кількості за статусом і топ причин; (4) нема ключа → код 0 без звернень до API;
(5) збій API / порожня відповідь → код 1, алерт, файл НЕ записано; (6) некоректні/неповні записи не валять (захисне читання). `python test_gmc_status_report.py` → exit 0/1."""
import csv
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"

import gmc_status_report as g  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


alerts = []
g._alert = lambda k, t: alerts.append(k)


class Resp:
    def __init__(self, d, code=200):
        self.d, self.code = d, code

    def raise_for_status(self):
        if self.code >= 400:
            raise RuntimeError(f"HTTP {self.code}")

    def json(self):
        return self.d


class FakeSession:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        tok = (params or {}).get("pageToken")
        idx = int(tok) if tok else 0
        return Resp(self.pages[idx])

    def post(self, *a, **k):
        raise AssertionError("звіт лише читає: POST заборонений")


def prod(offer, status="approved", issues=None, lang="ru", label="UA", ds="accounts/1/dataSources/77"):
    dests = {"approved": [{"reportingContext": "SHOPPING_ADS", "approvedCountries": ["UA"]}],
             "disapproved": [{"reportingContext": "SHOPPING_ADS", "disapprovedCountries": ["UA"]}],
             "pending": [{"reportingContext": "SHOPPING_ADS", "pendingCountries": ["UA"]}],
             "none": []}[status]
    return {"name": f"accounts/1/products/online~{lang}~{label}~{offer}", "offerId": offer, "contentLanguage": lang, "feedLabel": label, "dataSource": ds,
            "productStatus": {"destinationStatuses": dests, "itemLevelIssues": issues or []}}


iss_price = {"code": "missing_price", "severity": "DISAPPROVED", "attribute": "price", "description": "Не указана цена"}
iss_img = {"code": "image_not_processed", "severity": "DISAPPROVED", "attribute": "image_link", "description": "Изображение не обработано"}
pages = [{"products": [prod("1", "approved"), prod("2", "disapproved", [iss_price]), prod("3", "disapproved", [iss_price, iss_img])], "nextPageToken": "1"},
         {"products": [prod("4", "pending"), prod("5", "none"), prod("6", "approved", [{"code": "x", "severity": "DISAPPROVED", "description": "d"}])], "nextPageToken": "2"},
         {"products": [prod("7", "disapproved", [iss_img])]}]
sess = FakeSession(pages)
rows = [g.product_row(p, "2026-10-14") for p in g.fetch_products(sess, "999")]
chk("пагінація: 3 сторінки, 7 товарів, pageSize=1000, токени передані", len(rows) == 7 and len(sess.calls) == 3 and sess.calls[0][1] == {"pageSize": 1000} and sess.calls[1][1].get("pageToken") == "1" and sess.calls[2][1].get("pageToken") == "2" and sess.calls[0][0].endswith("/accounts/999/products"))
st = {r["offerId"]: r["status"] for r in rows}
chk("статуси: approved/disapproved/pending/unknown; severity DISAPPROVED при approved-destination → disapproved", st == {"1": "approved", "2": "disapproved", "3": "disapproved", "4": "pending", "5": "unknown", "6": "disapproved", "7": "disapproved"})
r3 = next(r for r in rows if r["offerId"] == "3")
chk("причини: коди й описи через ' | ', країни, мова/мітка, джерело (id без шляху)", r3["issue_codes"] == "missing_price | image_not_processed" and r3["issue_descriptions"] == "Не указана цена | Изображение не обработано" and r3["disapproved_countries"] == "UA" and r3["contentLanguage"] == "ru" and r3["feedLabel"] == "UA" and r3["dataSource"] == "77")

with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "gmc"
    rc = g.run(session=FakeSession(pages), out_dir=out, now=datetime(2026, 10, 14, 6, 30))
    csvp, sump = out / "gmc_status_2026-10-14.csv", out / "gmc_summary_2026-10-14.txt"
    chk("run: код 0, CSV і summary записані, без .tmp", rc == 0 and csvp.exists() and sump.exists() and not list(out.glob("*.tmp")))
    raw = csvp.read_bytes()
    chk("CSV з BOM (Excel), читається назад, 7 рядків, колонки як задано", raw.startswith(b"\xef\xbb\xbf") and list(csv.DictReader(open(csvp, encoding="utf-8-sig")))[1]["offerId"] == "2" and len(list(csv.DictReader(open(csvp, encoding="utf-8-sig")))) == 7)
    s = sump.read_text(encoding="utf-8")
    chk("summary: кількості за статусом і топ причин (missing_price 2, image_not_processed 2)", "товарів 7" in s and "disapproved 4" in s and "missing_price" in s and "image_not_processed" in s and "Не указана цена" in s)

    # нема ключа → 0, API не чіпаємо
    old_key = g.KEY_FILE
    g.KEY_FILE = Path(td) / "нема.json"
    chk("нема ключа → код 0 (підказка), файлів нема", g.run(session=None, out_dir=out / "x", now=datetime(2026, 10, 15)) == 0 and not (out / "x").exists())
    g.KEY_FILE = old_key

    # збій API
    class BadSession:
        def get(self, *a, **k):
            return Resp({}, 403)
    alerts.clear()
    out2 = Path(td) / "gmc2"
    rc = g.run(session=BadSession(), out_dir=out2, now=datetime(2026, 10, 14))
    chk("збій API (403): код 1, алерт gmc_report_api, файл НЕ записано", rc == 1 and "gmc_report_api" in alerts and not out2.exists())
    alerts.clear()
    rc = g.run(session=FakeSession([{"products": []}]), out_dir=out2, now=datetime(2026, 10, 14))
    chk("порожня відповідь: код 1, алерт gmc_report_empty, файл НЕ записано (вчорашній звіт не затираємо)", rc == 1 and "gmc_report_empty" in alerts and not out2.exists())

# дедуплікація причин за товаром (одна причина на кілька reportingContext) + колонка серйозності
dup = prod("20", "disapproved", [dict(iss_price, reportingContext="SHOPPING_ADS"), dict(iss_price, reportingContext="FREE_LISTINGS"), {"code": "w", "severity": "NOT_IMPACTED", "description": "попередження"}])
rd = g.product_row(dup, "d")
chk("причина, що повторюється для кількох reportingContext, у рядку товару одна; серйозності в окремій колонці", rd["issue_codes"] == "missing_price | w" and rd["issue_severities"] == "DISAPPROVED | NOT_IMPACTED" and rd["issue_descriptions"].count("Не указана цена") == 1)
chk("CSV_FIELDS містить issue_severities", "issue_severities" in g.CSV_FIELDS)
gi = open(Path(__file__).parent / ".gitignore", encoding="utf-8").read()
chk(".gitignore містить .secrets/ і gmc_service_account*.json (/opt/plutustoys — git-клон)", ".secrets/" in gi.splitlines() and "gmc_service_account*.json" in gi.splitlines())

# захисне читання
weird = [{}, {"offerId": "9"}, {"offerId": "10", "productStatus": {"destinationStatuses": [{}], "itemLevelIssues": [{}, {"severity": None}]}}, {"offerId": "11", "productStatus": None}]
wr = [g.product_row(p, "d") for p in weird]
chk("порожні/неповні/None-поля не валять, статус unknown", all(r["status"] == "unknown" for r in wr) and len(wr) == 4)
chk("MAX_PAGES: нескінченна пагінація обривається", len(list(g.fetch_products(FakeSession([{"products": [prod("1")], "nextPageToken": "0"}]), "1", max_pages=5))) == 5)

# register_gcp / check_access
class PostSession:
    def __init__(self, code=200, body=None):
        self.code, self.body, self.sent = code, body or {}, []

    def post(self, url, json=None, timeout=None):
        self.sent.append((url, json))
        return type("R", (), {"status_code": self.code, "json": lambda s: self.body})()

    def get(self, url, params=None, timeout=None):
        return type("R", (), {"status_code": self.code, "json": lambda s: self.body})()


ps = PostSession(200, {"name": "accounts/5824264949/developerRegistration", "gcpIds": ["proj-1"]})
ok, msg = g.register_gcp(ps, "owner@gmail.com", "5824264949")
chk("register_gcp: POST на .../developerRegistration:registerGcp з developerEmail, ok", ok and ps.sent[0][0].endswith("/accounts/5824264949/developerRegistration:registerGcp") and ps.sent[0][1] == {"developerEmail": "owner@gmail.com"} and "proj-1" in msg)
ps2 = PostSession()
chk("register_gcp відхиляє email сервіс-акаунта і порожній без запиту", g.register_gcp(ps2, "sa@proj.iam.gserviceaccount.com")[0] is False and g.register_gcp(ps2, "")[0] is False and not ps2.sent)
chk("register_gcp: 403 → False з кодом і текстом помилки, без винятку", g.register_gcp(PostSession(403, {"error": {"message": "caller does not have ADMIN"}}), "o@gmail.com")[0] is False)
chk("check_access: 200 → ok; 403 → False", g.check_access(PostSession(200, {"products": [{}]}))[0] is True and g.check_access(PostSession(403, {"error": {"message": "x"}}))[0] is False)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
