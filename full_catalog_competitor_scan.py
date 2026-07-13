"""
full_catalog_competitor_scan.py — одноразовий (не щоденний) конкурентний
скан ПОВНОГО каталогу Toysi, готуючи дані для побудови нового топ-1000.

СТРАТЕГІЯ (рішення власника, 2026-07-13, змінює попередній підхід): НЕ
відбирати спочатку за власною маржею й перевіряти конкурентів реактивно
(як довго лишалось непоміченим для SKU 242610/289818) — а спочатку
прогнати ВЕСЬ наявний каталог через реальну конкурентну ціну, і лише
ПОТІМ будувати топ-1000 із того, де ми реально сильні. Тому фільтр тут
навмисно ШИРШИЙ за select_top_items() — жодного попереднього відбору за
маржею, лише те, що взагалі може бути в каталозі (stock>0, не уцінка).

НАВІЩО ОКРЕМИЙ СКРИПТ, НЕ prom_competitor_pricer.py: той файл свідомо
сфокусований на щоденному топ-970/1000 (MIN_FULL_RUN_INTERVAL_HOURS=20,
--apply реально міняє живі ціни). Цей скрипт — суто інформаційний
(dry-run, НІКОЛИ не застосовує ціни в Prom) і працює на іншому,
набагато ширшому пулі — окремий стан, окремий workflow, не втручається
в щоденний ритм репрайсера.

ОБСЯГ (перевірено напряму 2026-07-13): повний каталог Toysi — 29 330
SKU. Scope (stock>0, не Уцінка/Уценка через is_clearance_item() з
generate_prom_feed.py) — 17 888 SKU.

ЧАС (реальний бенчмарк 40 SKU, 2026-07-13, find_best_competitor() +
verify_competitor_really_available() разом) — 2.521 с/SKU у середньому
(100% mатчів конкурента в тестовій вибірці). Повний scope (17 888) —
~12.5 год сумарно. GitHub Actions job має жорсткий ліміт 6 годин
незалежно від timeout-minutes — тому обов'язкове розбиття.

ГРУПУВАННЯ ЗА МОДЕЛЛЮ — розглянуто й ВІДХИЛЕНО: евристика "обрізати
дужки-варіанти в назві" дає лише ~14% скорочення обсягу, і, що
важливіше, ненадійна — дужки часто кодують СУТТЄВУ відмінність
(кількість елементів пазла: 80/120/140/240 шт — не колір), а не просто
варіант кольору. Ризик підмінити конкурентні дані для різних товарів
переважує невелику економію. Замість групування — чисте розбиття за
часом (BATCH_SIZE нижче).

БЕЗПЕКА (рішення власника, 2026-07-13): ризик rate-limit на
реверс-інженерний GraphQL-пошук Prom (і на звичайні сторінки товару
для verify_competitor_really_available()) невідомий на такому обсязі
(17-30x звичайного добового навантаження репрайсера, ~970/добу). Тому:
- НІКОЛИ не --apply, лише читання/розрахунок — verify_competitor_
  really_available() тут викликається для КОЖНОГО знайденого
  конкурента (не лише перед delist, як у prom_competitor_pricer.py),
  бо мета — знати, чи конкурент реально живий, для кожного рядка
  таблиці, а не лише для рішень про видалення.
- Той самий BATCH_SIZE=3000/день, ~6 днів — з новим, довшим часом
  (~2.1 год/день замість ~1.25 год), досі в межах 180-хв safety
  timeout workflow'у.

РЕЗЮМОВАНІСТЬ: стан (FULL_SCAN_STATE_FILE) зберігає вже скановані SKU
{sku: {name, category_name, cost, competitor_price, competitor_score,
competitor_alive, margin_pct, price_category}}. Кожен запуск бере
наступні BATCH_SIZE ще НЕ сканованих SKU (стабільний порядок за
ідентифікатором — тут немає "пріоритету за маржею", бо мета саме
уникнути попередньої маржинальної фільтрації) — ідемпотентно, безпечно
перезапускати.

ФОРМАТ ВИВОДУ (за запитом власника) — код, категорія, собівартість,
конкурент (ціна + підтверджено живий чи ні), маржа при цій ціні:
повна таблиця для побудови нового топ-1000 буде згенерована окремо
(build_top1000_report.py чи еквівалент) з готового state-файлу, коли
скан завершиться повністю.

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
from generate_prom_feed import fetch_russian_text, is_clearance_item
from competitor_pricing import decide_price_for_platform
from prom_competitor_pricer import find_best_competitor, verify_competitor_really_available, SEARCH_DELAY

BASE_DIR = Path(__file__).parent
FULL_SCAN_STATE_FILE = BASE_DIR / "full_catalog_scan_state.json"

# Узгоджено з власником 2026-07-13: ~3000/день, розтягнуто на ~6 днів —
# консервативний темп проти невідомого ризику rate-limit на 17-30x
# звичайного добового навантаження репрайсера. Той самий розмір пакету,
# що й у першій версії плану — довший час/день (~2.1 год замість ~1.25),
# бо додано verify_competitor_really_available(), але досі в межах
# 180-хв safety timeout workflow'у.
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


def _scope_items(catalog: dict) -> dict:
    """Обсяг для повного сканування (рішення власника 2026-07-13): stock>0
    і не уцінка/пошкоджений товар (is_clearance_item()) — НАВМИСНО без
    попередньої фільтрації за маржею чи "виключеними" категоріями
    (bicycles тощо, EXCLUDED_CATEGORIES у generate_prom_feed_top.py) —
    мета саме побачити конкурентну ситуацію для ВСЬОГО каталогу перед
    тим, як вирішувати, що варте топ-1000, а не звужувати заздалегідь
    за власною економікою."""
    return {
        pid: item for pid, item in catalog.items()
        if item.get("stock", 0) > 0
        and not is_clearance_item(item.get("name"), item.get("category_name"), item.get("category_id"))
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

    scope = _scope_items(catalog)
    state = load_state()
    scanned_ids = set(state.keys())
    remaining_ids = set(scope.keys()) - scanned_ids

    print(f"[FullScan] Повний каталог: {len(catalog)} | обсяг (stock>0, не уцінка): {len(scope)}")
    print(f"[FullScan] Уже сканованих: {len(scanned_ids)} | лишилось: {len(remaining_ids)}")

    if args.status:
        return

    if not remaining_ids:
        print("[FullScan] Увесь обсяг вже сканований — нема чого робити. "
              "Видали/перейменуй full_catalog_scan_state.json, щоб пересканувати з нуля.")
        return

    # Стабільний порядок (за ID) — навмисно БЕЗ пріоритету за маржею, на
    # відміну від попередньої версії плану: мета саме уникнути будь-якої
    # попередньої економічної фільтрації/сортування до того, як зберемо
    # дані по ВСЬОМУ обсягу.
    ordered_remaining = sorted(remaining_ids)
    batch_ids = ordered_remaining[:args.batch_size]

    print(f"[FullScan] Пакет цього запуску: {len(batch_ids)} SKU "
          f"(~{len(batch_ids) * 2.521 / 60:.1f} хв за виміряним темпом 2.521с/SKU).")

    print("[FullScan] Завантажую російськомовні назви (кращий збіг з пошуком Prom)...")
    russian_text = fetch_russian_text()

    for i, pid in enumerate(batch_ids, start=1):
        item = scope[pid]
        cost = float(item.get("price") or 0)
        name_ukr = (item.get("name") or "").strip()
        name_rus = (russian_text.get(pid, {}) or {}).get("name") or name_ukr
        category_name = item.get("category_name")

        competitor = find_best_competitor(name_rus, cost)
        min_competitor_price = competitor["price"] if competitor else None
        decision = decide_price_for_platform(cost, min_competitor_price, "prom", category_name)
        time.sleep(SEARCH_DELAY)

        competitor_alive = None
        if competitor:
            competitor_alive = verify_competitor_really_available(competitor)
            time.sleep(SEARCH_DELAY)

        state[pid] = {
            "name": name_ukr,
            "category_name": category_name,
            "cost": cost,
            "competitor_price": competitor["price"] if competitor else None,
            "competitor_score": round(competitor["score"], 2) if competitor else None,
            "competitor_alive": competitor_alive,
            "margin_pct": decision["margin_pct"],
            "price_category": decision["category"],
        }

        if i % 200 == 0:
            print(f"[FullScan] ...{i}/{len(batch_ids)} цього пакету оброблено, зберігаю проміжний стан...")
            save_state(state)

    save_state(state)
    scanned_now = len(scanned_ids) + len(batch_ids)
    print(f"[FullScan] Готово. Скановано цього разу: {len(batch_ids)}. "
          f"Всього скановано: {scanned_now}/{len(scope)} SKU обсягу "
          f"({scanned_now / len(scope) * 100:.1f}%).")
    if scanned_now < len(scope):
        print(f"[FullScan] Лишилось {len(scope) - scanned_now} SKU — наступний прогін продовжить звідси.")
    else:
        print("[FullScan] Повний скан обсягу завершено!")


if __name__ == "__main__":
    main()
