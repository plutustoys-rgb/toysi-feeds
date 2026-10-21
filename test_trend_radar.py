#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_trend_radar.py — радар трендів (trend_radar.py, 21.10.2026). Інваріанти: (1) основи слів: сквіш/сквіші/сквішів збігаються, службові слова ігноруються;
(2) журнал першої появи: перший запуск — дата з фіду Toysi або baseline (baseline НЕ потрапляє в радар), далі невідомі id = сьогодні (observed); існуючі записи не змінюються; товар, що зник і повернувся, не «новий»;
(3) вікно 7 діб включно; лише те, що зараз на сайті; (4) колонки vendor/постачальник/ціна/залишок; (5) join запитів top_growth за основою слова, без порогів, запит без збігу відкидається;
(6) main: два CSV, ідемпотентність, без index.json нічого не пишемо і журнал не чіпаємо."""
import csv
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import trend_radar as tr  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


chk("stems: сквіш/сквіші/сквішів → одна основа; «купити», «недорого», слова <4 символів — ігноруються",
    tr.stems("сквіш") == tr.stems("сквіші") == tr.stems("сквішів") == {"сквіш"} and tr.stems("купити недорого для ні") == set())
chk("stems: лабубу / Labubu — основи «лабуб» / «labub»; апостроф не ламає слово", tr.stems("Лабубу") == {"лабуб"} and tr.stems("Labubu") == {"labub"} and tr.stems("м'яка") == tr.stems("мяка"))

T = date(2026, 10, 22)
L = tr.update_ledger({}, ["1", "2", "3"], T, {"1": "2026-10-20", "2": "2025-01-01", "3": "", "9": "2026-10-21"})
chk("перший запуск: id з датою фіду → toysi_feed_date; без дати → baseline (дата = сьогодні, але з позначкою); id поза каталогом сайту в журнал не йдуть",
    L["seen"]["1"] == {"first": "2026-10-20", "src": "toysi_feed_date"} and L["seen"]["2"]["src"] == "toysi_feed_date" and L["seen"]["3"]["src"] == "baseline" and "9" not in L["seen"] and L["baseline_date"] == "2026-10-22")
chk("дата фіду з МАЙБУТНЬОГО → baseline (не вигадуємо)", tr.update_ledger({}, ["5"], T, {"5": "2027-01-01"})["seen"]["5"]["src"] == "baseline")
T2 = date(2026, 10, 24)
L = tr.update_ledger(L, ["1", "2", "3", "4"], T2, {})
chk("наступний запуск: невідомий id 4 → сьогодні, observed; існуючі записи НЕ змінились", L["seen"]["4"] == {"first": "2026-10-24", "src": "observed"} and L["seen"]["1"]["first"] == "2026-10-20" and L["seen"]["3"]["src"] == "baseline")
n = tr.new_ids(L, ["1", "2", "3", "4"], T2)
chk("new_ids: у вікні 7 діб (20–24.10): 1 (feed, 20.10) і 4 (observed, 24.10); 2 (2025) і 3 (baseline) — ні; найновіші першими", [r[1] for r in n] == ["4", "1"])
chk("new_ids: вікно включно — перший день (18.10 для 24.10) входить, 17.10 — ні", [r[1] for r in tr.new_ids({"seen": {"a": {"first": "2026-10-18", "src": "observed"}, "b": {"first": "2026-10-17", "src": "observed"}}}, ["a", "b"], T2)] == ["a"])
chk("new_ids: товар, якого зараз нема на сайті, не виводиться", tr.new_ids(L, ["1"], T2) == [("2026-10-20", "1", "toysi_feed_date")])
L = tr.update_ledger(L, ["1", "2", "3"], date(2026, 10, 30), {})
L = tr.update_ledger(L, ["1", "2", "3", "4"], date(2026, 11, 5), {})
chk("товар 4 зник і повернувся — дата першої появи лишилась 24.10 (не новий)", L["seen"]["4"]["first"] == "2026-10-24")

idx = {"4": {"id": "4", "n": "Сквіш Лабубу «Labubu» 12 см", "pr": 350, "c": "Сквіші", "s": 1}, "rt-7": {"id": "rt-7", "n": "М'яка іграшка Labubu Black", "pr": 500, "c": "М'які іграшки", "s": 1}}
rows = tr.product_rows([("2026-10-24", "4", "observed"), ("2026-10-24", "rt-7", "observed")], idx, {"4": {"vendor": "NoName", "stock": "5"}}, {"rt-7": {"vendor": "POP MART?", "stock": 12}})
chk("product_rows: постачальник, vendor, ціна, залишок, категорія сайту, дата та джерело дати", rows[0]["supplier"] == "Toysi" and rows[0]["vendor"] == "NoName" and rows[0]["stock"] == "5" and rows[0]["price"] == 350 and rows[0]["site_category"] == "Сквіші"
    and rows[1]["supplier"] == "RoyalToys" and rows[1]["vendor"] == "POP MART?" and rows[1]["stock"] == 12 and rows[1]["first_seen_source"] == "observed")
growth = [{"query": "лабубу іграшка купити", "clicks": "0", "impressions": "23", "position": "10.5", "extra": "prev_impressions=0"},
          {"query": "вишивка хрестиком набори", "clicks": "0", "impressions": "22", "position": "1.1", "extra": ""},
          {"query": "сквіші масло", "clicks": "0", "impressions": "12", "position": "3", "extra": ""}]
mq = tr.match_queries(growth, rows)
chk("match_queries: «лабубу…» і «сквіші…» збіглись з назвами нових товарів, «вишивка…» відкинуто; видно основи, кількість товарів і приклади id",
    [r["query"] for r in mq] == ["лабубу іграшка купити", "сквіші масло"] and "лабуб=" in mq[0]["new_products_with_stem"] and mq[0]["example_ids"].split()[0] in ("4", "rt-7") and "сквіш=1" in mq[1]["new_products_with_stem"])
chk("match_queries: порожній вхід → порожній вихід", tr.match_queries([], rows) == [] and tr.match_queries(growth, []) == [])

# main: два CSV, ідемпотентність, без index.json — нічого
td = Path(tempfile.mkdtemp())
site = td / "site"
site.mkdir()
out = td / "reports"
os.makedirs(out)
(site / "index.json").write_text(json.dumps([{"id": "10", "n": "Сквіш Мороженка", "pr": 100, "c": "Сквіші", "s": 1}, {"id": "11", "n": "Конструктор", "pr": 200, "c": "Конструктори", "s": 1}, {"id": "rt-5", "n": "Лабубу плюш", "pr": 300, "c": "М'які іграшки", "s": 1}]), encoding="utf-8")
(td / "toysi.json").write_text(json.dumps({"10": {"vendor": "V1", "stock": 3, "date": "2026-10-21"}, "11": {"vendor": "V2", "stock": 9, "date": ""}}), encoding="utf-8")
(td / "rt.json").write_text(json.dumps({"updated": "x", "items": {"rt-5": {"vendor": "V3", "stock": 4}}}), encoding="utf-8")
(out / "gsc_weekly_summary_2026-10-22.csv").write_text("section,key1,key2,clicks,impressions,position,extra\nweekly,2026-08-17,,1,395,52.8,queries=175\ntop_growth,сквіш масло,,0,12,1.0,prev_impressions=2\ntop_growth,вишивка,,0,22,1.1,x\ntop_drop,сквіш,,0,1,1,y\n", encoding="utf-8-sig")
tr.SITE_DIR, tr.OUT_DIR, tr.LEDGER, tr.TOYSI_CACHE, tr.SUPPLIER_RT = site, out, td / "ledger.json", td / "toysi.json", td / "rt.json"
rc = tr.main(date(2026, 10, 22))
r1 = list(csv.DictReader(open(out / "trend_radar_2026-10-22.csv", encoding="utf-8-sig")))
q1 = list(csv.DictReader(open(out / "trend_radar_queries_2026-10-22.csv", encoding="utf-8-sig")))
chk("main (перший запуск): новий лише id 10 (дата фіду 21.10); 11 і rt-5 — baseline, у радар не йдуть; запит «сквіш масло» збігся, «вишивка» і top_drop — ні",
    rc == 0 and [r["id"] for r in r1] == ["10"] and r1[0]["vendor"] == "V1" and r1[0]["first_seen_source"] == "toysi_feed_date" and [r["query"] for r in q1] == ["сквіш масло"])
rc = tr.main(date(2026, 10, 22))
r1b = list(csv.DictReader(open(out / "trend_radar_2026-10-22.csv", encoding="utf-8-sig")))
chk("повторний запуск того ж дня — той самий результат (ідемпотентно)", r1b == r1)
idx2 = json.loads((site / "index.json").read_text(encoding="utf-8")) + [{"id": "rt-6", "n": "Нова лабубу", "pr": 400, "c": "М'які іграшки", "s": 1}]
(site / "index.json").write_text(json.dumps(idx2), encoding="utf-8")
tr.main(date(2026, 10, 25))
r2 = list(csv.DictReader(open(out / "trend_radar_2026-10-25.csv", encoding="utf-8-sig")))
chk("наступні дні: з'явився rt-6 → observed 25.10, постачальник RoyalToys; id 10 (21.10) ще у вікні", {r["id"] for r in r2} == {"10", "rt-6"} and [r for r in r2 if r["id"] == "rt-6"][0]["supplier"] == "RoyalToys")
before = (td / "ledger.json").read_text(encoding="utf-8")
(site / "index.json").unlink()
tr.main(date(2026, 10, 26))
chk("без index.json: нічого не пишемо, журнал не чіпаємо, не падаємо", (td / "ledger.json").read_text(encoding="utf-8") == before and not (out / "trend_radar_2026-10-26.csv").exists())

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
