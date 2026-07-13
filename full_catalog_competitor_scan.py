"""
full_catalog_competitor_scan.py — одноразовий (не щоденний) конкурентний
скан ПОВНОГО каталогу Toysi, готуючи дані для побудови нового топ-1000
(демонд + маржа + реальна конкурентність, той самий принцип, що й
підбір нових товарів 2026-07-13).

НАВІЩО ОКРЕМИЙ СКРИПТ, НЕ prom_competitor_pricer.py: той файл свідомо
сфокусований на щоденному топ-970/1000 (MIN_FULL_RUN_INTERVAL_HOURS=20,
--apply реально міняє живі ціни). Цей скрипт — суто інформаційний
(dry-run, НІКОЛИ не застосовує ціни в Prom) і працює на іншому,
набагато ширшому пулі (17 000+ SKU замість ~970) — окремий стан, окремий
workflow, не втручається в щоденний ритм репрайсера.

ОБСЯГ (оцінено 2026-07-13): повний каталог Toysi — 29 330 SKU, з них
"eligible" (той самий фільтр, що й select_top_items() — margin>=0, не
виключена категорія) — 17 193. Решта (уцінка, виключені категорії,
нульовий залишок/собівартість) ніколи не потраплять у топ-1000
незалежно від конкурентних даних — скановані не будуть.

ЧАС: виміряно емпірично на топ-970 (2026-07-13) — 1.52 с/SKU у dry-run
режимі (пошук без --apply). Повний eligible-пул (17 193) — ~7.25 год
сумарно. GitHub Actions job має жорсткий ліміт 6 годин незалежно від
timeout-minutes — тому обов'язкове розбиття на кілька прогонів.

БЕЗПЕКА (рішення власника, 2026-07-13): ризик rate-limit на
реверс-інженерний GraphQL-пошук Prom невідомий на такому обсязі (17-30x
звичайного добового навантаження репрайсера, ~970/добу). Тому:
- НІКОЛИ не --apply, лише читання/розрахунок.
- Розтягнуто на ~6 днів по ~3000 SKU/день (BATCH_SIZE нижче), не
  кілька прогонів поспіль за 1-2 дні.
- Ручний workflow_dispatch (full-catalog-scan.yml) — не автоматичний
  cron, щоб власник/сесія могли перевірити результат кожного дня перед
  наступним пакетом.

РЕЗЮМОВАНІСТЬ: стан (FULL_SCAN_STATE_FILE) зберігає вже скановані SKU
{sku: {cost, category_name, competitor_price, competitor_score,
margin_pct, price_category, scanned_at}}. Кожен запуск бере наступні
BATCH_SIZE ще НЕ сканованих SKU (за спаданням маржі — той самий порядок
пріоритету, що й у select_top_items(), про всяк випадок, якщо скан не
завершиться повністю за 6 днів) — ідемпотентно, безпечно перезапускати.

Запуск:
    python full_catalog_competitor_scan.py              # наступний пакет (BATCH_SIZE)
    python full_catalog_competitor_scan.py --batch-size 500  # менший пакет (тест)
    python full_catalog_competitor_scan.py --status      # лише прогрес, без сканування
"""
import argparse
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from parser import fetch_toysi_catalog
from generate_prom_feed import fetch_russian_text
from generate_prom_feed_top import is_excluded_category, _margin
from competitor_pricing import decide_price_for_platform
from prom_competitor_pricer import find_best_competitor, SEARCH_DELAY

BASE_DIR = Path(__file__).parent
FULL_SCAN_STATE_FILE = BASE_DIR / "full_catalog_scan_state.json"

# Узгоджено з власником 2026-07-13: ~3000/день, розтягнуто на ~6 днів —
# консервативний темп проти невідомого ризику rate-limit на 17-30x
# звичайного добового навантаження репрайсера.
BATCH_SIZE = 3000


def load_state() -> dict:
    if not FULL_SCAN_STATE_FILE.exists():
        return {}
    try:
        return json.loads(FULL_SCAN_STATE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def save_state(state: dict) -> None:
    FULL_SCAN_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _eligible_items(catalog: dict) -> dict:
    """Той самий фільтр, що й select_top_items() (generate_prom_feed_top.py)
    — margin>=0 (виключає stock=0/cost<=0) і не виключена категорія.
    Уцінка/виключені категорії ніколи не потраплять у топ-1000, тож
    скановані не будуть — економить ~41% обсягу (12 137 з 29 330 SKU)."""
    return {
        pid: item for pid, item in catalog.items()
        if _margin(item) >= 0 and not is_excluded_category(item)
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE,
                     help=f"Скільки ще не сканованих SKU обробити за цей запуск (дефолт {BATCH_SIZE}).")
    ap.add_argument("--status", action="store_true",
                     help="Лише показати прогрес (скільки скановано/лишилось), без жодного сканування.")
    args = ap.parse_args()

    print("[FullScan] Завантажую повний каталог Toysi...")
    catalog = fetch_toysi_catalog()
    if not catalog:
        print("[FullScan] Каталог порожній — зупиняюсь.", file=sys.stderr)
        sys.exit(1)

    eligible = _eligible_items(catalog)
    state = load_state()
    scanned_ids = set(state.keys())
    remaining_ids = set(eligible.keys()) - scanned_ids

    print(f"[FullScan] Повний каталог: {len(catalog)} | eligible (може потрапити в топ-1000): {len(eligible)}")
    print(f"[FullScan] Уже сканованих: {len(scanned_ids)} | лишилось: {len(remaining_ids)}")

    if args.status:
        return

    if not remaining_ids:
        print("[FullScan] Усі eligible SKU вже скановані — нема чого робити. "
              "Видали/перейменуй full_catalog_scan_state.json, щоб пересканувати з нуля.")
        return

    # Пріоритет — за спаданням маржі (той самий порядок, що й select_top_items()),
    # про всяк випадок, якщо повний скан не завершиться за заплановані ~6 днів —
    # найцінніші кандидати вже будуть скановані.
    ordered_remaining = sorted(remaining_ids, key=lambda pid: _margin(eligible[pid]), reverse=True)
    batch_ids = ordered_remaining[:args.batch_size]

    print(f"[FullScan] Пакет цього запуску: {len(batch_ids)} SKU "
          f"(~{len(batch_ids) * SEARCH_DELAY / 60:.1f} хв мінімум на затримки між запитами).")

    print("[FullScan] Завантажую російськомовні назви (кращий збіг з пошуком Prom)...")
    russian_text = fetch_russian_text()

    for i, pid in enumerate(batch_ids, start=1):
        item = eligible[pid]
        cost = float(item.get("price") or 0)
        name_ukr = (item.get("name") or "").strip()
        name_rus = (russian_text.get(pid, {}) or {}).get("name") or name_ukr
        category_name = item.get("category_name")

        competitor = find_best_competitor(name_rus, cost)
        min_competitor_price = competitor["price"] if competitor else None
        decision = decide_price_for_platform(cost, min_competitor_price, "prom", category_name)
        time.sleep(SEARCH_DELAY)

        state[pid] = {
            "cost": cost,
            "category_name": category_name,
            "competitor_price": competitor["price"] if competitor else None,
            "competitor_score": round(competitor["score"], 2) if competitor else None,
            "margin_pct": decision["margin_pct"],
            "price_category": decision["category"],
        }

        if i % 200 == 0:
            print(f"[FullScan] ...{i}/{len(batch_ids)} цього пакету оброблено, зберігаю проміжний стан...")
            save_state(state)

    save_state(state)
    scanned_now = len(scanned_ids) + len(batch_ids)
    print(f"[FullScan] Готово. Скановано цього разу: {len(batch_ids)}. "
          f"Всього скановано: {scanned_now}/{len(eligible)} eligible SKU "
          f"({scanned_now / len(eligible) * 100:.1f}%).")
    if scanned_now < len(eligible):
        print(f"[FullScan] Лишилось {len(eligible) - scanned_now} SKU — запусти ще раз "
              "(наступного дня, за планом) для продовження.")
    else:
        print("[FullScan] Повний скан eligible-пулу завершено!")


if __name__ == "__main__":
    main()
