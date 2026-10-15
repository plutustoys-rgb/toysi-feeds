# -*- coding: utf-8 -*-
"""gsc_report.py — щоденний знімок Google Search Console для SEO-агента (лише читання; запит SEO 15.10.2026, власник дозволив доступ: «даю»).

Сервіс-акаунт plutustoys-gmc-reader@… доданий у GSC властивість https://plutustoys.com.ua/ з рівнем «Обмежений доступ» (read-only); scope webmasters.readonly; ключ той самий, що в gmc_status_report (KEY_FILE).
Потрібно ОДНОРАЗОВО ввімкнути «Google Search Console API» у GCP-проєкті (дія власника в консолі: APIs & Services → Library → Google Search Console API → Enable). Поки API вимкнено — скрипт мовчки пише підказку й завершується кодом 0 (без алерту).

ВИХІД (приватна папка reports/gmc/, не публікується; YYYY-MM-DD = дата запуску):
  • gsc_query_page_YYYY-MM-DD.csv — searchanalytics.query за ОДНУ добу з відставанням 3 дні (дані GSC фінальні з ~2–3 днів): dimensions=query,page; clicks, impressions, ctr, position. До GSC_ROWS_MAX рядків.
  • gsc_daily_YYYY-MM-DD.csv — searchanalytics.query dimensions=date за 90 днів (до «сьогодні−3»): clicks, impressions, ctr, position.
  • gsc_sitemaps_YYYY-MM-DD.csv — sitemaps.list: path, lastSubmitted, lastDownloaded, isPending, errors, warnings, type, submitted (по кожному з 5 sitemap). Поле `indexed` Google позначив deprecated (завжди порожнє/0) — НЕ збираємо: індексацію дивитись у gsc_inspect (coverageState).
  • gsc_inspect_YYYY-MM-DD.csv — URL Inspection API (urlInspection.index.inspect) для перших INSPECT_N URL з sitemap-priority.xml: coverageState, indexingState, lastCrawlTime, googleCanonical, pageFetchState, robotsTxtState. Лише читання, без «запитів на індексацію».
Кожна частина незалежна: збій однієї не скасовує інших. Збій (не «API вимкнено») — Telegram-алерт з throttle. Код виходу: 0, якщо хоч одна частина записана або API вимкнено; 1 — якщо жодна частина не вдалась через помилку.
CLI: python gsc_report.py [--check]    # --check: один запит sites.list, нічого не пише
"""
import csv
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
KEY_FILE = Path(os.environ.get("GMC_SERVICE_ACCOUNT_FILE", "/opt/plutustoys/.secrets/gmc_service_account.json"))
OUT_DIR = Path(os.environ.get("GMC_REPORT_DIR", str(HERE / "reports" / "gmc")))
SITE_URL = os.environ.get("GSC_SITE_URL", "https://plutustoys.com.ua/")
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
API = "https://www.googleapis.com/webmasters/v3"
INSPECT_API = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
PRIORITY_SITEMAP = os.environ.get("GSC_PRIORITY_SITEMAP", "https://plutustoys.com.ua/sitemap-priority.xml")
LAG_DAYS = 3
DAILY_DAYS = 90
ROW_LIMIT = 25000            # максимум API на сторінку
ROWS_MAX = int(os.environ.get("GSC_ROWS_MAX", "100000"))     # запобіжник пагінації
INSPECT_N = 100
INSPECT_BUDGET_SEC = int(os.environ.get("GSC_INSPECT_BUDGET_SEC", "400"))      # загальний бюджет часу inspect: служба має TimeoutStartSec=900 на всі кроки; повільний Google не повинен вбити сусідні кроки

QP_FIELDS = ["date", "query", "page", "clicks", "impressions", "ctr", "position"]
DAILY_FIELDS = ["date", "clicks", "impressions", "ctr", "position"]
SITEMAP_FIELDS = ["path", "lastSubmitted", "lastDownloaded", "isPending", "isSitemapsIndex", "errors", "warnings", "type", "submitted"]
INSPECT_FIELDS = ["url", "verdict", "coverageState", "indexingState", "lastCrawlTime", "googleCanonical", "userCanonical", "pageFetchState", "robotsTxtState"]


class ApiDisabled(Exception):
    """Search Console API не ввімкнено в GCP-проєкті (SERVICE_DISABLED) — очікувана ситуація до дії власника."""


def _alert(key: str, text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=24 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[gsc_report] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


def make_session():
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
    except ImportError:
        raise SystemExit("[gsc_report] нема пакета google-auth: /opt/plutustoys/venv/bin/pip install google-auth")
    creds = service_account.Credentials.from_service_account_file(str(KEY_FILE), scopes=[SCOPE])
    return AuthorizedSession(creds)


def _check(r):
    """HTTP-відповідь → дані або виняток; SERVICE_DISABLED → ApiDisabled; без тексту відповіді в винятку (може містити ідентифікатори проєкту, але не секрети)."""
    code = getattr(r, "status_code", 200)
    if code >= 400:
        try:
            err = r.json().get("error", {})
        except Exception:  # noqa: BLE001
            err = {}
        reasons = {str(x.get("reason")) for x in (err.get("errors") or []) if isinstance(x, dict)}
        reasons |= {str(d.get("reason")) for d in (err.get("details") or []) if isinstance(d, dict)}
        if code == 403 and ("accessNotConfigured" in reasons or "SERVICE_DISABLED" in reasons):
            raise ApiDisabled()
        raise RuntimeError(f"HTTP {code}: {str(err.get('message', ''))[:160]}")
    return r.json()


def _site_path(site_url: str) -> str:
    return quote(site_url, safe="")


def query_rows(session, start: str, end: str, dimensions: list, site_url: str = SITE_URL, rows_max: int = ROWS_MAX):
    """searchanalytics.query з пагінацією startRow; повертає список {keys, clicks, impressions, ctr, position}."""
    url = f"{API}/sites/{_site_path(site_url)}/searchAnalytics/query"
    out, start_row = [], 0
    while len(out) < rows_max:
        body = {"startDate": start, "endDate": end, "dimensions": dimensions, "rowLimit": ROW_LIMIT, "startRow": start_row, "dataState": "final"}
        d = _check(session.post(url, json=body, timeout=120))
        rows = d.get("rows") or []
        out.extend(rows)
        if len(rows) < ROW_LIMIT:
            break
        start_row += ROW_LIMIT
    return out[:rows_max]


def _fmt(x, nd=4):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return ""


def write_csv(path: Path, fields: list, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(fields)
        w.writerows(rows)
    os.replace(tmp, path)


def part_query_page(session, today: datetime, out_dir: Path):
    day = (today - timedelta(days=LAG_DAYS)).strftime("%Y-%m-%d")
    rows = query_rows(session, day, day, ["query", "page"])
    data = [[day, r["keys"][0], r["keys"][1], r.get("clicks", 0), r.get("impressions", 0), _fmt(r.get("ctr")), _fmt(r.get("position"), 2)] for r in rows if len(r.get("keys") or []) == 2]
    p = out_dir / f"gsc_query_page_{today:%Y-%m-%d}.csv"
    write_csv(p, QP_FIELDS, data)
    return p, len(data)


def part_daily(session, today: datetime, out_dir: Path):
    end = (today - timedelta(days=LAG_DAYS)).strftime("%Y-%m-%d")
    start = (today - timedelta(days=LAG_DAYS + DAILY_DAYS - 1)).strftime("%Y-%m-%d")
    rows = query_rows(session, start, end, ["date"])
    data = sorted([[r["keys"][0], r.get("clicks", 0), r.get("impressions", 0), _fmt(r.get("ctr")), _fmt(r.get("position"), 2)] for r in rows if r.get("keys")])
    p = out_dir / f"gsc_daily_{today:%Y-%m-%d}.csv"
    write_csv(p, DAILY_FIELDS, data)
    return p, len(data)


def part_sitemaps(session, today: datetime, out_dir: Path, site_url: str = SITE_URL):
    d = _check(session.get(f"{API}/sites/{_site_path(site_url)}/sitemaps", timeout=60))
    data = []
    for s in d.get("sitemap") or []:
        contents = s.get("contents") or [{}]
        for c in contents:
            data.append([s.get("path", ""), s.get("lastSubmitted", ""), s.get("lastDownloaded", ""), s.get("isPending", ""), s.get("isSitemapsIndex", ""), s.get("errors", 0), s.get("warnings", 0), c.get("type", ""), c.get("submitted", "")])
    p = out_dir / f"gsc_sitemaps_{today:%Y-%m-%d}.csv"
    write_csv(p, SITEMAP_FIELDS, data)
    return p, len(data)


def priority_urls(n: int = INSPECT_N, sitemap_url: str = PRIORITY_SITEMAP) -> list:
    """Перші n <loc> зі sitemap-priority.xml нашого ж сайту (стабільний порядок файлу); нема файлу/мережі → []."""
    try:
        with urllib.request.urlopen(sitemap_url, timeout=30) as r:
            xml = r.read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"[gsc_report] priority-sitemap не прочитано: {type(e).__name__}", file=sys.stderr)
        return []
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)[:n]


def inspect_url(session, url: str, site_url: str = SITE_URL) -> list:
    d = _check(session.post(INSPECT_API, json={"inspectionUrl": url, "siteUrl": site_url}, timeout=60))
    ix = ((d.get("inspectionResult") or {}).get("indexStatusResult")) or {}
    return [url, ix.get("verdict", ""), ix.get("coverageState", ""), ix.get("indexingState", ""), ix.get("lastCrawlTime", ""), ix.get("googleCanonical", ""), ix.get("userCanonical", ""), ix.get("pageFetchState", ""), ix.get("robotsTxtState", "")]


def part_inspect(session, today: datetime, out_dir: Path, urls=None, budget_sec: int = INSPECT_BUDGET_SEC):
    urls = priority_urls() if urls is None else urls
    if not urls:
        raise RuntimeError("порожній перелік URL із priority-sitemap")
    data, errs, first_err = [], 0, ""
    t0 = time.monotonic()
    for i, u in enumerate(urls):
        if time.monotonic() - t0 > budget_sec:
            print(f"[gsc_report] inspect: бюджет {budget_sec} с вичерпано, перевірено {i} з {len(urls)} URL", file=sys.stderr)
            break
        try:
            data.append(inspect_url(session, u))
        except ApiDisabled:
            raise
        except Exception as e:  # noqa: BLE001 — один URL не валить решту
            errs += 1
            first_err = first_err or f"{type(e).__name__}: {str(e)[:120]}"
            data.append([u, f"ERROR {type(e).__name__}", "", "", "", "", "", "", ""])
    if data and errs == len(data):
        raise RuntimeError(f"URL Inspection: усі {errs} запитів із помилкою; перша: {first_err}")
    if not data:
        raise RuntimeError("URL Inspection: бюджет часу вичерпано до першого запиту")
    p = out_dir / f"gsc_inspect_{today:%Y-%m-%d}.csv"
    write_csv(p, INSPECT_FIELDS, data)
    return p, len(data)


PARTS = [("query_page", part_query_page), ("daily", part_daily), ("sitemaps", part_sitemaps), ("inspect", part_inspect)]


def run(session=None, out_dir: Path | None = None, now: datetime | None = None) -> int:
    now = now or datetime.now()
    out_dir = Path(out_dir or OUT_DIR)
    if session is None:
        if not KEY_FILE.exists():
            print(f"[gsc_report] нема ключа {KEY_FILE} — звіт не збирається")
            return 0
        try:
            session = make_session()
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[gsc_report] ключ не прочитано: {type(e).__name__}", file=sys.stderr)
            _alert("gsc_report_key", "⚠️ gsc_report: ключ сервіс-акаунта не прочитано (перевірити файл) — знімок Search Console не зібрано.")
            return 1
    ok, failed = [], []
    for name, fn in PARTS:
        try:
            p, n = fn(session, now, out_dir)
            ok.append(name)
            print(f"[gsc_report] {name}: {n} рядків → {p.name}")
        except ApiDisabled:
            print("[gsc_report] Search Console API не ввімкнено в GCP-проєкті: APIs & Services → Library → «Google Search Console API» → Enable (дія власника). Знімок не зібрано.")
            return 0
        except Exception as e:  # noqa: BLE001
            failed.append(f"{name} ({type(e).__name__}: {str(e)[:140]})")
            print(f"[gsc_report] {name}: збій {type(e).__name__}: {str(e)[:150]}", file=sys.stderr)
    if failed:
        _alert("gsc_report_part", f"⚠️ gsc_report: частини знімка Search Console не зібрано: {', '.join(failed)} (зібрано: {', '.join(ok) or 'нічого'}).")
    return 0 if ok else 1


def check(session=None) -> int:
    session = session or make_session()
    try:
        d = _check(session.get(f"{API}/sites", timeout=60))
    except ApiDisabled:
        print("[gsc_report] API вимкнено (Enable у GCP-консолі)")
        return 2
    sites = [s.get("siteUrl") for s in d.get("siteEntry") or []]
    print(f"[gsc_report] доступні властивості: {sites}; наша {SITE_URL} {'є' if SITE_URL in sites else 'НЕМАЄ'}")
    return 0 if SITE_URL in sites else 1


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else run())
