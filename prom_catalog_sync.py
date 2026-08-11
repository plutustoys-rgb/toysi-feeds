"""
Активна синхронізація каталогу Prom із поточним відбором топ-6000.

Навіщо цей скрипт існує (не дублює вбудований імпорт Prom):
Кабінет Prom при повторному імпорті з "Статус товарів, яких немає в файлі" =
"Немає в наявності" лише позначає товар недоступним — товар лишається
"Опубліковано" і далі займає одне з обмежених місць тарифу (тепер 6000; перевірено на 1000 у липні,
емпірично 2026-07-10: SKU 267230 після такого повторного імпорту досі
"Опубліковано", а лічильник "Додано: X/N" (тариф підняли 1000→6000) не зменшився). Прибирання
товару з АКТИВНОГО каталогу (реальне звільнення місця під заміну кращим за
маржею SKU) вбудований імпорт не робить — ні на 4-годинному, ні на нічному
циклі. Цей скрипт закриває саме цю прогалину через Prom API.

Що робить:
1. Рахує актуальний відбір топ-6000 (select_top_items — та сама логіка, що й
   у фактичному фіді).
2. Тягне ПОВНИЙ список товарів, які зараз реально опубліковані в кабінеті
   Prom (GET /products/list).
3. Товар деактивується (status="deleted" через POST /products/edit_by_external_id),
   лише якщо ОБИДВІ умови виконані:
   - його external_id є в ПОВНОМУ каталозі Toysi (тобто це наш дропшип-товар,
     а не щось, додане вручну власником чи іншим постачальником — таких
     ніколи не чіпаємо);
   - його немає в поточному відборі топ-6000 (нульовий залишок, витіснений
     кращим за маржею SKU, чи категорія виключена).
   Звільнене місце забирає наступний імпорт Prom (нічне створення нових
   товарів — уже не задача цього скрипта, воно й так увімкнене через
   "Автоматичне оновлення посилання: Раз на 4 години").

Безпека: за замовчуванням DRY-RUN — лише друкує, що БУЛО Б деактивовано.
Реальні зміни в кабінеті Prom — тільки з явним --apply.
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from generate_prom_feed_top import select_top_items
from parser import fetch_toysi_catalog, assert_catalog_size_sane, CatalogSizeError
from telegram_notify import send_telegram_message
from prom_api_client import PromEditError, delist as _prom_delist
from competitor_pricing import load_prom_price_state, save_prom_price_state
from prom_pushed_ledger import load_ledger, prune as _prune_ledger

# Консоль Windows (cp1251) не показує деякі символи — без цього локальний
# тестовий запуск падає на print() (див. daily_report.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

PROM_API_KEY    = os.environ.get("PROM_API_KEY", "")
PROM_API_URL    = "https://my.prom.ua/api/v1"
REQUEST_TIMEOUT = 30

PAGE_SIZE  = 100  # /products/list

# ДОДАНО (2026-07-21, findings_log.md "prom-delete-still-rejected-for-specific-sku",
# PR #123/#124): дослідження встановило, що для частини товарів
# edit_by_external_id(status=deleted) структурно не працює на боці Prom
# (публічний API v1.0, "закритий бета-тест" — жодного альтернативного
# документованого способу немає; живо підтверджено, що товар НЕ
# заблокований бізнес-логікою — ручне видалення в кабінеті спрацьовує
# миттєво). Без цього лічильника `deactivate()` намагався б видалити ті
# самі SKU щоцикл (кожні 4 год) вічно, без результату і без сповіщення.
# Окремий стейт-файл — prom_catalog_sync.py досі не мав ЖОДНОГО
# персистентного стану (на відміну від prom_competitor_pricer.py чи
# generate_rozetka_feed.py).
STUCK_STATE_FILE     = Path(__file__).parent / "prom_catalog_sync_stuck_state.json"
STUCK_ALERT_THRESHOLD = 3  # послідовних невдалих прогонів (~12г при циклі 4г)


def _load_stuck_state() -> dict:
    if not STUCK_STATE_FILE.exists():
        return {}
    try:
        return json.loads(STUCK_STATE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_stuck_state(state: dict) -> None:
    try:
        STUCK_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        print(f"[Sync] Не вдалось зберегти prom_catalog_sync_stuck_state.json ({e})", file=sys.stderr)


def _update_stuck_state_and_alert(state: dict, stale_ids: list, processed: list, errors: dict,
                                   prom_products: dict) -> dict:
    """Оновлює лічильник послідовних невдалих спроб видалення на external_id
    і повертає (мутований) стан. Скидає лічильник, щойно SKU успішно
    видалився чи зник зі списку застарілих (повернувся в наявність/топ-6000,
    чи вже видалений раніше). Надсилає ОДИН Telegram-алерт за прогін для
    щойно "застряглих" SKU (fail_count досяг порогу вперше) — не повторює
    сповіщення про вже позначений SKU, доки він не зникне зі списку
    (пункт 3 задачі: не спамити на кожен наступний прогін)."""
    stale_set = set(stale_ids)

    for pid in processed:
        state.pop(pid, None)
    for pid in list(state.keys()):
        if pid not in stale_set:
            state.pop(pid, None)

    for pid in errors:
        entry = state.get(pid, {"fail_count": 0, "alerted": False})
        entry["fail_count"] = entry.get("fail_count", 0) + 1
        state[pid] = entry

    newly_stuck = [
        pid for pid, entry in state.items()
        if entry.get("fail_count", 0) >= STUCK_ALERT_THRESHOLD and not entry.get("alerted", False)
    ]
    if newly_stuck:
        lines = []
        for pid in newly_stuck:
            name = (prom_products.get(pid) or {}).get("name", "")[:60]
            lines.append(f"{pid} {name} ({state[pid]['fail_count']} невдалих спроб поспіль)")
            state[pid]["alerted"] = True
        send_telegram_message(
            f"⚠️ prom_catalog_sync.py: {len(newly_stuck)} товарів не вдається видалити через Prom API "
            f"вже {STUCK_ALERT_THRESHOLD}+ прогонів поспіль (структурний дефект edit_by_external_id, "
            "findings_log.md \"prom-delete-still-rejected-for-specific-sku\") — потребують РУЧНОГО "
            "видалення в кабінеті Prom (Товари -> Дії -> Видалити):\n\n" + "\n".join(lines)
        )

    return state


TRUE_ROOT_GROUP_ID = 155011713  # "Корнева група" — підтверджено напряму 2026-07-11:
# GET /products/{id} для одного з 4 вручну доданих товарів (external_id=null,
# завжди були поза Toysi-імпортом) повернув саме цей group.id. /groups/list
# НЕ включає цю групу у власну пагінацію (типова поведінка API — коренева
# група не є "підгрупою" самої себе), а group_id=0 — окрема, явно ІНША
# сутність (повертає інший набір товарів, не збігається з "Корнева група").
# Без цього ці 4 товари випадали з fetch_prom_products() повністю.

# Поріг для сигналізації (НЕ гарантія повноти) — 2026-07-12 виявлено ще один,
# ширший випадок того самого класу проблем: /groups/list мовчки НЕ повертає
# групу "Сквіші" (реальна, активна група з десятками опублікованих товарів —
# підтверджено напряму через my.prom.ua/cms/product?search_term=<sku> для 5
# зразків, 5/5 живі й опубліковані). Перевірено вичерпно: усі group_id, які
# `_fetch_group_ids()` реально повертає (133 на момент перевірки), НЕ містять
# жодного товару з цієї групи — це не помилка нашої пагінації (post's
# `last_id`-пагінація сама по собі коректно доходить до кінця списку, що
# API готовий віддати), а обмеження на боці Prom: ця конкретна група просто
# відсутня у відповіді /groups/list для цього акаунта. Надійного API-способу
# самостійно виявити ID "невидимих" груп немає. Замість спроби (недосяжної
# зараз) гарантувати повноту — цей поріг робить недорахунок ГОЛОСНИМ, а не
# тихим, щоб наступного разу хтось не покладався мовчки на "відсутній у
# Prom" висновок без прямої перевірки в кабінеті.
#
# Кабінет показує ~6116/6000 опублікованих (тариф піднято до 6000; лишки — застарілий стік, що й чистить цей скрипт). Поріг узгоджено із
# запасом ~5-6% нижче цього (не впритул, як спершу поставлений 950 — лише
# ~1.5% запасу, замало проти звичайного денного коливання) — той самий підхід
# із запасом, що й TOYSI_EXPECTED_MIN_SIZE у parser.py (там ~15% запасу проти
# ~29 325 реальних SKU; тут менший відсоток обґрунтований тим, що каталог
# Prom природно коливається значно менше день у день, ніж повний каталог
# постачальника Toysi).
MIN_EXPECTED_PRODUCT_COUNT = 910


# ДОДАНО (2026-07-27, живий інцидент: apply_live_dumping_fix.py впав на
# 429 Too Many Requests ДВІЧІ поспіль, на самому старті, ще ДО будь-якого
# apply_price() — findings_log.md "fetch-prom-products-429-silent",
# відкрито 2026-07-25, аудит попереджав про це ще pt9 як "успадкований,
# не блокуючий" ризик, який тепер реально заважає скрипту хоч раз
# доїхати до живого apply_price()). ThreadPoolExecutor(max_workers=10)
# у fetch_prom_products() б'є по /products/list паралельно на кожен
# group_id БЕЗ жодної паузи — саме такий паралельний сплеск і призводить
# до rate-limit, підтверджено живо. MAX_429_RETRIES спроб з експоненційним
# backoff (поважає Retry-After, якщо Prom його повертає) замість негайного
# краху на першому ж 429.
MAX_429_RETRIES = 5
RETRY_BACKOFF_BASE_SECONDS = 3

# ДОДАНО (2026-07-30, друге звернення — root_cause_report_2026-07-25_why_not_6000.md
# пункт 4, і root_cause_report_2026-07-30_sales_stopped.md): _get_with_retry вище лише
# ВІДНОВЛЮЄТЬСЯ після 429, але не ЗАПОБІГАЄ сплеску. Корінь — ThreadPoolExecutor
# (fetch_prom_products / fetch_prom_products_by_external_ids) б'є по /products/list
# паралельно БЕЗ пауз. Тут — глобальний (на весь модуль, для всіх потоків) throttle:
# мінімум PROM_API_MIN_INTERVAL_SECONDS між СТАРТАМИ будь-яких двох запитів, той самий
# принцип "власної ввічливості", що й SEARCH_DELAY=0.4 в prom_competitor_pricer.py.
# Лок утримується під час sleep навмисно — так старти запитів рознесені в часі навіть
# коли їх ініціюють 10 потоків одночасно (сам HTTP-I/O далі йде поза локом, тож мережеві
# затримки й далі перекриваються між потоками). Проактивний throttle + зменшений
# max_workers разом усувають сплеск, а не лише лікують його наслідок.
PROM_API_MIN_INTERVAL_SECONDS = 0.4
# Зменшено 10 -> 4: глобальний _throttle() і так серіалізує старти запитів, тож високий
# паралелізм давав лише сплеск на старті (усі 10 стартують одночасно) без виграшу в
# швидкості. 4 потоки перекривають мережеву латентність, не створюючи "громового стада".
PROM_FETCH_MAX_WORKERS = 4
_throttle_lock = threading.Lock()
_last_request_ts = 0.0


def _throttle() -> None:
    """Рознести старти запитів до Prom API щонайменше на PROM_API_MIN_INTERVAL_SECONDS,
    потокобезпечно (для паралельних викликів через ThreadPoolExecutor)."""
    global _last_request_ts
    with _throttle_lock:
        wait = PROM_API_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request_ts)
        if wait > 0:
            time.sleep(wait)
        _last_request_ts = time.monotonic()


def _get_with_retry(url: str, params: dict) -> requests.Response:
    for attempt in range(MAX_429_RETRIES + 1):
        _throttle()
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {PROM_API_KEY}"},
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 429 or attempt == MAX_429_RETRIES:
            response.raise_for_status()
            return response
        retry_after = response.headers.get("Retry-After")
        wait = float(retry_after) if retry_after and retry_after.isdigit() else RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
        print(f"[prom_catalog_sync] 429 Too Many Requests на {url} — чекаю {wait:.0f}с "
              f"(спроба {attempt + 1}/{MAX_429_RETRIES})...", file=sys.stderr)
        time.sleep(wait)
    raise AssertionError("unreachable")  # цикл завжди повертає чи кидає виняток на attempt == MAX_429_RETRIES


def _fetch_group_ids() -> list:
    """Усі group_id кабінету, пагінація за last_id (та сама механіка, що й
    products/list нижче). +[0, TRUE_ROOT_GROUP_ID] — обидві "кореневі"
    сутності (див. коментар вище) — жодна з них не з'являється в самій
    пагінації /groups/list."""
    group_ids = [0, TRUE_ROOT_GROUP_ID]
    last_id = None
    while True:
        params = {"limit": PAGE_SIZE}
        if last_id is not None:
            params["last_id"] = last_id
        response = _get_with_retry(f"{PROM_API_URL}/groups/list", params)
        batch = response.json().get("groups", [])
        if not batch:
            break
        group_ids.extend(g["id"] for g in batch)
        if len(batch) < PAGE_SIZE:
            break
        last_id = min(g["id"] for g in batch) - 1
    return group_ids


def _fetch_products_in_group(group_id: int) -> list:
    products = []
    last_id = None
    while True:
        params = {"limit": PAGE_SIZE, "group_id": group_id}
        if last_id is not None:
            params["last_id"] = last_id
        response = _get_with_retry(f"{PROM_API_URL}/products/list", params)
        batch = response.json().get("products", [])
        if not batch:
            break
        products.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        last_id = min(p["id"] for p in batch) - 1
    return products


def check_product_count_sane(products: dict) -> str | None:
    """Повертає попередження (або None, якщо все в межах очікуваного), якщо
    fetch_prom_products() повернув підозріло мало товарів. Винесено окремою
    функцією (не лише inline-перевіркою всередині fetch_prom_products()),
    щоб той самий поріг/повідомлення міг використати й виклик, якому
    потрібен не просто друк у stderr, а сам текст попередження — напр.
    prom_catalog_auditor.py, щоб включити його у звіт/Telegram-підсумок,
    а не лишати лише в консольному виводі, який на VPS-кроні ніхто не
    читає інтерактивно."""
    if len(products) < MIN_EXPECTED_PRODUCT_COUNT:
        return (
            f"fetch_prom_products() повернув лише {len(products)} товарів — "
            f"підозріло мало проти очікуваних ~{MIN_EXPECTED_PRODUCT_COUNT}+. "
            f"Відомий випадок: /groups/list може мовчки не повертати реальні "
            f"групи (напр. 'Сквіші', виявлено 2026-07-12) — перш ніж довіряти "
            f"висновку 'товар відсутній у Prom' на основі цього результату, "
            f"перевір напряму через my.prom.ua/cms/product?search_term=<sku>."
        )
    return None


# ДОДАНО (2026-07-30, root_cause_report_2026-07-30_sales_stopped.md): deactivate()
# раніше не мав жодного капу — видаляв УСЕ, що find_stale_external_ids() позначив,
# скільки б не було (живо: 348-661/прогін у липні; нотифікація Prom "видалено 2256").
# Легітимна ротація зараз ~50-80/прогін. Партія понад цей ліміт — сигнал імовірного
# збою (колапс select_top_items, стрибок "невидимої групи"), а не реальної ротації.
MAX_DEACTIVATIONS_PER_RUN = 200


def deletion_guard_reason(prom_products: dict, stale_ids: list) -> str | None:
    """Повертає причину ПРОПУСТИТИ масове видалення цього прогону (і заалертити),
    або None, якщо видаляти безпечно. Два незалежні запобіжники:
    (1) надійність виду кабінету — не вирішувати про видалення на неповному зрізі
        (відома "невидима група" в fetch_prom_products());
    (2) кап на масовість — партія > MAX_DEACTIVATIONS_PER_RUN не видаляється
        автоматично, а віддається на огляд людині."""
    view_warning = check_product_count_sane(prom_products)
    if view_warning:
        return (f"НЕнадійний вид кабінету — {len(prom_products)} товарів "
                f"(< {MIN_EXPECTED_PRODUCT_COUNT}), рішення про видалення наосліп небезпечне. "
                f"{view_warning}")
    if len(stale_ids) > MAX_DEACTIVATIONS_PER_RUN:
        return (f"{len(stale_ids)} застарілих товарів перевищує ліміт "
                f"{MAX_DEACTIVATIONS_PER_RUN}/прогін — ймовірний збій відбору/скану, "
                f"а не реальна ротація.")
    return None


# ДОДАНО (2026-07-30, аудит PR #188 pt2, GOTCHAS #5): блок-алерт deletion_guard
# спрацьовує до 4×/день (таймер 07:30/11:30/15:30/19:30), і при затяжному
# нестабільному виді "невидимої групи" спамив би Telegram щопрогону. Той самий
# 24-год throttle-патерн, що й у service_watchdog.py (check_autodeploy_status /
# feed_pipeline): не частіше 1 алерту / BLOCK_ALERT_INTERVAL_HOURS; здоровий прогін
# (видалення дозволено) скидає стан, щоб наступний НОВИЙ епізод блокування
# заалертив одразу (new/repeat-throttled/recovery-цикл, як у watchdog).
BLOCK_ALERT_STATE_FILE      = Path(__file__).parent / "prom_catalog_sync_block_alert_state.json"
BLOCK_ALERT_INTERVAL_HOURS  = 24


def _block_alert_due(now: datetime) -> bool:
    """True, якщо блок-алерт ще не слався або минуло >= BLOCK_ALERT_INTERVAL_HOURS."""
    try:
        data = json.loads(BLOCK_ALERT_STATE_FILE.read_text(encoding="utf-8"))
        # isinstance-guard (аудит #189 pt3): валідний JSON-нескаляр (0/[]/"str") інакше
        # кинув би AttributeError на .get() поза except нижче — fail-open замість краху.
        last = data.get("last_block_alert") if isinstance(data, dict) else None
    except (OSError, ValueError):
        last = None
    if not last:
        return True
    try:
        return (now - datetime.fromisoformat(last)).total_seconds() / 3600 >= BLOCK_ALERT_INTERVAL_HOURS
    except ValueError:
        return True  # пошкоджений таймстемп — не глушити алерт


def _record_block_alert(now: datetime) -> None:
    try:
        BLOCK_ALERT_STATE_FILE.write_text(
            json.dumps({"last_block_alert": now.isoformat()}, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        print(f"[Sync] Не вдалось зберегти {BLOCK_ALERT_STATE_FILE.name} ({e})", file=sys.stderr)


def _clear_block_alert() -> None:
    """Скинути throttle-стан — викликається, коли прогін ДОЗВОЛИВ видалення (епізод
    блокування завершився), щоб наступне блокування заалертило без затримки."""
    try:
        BLOCK_ALERT_STATE_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"[Sync] Не вдалось скинути {BLOCK_ALERT_STATE_FILE.name} ({e})", file=sys.stderr)


def fetch_prom_products() -> dict:
    """Повний список товарів кабінету Prom (усі групи), ключ — external_id.

    ВАЖЛИВО: GET /products/list БЕЗ параметра group_id мовчки повертає лише
    КОРЕНЕВУ групу (group_id=0), а не весь каталог — підтверджено напряму
    2026-07-10 (запит із limit=100 і навіть зі штучно завищеним last_id
    незмінно повертав рівно ту саму підмножину; явний group_id для відомої
    підгрупи "Басейни" повернув інший, коректний набір). Це означає, що
    попередні версії цієї функції РЕАЛЬНО недорахувати каталог щоразу, коли
    Prom встигав розкласти товари по підгрупах — не лише зараз (тоді
    показував 97 замість ~430). Тому тут явно перебираємо ВСІ group_id
    (через /groups/list) і зводимо результати в один словник; паралельно
    (ThreadPoolExecutor), бо групи вже налічують 100+."""
    group_ids = _fetch_group_ids()

    products: dict = {}
    with ThreadPoolExecutor(max_workers=PROM_FETCH_MAX_WORKERS) as executor:
        for group_products in executor.map(_fetch_products_in_group, group_ids):
            for p in group_products:
                ext_id = p.get("external_id")
                if ext_id:
                    products[ext_id] = p

    warning = check_product_count_sane(products)
    if warning:
        print(f"[prom_catalog_sync] УВАГА: {warning}", file=sys.stderr)

    return products


def fetch_prom_products_by_external_ids(external_ids: set) -> tuple[dict, set]:
    """GET /products/by_external_id/{id} для КОЖНОГО external_id окремо —
    підтверджено офіційною документацією (public-api.docs.prom.ua) і живим
    викликом 2026-07-13 (SKU 289818, категорія "Сквіші" — знайдено напряму
    цим шляхом, хоча group-based fetch_prom_products() його НЕ бачить). На
    відміну від fetch_prom_products(), цей шлях НЕ йде через /groups/list
    взагалі, тож структурно не може мати того самого "невидима група"
    сліпого місця (задача #47/#64).

    ВАЖЛИВО — це НЕ повна заміна fetch_prom_products(): придатний лише
    коли ЗАЗДАЛЕГІДЬ відомий конкретний набір external_id для перевірки
    (тут — кандидати на "відсутні", щоб підтвердити чи спростувати перед
    ескалацією). НЕ може виявити НЕВІДОМІ/чужі лістинги в кабінеті (для
    цього потрібне ім'я/ID заздалегідь) — find_stale_external_ids()
    (виявлення застарілих товарів поза топ-6000) і далі потребує повного
    переліку каталогу через fetch_prom_products(), не зачіпається цим
    фіксом.

    Повертає (found, indeterminate):
      - found: {external_id: product} для ПІДТВЕРДЖЕНО присутніх (200 OK).
      - indeterminate: external_id, для яких сама перевірка НЕ вдалась
        (мережева помилка, timeout, 401/403, 5xx, невалідний JSON) — це
        НЕ доказ відсутності, лише те, що зараз перевірити не вдалось.
        ВИПРАВЛЕНО (рев'ю PR #43): раніше 404 (справжня відсутність) і
        будь-яка ІНША помилка поверталися однаково як None — транзиєнтний
        мережевий/авторизаційний збій міг хибно підтвердити товар
        "відсутнім" замість "невідомо". Викликач має трактувати
        indeterminate як "спробувати наступного разу", а не як "відсутній"."""
    found: dict = {}
    indeterminate: set = set()

    def _fetch_one(ext_id: str) -> tuple:
        try:
            _throttle()  # той самий глобальний throttle, що й _get_with_retry — цей шлях теж паралельний і теж бив 429
            response = requests.get(
                f"{PROM_API_URL}/products/by_external_id/{ext_id}",
                headers={"Authorization": f"Bearer {PROM_API_KEY}"},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 404:
                return "absent", None
            response.raise_for_status()
            return "found", response.json().get("product")
        except (requests.exceptions.RequestException, ValueError):
            return "indeterminate", None

    ids_list = list(external_ids)
    with ThreadPoolExecutor(max_workers=PROM_FETCH_MAX_WORKERS) as executor:
        for ext_id, (status, product) in zip(ids_list, executor.map(_fetch_one, ids_list)):
            if status == "found" and product:
                found[ext_id] = product
            elif status == "indeterminate":
                indeterminate.add(ext_id)
            # status == "absent" (справжній 404) -> ні found, ні indeterminate;
            # викликач трактує таке як підтверджено відсутнє.

    return found, indeterminate


# ДОДАНО (2026-07-30, STATUS.md пункт 6 — ПРІОРИТЕТ Code Desktop): обхід "невидимої
# групи". /groups/list стабільно не віддає частину груп цього акаунта (докум. "Сквіші"
# вище), тож груповий fetch_prom_products() періодично недорахує кабінет (історично 38%
# прогонів <910), і view-guard (deletion_guard_reason) на таких прогонах блокує видалення,
# не звільняючи місце під нові товари. Тут — bounded-супплемент через /by_external_id
# (структурно НЕ залежить від /groups/list — задача #47/#64), той самий патерн, що
# build_prom_category_cache()/PR #165/#180. Кандидати — DESIRED (топ-відбір, які МАЄМО в
# Prom): набагато вища влучність, ніж сліпо по всьому каталозі Toysi (~27k з якого —
# "не додано", гарантований 404). Дані завжди СВІЖІ (by_external_id цього прогону, не
# кеш) — безпечно для рішення про видалення. Sweep-курсор (persistent checked-set)
# гарантовано покриває ВСЮ множину desired-missing по CABINET_SUPPLEMENT_LOOKUP_LIMIT за
# ceil(N/LIMIT) прогонів, потім скидається на новий цикл — не застрягає на першому батчі.
CABINET_SUPPLEMENT_LOOKUP_LIMIT = 800
CABINET_SUPPLEMENT_CHECKED_FILE = Path(__file__).parent / "prom_cabinet_supplement_checked.json"


def _load_cabinet_checked() -> set:
    try:
        data = json.loads(CABINET_SUPPLEMENT_CHECKED_FILE.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except (OSError, ValueError):
        return set()


def _save_cabinet_checked(checked: set) -> None:
    try:
        CABINET_SUPPLEMENT_CHECKED_FILE.write_text(
            json.dumps(sorted(checked), ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        print(f"[Sync] Не вдалось зберегти {CABINET_SUPPLEMENT_CHECKED_FILE.name} ({e})", file=sys.stderr)


def supplement_cabinet_view(prom_products: dict, desired_ids: set) -> dict:
    """Дочитує bounded-батч DESIRED-товарів, яких нема в груповому фетчі, через
    /by_external_id (обхід "невидимої групи"), і додає ЗНАЙДЕНІ (свіжі дані цього прогону)
    у prom_products. Мутує й повертає той самий словник. Sweep-курсор: спершу ще-не-
    перевірені цього циклу; коли всі поточні desired-missing пройдено — скидаємо checked
    і починаємо новий цикл. Так за кілька прогонів покривається вся множина, і жоден
    підмножинний "застряглий" батч не блокує прогрес."""
    missing = [e for e in desired_ids if e not in prom_products]
    if not missing:
        return prom_products
    missing_set = set(missing)
    checked = _load_cabinet_checked() & missing_set  # лише актуальні desired-missing
    pool = [e for e in missing if e not in checked]
    if not pool:                    # весь поточний missing уже пройдено — новий цикл
        checked = set()
        pool = missing
    batch = pool[:CABINET_SUPPLEMENT_LOOKUP_LIMIT]
    found, _indeterminate = fetch_prom_products_by_external_ids(set(batch))
    prom_products.update(found)     # свіжі дані, не кеш
    checked.update(batch)
    _save_cabinet_checked(checked)
    print(f"[Sync] Обхід невидимої групи: {len(missing)} desired-id поза груповим фетчем, "
          f"перевірено {len(batch)} цього прогону через by_external_id, знайдено в кабінеті "
          f"{len(found)} (додано до виду).")
    return prom_products


# ДОДАНО (2026-08-11): автоматична чистка «невидимих» OOS через журнал штовхнутих
# (prom_pushed_ledger.py). Досі find_stale_external_ids() бачив лише товари, які
# fetch_prom_products() дістав через /groups/list — а він стабільно недобирає «невидимі
# групи» (історично ~2402 з ~5836). Тому OOS-мотлох, що випав з топ-6000 і осів у
# невидимій групі, автоматично НЕ чистився — лише вручну (clear_invisible_oos.py, Phase 3,
# через сесію кабінету /cms/product/list?presence=not_avail). Журнал дає повний набір
# «наших» лістингів (усе, що ми колись штовхали у prom_feed_top.xml); кандидатів на
# застарілість звіряємо НАЖИВО через by_external_id (структурно НЕ через /groups/list —
# задача #47/#64), тож рішення безпечне навіть коли груповий зріз неповний (той самий
# принцип свіжої звірки, що й supplement_cabinet_view). Bounded lookup + delist-кап +
# sweep-курсор — як у супплементі вище.
LEDGER_SWEEP_LOOKUP_LIMIT = 800   # by_external_id-звірок за прогін (як CABINET_SUPPLEMENT_LOOKUP_LIMIT)
LEDGER_MAX_DELIST_PER_RUN = 200   # кап на деактивації з цього шляху (дзеркало MAX_DEACTIVATIONS_PER_RUN)
LEDGER_SWEEP_CHECKED_FILE = Path(__file__).parent / "prom_ledger_sweep_checked.json"


def _load_ledger_checked() -> set:
    try:
        data = json.loads(LEDGER_SWEEP_CHECKED_FILE.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except (OSError, ValueError):
        return set()


def _save_ledger_checked(checked: set) -> None:
    try:
        LEDGER_SWEEP_CHECKED_FILE.write_text(
            json.dumps(sorted(checked), ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        print(f"[Sync] Не вдалось зберегти {LEDGER_SWEEP_CHECKED_FILE.name} ({e})", file=sys.stderr)


def find_stale_via_ledger(desired_ids: set, toysi_ids: set, prom_products: dict) -> tuple:
    """Знаходить застарілі товари в «невидимих групах» через журнал штовхнутих.

    Кандидати = журнал − поточний_топ − вже_видимий_зріз (тобто наші лістинги, які випали
    з топ-6000 і яких груповий fetch_prom_products() НЕ побачив), звужені до наших дропшип-
    товарів (toysi_ids). Кожного звіряємо НАЖИВО через by_external_id.

    Повертає (to_delist, to_prune):
      to_delist — ПІДТВЕРДЖЕНО живі (200) + ще не deleted → безпечно деактивувати (свіжа
                  звірка цього прогону, не залежить від повноти /groups/list).
      to_prune  — підтверджено відсутні (404) або вже deleted (надгробки) → прибрати з
                  журналу (самоочищення, щоб пул кандидатів не ріс). indeterminate
                  (мережевий/авторизаційний збій) НЕ чіпаємо — це не доказ відсутності,
                  повторимо наступного прогону.

    Sweep-курсор (persistent checked-set, як supplement_cabinet_view): за кілька прогонів
    покриває ВЕСЬ пул по LEDGER_SWEEP_LOOKUP_LIMIT, потім скидається на новий цикл —
    жоден підмножинний батч не блокує прогрес по бэклогу."""
    ledger = load_ledger()
    candidates = [
        e for e in ledger
        if e in toysi_ids and e not in desired_ids and e not in prom_products
    ]
    if not candidates:
        return [], []

    cand_set = set(candidates)
    checked = _load_ledger_checked() & cand_set   # лише актуальні кандидати
    pool = [e for e in candidates if e not in checked]
    if not pool:                                  # весь поточний пул пройдено — новий цикл
        checked = set()
        pool = candidates
    batch = pool[:LEDGER_SWEEP_LOOKUP_LIMIT]

    found, indeterminate = fetch_prom_products_by_external_ids(set(batch))
    to_delist = [e for e, p in found.items() if p.get("status") != "deleted"]
    to_prune = [e for e in batch if e not in found and e not in indeterminate]   # підтверджено 404
    to_prune += [e for e, p in found.items() if p.get("status") == "deleted"]    # надгробки

    checked.update(batch)                         # sweep-курсор (як supplement_cabinet_view)
    _save_ledger_checked(checked)

    print(f"[Sync] Журнал невидимих OOS: {len(candidates)} кандидатів (журнал−топ−видимий зріз), "
          f"звірено {len(batch)} через by_external_id — живих застарілих {len(to_delist)}, "
          f"на очищення журналу {len(to_prune)}, невизначених {len(indeterminate)}.")
    return to_delist, to_prune


def find_stale_external_ids(prom_products: dict, desired_ids: set, toysi_ids: set) -> list:
    """Товари, які реально опубліковані в Prom, походять з нашого Toysi-фіда
    (не додані вручну власником — таких не чіпаємо), більше не входять у
    поточний топ-6000, і ще не позначені видаленими."""
    return [
        ext_id for ext_id, p in prom_products.items()
        if ext_id in toysi_ids
        and ext_id not in desired_ids
        and p.get("status") != "deleted"
    ]


def deactivate(stale_ids: list) -> tuple:
    """delist() ПО ОДНОМУ товару (prom_api_client.py), з живою GET-звіркою
    після кожного виклику. Повертає (processed_ids, errors).

    ВИПРАВЛЕНО (2026-07-21, пряма вимога власниці "роби фікси глобальні,
    щоб ця проблема більше не повторювалась" — знахідка з живого прикладу
    SKU 201887/185297): стара реалізація надсилала ВЕСЬ stale_ids ОДНИМ
    батч-POST (пачками по 100) і сліпо довіряла processed_ids/errors з
    відповіді Prom — той самий мовчазний відхил API, вже виявлений і
    виправлений 2026-07-18 для prom_competitor_pricer.py::delist()
    (SKU 266990: HTTP 200, "Помилок: 0", але товар живо НЕ видалений),
    тут не мав жодного захисту. Живо підтверджено: 3 прогони поспіль
    (07:30/11:30/15:30) показували SKU 201887/185297 "застарілими",
    реально оброблялось 8-90% пачки залежно від прогону — решта мовчки
    губилась без єдиного запису в errors. Повільніше (один HTTP-виклик +
    жива GET-звірка на SKU, замість трьох батч-запитів), але реально
    видаляє, а не недораховує без сліду."""
    processed, errors = [], {}
    for ext_id in stale_ids:
        try:
            _prom_delist(ext_id)
            processed.append(ext_id)
        except (requests.exceptions.RequestException, PromEditError) as e:
            errors[ext_id] = str(e)
    return processed, errors


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                     help="Реально викликати Prom API та деактивувати товари. "
                          "Без цього прапорця — лише dry-run звіт.")
    ap.add_argument("--max-deactivations", type=int, default=None, metavar="N",
                     help="СУПЕРВІЗОВАНЕ чищення бэклогу: дозволити цього прогону деактивувати до N "
                          "товарів (людина вже переглянула dry-run список). Обходить автоматичний кап "
                          f"{MAX_DEACTIVATIONS_PER_RUN} (probable-bug), АЛЕ view-guard «неповний зріз кабінету» "
                          "лишається. Понад N — решта наступними прогонами. Діє лише з --apply. "
                          "Без прапорця автоматичний таймер тримає кап "
                          f"{MAX_DEACTIVATIONS_PER_RUN}/прогін.")
    args = ap.parse_args()

    if args.max_deactivations is not None and args.max_deactivations < 0:
        # Захист від помилки знаку ("-200" замість "200"): зріз stale_ids[:-N] = "всі,
        # КРІМ останніх N" → обійшов би кап у зворотний бік (масове видалення). dry-run
        # цього не ловить (показує повний список ДО обрізання). 0 = безпечний no-op.
        print("[Sync] --max-deactivations не може бути від'ємним (вкажи 0 або додатне число).",
              file=sys.stderr)
        sys.exit(2)

    if not PROM_API_KEY:
        print("[Sync] PROM_API_KEY не задано — зупиняюсь.", file=sys.stderr)
        sys.exit(1)

    print("[Sync] Рахую поточний відбір топ-6000...")
    toysi_catalog = fetch_toysi_catalog()
    try:
        assert_catalog_size_sane(toysi_catalog)
    except CatalogSizeError as e:
        # Успішний HTTP-запит + валідний XML, але Toysi віддав менше офферів,
        # ніж реально є (усічений, але структурно коректний фід) — на відміну
        # від повної мережевої/HTTP-помилки (тоді fetch_toysi_catalog() сам
        # повертає {} і toysi_ids виходить порожнім, що вже безпечно саме по
        # собі), цей випадок інакше пройшов би непоміченим і призвів би до
        # масового хибного видалення живих товарів, чий external_id просто не
        # потрапив у цей конкретний усічений фетч.
        print(f"[Sync] {e}", file=sys.stderr)
        send_telegram_message(f"🚨 prom_catalog_sync.py зупинено: {e}")
        sys.exit(1)
    top_catalog   = select_top_items(toysi_catalog)
    desired_ids   = {str(pid) for pid in top_catalog}
    toysi_ids     = {str(pid) for pid in toysi_catalog}

    print("[Sync] Тягну повний список товарів кабінету Prom...")
    prom_products = fetch_prom_products()
    # Обхід "невидимої групи" (STATUS.md п.6): дочитати desired-товари, яких груповий
    # /groups/list не повернув, через by_external_id — робить вид повнішим (менше хибних
    # блокувань view-guard) і дає find_stale бачити застарілі товари з невидимих груп.
    prom_products = supplement_cabinet_view(prom_products, desired_ids)
    print(f"[Sync] У кабінеті Prom: {len(prom_products)} товарів. "
          f"У поточному топ-6000: {len(desired_ids)}.")

    # АВТО-ЧИСТКА «НЕВИДИМИХ» OOS (2026-08-11) — окремий, незалежний від видимого зрізу
    # шлях: кандидати з журналу штовхнутих, звірені НАЖИВО через by_external_id, тож
    # безпечні навіть коли /groups/list неповний (той самий принцип, що supplement_cabinet_
    # view). Замінює РУЧНИЙ clear_invisible_oos.py (Phase 3). Виконується ЗАВЖДИ (навіть якщо
    # видимий find_stale нижче порожній чи заблокований view-guard), бо це різний клас даних.
    ledger_delist, ledger_prune = find_stale_via_ledger(desired_ids, toysi_ids, prom_products)
    if ledger_prune:
        removed = _prune_ledger(ledger_prune)
        print(f"[Sync] Журнал: прибрано {removed} відсутніх/видалених external_id (самоочищення).")
    if ledger_delist:
        if not args.apply:
            print(f"[Sync] DRY-RUN (журнал): деактивував би {len(ledger_delist)} невидимих застарілих "
                  f"(перші 10: {ledger_delist[:10]}).")
        else:
            l_batch = ledger_delist[:LEDGER_MAX_DELIST_PER_RUN]
            if len(ledger_delist) > LEDGER_MAX_DELIST_PER_RUN:
                print(f"[Sync] Журнал: {len(ledger_delist)} живих застарілих — беру перші "
                      f"{LEDGER_MAX_DELIST_PER_RUN} цього прогону, решту наступними.")
            print(f"[Sync] Деактивую {len(l_batch)} невидимих застарілих (журнал, свіжа GET-звірка на кожен)...")
            l_processed, l_errors = deactivate(l_batch)
            print(f"[Sync] Журнал: оброблено {len(l_processed)}, помилок {len(l_errors)}.")
            if l_processed:
                l_price_state = load_prom_price_state()
                l_now_iso = datetime.now().isoformat()
                l_delisted_since = l_price_state.setdefault("_delisted_since", {})
                for e in l_processed:
                    l_delisted_since[e] = l_now_iso
                save_prom_price_state(l_price_state)
                print(f"[Sync] Журнал: записано {len(l_processed)} SKU у _delisted_since "
                      "(select_top_items() не пропонуватиме їх знову).")

    stale_ids = find_stale_external_ids(prom_products, desired_ids, toysi_ids)
    print(f"[Sync] Застарілих товарів (є в Prom, походять з Toysi, "
          f"випали з топ-6000, ще не видалені): {len(stale_ids)}")

    if not stale_ids:
        print("[Sync] Нічого деактивувати — каталог відповідає топ-6000.")
        return

    for ext_id in stale_ids[:20]:
        p = prom_products[ext_id]
        print(f"  - {ext_id}: {p.get('name', '')[:60]!r} "
              f"(presence={p.get('presence')}, status={p.get('status')})")
    if len(stale_ids) > 20:
        print(f"  ... та ще {len(stale_ids) - 20}")

    if not args.apply:
        print("\n[Sync] DRY-RUN: жодних змін не внесено. Запусти з --apply, щоб реально деактивувати.")
        return

    if args.max_deactivations is not None:
        # СУПЕРВІЗОВАНЕ чищення бэклогу (людина переглянула dry-run список і свідомо
        # дозволяє більший батч). Кап на масовість (probable-bug) обходиться людиною,
        # АЛЕ view-guard «неповний зріз кабінету» ЛИШАЄТЬСЯ — на битому зрізі не видаляємо.
        view_warning = check_product_count_sane(prom_products)
        if view_warning:
            msg = (f"prom_catalog_sync: супервізоване чищення ПРОПУЩЕНО — НЕнадійний вид кабінету "
                   f"({len(prom_products)} товарів), видалення наосліп небезпечне. {view_warning}")
            print(f"[Sync] {msg}", file=sys.stderr)
            send_telegram_message("🚨 " + msg)
            return
        if len(stale_ids) > args.max_deactivations:
            print(f"[Sync] Супервізований батч: {len(stale_ids)} застарілих — беру перші "
                  f"{args.max_deactivations} цього прогону, решту наступними прогонами.")
            stale_ids = stale_ids[:args.max_deactivations]
    else:
        # ЗАХИСТ від масового видалення (2026-07-30): перш ніж реально видаляти —
        # надійність виду кабінету + кап на масовість. Понад ліміт або на частковому
        # зрізі: НЕ видаляємо, алертимо в Telegram, лишаємо рішення людині.
        block_reason = deletion_guard_reason(prom_products, stale_ids)
        if block_reason:
            msg = f"prom_catalog_sync: масове видалення ПРОПУЩЕНО — {block_reason}"
            print(f"[Sync] {msg}", file=sys.stderr)  # у лог — ЗАВЖДИ, на кожному заблокованому прогоні
            now = datetime.now()
            if _block_alert_due(now):  # Telegram — не частіше 1/24год (throttle, аудит #188/GOTCHAS #5)
                send_telegram_message("🚨 " + msg)
                _record_block_alert(now)
            else:
                print("[Sync] Telegram-алерт про блокування пропущено — 24-год throttle (вже алертив нещодавно).",
                      file=sys.stderr)
            return

    _clear_block_alert()  # видалення дозволено цього прогону → епізод блокування завершено, скидаємо throttle
    print(f"\n[Sync] Деактивую {len(stale_ids)} товарів (status=deleted)...")
    processed, errors = deactivate(stale_ids)
    print(f"[Sync] Оброблено: {len(processed)}. Помилок: {len(errors)}.")
    if errors:
        for ext_id, err in list(errors.items())[:20]:
            print(f"  - {ext_id}: {err}")

    # ДОДАНО (2026-07-24, живий root-cause: Prom import падав на "Поле status:
    # Позиція недоступна для оновлення" для 179 SKU, підтверджено 404 напряму
    # через products/by_external_id — товари реально й назавжди видалені, але
    # select_top_items() продовжувала їх пропонувати знову). deactivate() тут —
    # ДРУГИЙ, окремий від prom_competitor_pricer.py::delist() шлях реального
    # видалення (SKU випав з топ-6000, не через ціну конкурента), і єдиний, який
    # НІКОЛИ не записував _delisted_since — той самий запис, який
    # generate_prom_feed_top.py::_margin() вже й так перевіряє (load_delisted_
    # pids()), просто досі покривав лише ОДИН з двох реальних шляхів видалення.
    # Той самий принцип, що вже застосований для repricer-делісту — тепер
    # уніфіковано на ОБИДВА шляхи, а не патч лише для цих конкретних 179 SKU.
    if processed:
        price_state = load_prom_price_state()
        now_iso = datetime.now().isoformat()
        delisted_since = price_state.setdefault("_delisted_since", {})
        for ext_id in processed:
            delisted_since[ext_id] = now_iso
        save_prom_price_state(price_state)
        print(f"[Sync] Записано {len(processed)} SKU у _delisted_since — "
              f"select_top_items() більше не пропонуватиме їх знову.")

    stuck_state = _load_stuck_state()
    stuck_state = _update_stuck_state_and_alert(stuck_state, stale_ids, processed, errors, prom_products)
    _save_stuck_state(stuck_state)
    still_stuck = sum(1 for e in stuck_state.values() if e.get("fail_count", 0) >= STUCK_ALERT_THRESHOLD)
    if still_stuck:
        print(f"[Sync] {still_stuck} товарів застрягли на видаленні {STUCK_ALERT_THRESHOLD}+ прогонів поспіль "
              "(алерт надіслано щойно для нових, повторно не спамимо).")


if __name__ == "__main__":
    main()
