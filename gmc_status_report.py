# -*- coding: utf-8 -*-
"""gmc_status_report.py — щоденний звіт про статус і причини відхилення товарів у Google Merchant Center (лише читання; рішення власника 14.10.2026 «так», запит SEO-агента).

ЧОМУ API, А НЕ СКРЕЙПІНГ: кабінет GMC headless не читається (gmc_scraper.py), вручну в Chrome зависає. Merchant API (products/v1 accounts.products.list, scope https://www.googleapis.com/auth/content) віддає кожен
оброблений товар разом з productStatus: destinationStatuses[] (approved/pending/disapprovedCountries по reportingContext) і itemLevelIssues[] (code, severity, attribute, description…). Content API for Shopping Google вимкнув 18.08.2026
(сторінка developers.google.com/merchant/api/guides/products/overview), тому лише Merchant API. Форми полів звірені з довідником лише за назвами; код читає їх захисно (.get), перший живий прогін покаже точну форму.

АВТОРИЗАЦІЯ: сервіс-акаунт Google Cloud (JSON-ключ), який створює ВЛАСНИК і кладе на VPS (права 600; /opt/plutustoys — git-клон, тому `.secrets/` і `gmc_service_account*.json` в .gitignore): GMC_SERVICE_ACCOUNT_FILE (за замовчуванням /opt/plutustoys/.secrets/gmc_service_account.json). Email акаунта додається в
Merchant Center → Налаштування → Люди й доступ (роль лише читання). Merchant API вимагає ОДНОРАЗОВОЇ реєстрації GCP-проєкту (POST accounts/v1/accounts/{id}/developerRegistration:registerGcp з developerEmail) і ввімкненого Merchant API в проєкті; без цього перший прогін дасть 401/403 і алерт. Без ключа скрипт пише підказку й завершується кодом 0. Потрібен пакет google-auth (`/opt/plutustoys/venv/bin/pip install google-auth`).
ВИХІД (приватна папка reports/gmc/, не публікується): gmc_status_YYYY-MM-DD.csv (рядок на товар: offerId, мова, мітка, джерело, статус, коди й описи причин, країни, дата) і gmc_summary_YYYY-MM-DD.txt (кількості за статусом і топ причин).
Статус товару: disapproved — є destination з disapprovedCountries АБО issue із severity DISAPPROVED; pending — немає approved, але є pending; approved — є approved і немає disapproved; unknown — нема даних.
CLI: python gmc_status_report.py                      # зібрати звіт
     python gmc_status_report.py --summary            # надрукувати останнє summary
     python gmc_status_report.py --check              # перевірити доступ: один запит products.list (pageSize=1), нічого не змінює
     python gmc_status_report.py --register EMAIL     # ОДНОРАЗОВО: зареєструвати GCP-проєкт у Merchant API (POST developerRegistration:registerGcp). Єдиний запис у Google; потребує, щоб
                                                      # сервіс-акаунт мав у Merchant Center доступ ADMIN (на час реєстрації; після неї понизити до STANDARD). EMAIL — звичайний акаунт Google, НЕ сервіс-акаунт.
"""
import csv
import json
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


def _stats_from_rows(rows: list):
    st = Counter(r["status"] for r in rows)
    codes = Counter()
    for r in rows:
        for c in [x.strip() for x in (r.get("issue_codes") or "").split("|") if x.strip()]:
            codes[c] += 1
    return st, codes


def _previous_csv(out_dir: Path, today: str):
    """Найновіший gmc_status_<дата>.csv СТАРШИЙ за today (або None)."""
    best = None
    for p in Path(out_dir).glob("gmc_status_*.csv"):
        d = p.stem.replace("gmc_status_", "")
        if d < today and (best is None or d > best[0]):
            best = (d, p)
    return best


def compare_with_previous(rows: list, out_dir: Path, today: str) -> tuple:
    """(рядки для summary, прапорці). Без числових порогів — лише ФАКТ зміни: прапорець 🚩, якщо approved ВПАВ проти попереднього знімка або з'явилась причина (з лічильником >0), якої НЕ було в жодному попередньому знімку (історія кодів — gmc_seen_codes.json, при першому запуску збирається з наявних CSV)."""
    out_dir = Path(out_dir)
    seen_file = out_dir / "gmc_seen_codes.json"
    cur_st, cur_codes = _stats_from_rows(rows)
    prev = _previous_csv(out_dir, today)
    seen = None            # {код причини: дата ПЕРШОЇ появи}; повторний запуск того ж дня не стирає прапорець «нова» (first_seen == today)
    try:
        raw = json.loads(seen_file.read_text(encoding="utf-8"))
        seen = {c: "0000-00-00" for c in raw} if isinstance(raw, list) else {str(k): str(v) for k, v in raw.items()}
    except (OSError, ValueError, AttributeError):
        seen = None
    if seen is None:
        seen = {}
        for p in sorted(out_dir.glob("gmc_status_*.csv")):
            d = p.stem.replace("gmc_status_", "")
            if d < today:
                try:
                    with open(p, encoding="utf-8-sig", newline="") as f:
                        for c in _stats_from_rows(list(csv.DictReader(f)))[1]:
                            seen.setdefault(c, d)
                except OSError:
                    continue
    lines, flags = [], []
    if prev is None:
        lines.append("Порівняння з попереднім днем: попереднього знімка нема (перший запуск).")
    else:
        pd, pp = prev
        with open(pp, encoding="utf-8-sig", newline="") as f:
            prev_rows = list(csv.DictReader(f))
        pst, pcodes = _stats_from_rows(prev_rows)
        lines.append(f"Порівняння з {pd}: " + ", ".join(f"{k} {pst.get(k, 0)}→{cur_st.get(k, 0)} ({cur_st.get(k, 0) - pst.get(k, 0):+d})" for k in ("approved", "disapproved", "unknown")))
        ch = sorted(((cur_codes.get(c, 0) - pcodes.get(c, 0), c) for c in set(cur_codes) | set(pcodes)), key=lambda x: -abs(x[0]))[:6]
        if any(d for d, _ in ch):
            lines.append("Найбільші зміни причин: " + "; ".join(f"{c} {pcodes.get(c, 0)}→{cur_codes.get(c, 0)} ({d:+d})" for d, c in ch if d))
        if cur_st.get("approved", 0) < pst.get("approved", 0):
            flags.append(f"🚩 approved ВПАВ: {pst.get('approved', 0)} → {cur_st.get('approved', 0)} ({cur_st.get('approved', 0) - pst.get('approved', 0):+d}); усього товарів {sum(pst.values())} → {sum(cur_st.values())}, disapproved {pst.get('disapproved', 0)} → {cur_st.get('disapproved', 0)}, unknown {pst.get('unknown', 0)} → {cur_st.get('unknown', 0)} (щоб відрізнити падіння якості від скорочення фіду)")
    new = sorted(c for c, n in cur_codes.items() if n > 0 and (c not in seen or seen[c] == today))
    if new and (prev is not None or seen):
        flags.append("🚩 НОВА причина (раніше в звітах не було): " + ", ".join(f"{c} ({cur_codes[c]})" for c in new))
    try:
        tmp = seen_file.with_name(seen_file.name + ".tmp")
        for c in cur_codes:
            seen.setdefault(c, today)
        tmp.write_text(json.dumps(seen, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, seen_file)
    except OSError:
        pass
    if flags:
        lines.append("ПРАПОРЦІ:")
        lines += ["  " + f for f in flags]
    else:
        lines.append("Прапорців нема (approved не впав, нових причин нема).")
    return lines, flags


def write_report(rows: list, out_dir: Path, today: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_p, sum_p = out_dir / f"gmc_status_{today}.csv", out_dir / f"gmc_summary_{today}.txt"
    tmp = csv_p.with_name(csv_p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, csv_p)
    try:
        cmp_lines, _ = compare_with_previous(rows, out_dir, today)
    except Exception as e:  # noqa: BLE001 — порівняння допоміжне: не губить сам звіт
        cmp_lines = [f"Порівняння з попереднім днем не вдалось: {type(e).__name__}"]
    sum_p.write_text(summarize(rows) + "\n\n" + "\n".join(cmp_lines) + "\n", encoding="utf-8")
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


def register_gcp(session, developer_email: str, account_id: str = ACCOUNT_ID):
    """POST accounts/v1/accounts/{id}/developerRegistration:registerGcp {developerEmail}. Повертає (ok, текст без секретів)."""
    if not developer_email or "@" not in developer_email or developer_email.lower().endswith("iam.gserviceaccount.com"):
        return False, "developerEmail має бути звичайним Google-акаунтом (не сервіс-акаунтом)"
    url = f"https://merchantapi.googleapis.com/accounts/v1/accounts/{account_id}/developerRegistration:registerGcp"
    r = session.post(url, json={"developerEmail": developer_email}, timeout=60)
    if getattr(r, "status_code", 200) >= 400:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            msg = ""
        return False, f"HTTP {r.status_code}: {str(msg)[:200]}"
    d = r.json()
    return True, f"зареєстровано: {d.get('name', '')}, gcpIds={d.get('gcpIds')}"


def check_access(session, account_id: str = ACCOUNT_ID):
    """Один запит products.list (pageSize=1): (ok, текст)."""
    r = session.get(f"{API}/accounts/{account_id}/products", params={"pageSize": 1}, timeout=60)
    if getattr(r, "status_code", 200) >= 400:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            msg = ""
        return False, f"HTTP {r.status_code}: {str(msg)[:200]}"
    n = len(r.json().get("products") or [])
    return True, f"доступ є, products.list відповів ({n} товар у пробному запиті)"


if __name__ == "__main__":
    if "--register" in sys.argv or "--check" in sys.argv:
        if not KEY_FILE.exists():
            print(f"[gmc_status_report] нема ключа {KEY_FILE}")
            sys.exit(1)
        sess = make_session()
        if "--register" in sys.argv:
            i = sys.argv.index("--register")
            ok, msg = register_gcp(sess, sys.argv[i + 1] if len(sys.argv) > i + 1 else "")
        else:
            ok, msg = check_access(sess)
        print(("OK: " if ok else "ПОМИЛКА: ") + msg)
        sys.exit(0 if ok else 1)
    if "--summary" in sys.argv:
        files = sorted(OUT_DIR.glob("gmc_summary_*.txt"))
        print(files[-1].read_text(encoding="utf-8") if files else "звітів ще нема")
        sys.exit(0)
    sys.exit(run())
