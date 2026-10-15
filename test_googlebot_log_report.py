#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_googlebot_log_report.py — щоденне зведення Googlebot з Apache-логів (googlebot_log_report.py, запит SEO 15.10).
Інваріанти: (1) рахує ЛИШЕ рядки з UA Googlebot (PerplexityBot, звичайні браузери ігноруються); (2) по добах: запити, коди 200/301/304/404/410/5xx, категорії шляхів (/product-, /category-, /catalog, старі /p…, /g…, sitemap, robots);
(3) читає і звичайні, і .gz-файли; доби поза вікном відкидаються; (4) у вихідних файлах НЕМАЄ IP-адрес відвідувачів; (5) порожній/відсутній каталог логів не валить скрипт. `python test_googlebot_log_report.py` → exit 0/1."""
import csv
import gzip
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def line(day, path, code, ua="Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", ip="66.249.65.4"):
    ts = day.strftime("%d/%b/%Y:12:00:00 +0300")
    return f'{ip} - - [{ts}] "GET {path} HTTP/1.1" {code} 1234 "-" "{ua}"\n'


today = datetime.now().date()
d1, d2, d_old = today - timedelta(days=2), today - timedelta(days=1), today - timedelta(days=30)
tmp = Path(tempfile.mkdtemp())
logs = tmp / "logs"
logs.mkdir()
(logs / "access_log").write_text(
    line(d2, "/product-1.html", 200) + line(d2, "/product-2.html", 200) + line(d2, "/p3139-old.html", 301) + line(d2, "/g55-old", 404) + line(d2, "/robots.txt", 200) + line(d2, "/sitemap.xml", 200)
    + line(d2, "/product-3.html", 410) + line(d2, "/catalog.html", 304) + line(d2, "/category-toys.html", 200) + line(d2, "/product-9.html", 503)
    + line(d2, "/product-5.html", 200, ua="Mozilla/5.0 (compatible; PerplexityBot/1.0)", ip="18.97.9.103")
    + line(d2, "/product-6.html", 200, ua="Mozilla/5.0 (Windows NT 10.0) Chrome/120", ip="203.0.113.77"), encoding="utf-8")
with gzip.open(logs / "access_log-backup-old.gz", "wt", encoding="utf-8") as g:
    g.write(line(d1, "/product-10.html", 200) + line(d1, "/product-11.html", 404) + line(d_old, "/product-99.html", 200))

out_csv, out_txt = tmp / "d.csv", tmp / "s.txt"
env = dict(os.environ, GOOGLEBOT_LOG_DIR=str(logs) + os.sep)
r = subprocess.run([sys.executable, str(HERE / "googlebot_log_report.py"), "14", str(out_csv), str(out_txt)], env=env, capture_output=True, text=True, encoding="utf-8")
chk("скрипт завершується кодом 0", r.returncode == 0)
rows = {x["date"]: x for x in csv.DictReader(open(out_csv, encoding="utf-8-sig"))}
a, b = rows.get(d2.isoformat()), rows.get(d1.isoformat())
chk("доба d-1: 10 запитів Googlebot (PerplexityBot і Chrome не рахуються)", a is not None and a["requests"] == "10")
chk("коди d-1: 200→5, 301→1, 304→1, 404→1, 410→1, 5xx→1", a is not None and (a["200"], a["301"], a["304"], a["404"], a["410"], a["5xx"]) == ("5", "1", "1", "1", "1", "1"))
chk("категорії d-1: product 5 (1,2,3,9 + ...), category 1, catalog 1, старі /p 1, /g 1, sitemap 1, robots 1", a is not None and (a["cat_product"], a["cat_category"], a["cat_catalog"], a["cat_old_prom_p"], a["cat_old_prom_g"], a["cat_sitemap/xml"], a["cat_robots"]) == ("4", "1", "1", "1", "1", "1", "1"))
chk(".gz читається: доба d-2 має 2 запити (200 і 404)", b is not None and b["requests"] == "2" and b["200"] == "1" and b["404"] == "1")
chk("доба поза вікном (30 днів тому) відкинута", d_old.isoformat() not in rows)
txt = out_txt.read_text(encoding="utf-8")
alltext = txt + out_csv.read_text(encoding="utf-8-sig")
chk("у вихідних файлах нема жодної IP-адреси відвідувачів", not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", alltext))
chk("summary: є РАЗОМ, топ шляхів, топ 404/410 і примітка про відсутній час відповіді", "РАЗОМ" in txt and "ТОП-20 шляхів" in txt and "404/410" in txt and "НЕ логується" in txt)

empty = tmp / "empty"
empty.mkdir()
env2 = dict(os.environ, GOOGLEBOT_LOG_DIR=str(empty) + os.sep)
r2 = subprocess.run([sys.executable, str(HERE / "googlebot_log_report.py"), "14", str(tmp / "e.csv"), str(tmp / "e.txt")], env=env2, capture_output=True, text=True, encoding="utf-8")
chk("порожній каталог логів: код 0, без винятку", r2.returncode == 0 and "Traceback" not in r2.stderr)
env3 = dict(os.environ, GOOGLEBOT_LOG_DIR=str(tmp / "нема") + os.sep)
r3 = subprocess.run([sys.executable, str(HERE / "googlebot_log_report.py"), "14", str(tmp / "n.csv"), str(tmp / "n.txt")], env=env3, capture_output=True, text=True, encoding="utf-8")
chk("відсутній каталог логів: код 0, без винятку", r3.returncode == 0 and "Traceback" not in r3.stderr)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
