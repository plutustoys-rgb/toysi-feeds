# -*- coding: utf-8 -*-
"""gmc_price_competitiveness.py — мітка «вигідний» для реклами Google (рішення власника 13.11.2026: «Реклама лише вигідних»; завдання Аудитора SEO).

ЩО РОБИТЬ: читає звіт Merchant API Reports `price_competitiveness_product_view` (лише читання: POST reports:search — це запит на ЧИТАННЯ звіту, нічого не змінює в акаунті),
для кожного товару (offerId) порівнює НАШУ ціну з контрольною (benchmark_price, країна UA) і пише знімок `reports/gmc/price_competitiveness_labels.json`.
Фід-генератор (generate_google_feed._build_xml) кладе з нього `g:custom_label_1` = `vyhidni` / `ni` в google_merchant_feed.xml і _ru.xml.

ПРАВИЛО (без вигаданих порогів; Аудитор SEO 13.11):
  • `vyhidni` ⇔ є контрольна ціна І наша ціна ≤ контрольної (та сама валюта);
  • усе інше — `ni`: дорожчі за контрольну, АБО контрольної ціни немає (доведено вигідний ≠ «не доведено протилежне»).
  • товар (offerId) один, хоч у звіті рядки uk і ru (одиниця = товар, не рядок); якщо рядків кілька — `vyhidni` лише коли ЖОДЕН рядок із контрольною ціною не дорожчий.
АВТОРИЗАЦІЯ: той самий сервіс-акаунт, що в gmc_status_report (scope content; `GMC_SERVICE_ACCOUNT_FILE`).
СВІЖІСТЬ: знімок старший за MAX_AGE_DAYS = ігнорується (мітки не ставляться, лишається те, що було б без цього фічі) + алерт; оновлення best-effort раз на добу з фід-прогону (refresh_if_stale).
НЕВІДОМІ ФОРМИ API: назву таблиці й стиль полів у MCQL документація не підтверджує (snake_case у прикладах) — тому кілька кандидатів по черзі; перший, що відповів, запам'ятовується в знімку (`query_variant`). Усі відмови API друкуються в `--check` повністю.
CLI: python gmc_price_competitiveness.py --check     # 5 рядків звіту як є (нічого не пишемо)
     python gmc_price_competitiveness.py             # зібрати знімок і надрукувати зведення
     python gmc_price_competitiveness.py --summary   # зведення останнього знімка
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ACCOUNT_ID = os.environ.get("GMC_ACCOUNT_ID", "5824264949")
OUT_DIR = Path(os.environ.get("GMC_REPORT_DIR", str(HERE / "reports" / "gmc")))
SNAPSHOT = OUT_DIR / "price_competitiveness_labels.json"
API = "https://merchantapi.googleapis.com/reports/v1"
COUNTRY = "UA"
PAGE_SIZE = 5000            # консервативно (аудит #1346: верхня межа reports:search могла бути менша за 100000); решту добирає nextPageToken
MAX_PAGES = 100
DEADLINE_SEC = 600         # загальний бюджет часу на ВЕСЬ запит до API (усі сторінки й варіанти): фід-прогін не має чекати годинами
MAX_AGE_DAYS = 7
REFRESH_AFTER_HOURS = 20
LABEL_YES, LABEL_NO = "vyhidni", "ni"
# (таблиця, префікс полів): у прикладах гіда — `SELECT offer_id FROM product_performance_view`; другий варіант — на випадок, якщо API вимагає camelCase.
QUERY_VARIANTS = (
    ("price_competitiveness_product_view", ("id", "offer_id", "report_country_code", "price", "benchmark_price")),
    ("PriceCompetitivenessProductView", ("id", "offerId", "reportCountryCode", "price", "benchmarkPrice")),
)


def _alert(key: str, text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=24 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[gmc_price_competitiveness] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


def build_query(table: str, fields) -> str:
    return f"SELECT {', '.join(fields)} FROM {table}"


def fetch_rows(session, query: str, account_id: str = ACCOUNT_ID, page_size: int = PAGE_SIZE, max_pages: int = MAX_PAGES, deadline: float | None = None) -> list:
    """Усі рядки звіту (пагінація nextPageToken). session.post(url, json=..., timeout=...) → response; у проді AuthorizedSession. Повертає список priceCompetitivenessProductView-обʼєктів."""
    url = f"{API}/accounts/{account_id}/reports:search"
    token, pages, out = None, 0, []
    while pages < max_pages:
        if deadline is not None and time.monotonic() > deadline:
            raise RuntimeError(f"звіт: перевищено бюджет часу {DEADLINE_SEC} с — повного знімка нема")
        body = {"query": query, "pageSize": page_size}
        if token:
            body["pageToken"] = token
        r = session.post(url, json=body, timeout=180)
        r.raise_for_status()
        d = r.json()
        for row in d.get("results") or []:
            v = row.get("priceCompetitivenessProductView")
            if isinstance(v, dict):
                out.append(v)
        token = d.get("nextPageToken")
        pages += 1
        if not token:
            return out
    raise RuntimeError(f"звіт: більше {max_pages} сторінок — зупинено запобіжником (повного знімка нема)")


def fetch_any(session, account_id: str = ACCOUNT_ID, variants=QUERY_VARIANTS) -> tuple:
    """Пробує кандидатів по черзі. → (rows, query_variant). Усі відмовили → RuntimeError з відповідями API (для канал/алерту)."""
    errors = []
    deadline = time.monotonic() + DEADLINE_SEC
    for table, fields in variants:
        q = build_query(table, fields)
        try:
            rows = fetch_rows(session, q, account_id, deadline=deadline)
            return rows, table
        except Exception as e:  # noqa: BLE001
            body = ""
            try:
                body = (getattr(getattr(e, "response", None), "text", "") or "")[:300]
            except Exception:  # noqa: BLE001
                pass
            errors.append(f"{table}: {type(e).__name__}: {str(e)[:160]} {body}".strip())
    raise RuntimeError("Merchant API Reports відмовив усім варіантам запиту: " + " | ".join(errors))


def _micros(price):
    """{"amountMicros": "123", "currencyCode": "UAH"} → (int micros, "UAH") або (None, None)."""
    if not isinstance(price, dict):
        return None, None
    try:
        return int(price.get("amountMicros")), str(price.get("currencyCode") or "")
    except (TypeError, ValueError):
        return None, None


def classify(rows: list, country: str = COUNTRY) -> tuple:
    """rows → (labels {offerId: 'vyhidni'|'ni'}, stats). Одиниця — offerId (рядки uk/ru одного товару зводяться в один).
    stats: units, vyhidni, ni_pricier, ni_no_benchmark, rows, rows_other_country, rows_no_offer."""
    per = {}
    st = {"rows": len(rows), "rows_other_country": 0, "rows_no_offer": 0}
    for r in rows:
        if (r.get("reportCountryCode") or r.get("report_country_code") or country) != country:
            st["rows_other_country"] += 1
            continue
        oid = str(r.get("offerId") or r.get("offer_id") or "").strip()
        if not oid:
            st["rows_no_offer"] += 1
            continue
        p, pc = _micros(r.get("price"))
        b, bc = _micros(r.get("benchmarkPrice") if "benchmarkPrice" in r else r.get("benchmark_price"))
        d = per.setdefault(oid, {"with_bench": 0, "pricier": 0, "ratio": None})
        if p is not None and b is not None and pc and pc == bc:
            d["with_bench"] += 1
            if p > b:
                d["pricier"] += 1
            if b > 0:
                try:
                    q = p / b
                except OverflowError:      # int поза діапазоном float — статистика не має валити мітки
                    q = None
                if q is not None and math.isfinite(q):
                    d["ratio"] = q if d["ratio"] is None else max(d["ratio"], q)      # кілька рядків товару (uk+ru) — найгірший (найдорожчий) із них
    labels = {}
    pricier = nobench = 0
    for oid, d in per.items():
        if d["with_bench"] and not d["pricier"]:
            labels[oid] = LABEL_YES
        else:
            labels[oid] = LABEL_NO
            if d["with_bench"]:
                pricier += 1
            else:
                nobench += 1
    st.update({"units": len(per), "vyhidni": sum(1 for v in labels.values() if v == LABEL_YES), "ni_pricier": pricier, "ni_no_benchmark": nobench})
    try:
        st["spread"] = spread_stats([d["ratio"] for d in per.values() if d.get("ratio") is not None])
    except Exception:  # noqa: BLE001 — статистика НЕ має впливати на мітки
        st["spread"] = None
    return labels, st


def _pct(sorted_vals: list, q: float) -> float:
    """Лінійна інтерполяція перцентиля q∈[0,1] на відсортованому списку."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def spread_stats(ratios: list) -> dict:
    """Розподіл відхилення нашої ціни від контрольної (МІРА, не поріг; мітки з неї не залежать). ratios = ціна/контрольна по товарах із контрольною ціною.
    → {n, pct{p10,p25,p50,p75,p90} (у % відхилення: +12.4 = на 12,4 % дорожче, −5 = на 5 % дешевше), pricier_median_pct, pricier_buckets{le5, 5_10, 10_20, 20_50, gt50}}."""
    pct = sorted(round((r - 1.0) * 100.0, 6) for r in ratios if r and r > 0 and math.isfinite(r))      # round: 110/100 дає 10.000000000000009 і потрапляло б не в той проміжок
    pricier = [x for x in pct if x > 0]
    b = {"le5": 0, "5_10": 0, "10_20": 0, "20_50": 0, "gt50": 0}
    for x in pricier:
        k = "le5" if x <= 5 else "5_10" if x <= 10 else "10_20" if x <= 20 else "20_50" if x <= 50 else "gt50"
        b[k] += 1
    return {"n": len(pct), "pct": {k: round(_pct(pct, q), 1) for k, q in (("p10", .10), ("p25", .25), ("p50", .50), ("p75", .75), ("p90", .90))},
            "pricier_median_pct": round(_pct(pricier, 0.5), 1) if pricier else None, "pricier_buckets": b}


def write_snapshot(labels: dict, stats: dict, variant: str, now: datetime | None = None, path: Path = SNAPSHOT) -> Path:
    now = now or datetime.now()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": now.strftime("%Y-%m-%d"), "generated": now.strftime("%Y-%m-%dT%H:%M:%S"), "country": COUNTRY, "query_variant": variant, "stats": stats, "labels": labels}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def spread_text(stats: dict) -> str:
    sp = (stats or {}).get("spread")
    if not sp or not sp.get("n"):
        return ""
    p, b = sp["pct"], sp["pricier_buckets"]
    return (f" РОЗПОДІЛ відхилення нашої ціни від контрольної (товарів з контрольною ціною {sp['n']}; + = дорожче, − = дешевше): "
            f"p10 {p['p10']:+}%, p25 {p['p25']:+}%, медіана {p['p50']:+}%, p75 {p['p75']:+}%, p90 {p['p90']:+}%. "
            f"Серед дорожчих (медіана перевищення {sp['pricier_median_pct']}%): до 5% — {b['le5']}, 5–10% — {b['5_10']}, 10–20% — {b['10_20']}, 20–50% — {b['20_50']}, понад 50% — {b['gt50']}.")


def summary_text(stats: dict, date: str, variant: str = "") -> str:
    return (f"Порівняння цін Google (Merchant API Reports), знімок {date}, країна {COUNTRY}{', таблиця ' + variant if variant else ''}. "
            f"ОДИНИЦЯ — товар (offerId); рядків звіту {stats.get('rows')} (uk+ru зведено). Товарів: {stats.get('units')}; "
            f"vyhidni (ціна ≤ контрольної): {stats.get('vyhidni')}; ni, бо дорожчі за контрольну: {stats.get('ni_pricier')}; "
            f"ni, бо контрольної ціни нема (серед товарів звіту): {stats.get('ni_no_benchmark')}; товари фіду, яких у звіті взагалі нема, теж отримують ni (рахувати: позицій фіду мінус units). Рядків іншої країни пропущено {stats.get('rows_other_country')}, без offerId {stats.get('rows_no_offer')}." + spread_text(stats))


def load_labels(path: Path = SNAPSHOT, max_age_days: int = MAX_AGE_DAYS, now: datetime | None = None) -> dict:
    """{offerId: label} із знімка або {} (нема файлу / битий / старший за max_age_days). Ніколи не кидає."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        gen = datetime.strptime(str(d.get("generated") or d.get("date"))[:10], "%Y-%m-%d")
        if ((now or datetime.now()) - gen) > timedelta(days=max_age_days):
            return {}
        lab = d.get("labels")
        return {str(k): v for k, v in lab.items() if v in (LABEL_YES, LABEL_NO)} if isinstance(lab, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def snapshot_age_hours(path: Path = SNAPSHOT, now: datetime | None = None):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        gen = datetime.strptime(str(d.get("generated"))[:19], "%Y-%m-%dT%H:%M:%S")
        return ((now or datetime.now()) - gen).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        return None


def make_session():
    from gmc_status_report import make_session as _ms
    return _ms()


def run(session=None, now: datetime | None = None, path: Path = SNAPSHOT) -> int:
    """Зібрати знімок. 0 — ок. Порожня відповідь/збій — алерт і знімок НЕ перезаписується (лишається попередній)."""
    now = now or datetime.now()
    try:
        session = session or make_session()
        rows, variant = fetch_any(session)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        msg = f"gmc_price_competitiveness: звіт порівняння цін недоступний — мітка vyhidni/ni НЕ оновлена. {type(e).__name__}: {str(e)[:500]}"
        print(msg, file=sys.stderr)
        _alert("gmc_price_comp_fail", msg)
        return 1
    labels, st = classify(rows)
    if not labels:
        msg = f"gmc_price_competitiveness: звіт порожній (рядків {st['rows']}, товарів 0) — знімок не перезаписано"
        print(msg, file=sys.stderr)
        _alert("gmc_price_comp_empty", msg)
        return 1
    write_snapshot(labels, st, variant, now, path)
    print(summary_text(st, now.strftime("%Y-%m-%d"), variant))
    return 0


_REFRESH_DONE = False


def refresh_if_stale(now: datetime | None = None, path: Path = SNAPSHOT, runner=None) -> None:
    """Best-effort оновлення зі шляху фід-генерації: лише якщо є ключ (або runner у тесті) і знімок старший за REFRESH_AFTER_HOURS. Раз за процес. Ніколи не кидає."""
    global _REFRESH_DONE
    if _REFRESH_DONE:
        return
    _REFRESH_DONE = True
    try:
        age = snapshot_age_hours(path, now)
        if age is not None and age < REFRESH_AFTER_HOURS:
            return
        if runner is None:
            from gmc_status_report import KEY_FILE
            if not Path(KEY_FILE).exists():
                return
            runner = run
        runner()
    except BaseException as e:  # noqa: BLE001 — SystemExit (нема google-auth) теж не валить фід
        print(f"[gmc_price_competitiveness] оновлення пропущено: {type(e).__name__}", file=sys.stderr)


def labels_for_feed() -> dict:
    """Для generate_google_feed: оновити (якщо треба) і віддати мітки; {} → міток нема."""
    refresh_if_stale()
    labels = load_labels()
    check_freshness_alert()
    return labels


STALE_WARN_HOURS = 36      # раннє попередження: знімок має оновлюватись ≤раз на 20 год; за 36 год щось не так (ключ, API, google-auth) — мітки ще діють до MAX_AGE_DAYS


def check_freshness_alert(path: Path = SNAPSHOT, now: datetime | None = None, key_exists: bool | None = None) -> str | None:
    """Раннє попередження в журнал алертів (→ health_check → Код; не Telegram): знімок цін старий/відсутній, а мітки від нього живлять рекламу Google Ads (SEO 14.11: якщо вони зникнуть, кампанія не покаже жодного товару).
    → код сигналу або None. Ніколи не кидає."""
    try:
        age = snapshot_age_hours(path, now)
        if age is None:
            if key_exists is None:
                from gmc_status_report import KEY_FILE
                key_exists = Path(KEY_FILE).exists()
            if key_exists:
                _alert("gmc_price_comp_missing", "gmc_price_competitiveness: знімка порівняння цін ще НЕМАЄ (ключ сервіс-акаунта є) — мітки vyhidni/ni не ставляться, реклама «лише вигідних» без товарів. Перевірити журнал фід-прогону.")
                return "missing"
            return None
        if age > MAX_AGE_DAYS * 24:
            _alert("gmc_price_comp_expired", f"gmc_price_competitiveness: знімок цін застарів ({age / 24:.1f} діб > {MAX_AGE_DAYS}) — мітки custom_label_1 ЗНИКЛИ з фіду, кампанія Google Ads не покаже жодного товару. Перевірити ключ/API.")
            return "expired"
        if age > STALE_WARN_HOURS:
            _alert("gmc_price_comp_old", f"gmc_price_competitiveness: знімок цін не оновлювався {age:.0f} год (норма ≤20) — мітки ще діють (до {MAX_AGE_DAYS} діб), але оновлення не працює. Перевірити журнал фід-прогону/ключ/API.")
            return "old"
    except Exception:  # noqa: BLE001
        return None
    return None


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--summary" in argv:
        try:
            d = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
            print(summary_text(d.get("stats") or {}, d.get("date") or "?", d.get("query_variant") or ""))
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"знімка нема або битий: {type(e).__name__}")
            return 1
    if "--check" in argv:
        s = make_session()
        for table, fields in QUERY_VARIANTS:
            q = build_query(table, fields) + " LIMIT 5"
            r = s.post(f"{API}/accounts/{ACCOUNT_ID}/reports:search", json={"query": q, "pageSize": 5}, timeout=60)
            print(f"== {table}: HTTP {r.status_code}")
            print(r.text[:1500])
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
