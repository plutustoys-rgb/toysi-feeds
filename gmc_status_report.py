# -*- coding: utf-8 -*-
"""gmc_status_report.py — щоденний звіт про статус і причини відхилення товарів у Google Merchant Center (лише читання; рішення власника 14.10.2026 «так», запит SEO-агента).

ЧОМУ API, А НЕ СКРЕЙПІНГ: кабінет GMC headless не читається (gmc_scraper.py), вручну в Chrome зависає. Merchant API (products/v1 accounts.products.list, scope https://www.googleapis.com/auth/content) віддає кожен
оброблений товар разом з productStatus: destinationStatuses[] (approved/pending/disapprovedCountries по reportingContext) і itemLevelIssues[] (code, severity, attribute, description…). Content API for Shopping Google вимкнув 18.08.2026
(сторінка developers.google.com/merchant/api/guides/products/overview), тому лише Merchant API. Форми полів звірені з довідником лише за назвами; код читає їх захисно (.get), перший живий прогін покаже точну форму.

АВТОРИЗАЦІЯ: сервіс-акаунт Google Cloud (JSON-ключ), який створює ВЛАСНИК і кладе на VPS (права 600; /opt/plutustoys — git-клон, тому `.secrets/` і `gmc_service_account*.json` в .gitignore): GMC_SERVICE_ACCOUNT_FILE (за замовчуванням /opt/plutustoys/.secrets/gmc_service_account.json). Email акаунта додається в
Merchant Center → Налаштування → Люди й доступ (роль лише читання). Merchant API вимагає ОДНОРАЗОВОЇ реєстрації GCP-проєкту (POST accounts/v1/accounts/{id}/developerRegistration:registerGcp з developerEmail) і ввімкненого Merchant API в проєкті; без цього перший прогін дасть 401/403 і алерт. Без ключа скрипт пише підказку й завершується кодом 0. Потрібен пакет google-auth (`/opt/plutustoys/venv/bin/pip install google-auth`).
ВИХІД (приватна папка reports/gmc/, не публікується): gmc_status_YYYY-MM-DD.csv (рядок на товар: offerId, мова, мітка, джерело, статус, коди й описи причин, країни, дата) і gmc_summary_YYYY-MM-DD.txt (кількості за статусом і топ причин).
Статус товару: disapproved — є destination з disapprovedCountries АБО issue із severity DISAPPROVED; pending — немає approved, але є pending; approved — є approved і немає disapproved; unknown — нема даних.
CLI: python gmc_status_report.py            # зібрати звіт
     python gmc_status_report.py --summary  # надрукувати останнє summary
"""
import csv
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
KEY_FILE = Path(os.environ.get("GMC_SERVICE_ACCOUNT_FILE", "/opt/plutustoys/.secrets/gmc_service_account.json"))
ACCOUNT_ID = os.environ.get("GMC_ACCOUNT_ID", "5824264949")
OUT_DIR = Path(os.environ.get("GMC_REPORT_DIR", str(HERE / "reports" / "gmc")))
API = "https://merchantapi.googleapis.com/products/v1"
SCOPE = "https://www.googleapis.com/auth/content"
PAGE_SIZE = 1000
MAX_PAGES = 200            # запобіжник від нескінченної пагінації (200×1000 товарів)

CSV_FIELDS = ["date", "offerId", "contentLanguage", "feedLabel", "dataSource", "status", "issue_codes", "issue_severities", "issue_descriptions", "disapproved_countries", "approved_countries", "pending_countries"]


def _alert(key: str, text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=24 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[gmc_status_report] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


def fetch_products(session, account_id: str = ACCOUNT_ID, page_size: int = PAGE_SIZE, max_pages: int = MAX_PAGES):
    """Усі товари акаунта (пагінація nextPageToken). session — об'єкт із .get(url, params=...) → response (.json(), .raise_for_status()); у проді AuthorizedSession."""
    url = f"{API}/accounts/{account_id}/products"
    token, pages = None, 0
    while pages < max_pages:
        params = {"pageSize": page_size}
        if token:
            params["pageToken"] = token
        r = session.get(url, params=params, timeout=120)
        r.raise_for_status()
        d = r.json()
        for p in d.get("products") or []:
            yield p
        token = d.get("nextPageToken")
        pages += 1
        if not token:
            return
    print(f"[gmc_status_report] ⚠️ досягнуто MAX_PAGES={max_pages}: вибірка може бути неповною", file=sys.stderr)


def _countries(dest_list, key):
    out = []
    for d in dest_list or []:
        for c in d.get(key) or []:
            if c not in out:
                out.append(c)
    return out


def product_row(p: dict, today: str) -> dict:
    ps = p.get("productStatus") or {}
    dests = ps.get("destinationStatuses") or []
    issues = ps.get("itemLevelIssues") or []
    dis, app, pen = _countries(dests, "disapprovedCountries"), _countries(dests, "approvedCountries"), _countries(dests, "pendingCountries")
    sev_dis = any(str(i.get("severity", "")).upper() == "DISAPPROVED" for i in issues)
    # одна причина (код+серйозність) може йти окремо для кількох reportingContext: у звіті дедуплікуємо за товаром, щоб топ причин не двоїв
    uniq, seen = [], set()
    for i in issues:
        key = (str(i.get("code", "")), str(i.get("severity", "")))
        if key not in seen:
            seen.add(key)
            uniq.append((key[0], key[1], str(i.get("description", ""))))
    if dis or sev_dis:
        status = "disapproved"
    elif app:
        status = "approved"
    elif pen:
        status = "pending"
    else:
        status = "unknown"
    return {
        "date": today,
        "offerId": p.get("offerId", ""),
        "contentLanguage": p.get("contentLanguage", ""),
        "feedLabel": p.get("feedLabel", ""),
        "dataSource": str(p.get("dataSource", "")).split("/")[-1],
        "status": status,
        "issue_codes": " | ".join(c for c, _, _ in uniq),
        "issue_severities": " | ".join(sv for _, sv, _ in uniq),
        "issue_descriptions": " | ".join(d for _, _, d in uniq),
        "disapproved_countries": ",".join(dis),
        "approved_countries": ",".join(app),
        "pending_countries": ",".join(pen),
    }


def summarize(rows: list, products_n: int | None = None) -> str:
    st = Counter(r["status"] for r in rows)
    codes = Counter()
    desc = {}
    for r in rows:
        cs = [c for c in r["issue_codes"].split(" | ") if c]
        ds = r["issue_descriptions"].split(" | ")
        for k, c in enumerate(cs):
            codes[c] += 1
            desc.setdefault(c, ds[k] if k < len(ds) else "")
    lines = [f"Merchant Center {ACCOUNT_ID}: товарів {len(rows)}; " + ", ".join(f"{k} {v}" for k, v in st.most_common())]
    lines.append("Топ причин (позицій з такою причиною):")
    for c, n in codes.most_common(15):
        lines.append(f"  {n:6d}  {c}  — {desc.get(c, '')[:120]}")
    return "\n".join(lines)


def write_report(rows: list, out_dir: Path, today: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_p, sum_p = out_dir / f"gmc_status_{today}.csv", out_dir / f"gmc_summary_{today}.txt"
    tmp = csv_p.with_name(csv_p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, csv_p)
    sum_p.write_text(summarize(rows) + "\n", encoding="utf-8")
    return csv_p, sum_p


def make_session():
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
    except ImportError:
        raise SystemExit("[gmc_status_report] нема пакета google-auth: /opt/plutustoys/venv/bin/pip install google-auth")
    creds = service_account.Credentials.from_service_account_file(str(KEY_FILE), scopes=[SCOPE])
    return AuthorizedSession(creds)


def run(session=None, out_dir: Path | None = None, now: datetime | None = None) -> int:
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    if session is None:
        if not KEY_FILE.exists():
            print(f"[gmc_status_report] нема ключа {KEY_FILE} — звіт не збирається (власник створює сервіс-акаунт Google Cloud, кладе JSON-ключ на VPS з правами 600 і додає його email у Merchant Center як читача).")
            return 0
        try:
            session = make_session()
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 — повідомлення може містити шлях/частину ключа: лише тип
            print(f"[gmc_status_report] ключ не прочитано: {type(e).__name__}", file=sys.stderr)
            _alert("gmc_report_key", "⚠️ gmc_status_report: ключ сервіс-акаунта не прочитано (перевірити файл і формат JSON) — звіт Merchant Center не зібрано.")
            return 1
    try:
        rows = [product_row(p, today) for p in fetch_products(session)]
    except Exception as e:  # noqa: BLE001
        print(f"[gmc_status_report] API збій: {type(e).__name__}: {str(e)[:150]}", file=sys.stderr)
        _alert("gmc_report_api", f"⚠️ gmc_status_report: запит до Merchant API не вдався ({type(e).__name__}) — звіт за {today} не зібрано (перевірити доступ сервіс-акаунта в Merchant Center і ввімкнений Merchant API).")
        return 1
    if not rows:
        print("[gmc_status_report] акаунт повернув 0 товарів — файл не пишемо (перевірити account id/доступ)", file=sys.stderr)
        _alert("gmc_report_empty", f"⚠️ gmc_status_report: Merchant API повернув 0 товарів для акаунта {ACCOUNT_ID} — звіт не записано.")
        return 1
    csv_p, sum_p = write_report(rows, Path(out_dir or OUT_DIR), today)
    print(summarize(rows))
    print(f"[gmc_status_report] записано {csv_p.name} і {sum_p.name} у {csv_p.parent}")
    return 0


if __name__ == "__main__":
    if "--summary" in sys.argv:
        files = sorted(OUT_DIR.glob("gmc_summary_*.txt"))
        print(files[-1].read_text(encoding="utf-8") if files else "звітів ще нема")
        sys.exit(0)
    sys.exit(run())
