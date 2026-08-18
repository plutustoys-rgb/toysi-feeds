"""prom_analytics_scraper.py — per-SKU аналітика видимості Prom за період(и) у CSV.

НАВІЩО (2026-08-18, запит R1 SEO-агента, чек-поінт 20.08): для заміру ефекту SEO-описів
(«до» 06–12.08 vs «після» 13–19.08) потрібні per-товарні покази/переходи/замовлення. Prom
дає це в кабінеті: `GET /cms_analytics/api/v1/product/products_summary` (той самий host і
storageState-патерн, що prom_cabinet_catalog.py). Публічний API цих даних НЕ має.

ЕНДПОІНТ (розвіданий живо): products_summary?dateStart=YYYY-MM-DD&dateEnd=YYYY-MM-DD&
sources=company_sites&sources=portal&sources=bigl&platforms=desktop&platforms=mobile&
platforms=app&sort=orders:desc&page=N&perPage=M&lang=uk → {data:[{id, name, viewsInCatalog,
productViews, orders, gmv, ...}], metadata}. `id` — ВНУТРІШНІЙ Prom-id, НЕ наш sku, тому
мапимо через /cms/product/list (prom_cabinet_catalog: у кожному рядку є і `id`, і `sku`).

ВИХІД: reports/prom_seo_pilot_stats_YYYY-MM-DD.csv — колонки sku, period, impressions,
clicks, orders (impressions=viewsInCatalog, clicks=productViews). Два періоди за замовч.:
before=2026-08-06..2026-08-12, after=2026-08-13..2026-08-19 (перевизначаються прапорцями).

САМОДІАГНОСТИКА (правило власника): у шапці CSV + лозі — скільки товарів віддав кабінет,
скільки змаплено на sku, скільки НЕ змаплено (Prom-id без нашого sku) і за який період.

ЛОКАЛЬНО (кабінетна сесія лише тут). Сесія протухла → PromCabinetError (--login у
prom_notifications_scraper). Запуск SEO-агентом 20.08 (щоб вікно «after» було повним).
"""
import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / ".local_secrets" / "prom_cabinet_state.json"
REPORTS_DIR = BASE_DIR / "reports"

SUMMARY_URL = "https://my.prom.ua/cms_analytics/api/v1/product/products_summary"
WARMUP_URL = "https://my.prom.ua/cms/statistic_summary"
NAV_TIMEOUT_MS = 45000
PER_PAGE = 100
MAX_PAGES = 400   # стеля проти нескінченного циклу

DEFAULT_WINDOWS = {"before": ("2026-08-06", "2026-08-12"), "after": ("2026-08-13", "2026-08-19")}


class PromCabinetError(Exception):
    pass


def _summary_query(date_start: str, date_end: str, page: int) -> str:
    return (f"?dateStart={date_start}&dateEnd={date_end}&lang=uk&sort=orders:desc"
            f"&sources=company_sites&sources=portal&sources=bigl"
            f"&platforms=desktop&platforms=mobile&platforms=app"
            f"&page={page}&perPage={PER_PAGE}&limit={PER_PAGE}")


def _fetch_json(page, url: str):
    r = page.request.get(url, headers={"Accept": "application/json"}, timeout=NAV_TIMEOUT_MS)
    if not r.ok:
        raise PromCabinetError(f"{url.split('?')[0]} → HTTP {r.status}: {r.text()[:200]}")
    return r.json()


def _id_to_sku(page) -> dict:
    """Мапа {Prom-id(str): sku} з /cms/product/list (кожен рядок має і id, і sku)."""
    out, pg = {}, 1
    while pg <= MAX_PAGES:
        data = _fetch_json(page, f"https://my.prom.ua/cms/product/list?page={pg}&per_page={PER_PAGE}")
        rows = data.get("products") or []
        if not rows:
            break
        for p in rows:
            pid, sku = p.get("id"), p.get("sku")
            if pid is not None and sku:
                out[str(pid)] = str(sku)
        pg += 1
    return out


def _fetch_window(page, date_start: str, date_end: str) -> list:
    """Усі per-товарні рядки аналітики за вікно (пагінація до порожньої сторінки)."""
    rows, pg = [], 1
    while pg <= MAX_PAGES:
        data = _fetch_json(page, SUMMARY_URL + _summary_query(date_start, date_end, pg))
        batch = data.get("data") or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < PER_PAGE:
            break
        pg += 1
    return rows


def run(windows: dict, out_csv: Path) -> dict:
    if not STATE_FILE.exists():
        raise PromCabinetError(f"нема кабінетної сесії ({STATE_FILE.name}) — `prom_notifications_scraper.py --login`")

    result_rows, diag = [], {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            page.goto(WARMUP_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            if "login" in page.url.lower() or "my.prom.ua" not in page.url:
                raise PromCabinetError(f"сесію не прийнято — редірект на {page.url} (треба --login)")
            id_sku = _id_to_sku(page)
            for period, (ds, de) in windows.items():
                rows = _fetch_window(page, ds, de)
                mapped = unmapped = 0
                for r in rows:
                    sku = id_sku.get(str(r.get("id")))
                    if not sku:
                        unmapped += 1
                        continue
                    mapped += 1
                    result_rows.append({
                        "sku": sku, "period": period,
                        "impressions": r.get("viewsInCatalog") or 0,
                        "clicks": r.get("productViews") or 0,
                        "orders": r.get("orders") or 0,
                    })
                diag[period] = {"dateStart": ds, "dateEnd": de, "returned": len(rows),
                                "mapped": mapped, "unmapped": unmapped}
        except PlaywrightTimeoutError as e:
            raise PromCabinetError(f"timeout кабінету: {e}")
        finally:
            browser.close()

    # САМОДІАГНОСТИКА в шапці CSV (коментарі #) + рядки
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        f.write(f"# prom_analytics_scraper {datetime.now().isoformat(timespec='minutes')} | id↔sku мапа: {len(id_sku)}\n")
        for period, d in diag.items():
            f.write(f"# {period}: {d['dateStart']}..{d['dateEnd']} — кабінет віддав {d['returned']}, "
                    f"змаплено на sku {d['mapped']}, БЕЗ нашого sku {d['unmapped']}\n")
        w = csv.DictWriter(f, fieldnames=["sku", "period", "impressions", "clicks", "orders"])
        w.writeheader()
        w.writerows(result_rows)

    print(f"[PromAnalytics] {out_csv} — рядків {len(result_rows)} по {len(windows)} вікнах.")
    for period, d in diag.items():
        print(f"[PromAnalytics]   {period} {d['dateStart']}..{d['dateEnd']}: віддано {d['returned']}, "
              f"змаплено {d['mapped']}, без sku {d['unmapped']}")
    return {"rows": len(result_rows), "diag": diag}


def _notify_fail(msg: str) -> None:
    try:
        sys.path.insert(0, str(BASE_DIR))
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[PromAnalytics] Telegram не надіслано: {e}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description="Per-SKU аналітика видимості Prom за період(и) → CSV.")
    ap.add_argument("--before", nargs=2, metavar=("START", "END"), help="вікно 'до' (YYYY-MM-DD YYYY-MM-DD)")
    ap.add_argument("--after", nargs=2, metavar=("START", "END"), help="вікно 'після'")
    ap.add_argument("--out", help="шлях CSV (за замовч. reports/prom_seo_pilot_stats_YYYY-MM-DD.csv)")
    a = ap.parse_args()
    windows = dict(DEFAULT_WINDOWS)
    if a.before:
        windows["before"] = tuple(a.before)
    if a.after:
        windows["after"] = tuple(a.after)
    out_csv = Path(a.out) if a.out else REPORTS_DIR / f"prom_seo_pilot_stats_{datetime.now().strftime('%Y-%m-%d')}.csv"
    try:
        run(windows, out_csv)
    except PromCabinetError as e:
        msg = f"🚨 prom_analytics_scraper: {e}"
        print(f"[PromAnalytics] {msg}", file=sys.stderr)
        _notify_fail(msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
