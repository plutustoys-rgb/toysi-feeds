# -*- coding: utf-8 -*-
"""ga4_report.py — щоденний знімок Google Analytics 4 «звідки приходять сесії і покупки» (лише читання; запит SMM/власника 15.11.2026: виміряти рекламу блогера по utm).

ЧОМУ. Тег GA4 стоїть на сайті з 21.10 (site/build_site.py analytics_head), але ЧИТАЧА не було: цифри з utm_source=tiktok/instagram/… ніхто не міг побачити. Цей скрипт — читач.
ДОСТУП (одноразово, дії ВЛАСНИКА): (1) GA4 → Адміністратор → Доступ до ресурсу → додати e-mail сервіс-акаунта (той самий, що читає Search Console/GMC; `client_email` у gmc_service_account.json) з роллю «Глядач»;
(2) у GCP-проєкті цього акаунта ввімкнути «Google Analytics Data API»; (3) повідомити числовий ID ресурсу GA4 (Адміністратор → Налаштування ресурсу → «Ідентифікатор ресурсу»). Він не секретний — кладеться у ga4_config.json або GA4_PROPERTY_ID у .env.
Без ID скрипт нічого не робить і не тривожить (ще не налаштовано). Не «Measurement ID» G-… — це інше.

ВИХІД (приватна папка reports/gmc/, не публікується): ga4_channels_YYYY-MM-DD.csv — за останні WINDOW_DAYS діб (до вчора): date, source, medium, campaign, sessions, users, transactions, revenue.
Доставляється на ПК у SEO_метрики/ (daily_files_to_shared.PATTERNS). Одиниця — рядок = (доба × source × medium × campaign); сесії/покупки з utm_source=tiktok відфільтровує читач файлу.
Збій (не «не налаштовано») — Telegram-алерт з throttle 24 год. Секретів у виводі й винятках нема.
CLI: python ga4_report.py [--check]    # --check: один мінімальний запит, нічого не пише
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import gsc_report as gsc

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
KEY_FILE = gsc.KEY_FILE
OUT_DIR = gsc.OUT_DIR
SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
API = "https://analyticsdata.googleapis.com/v1beta"
CONFIG_FILE = Path(os.environ.get("GA4_CONFIG_FILE", str(HERE / "ga4_config.json")))
WINDOW_DAYS = 28
ROW_LIMIT = 10000
ROWS_MAX = 100000
FIELDS = ["date", "source", "medium", "campaign", "sessions", "users", "transactions", "revenue"]
DIMENSIONS = ["date", "sessionSource", "sessionMedium", "sessionCampaignName"]
METRICS = ["sessions", "activeUsers", "transactions", "purchaseRevenue"]


def property_id(env=None, config_file: Path = None) -> str | None:
    """Числовий ID ресурсу GA4: GA4_PROPERTY_ID (.env) → ga4_config.json {"property_id": "…"}; None — не налаштовано. Лише цифри (захист від «G-…» і сміття)."""
    env = os.environ if env is None else env
    cand = str(env.get("GA4_PROPERTY_ID") or "").strip()
    if not cand:
        try:
            cand = str(json.loads(Path(config_file or CONFIG_FILE).read_text(encoding="utf-8")).get("property_id") or "").strip()
        except (OSError, ValueError, AttributeError):
            cand = ""
    return cand if cand.isdigit() else None


def _load_env() -> None:
    """GA4_PROPERTY_ID із /opt/plutustoys/.env (як у telegram_notify/site_chat_bot: службі не дано EnvironmentFile). Викликається з main(), не на імпорті; нема python-dotenv/файлу → мовчки далі (лишається ga4_config.json)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env")
    except Exception:  # noqa: BLE001
        pass


def make_session():
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
    except ImportError:
        raise SystemExit("[ga4_report] нема пакета google-auth: /opt/plutustoys/venv/bin/pip install google-auth")
    creds = service_account.Credentials.from_service_account_file(str(KEY_FILE), scopes=[SCOPE])
    return AuthorizedSession(creds)


def run_report(session, prop: str, start: str, end: str, rows_max: int = ROWS_MAX) -> list:
    """runReport з пагінацією offset → список {dimensionValues:[…], metricValues:[…]}."""
    url = f"{API}/properties/{prop}:runReport"
    out, offset = [], 0
    while len(out) < rows_max:
        body = {"dateRanges": [{"startDate": start, "endDate": end}], "dimensions": [{"name": n} for n in DIMENSIONS], "metrics": [{"name": n} for n in METRICS],
                "limit": ROW_LIMIT, "offset": offset, "orderBys": [{"dimension": {"dimensionName": "date"}}]}
        d = gsc._check(session.post(url, json=body, timeout=120))
        rows = d.get("rows") or []
        out.extend(rows)
        if len(rows) < ROW_LIMIT:
            break
        offset += ROW_LIMIT
    return out[:rows_max]


def to_csv_rows(rows: list) -> list:
    """GA4 рядки → [date(YYYY-MM-DD), source, medium, campaign, sessions, users, transactions, revenue]. Некоректні рядки (не 4 виміри/4 метрики) відкидаються. (not set) лишаємо як є — це факт GA4."""
    data = []
    for r in rows:
        try:
            dv = [x.get("value", "") for x in r["dimensionValues"]]
            mv = [x.get("value", "") for x in r["metricValues"]]
        except (KeyError, TypeError, AttributeError):
            continue
        if len(dv) != 4 or len(mv) != 4:
            continue
        d = dv[0]
        if len(d) == 8 and d.isdigit():
            d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        data.append([d, dv[1], dv[2], dv[3], mv[0], mv[1], mv[2], gsc._fmt(mv[3], 2)])
    data.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    return data


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    _load_env()
    prop = property_id()
    if not prop:
        print("[ga4_report] GA4_PROPERTY_ID / ga4_config.json не задано — пропуск (ще не налаштовано власником)")
        return 0
    try:
        session = make_session()
        today = datetime.now()
        end = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        start = (today - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
        if "--check" in argv:
            rows = run_report(session, prop, end, end, rows_max=1)
            print(f"[ga4_report] доступ є, ресурс {prop}, рядків за {end}: {len(rows)}")
            return 0
        data = to_csv_rows(run_report(session, prop, start, end))
        p = OUT_DIR / f"ga4_channels_{today:%Y-%m-%d}.csv"
        gsc.write_csv(p, FIELDS, data)
        print(f"[ga4_report] {p.name}: {len(data)} рядків ({start}…{end})")
        return 0
    except gsc.ApiDisabled:
        gsc._alert("ga4_api_disabled", "ℹ️ ga4_report: «Google Analytics Data API» не ввімкнено в GCP-проєкті сервіс-акаунта (дія власника: APIs & Services → Library → Google Analytics Data API → Enable).")
        print("[ga4_report] Analytics Data API вимкнено", file=sys.stderr)
        return 0
    except Exception as e:  # noqa: BLE001      # без тексту винятку: може містити URL/ідентифікатори
        msg = str(e)
        hint = " Схоже, сервіс-акаунт не доданий у GA4 з роллю «Глядач» (дія власника)." if "403" in msg else ""
        gsc._alert("ga4_report_fail", f"⚠️ ga4_report: збій ({type(e).__name__}: {msg[:140]}).{hint}")
        print(f"[ga4_report] збій: {type(e).__name__}: {msg[:140]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
