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
from datetime import datetime
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from parser import fetch_toysi_catalog
from generate_prom_feed import fetch_russian_text, is_clearance_item
from competitor_pricing import decide_price_for_platform
from prom_competitor_pricer import (
    find_best_competitor, verify_competitor_really_available, SEARCH_DELAY,
    MIN_FULL_RUN_INTERVAL_HOURS, _load_prom_category_cache,
)
from telegram_notify import send_telegram_message

# Надійність, п.4: invalid_cost_count вже рахувався й друкувався в
# журнал/консоль щоночі, але ніхто не бачить це без ручного зазирання в
# journalctl — сирі, зіпсовані ціни Toysi для цілого пакету могли б
# накопичуватись місяцями непоміченими. Поріг — ЧАСТКА пакету, а не
# абсолютне число: кілька SKU без ціни за ніч — нормально (реальний
# асортимент завжди має якийсь "шум"), різке зростання частки — ознака
# структурної проблеми з фідом Toysi (не поодинокий SKU).
INVALID_COST_ALERT_FRACTION = 0.05

BASE_DIR = Path(__file__).parent
FULL_SCAN_STATE_FILE = BASE_DIR / "full_catalog_scan_state.json"

# ДОДАНО (2026-07-20, пряме прохання власниці): дотепер прогрес цього
# скану був видимий лише через `journalctl` на VPS — жодного дати-
# рованого звіту, жодного сповіщення. Той самий патерн, що вже
# встановлений у prom_catalog_auditor.py (див. його докстрінг): VPS
# виконує це через systemd, а не інтерактивно на Windows-машині, звідки
# code_report_*.md пишуться напряму в спільну папку — з Linux-таймера
# туди не дотягнутись, тож датований файл у REPORT_DIR + Telegram —
# еквівалентний канал доставки власниці.
REPORT_DIR = BASE_DIR / "reports"


def build_scan_report(today: str, batch_ids: list, state: dict, scanned_now: int, scope_total: int) -> str:
    """Розбивка ЛИШЕ по цьому прогону (batch_ids) — накопичений total
    рахується окремим числом (scanned_now/scope_total), не по категоріях,
    бо старіші записи вже й так відображені в попередніх звітах."""
    floor = undercut = no_competitor = errors = 0
    for pid in batch_ids:
        category = state.get(pid, {}).get("price_category")
        if category == "floor":
            floor += 1
        elif category == "undercut":
            undercut += 1
        elif category == "no_competitor":
            no_competitor += 1
        elif category == "invalid_cost":
            errors += 1

    pct = (scanned_now / scope_total * 100) if scope_total else 0.0
    lines = [
        f"# Нічний конкурентний скан каталогу — {today}",
        "",
        f"Оновлено: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"Просканировано цього прогону: {len(batch_ids)} SKU.",
        f"Накопичено всього: {scanned_now}/{scope_total} SKU обсягу ({pct:.1f}%).",
        "",
        "## Розбивка цього прогону",
        f"- Конкурентні (undercut — ми дешевші за конкурента): {undercut}",
        f"- Неконкурентні (floor — конкурент дешевший, навіть на нижній межі маржі): {floor}",
        f"- Без знайденого конкурента: {no_competitor}",
        f"- Помилки (немає валідної ціни постачальника Toysi): {errors}",
    ]
    return "\n".join(lines) + "\n"

# ВИПРАВЛЕНО (2026-07-16, знахідка незалежного рев'ю pt25): цей скрипт
# тепер запускається systemd-таймером на VPS о 01:00 Kyiv (раніше було
# 02:00 — рев'ю правильно спіймало, що заявлений "5+ год буфер" до
# наступного тика репрайсера о 03:00 був арифметичною помилкою, реально
# 1 год). Живо підтверджено (2026-07-16): фіксований час старту НЕ
# рятує сам по собі — GH Actions cron `0 */4 * * *` для update-feeds.yml
# у реальності зсувається на ГОДИНИ від номінального часу (підтверджено:
# номінальний тик 20:00 UTC фактично стартував о 23:36 UTC того ж дня,
# а номінальний тик 00:00 UTC 2026-07-16 не запустився взагалі за
# понад 4 години). Тому єдиний надійний сигнал — перевірити РЕАЛЬНИЙ
# стан гейту репрайсера (той самий MIN_FULL_RUN_INTERVAL_HOURS/
# _meta.last_full_run, що й у prom_competitor_pricer.py) через публічний
# prom_competitor_price_state.json на гілці feed-data, а не покладатись
# на номінальний розклад.
REPRICER_STATE_URL = (
    "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/"
    "feed-data/prom_competitor_price_state.json"
)
# Обмежене очікування, не безстрокове пропускання — якщо гейт відкритий
# (тобто повний прогін репрайсера МОЖЛИВИЙ на будь-якому наступному тику,
# час якого непередбачуваний через дрейф GH Actions), чекаємо один раз
# і йдемо далі — краще прийняти залишковий ризик накладання, ніж
# роками стояти й не просуватись у скані через дрейф чужого розкладу.
REPRICER_GATE_WAIT_MINUTES = 30


def _repricer_gate_is_open() -> bool:
    """True, якщо є реальний ризик, що повний прогін репрайсера активний
    зараз АБО можливий на будь-якому наступному GH Actions тику —
    важке навантаження на той самий Prom-пошук, що й цей скан.

    Дві окремі умови (ВИПРАВЛЕНО 2026-07-16, незалежне рев'ю pt5, вузька
    прогалина в первісній версії): prom_competitor_pricer.py пише
    _meta.last_full_run ОДРАЗУ на старті прогону, ДО самого пошуку
    конкурентів (рядок 655 у файлі) — сам пошук триває ~45-90+ хв ПІСЛЯ
    цього запису. Тобто щойно репрайсер стартує, hours_since миттєво
    падає до ~0, і стара перевірка (лише `>= MIN_FULL_RUN_INTERVAL_HOURS`)
    хибно вважала гейт "закритим" (нібито безпечно) саме в той момент,
    коли репрайсер активно навантажує Prom-пошук:
    - `hours_since >= MIN_FULL_RUN_INTERVAL_HOURS` — гейт скоро
      відкриється, повний прогін можливий на будь-якому наступному тику.
    - `hours_since < 2` — повний прогін, найімовірніше, щойно стартував
      і ще виконується (2 год — запас понад типові 45-90 хв, з запасом
      на нетипово довгий прогін).

    Мережева помилка чи відсутність даних — консервативно НЕ блокує скан
    (немає підстав вважати ризик підтвердженим, якщо ми навіть не змогли
    перевірити стан)."""
    try:
        r = requests.get(REPRICER_STATE_URL, timeout=15)
        r.raise_for_status()
        last_full_run = r.json().get("_meta", {}).get("last_full_run")
        if not last_full_run:
            return False
        hours_since = (datetime.now() - datetime.fromisoformat(last_full_run)).total_seconds() / 3600
        return hours_since >= MIN_FULL_RUN_INTERVAL_HOURS or hours_since < 2
    except Exception as e:
        print(f"[FullScan] Не вдалось перевірити гейт репрайсера ({e}) — "
              f"продовжую без затримки.", file=sys.stderr)
        return False

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

    if not args.status and _repricer_gate_is_open():
        print(f"[FullScan] Гейт репрайсера відкритий (>= {MIN_FULL_RUN_INTERVAL_HOURS} год від "
              f"останнього повного прогону) — повний прогін репрайсера можливий на будь-якому "
              f"наступному GH Actions тику. Чекаю {REPRICER_GATE_WAIT_MINUTES} хв перед стартом, "
              f"щоб зменшити ризик накладання на той самий Prom-пошук.")
        time.sleep(REPRICER_GATE_WAIT_MINUTES * 60)

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

    # Autonomy-11/Vis-11: та сама механіка, що й prom_competitor_pricer.py —
    # реальна Prom-категорія товару (якщо закешована) переважає здогад за
    # назвою Toysi при виборі комісії.
    prom_category_cache = _load_prom_category_cache()
    print(f"[FullScan] Кеш Prom-категорій: {len(prom_category_cache)} SKU "
          f"({'знайдено' if prom_category_cache else 'відсутній/застарілий — фолбек на Toysi-категорію'}).")

    invalid_cost_count = 0
    for i, pid in enumerate(batch_ids, start=1):
        item = scope[pid]
        # ВИПРАВЛЕНО (незалежне рев'ю PR #48): раніше float() без
        # try/except — один SKU з нечисловою/відсутньою ціною серед ~18 тис.
        # валив увесь ~2-годинний пакет (ValueError/TypeError на float()),
        # і жоден з уже відсканованих у цьому пакеті SKU НЕ зберігався (крах
        # ДО save_state() в кінці). Той самий патерн, що вже в
        # prom_competitor_pricer.py. На відміну від репрайсера (де просто
        # `continue` — там немає стійкого стану, наступний прогін і так
        # перебирає весь каталог заново), тут `remaining_ids` персистентний:
        # "тихий" `continue` без запису в state лишав би цей SKU в
        # remaining_ids НАЗАВЖДИ — він зайняв би місце в batch_ids щодня, і
        # скан ніколи не дійшов би до 100%. Тому записуємо явний
        # "invalid_cost" запис (без дорогого пошуку конкурента) — SKU
        # рахується відсканованим, і скан рухається далі.
        try:
            cost = float(item.get("price") or 0)
        except (TypeError, ValueError):
            cost = 0
        name_ukr = (item.get("name") or "").strip()
        name_rus = (russian_text.get(pid, {}) or {}).get("name") or name_ukr
        category_name = item.get("category_name")
        prom_category_id = (prom_category_cache.get(pid) or {}).get("category_id")

        if cost <= 0:
            invalid_cost_count += 1
            state[pid] = {
                "name": name_ukr,
                "category_name": category_name,
                "cost": cost,
                "competitor_price": None,
                "competitor_score": None,
                "competitor_alive": None,
                "margin_pct": None,
                "price_category": "invalid_cost",
            }
            continue

        competitor = find_best_competitor(name_rus, cost)
        min_competitor_price = competitor["price"] if competitor else None
        decision = decide_price_for_platform(cost, min_competitor_price, "prom", category_name, prom_category_id)
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
    print(f"[FullScan] Готово. Скановано цього разу: {len(batch_ids)} "
          f"(з них без валідної ціни постачальника: {invalid_cost_count}). "
          f"Всього скановано: {scanned_now}/{len(scope)} SKU обсягу "
          f"({scanned_now / len(scope) * 100:.1f}%).")

    invalid_cost_fraction = invalid_cost_count / len(batch_ids) if batch_ids else 0.0
    if invalid_cost_fraction > INVALID_COST_ALERT_FRACTION:
        send_telegram_message(
            f"⚠️ full_catalog_competitor_scan.py: {invalid_cost_count}/{len(batch_ids)} "
            f"SKU цього пакету ({invalid_cost_fraction * 100:.0f}%) без валідної ціни постачальника "
            f"Toysi — вище порогу {INVALID_COST_ALERT_FRACTION * 100:.0f}%. Можлива структурна "
            "проблема з фідом Toysi (не поодинокі SKU), перевір вручну."
        )
    if scanned_now < len(scope):
        print(f"[FullScan] Лишилось {len(scope) - scanned_now} SKU — наступний прогін продовжить звідси.")
    else:
        print("[FullScan] Повний скан обсягу завершено!")

    today = datetime.now().strftime("%Y-%m-%d")
    report = build_scan_report(today, batch_ids, state, scanned_now, len(scope))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"full_catalog_scan_report_{today}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"[FullScan] Звіт збережено: {report_path}")

    undercut_count = sum(1 for pid in batch_ids if state.get(pid, {}).get("price_category") == "undercut")
    scanned_pct = (scanned_now / len(scope) * 100) if scope else 0.0
    send_telegram_message(
        f"🌙 Нічний скан каталогу ({today}): просканировано {len(batch_ids)}, "
        f"з них конкурентних (undercut) {undercut_count}. Накопичено всього: "
        f"{scanned_now}/{len(scope)} ({scanned_pct:.1f}%)."
    )


if __name__ == "__main__":
    main()
