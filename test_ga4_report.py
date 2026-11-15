#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ga4_report.py — ga4_report.py (читач GA4, запит SMM/власника 15.11.2026). Без мережі. `python test_ga4_report.py` → exit 0/1."""
import csv
import json
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
import gsc_report as gsc  # noqa: E402
import ga4_report as ga  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


class Resp:
    def __init__(self, code=200, body=None):
        self.status_code = code
        self._b = body if body is not None else {}

    def json(self):
        return self._b


def row(d, src, med, camp, s, u, t, rev):
    return {"dimensionValues": [{"value": v} for v in (d, src, med, camp)], "metricValues": [{"value": str(v)} for v in (s, u, t, rev)]}


class Sess:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def post(self, url, json=None, timeout=0):
        self.calls.append((url, json))
        return self.pages.pop(0) if self.pages else Resp(200, {"rows": []})


# --- property_id ---------------------------------------------------------------
td = Path(tempfile.mkdtemp())
cfg = td / "ga4_config.json"
chk("нема ні env, ні файлу → None (не налаштовано)", ga.property_id({}, td / "nope.json") is None)
chk("env GA4_PROPERTY_ID=123456789 → '123456789'", ga.property_id({"GA4_PROPERTY_ID": " 123456789 "}, cfg) == "123456789")
chk("Measurement ID G-… (не числовий) відхиляється", ga.property_id({"GA4_PROPERTY_ID": "G-WXC4TYX0VF"}, cfg) is None)
cfg.write_text(json.dumps({"property_id": "987654321"}), encoding="utf-8")
chk("файл ga4_config.json → ID; env має пріоритет", ga.property_id({}, cfg) == "987654321" and ga.property_id({"GA4_PROPERTY_ID": "111"}, cfg) == "111")
cfg.write_text("не json", encoding="utf-8")
chk("зіпсований файл конфігурації → None без винятку", ga.property_id({}, cfg) is None)

# --- to_csv_rows ---------------------------------------------------------------
rows = [row("20261114", "tiktok", "influencer", "irina", 12, 10, 1, "450.5"), row("20261113", "(direct)", "(none)", "(not set)", 40, 35, 0, "0"),
        {"dimensionValues": [{"value": "x"}], "metricValues": []}, "сміття", row("20261114", "google", "cpc", "brand", 5, 5, 2, "1200")]
out = ga.to_csv_rows(rows)
chk("рядки перетворено: дата YYYY-MM-DD, відсортовано; некоректні відкинуто (3 з 5)", len(out) == 3 and out[0][0] == "2026-11-13" and out[1][1] == "google")
tt = next(r for r in out if r[1] == "tiktok")
chk("tiktok-рядок: сесії 12, користувачі 10, покупки 1, дохід 450.5", tt[4:] == ["12", "10", "1", 450.5] and tt[3] == "irina")
chk("'(not set)' і '(direct)' лишаються як факт GA4", any(r[3] == "(not set)" and r[1] == "(direct)" for r in out))

# --- run_report: запит і пагінація ----------------------------------------------
s = Sess([Resp(200, {"rows": [row("20261114", "a", "b", "c", 1, 1, 0, 0)]})])
r = ga.run_report(s, "123", "2026-10-18", "2026-11-14")
u, body = s.calls[0]
chk("URL з ID ресурсу; виміри date/sessionSource/sessionMedium/sessionCampaignName; метрики sessions/activeUsers/transactions/purchaseRevenue",
    u.endswith("/properties/123:runReport") and [d["name"] for d in body["dimensions"]] == ga.DIMENSIONS and [m["name"] for m in body["metrics"]] == ga.METRICS and len(r) == 1)
orig = ga.ROW_LIMIT
ga.ROW_LIMIT = 2
try:
    s2 = Sess([Resp(200, {"rows": [row("20261114", "a", "b", "c", 1, 1, 0, 0)] * 2}), Resp(200, {"rows": [row("20261113", "a", "b", "c", 1, 1, 0, 0)]})])
    r2 = ga.run_report(s2, "123", "x", "y")
finally:
    ga.ROW_LIMIT = orig
chk("пагінація: друга сторінка з offset=2, усього 3 рядки", len(r2) == 3 and s2.calls[1][1]["offset"] == 2)
bad = Sess([Resp(403, {"error": {"message": "User does not have sufficient permissions", "errors": [{"reason": "forbidden"}]}})])
try:
    ga.run_report(bad, "123", "x", "y")
    raised = None
except RuntimeError as e:
    raised = str(e)
chk("403 (немає доступу) → RuntimeError з HTTP 403 (не ApiDisabled)", raised and "HTTP 403" in raised)
dis = Sess([Resp(403, {"error": {"message": "API not enabled", "errors": [{"reason": "accessNotConfigured"}]}})])
try:
    ga.run_report(dis, "123", "x", "y")
    dis_raised = False
except gsc.ApiDisabled:
    dis_raised = True
chk("accessNotConfigured → ApiDisabled", dis_raised)

# --- .env (аудит #1550: служба не має EnvironmentFile, тож ID із .env читаємо самі) ---------
_envdir = Path(tempfile.mkdtemp())
(_envdir / ".env").write_text("GA4_PROPERTY_ID=4242\n", encoding="utf-8")
_saved_home, _saved_cfg = ga.HERE, ga.CONFIG_FILE
os.environ.pop("GA4_PROPERTY_ID", None)
ga.HERE, ga.CONFIG_FILE = _envdir, _envdir / "absent.json"
try:
    chk("до _load_env() ID із .env не видно (немає в оточенні)", ga.property_id() is None)
    ga._load_env()
    chk("після _load_env(): GA4_PROPERTY_ID із .env підхоплено", ga.property_id() == "4242")
finally:
    ga.HERE, ga.CONFIG_FILE = _saved_home, _saved_cfg
    os.environ.pop("GA4_PROPERTY_ID", None)

# --- main ------------------------------------------------------------------------
alerts = []
gsc._alert = lambda key, text: alerts.append((key, text))
ga.OUT_DIR = td / "out"
os.environ.pop("GA4_PROPERTY_ID", None)
ga.CONFIG_FILE = td / "absent.json"
ga.make_session = lambda: (_ for _ in ()).throw(AssertionError("не повинно викликатись без ID"))
chk("без ID: main → 0, сесію не створює, алерту нема, файл не пише", ga.main([]) == 0 and not alerts and not (td / "out").exists())
os.environ["GA4_PROPERTY_ID"] = "555"
ga.make_session = lambda: Sess([Resp(200, {"rows": [row("20261114", "tiktok", "influencer", "irina", 12, 10, 1, "450.5")]})])
code = ga.main([])
files = list((td / "out").glob("ga4_channels_*.csv"))
rd = list(csv.reader(open(files[0], encoding="utf-8-sig"))) if files else []
chk("з ID: файл ga4_channels_<дата>.csv з шапкою і рядком tiktok", code == 0 and rd and rd[0] == ga.FIELDS and rd[1][1] == "tiktok" and rd[1][4] == "12")
sess_check = Sess([Resp(200, {"rows": []})])
ga.make_session = lambda: sess_check
n_before = len(list((td / "out").glob("*")))
chk("--check: один запит, нічого не пише, код 0", ga.main(["--check"]) == 0 and len(sess_check.calls) == 1 and len(list((td / "out").glob("*"))) == n_before)
ga.make_session = lambda: Sess([Resp(403, {"error": {"message": "no access", "errors": [{"reason": "forbidden"}]}})])
alerts.clear()
chk("403: main → 1, алерт ga4_report_fail з підказкою про роль «Глядач»", ga.main([]) == 1 and alerts and alerts[0][0] == "ga4_report_fail" and "Глядач" in alerts[0][1])
ga.make_session = lambda: Sess([Resp(403, {"error": {"message": "x", "errors": [{"reason": "accessNotConfigured"}]}})])
alerts.clear()
chk("API вимкнено: main → 0 (очікувана ситуація), алерт ga4_api_disabled", ga.main([]) == 0 and alerts and alerts[0][0] == "ga4_api_disabled")

# --- інтеграція в доставку/службу -------------------------------------------------
import daily_files_to_shared as dl  # noqa: E402
chk("daily_files_to_shared.PATTERNS містить ga4_channels_ (файл доїде в SEO_метрики)", "ga4_channels_" in dl.PATTERNS)
svc = Path(__file__).resolve().parent / "gmc-status-report.service"
txt = svc.read_text(encoding="utf-8")
chk("gmc-status-report.service: крок ga4_report.py з «-» (збій не валить службу), venv-python, після gsc_report.py",
    "ExecStart=-/opt/plutustoys/venv/bin/python3 /opt/plutustoys/ga4_report.py" in txt and txt.index("ga4_report.py") > txt.index("gsc_report.py"))

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
