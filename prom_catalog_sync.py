"""
Активна синхронізація каталогу Prom із поточним відбором топ-970.

Навіщо цей скрипт існує (не дублює вбудований імпорт Prom):
Кабінет Prom при повторному імпорті з "Статус товарів, яких немає в файлі" =
"Немає в наявності" лише позначає товар недоступним — товар лишається
"Опубліковано" і далі займає одне з обмежених 1000 місць тарифу (перевірено
емпірично 2026-07-10: SKU 267230 після такого повторного імпорту досі
"Опубліковано", а лічильник "Додано: X/1000" не зменшився). Прибирання
товару з АКТИВНОГО каталогу (реальне звільнення місця під заміну кращим за
маржею SKU) вбудований імпорт не робить — ні на 4-годинному, ні на нічному
циклі. Цей скрипт закриває саме цю прогалину через Prom API.

Що робить:
1. Рахує актуальний відбір топ-970 (select_top_items — та сама логіка, що й
   у фактичному фіді).
2. Тягне ПОВНИЙ список товарів, які зараз реально опубліковані в кабінеті
   Prom (GET /products/list).
3. Товар деактивується (status="deleted" через POST /products/edit_by_external_id),
   лише якщо ОБИДВІ умови виконані:
   - його external_id є в ПОВНОМУ каталозі Toysi (тобто це наш дропшип-товар,
     а не щось, додане вручну власником чи іншим постачальником — таких
     ніколи не чіпаємо);
   - його немає в поточному відборі топ-970 (нульовий залишок, витіснений
     кращим за маржею SKU, чи категорія виключена).
   Звільнене місце забирає наступний імпорт Prom (нічне створення нових
   товарів — уже не задача цього скрипта, воно й так увімкнене через
   "Автоматичне оновлення посилання: Раз на 4 години").

Безпека: за замовчуванням DRY-RUN — лише друкує, що БУЛО Б деактивовано.
Реальні зміни в кабінеті Prom — тільки з явним --apply.
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv

from generate_prom_feed_top import select_top_items
from parser import fetch_toysi_catalog, assert_catalog_size_sane, CatalogSizeError
from telegram_notify import send_telegram_message

# Консоль Windows (cp1251) не показує деякі символи — без цього локальний
# тестовий запуск падає на print() (див. daily_report.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

PROM_API_KEY    = os.environ.get("PROM_API_KEY", "")
PROM_API_URL    = "https://my.prom.ua/api/v1"
REQUEST_TIMEOUT = 30

PAGE_SIZE  = 100  # /products/list
EDIT_BATCH = 100  # /products/edit_by_external_id — розмір пачки на запит


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
# Кабінет зараз показує ~964/1000 опублікованих товарів. Поріг узгоджено із
# запасом ~5-6% нижче цього (не впритул, як спершу поставлений 950 — лише
# ~1.5% запасу, замало проти звичайного денного коливання) — той самий підхід
# із запасом, що й TOYSI_EXPECTED_MIN_SIZE у parser.py (там ~15% запасу проти
# ~29 325 реальних SKU; тут менший відсоток обґрунтований тим, що каталог
# Prom природно коливається значно менше день у день, ніж повний каталог
# постачальника Toysi).
MIN_EXPECTED_PRODUCT_COUNT = 910


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
        response = requests.get(
            f"{PROM_API_URL}/groups/list",
            headers={"Authorization": f"Bearer {PROM_API_KEY}"},
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
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
        response = requests.get(
            f"{PROM_API_URL}/products/list",
            headers={"Authorization": f"Bearer {PROM_API_KEY}"},
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
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
    with ThreadPoolExecutor(max_workers=10) as executor:
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
    (виявлення застарілих товарів поза топ-970) і далі потребує повного
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
    with ThreadPoolExecutor(max_workers=10) as executor:
        for ext_id, (status, product) in zip(ids_list, executor.map(_fetch_one, ids_list)):
            if status == "found" and product:
                found[ext_id] = product
            elif status == "indeterminate":
                indeterminate.add(ext_id)
            # status == "absent" (справжній 404) -> ні found, ні indeterminate;
            # викликач трактує таке як підтверджено відсутнє.

    return found, indeterminate


def find_stale_external_ids(prom_products: dict, desired_ids: set, toysi_ids: set) -> list:
    """Товари, які реально опубліковані в Prom, походять з нашого Toysi-фіда
    (не додані вручну власником — таких не чіпаємо), більше не входять у
    поточний топ-970, і ще не позначені видаленими."""
    return [
        ext_id for ext_id, p in prom_products.items()
        if ext_id in toysi_ids
        and ext_id not in desired_ids
        and p.get("status") != "deleted"
    ]


def deactivate(stale_ids: list) -> tuple:
    """POST /products/edit_by_external_id, status=deleted, пачками по EDIT_BATCH.
    Повертає (processed_ids, errors).

    Кожна пачка — окремий try/except: якщо пізня пачка впаде HTTP-помилкою
    (5xx, rate-limit, мережевий збій), уже виконані попередні пачки НЕ
    втрачаються з результату (раніше виняток із raise_for_status() проривався
    з функції без return, і виклик губив облік уже реально деактивованих
    товарів на боці Prom — жодного логу, які саме id це були)."""
    processed, errors = [], {}
    for i in range(0, len(stale_ids), EDIT_BATCH):
        chunk = stale_ids[i:i + EDIT_BATCH]
        payload = [{"id": ext_id, "status": "deleted"} for ext_id in chunk]
        try:
            response = requests.post(
                f"{PROM_API_URL}/products/edit_by_external_id",
                headers={"Authorization": f"Bearer {PROM_API_KEY}"},
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            result = response.json()
            processed.extend(result.get("processed_ids", []))
            errors.update(result.get("errors", {}))
        except requests.exceptions.RequestException as e:
            print(f"[Sync] Пачка {i // EDIT_BATCH + 1} ({len(chunk)} товарів) впала: {e}", file=sys.stderr)
            for ext_id in chunk:
                errors[ext_id] = f"batch request failed: {e}"
    return processed, errors


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                     help="Реально викликати Prom API та деактивувати товари. "
                          "Без цього прапорця — лише dry-run звіт.")
    args = ap.parse_args()

    if not PROM_API_KEY:
        print("[Sync] PROM_API_KEY не задано — зупиняюсь.", file=sys.stderr)
        sys.exit(1)

    print("[Sync] Рахую поточний відбір топ-970...")
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
    print(f"[Sync] У кабінеті Prom: {len(prom_products)} товарів. "
          f"У поточному топ-970: {len(desired_ids)}.")

    stale_ids = find_stale_external_ids(prom_products, desired_ids, toysi_ids)
    print(f"[Sync] Застарілих товарів (є в Prom, походять з Toysi, "
          f"випали з топ-970, ще не видалені): {len(stale_ids)}")

    if not stale_ids:
        print("[Sync] Нічого деактивувати — каталог відповідає топ-970.")
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

    print(f"\n[Sync] Деактивую {len(stale_ids)} товарів (status=deleted)...")
    processed, errors = deactivate(stale_ids)
    print(f"[Sync] Оброблено: {len(processed)}. Помилок: {len(errors)}.")
    if errors:
        for ext_id, err in list(errors.items())[:20]:
            print(f"  - {ext_id}: {err}")


if __name__ == "__main__":
    main()
