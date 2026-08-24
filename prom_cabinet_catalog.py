"""prom_cabinet_catalog.py — ПОВНИЙ каталог Prom через КАБІНЕТ (обхід капу 499 API).

ПРОБЛЕМА: Prom API /products/list капить ~499 товарів на group_id (кореневі групи саме такі),
last_id/дата кап не пробивають → fetch_prom_products() бачить лише ~1419 із ~6000. Наслідок
(перевірено 2026-08-17): (1) find_stale_external_ids позначав НЕбачені товари як «зниклі» →
видаляло наосліп («втрачаємо товари»); (2) competitor-скан не бачив ~4300 товарів → вони соло,
без конкурента, тож нас підрізали («втрачаємо конкурентність»).

РІШЕННЯ: кабінетний CMS-ендпоінт `GET my.prom.ua/cms/product/list?page=N&per_page=100` під
storageState-сесією (як rozetka_price_monitor / prom_notifications_scraper) віддає ВЕСЬ каталог
через пагінацію (item_count у pagination). Дає `sku`(=наш external_id), `status`, `presence`,
`unadvertised_reasons` (ЧОМУ товар не в каталозі), `underpriced` (сигнал неконкурентності),
`view_catalog_url`. Сесія — .local_secrets/prom_cabinet_state.json (keepalive тримає теплою).

READ-ONLY: лише навігація + читання. Секрет-сесія у .local_secrets (gitignore).

ЗАПУСК:
    python prom_cabinet_catalog.py --summary   # розклад: статуси, причини «не в каталозі», неконкурентні
    (як модуль) fetch_full_catalog() -> {sku: {...}}
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_DIR = Path(__file__).parent
STATE_FILE = Path(os.environ.get("PROM_CABINET_STATE_FILE",
                                 str(BASE_DIR / ".local_secrets" / "prom_cabinet_state.json")))
OUTPUT_FILE = BASE_DIR / ".local_secrets" / "prom_full_catalog.json"
# Денна append-only історія per-SKU (запит R2 SEO-агента 2026-08-18): один файл на добу,
# щоб мати часовий ряд видимості (position/orders_count/quality_steps) і форвардну експозицію
# — на відміну від OUTPUT_FILE, що перезаписується. НЕ фінансові поля (commission_preview НЕ
# беремо — там rate/amount; правило «фінданих у спільне не кладемо»).
HISTORY_DIR = BASE_DIR / "reports" / "prom_catalog_history"
CMS_URL = "https://my.prom.ua/cms/product/list"
NAV_TIMEOUT_MS = 45000
PER_PAGE = 100
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

# Поля, які тримаємо з кожного товару (діагностика + мапінг + сигнали видимості для SEO). НЕ
# весь товстий запис і НЕ фінанси (commission_preview свідомо виключено).
_KEEP = ("sku", "id", "status", "presence", "presence_title", "quantity_in_stock",
         "price", "unadvertised_reasons", "underpriced", "group_id", "category_id",
         "view_catalog_url", "selling_type",
         # R2: сигнали видимості/якості для SEO-атрибуції
         "orders_count", "product_opinion_count", "quality_steps", "position",
         "in_running_cpa", "in_running_cpc",
         # R3 (2026-08-20, запит SEO): РЕАЛЬНА повнота характеристик. quality_steps.attributes=true
         # вмикається від ПЕРШОГО ж атрибута → «зелена якість» ≠ повнота. Реальне покриття = ці два
         # лічильники (напр. 7 із 22). attributes_map={id_атрибута: id_значення} — ЯКІ саме заповнені
         # (бонус для пріоритезації; ключі звірено з живого /cms/product/list 2026-08-20).
         "category_attrs_count", "product_attrs_count", "attributes_map",
         # R4 (2026-08-21, D2 чисті фото Rozetka): image_url — ЧИСТЕ головне фото images.prom.ua (без
         # вотермарки, звірено живо). Джерело для _clean_pictures у generate_rozetka_feed замість
         # замерзлого prom_products_raw_cache (23.07). Апскейл _w100→_w1024 робить фід.
         "image_url")


class PromCabinetError(Exception):
    pass


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:
        print(f"[PromCat] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def fetch_full_catalog(per_page: int = PER_PAGE) -> dict:
    """Тягне ВЕСЬ каталог Prom через кабінет (пагінація CMS). Повертає {sku: {поля}}.
    Сесія протухла/збій → PromCabinetError (keepalive/--login у prom_notifications_scraper)."""
    if not STATE_FILE.exists():
        raise PromCabinetError(f"нема кабінетної сесії ({STATE_FILE.name}) — `prom_notifications_scraper.py --login`")
    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            page.goto(CMS_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            if "login" in page.url.lower() or "my.prom.ua" not in page.url:
                raise PromCabinetError(f"сесію не прийнято — редірект на {page.url} (треба --login)")
            pg, num_pages, item_count = 1, None, None
            while pg <= (num_pages or 10_000):
                r = page.request.get(f"{CMS_URL}?page={pg}&per_page={per_page}",
                                     headers={"Accept": "application/json"}, timeout=NAV_TIMEOUT_MS)
                if not r.ok:
                    raise PromCabinetError(f"CMS сторінка {pg} → HTTP {r.status}")
                data = r.json()
                pagin = data.get("pagination") or {}
                num_pages = pagin.get("num_pages") or pagin.get("last_page")
                item_count = pagin.get("item_count", item_count)
                rows = data.get("products") or []
                if not rows:
                    break
                for p in rows:
                    sku = str(p.get("sku") or "").strip()
                    if not sku:
                        continue
                    out[sku] = {k: p.get(k) for k in _KEEP}
                pg += 1
        except PlaywrightTimeoutError as e:
            raise PromCabinetError(f"timeout кабінету: {e}")
        finally:
            browser.close()
    # САМОДІАГНОСТИКА (правило власника: код має сам казати, якщо щось не так)
    if item_count and len(out) < item_count * 0.9:
        raise PromCabinetError(
            f"кабінет обіцяв {item_count} товарів, зібрано лише {len(out)} — пагінація неповна "
            f"(сесія протухла посеред / змінився CMS). НЕ довіряти цьому виду для рішень про видалення.")
    return out


def _write_daily_history(cat: dict) -> Path:
    """Денний знімок каталогу → reports/prom_catalog_history/YYYY-MM-DD.json (один файл на добу).
    Часовий ряд видимості для SEO (position/orders_count/quality_steps) + форвардна експозиція.
    Перезапис У МЕЖАХ доби (останній зріз дня) — але НЕ між добами (кожна доба свій файл)."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    path = HISTORY_DIR / f"{day}.json"
    path.write_text(json.dumps({"at": datetime.now().isoformat(timespec="minutes"),
                                "count": len(cat), "items": cat}, ensure_ascii=False), encoding="utf-8")
    return path


def summary() -> None:
    print("[PromCat] Тягну повний каталог Prom через кабінет...")
    try:
        cat = fetch_full_catalog()
    except PromCabinetError as e:
        msg = f"🚨 prom_cabinet_catalog: {e}"
        print(f"[PromCat] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps({"at": datetime.now().isoformat(), "count": len(cat),
                                       "items": cat}, ensure_ascii=False), encoding="utf-8")
    hist_path = _write_daily_history(cat)   # R2: денна історія (не перезапис між добами)
    # Прапорець стабільності (SEO+SMM 2026-08-23): рахуємо ПІСЛЯ запису сьогоднішнього знімка,
    # щоб серія враховувала свіжий день. Читає лише історію, тож безпечно; помилку не піднімаємо
    # (знімок уже записано — це головне), але сигналимо.
    try:
        import stable_days
        sd_path = stable_days.refresh()
        print(f"[PromCat] stable_days -> {os.path.relpath(sd_path, BASE_DIR)}")
    except Exception as e:  # noqa: BLE001 — розрахунок допоміжний, не має валити денний прогін
        print(f"[PromCat] WARN stable_days не оновлено: {e}", file=sys.stderr)
    st = Counter(v.get("status") for v in cat.values())
    pres = Counter(v.get("presence") for v in cat.values())
    underpriced = sum(1 for v in cat.values() if v.get("underpriced"))
    unadv = Counter()
    for v in cat.values():
        for reason in (v.get("unadvertised_reasons") or []):
            unadv[str(reason)] += 1
    print(f"[PromCat] ПОВНИЙ каталог: {len(cat)} товарів (API бачив ~1419) → {OUTPUT_FILE.name} "
          f"+ денна історія → {hist_path.relative_to(BASE_DIR)}")
    print(f"[PromCat] status: {dict(st)}")
    print(f"[PromCat] presence: {dict(pres)}")
    print(f"[PromCat] underpriced (неконкурентні за оцінкою Prom): {underpriced}")
    print(f"[PromCat] причини 'не в каталозі' (unadvertised_reasons): {dict(unadv.most_common(15))}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--summary", action="store_true", help="Тягне повний каталог + друкує розклад.")
    ap.parse_args()
    summary()


if __name__ == "__main__":
    main()
