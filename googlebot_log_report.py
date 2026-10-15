# -*- coding: utf-8 -*-
"""googlebot_log_report.py — щоденне зведення Googlebot з Apache access-логів VPS (лише читання; запит SEO-агента 15.10.2026 «вимір за дні, не за тижні»).

Search Console «Crawl stats» запізнюється й агрегований; лог дає той самий факт щодня й показує реакцію Google на зміни (sitemap, lastmod, 410) уже наступної доби. Рахує ЛИШЕ рядки з UA «Googlebot» (IP відвідувачів у вихід не потрапляють).
Вихід (приватна папка reports/gmc/, не публікується): googlebot_daily_YYYY-MM-DD.csv (по добах: запити, коди 200/301/302/304/404/410/5xx, частки /product-, /category-, /catalog, старих /p… і /g… Prom, sitemap, robots…) і googlebot_summary_YYYY-MM-DD.txt (таблиця по добах, РАЗОМ, топ-20 шляхів, топ-15 404/410).
Час відповіді в журналі НЕ логується (формат combined без %D): у звіті це зазначено. Запуск: `python googlebot_log_report.py [днів=14] [csv] [txt]`; щодня в службі gmc-status-report (другий ExecStart, той самий таймер 06:30).
"""
import csv
import glob
import gzip
import io
import re
import sys
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import os
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
LOGDIR = os.environ.get("GOOGLEBOT_LOG_DIR", "/usr/local/apps/apache2/logs/")
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 14
_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports", "gmc")
os.makedirs(_OUT_DIR, exist_ok=True) if len(sys.argv) <= 2 else None
_STAMP = datetime.now().strftime("%Y-%m-%d")
OUT_CSV = sys.argv[2] if len(sys.argv) > 2 else os.path.join(_OUT_DIR, f"googlebot_daily_{_STAMP}.csv")
OUT_TXT = sys.argv[3] if len(sys.argv) > 3 else os.path.join(_OUT_DIR, f"googlebot_summary_{_STAMP}.txt")

LINE = re.compile(r'^(\S+) \S+ \S+ \[(\d{2})/(\w{3})/(\d{4}):[^\]]+\] "(\S+) (\S+)[^"]*" (\d{3}) (\S+) "[^"]*" "([^"]*)"')
MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
now = datetime.now()
since = (now - timedelta(days=DAYS)).date()

def open_log(p):
    if p.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(p), encoding="utf-8", errors="replace")
    return open(p, encoding="utf-8", errors="replace")

def cat(path):
    if path.startswith("/product-"): return "product"
    if path.startswith("/category-"): return "category"
    if re.match(r"^/catalog(_\d+)?\.html", path): return "catalog"
    if re.match(r"^/p\d+", path): return "old_prom_p"
    if re.match(r"^/g\d+", path): return "old_prom_g"
    if path.startswith("/sitemap") or path.endswith(".xml"): return "sitemap/xml"
    if path == "/robots.txt": return "robots"
    if path == "/" or path.startswith("/index"): return "home"
    if path.endswith((".css", ".js", ".png", ".jpg", ".webp", ".ico", ".svg", ".woff2")): return "assets"
    if path.endswith(".html"): return "other_html"
    return "other"

daily = defaultdict(Counter)
paths_all = Counter()
paths_404 = Counter()
paths_by_day = defaultdict(Counter)
variants = Counter()
files = sorted(glob.glob(LOGDIR + "access_log*"))
bad_files = []
for p in files:
    try:
        f = open_log(p)
    except OSError:
        continue
    try:
        with f:
            for line in f:
                if "Googlebot" not in line:
                    continue
                m = LINE.match(line)
                if not m:
                    continue
                _ip, d, mon, y, method, path, code, size, ua = m.groups()
                if "Googlebot" not in ua:
                    continue
                try:
                    day = datetime(int(y), MONTHS[mon], int(d)).date()
                except (KeyError, ValueError):
                    continue
                if day < since:
                    continue
                path = path.split("?")[0]
                k = day.isoformat()
                c = daily[k]
                c["requests"] += 1
                c[code if code in ("200", "301", "302", "304", "404", "410") else ("5xx" if code.startswith("5") else "other_code")] += 1
                c["cat_" + cat(path)] += 1
                paths_all[path] += 1
                paths_by_day[k][path] += 1
                if code in ("404", "410"):
                    paths_404[(code, path)] += 1
                v = re.search(r"Googlebot[-\w]*", ua)
                variants[v.group(0) if v else "Googlebot"] += 1
    except (OSError, EOFError, zlib.error, gzip.BadGzipFile):
        bad_files.append(os.path.basename(p))  # обрізаний .gz при ротації: беремо прочитане, не валимо звіт

cols = ["date", "requests", "200", "301", "302", "304", "404", "410", "5xx", "other_code", "cat_product", "cat_category", "cat_catalog", "cat_old_prom_p", "cat_old_prom_g", "cat_sitemap/xml", "cat_robots", "cat_home", "cat_assets", "cat_other_html", "cat_other"]
with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(cols)
    for k in sorted(daily):
        w.writerow([k] + [daily[k].get(c, 0) for c in cols[1:]])

lines = []
lines.append(f"Запити з UA Googlebot (без rDNS-перевірки) за {DAYS} діб з Apache access_log (повний день = доба 00:00–24:00 за часом сервера; поточна доба неповна). Файлів: {len(files)}. Час відповіді в журналі НЕ логується (формат combined без %D).")
lines.append("Варіанти UA: " + ", ".join(f"{k} {v}" for k, v in variants.most_common()))
lines.append("")
lines.append("Доба      запити   200   301   304   404  410  5xx | частки: product category catalog /p-мертві /g-мертві sitemap")
for k in sorted(daily):
    c = daily[k]; n = c["requests"] or 1
    def pct(x): return f"{100 * c.get(x, 0) / n:4.0f}%"
    lines.append(f"{k} {c['requests']:7d} {pct('200')} {pct('301')} {pct('304')} {pct('404')} {pct('410')} {pct('5xx')} |  {pct('cat_product')}   {pct('cat_category')}   {pct('cat_catalog')}   {pct('cat_old_prom_p')}   {pct('cat_old_prom_g')}   {pct('cat_sitemap/xml')}")
tot = Counter()
for c in daily.values():
    tot.update(c)
n = tot["requests"] or 1
lines.append("")
lines.append(f"РАЗОМ {tot['requests']} запитів: 200 {100*tot['200']/n:.1f}%, 301 {100*tot['301']/n:.1f}%, 304 {100*tot['304']/n:.1f}%, 404 {100*tot['404']/n:.1f}%, 410 {100*tot['410']/n:.1f}%, 5xx {100*tot['5xx']/n:.2f}%; /product- {100*tot['cat_product']/n:.1f}%, /category- {100*tot['cat_category']/n:.1f}%, старі /p… {100*tot['cat_old_prom_p']/n:.1f}%, старі /g… {100*tot['cat_old_prom_g']/n:.1f}%; інші коди (302/403/429/…) {tot['302'] + tot['other_code']} запитів")
if bad_files:
    lines.append("УВАГА: файли, прочитані не повністю (обрізаний/пошкоджений архів): " + ", ".join(bad_files))
lines.append("")
lines.append("ТОП-20 шляхів за період:")
for pth, cnt in paths_all.most_common(20):
    lines.append(f"  {cnt:6d}  {pth[:110]}")
if daily:
    last_full = (now.date() - timedelta(days=1)).isoformat()
    lines.append("")
    lines.append(f"ТОП-20 шляхів за останню повну добу {last_full}:")
    for pth, cnt in paths_by_day[last_full].most_common(20):
        lines.append(f"  {cnt:6d}  {pth[:110]}")
lines.append("")
lines.append("ТОП-15 шляхів з 404/410:")
for (code, pth), cnt in paths_404.most_common(15):
    lines.append(f"  {cnt:6d}  {code}  {pth[:110]}")
open(OUT_TXT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
print("\n".join(lines[:40]))
