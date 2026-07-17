import os
import re
import sys
from datetime import datetime, timedelta

from orders_db import (
    get_connection, get_orders_ready_to_forward, mark_forwarded_to_toysi,
    mark_ukrposhta_shipment, update_delivery_status,
    mark_stock_alert_sent, clear_stock_alert,
)
from parser import fetch_toysi_catalog
from toysi_order_submit import submit_order
from nova_poshta import resolve_shipping, NovaPoshtaAPIError
from ukrposhta_client import create_shipment_with_label, UkrposhtaAPIError
from telegram_notify import send_telegram_message
import rozetka_client
from orders_watcher import update_prom_order_status, check_prom_order_status, PromAPIError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

"""
Крок 5 плану: маршрутизація замовлень з orders.db до Toysi.
Накладені (cod) — одразу. Передоплачені (prepaid) — лише коли
payment_confirmed=1 (виставляє bank_check.py). Обирає кандидатів через
orders_db.get_orders_ready_to_forward() — воно вже враховує обидва правила
і виключає замовлення з попередньою помилкою (status='toysi_error').

Укрпошта (2026-07-10): паралельний шлях для order["carrier"]=="ukrposhta".
Toysi не приймає наш API-ключ Укрпошти (політика компанії) — ТТН і PDF-
етикетку створюємо самі через ukrposhta_client.py ПЕРЕД тим, як передати
замовлення в Toysi (order_create). Toysi order_create НЕ приймає ТТН через
API (підтверджено емпірично 2026-07-10: поле "ttn" у запиті ігнорується,
order_status після цього повертає порожній TTN) — тож ТТН/етикетку далі
треба вручну внести в кабінет toysi.ua/lk (одна дія, без третьої особи —
дивись orders_db.get_orders_awaiting_manual_ttn_entry(), кандидат на
браузерну автоматизацію Фази 2).
"""

UKRPOSHTA_STICKERS_DIR = "ukrposhta_stickers"

_WAREHOUSE_RE = re.compile(r"(?:відділенн\w*|відд\.?|№)\s*№?\s*(\d+)", re.IGNORECASE)
_CITY_PREFIX_RE = re.compile(r"^(м\.|с\.|смт\.?)\s*", re.IGNORECASE)
_CITY_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")
_CITY_AREA_RE = re.compile(r"\(([^)]*?)\s*обл\.?\)\s*$", re.IGNORECASE)


def parse_np_branch(np_branch: str) -> tuple:
    """
    Витягує (місто, запит_відділення, назва_області) з вільнотекстового
    np_branch (напр. "Київ, відділення №15" з мок-даних, або delivery_address
    з реального Prom).

    Перевірено на реальному замовленні №414634349: Prom для міст, чия назва
    збігається з назвою області, додає уточнення в дужках — "м. Київ
    (Київська обл.), №253 (до 30 кг на одне місце): вул. ...". Без відсікання
    цього суфіксу Нова Пошта не знаходить місто взагалі ("Місто не знайдено:
    Київ (Київська обл.)"). Але просто відкидати суфікс небезпечно: багато
    сіл/селищ мають ОДНАКОВУ назву в кількох областях (Миколаївка, Іванівка
    тощо), і без області getCities() може повернути геть інший населений
    пункт першим результатом. Тому область не відкидаємо, а повертаємо
    окремо — resolve_shipping()/find_city() використовують її, щоб серед
    кількох збігів обрати той, що з потрібної області.
    """
    if not np_branch:
        return "", "", ""

    warehouse_match = _WAREHOUSE_RE.search(np_branch)
    warehouse_query = warehouse_match.group(1) if warehouse_match else ""

    city_part = np_branch.split(",")[0]
    area_match = _CITY_AREA_RE.search(city_part)
    area_hint = area_match.group(1).strip() if area_match else ""

    city = _CITY_PREFIX_RE.sub("", city_part).strip()
    city = _CITY_SUFFIX_RE.sub("", city).strip()
    return city, warehouse_query, area_hint


def _split_name(customer_name: str) -> tuple:
    """Toysi вимагає ім'я/прізвище окремо; customer_name у нас — один рядок."""
    parts = (customer_name or "").split(maxsplit=1)
    first_name = parts[0] if parts else "Клієнт"
    last_name = parts[1] if len(parts) > 1 else "Невідомо"
    return first_name, last_name


def _normalize_phone_for_toysi(phone: str) -> str:
    """Toysi документує shipping_phone як "12 цифр з '380...'" — без "+" та
    без пробілів/дефісів. Реальний Prom order["phone"] прийшов у форматі
    "+380504287634" (з "+") — мок-дані (єдине джерело перевірки логіки до
    першого реального замовлення) завжди були без "+", тому це не спливало.
    Перевірено на реальному замовленні №414634349: Toysi відхилив саме з цієї
    причини (response_code=16, "Невірний телефон отримувача")."""
    return re.sub(r"\D", "", phone or "")


# P0-6 (2026-07-17): Prom-фід (generate_prom_feed*.py) регенерується й
# перезаливається кожні 4 год через GitHub Actions, тож клієнт міг
# оформити замовлення на товар, чий залишок у Toysi змінився вже ПІСЛЯ
# останньої синхронізації фіда — вікно застарілості до ~8 год (два цикли
# поспіль). Це структурно годує показник скасувань Prom (P0-2): ми
# передаємо в Toysi замовлення на товар, якого вже немає, Toysi/Нова
# Пошта згодом самі це виявляють, і це стає скасуванням, яке рахується
# ПРОТИ нас. Перевірка тут — ОСТАННІЙ живий погляд на залишок Toysi САМЕ
# в момент передачі (а не довіра фіду, яким клієнт користувався), ПЕРЕД
# тим, як ми підтверджуємо замовлення на маркетплейсі (_update_marketplace_
# status() нижче, викликається лише ПІСЛЯ успішної передачі Toysi).
#
# Toysi не має окремого API для перевірки залишку ОДНОГО товару — єдине
# джерело це весь каталог (fetch_toysi_catalog(), ~70МБ). Тому
# route_pending_orders() завантажує його ОДИН РАЗ на весь цикл (не на
# кожне замовлення) і передає сюди.
def _check_toysi_stock(order: dict, toysi_catalog: dict) -> tuple:
    """Повертає (є_в_наявності, опис) для ВСІХ позицій замовлення разом.
    Позиція, відсутня в поточному каталозі Toysi взагалі (не лише
    stock=0) — теж трактується як недоступна (могла зникнути з
    асортименту постачальника цілком, не лише скінчитись на складі)."""
    shortages = []
    for item in order["items"]:
        toysi_code = str(item.get("toysi_code") or "")
        needed_qty = item.get("qty", 1)
        cat_item = toysi_catalog.get(toysi_code)
        available = cat_item.get("stock", 0) if cat_item else 0
        if available < needed_qty:
            name = (cat_item or {}).get("name") or item.get("name") or toysi_code
            shortages.append(f"{name} (потрібно {needed_qty}, є {available})")
    if shortages:
        return False, "; ".join(shortages)
    return True, ""


def build_toysi_order(order: dict) -> dict:
    """Перетворює запис orders_db на структуру для toysi_order_submit.submit_order()."""
    city, warehouse_query, area_hint = parse_np_branch(order.get("np_branch", ""))

    shipping_fields = {}
    # NP-резолв (getCities/getWarehouses) стосується лише Нової Пошти — для
    # Укрпошти shipping_city_id/warehouse_id взагалі не мають сенсу (Toysi
    # не інтегрована з Укрпоштою, ці поля не використовуються на її боці),
    # і сам виклик resolve_shipping() був би зайвим мережевим запитом.
    if city and order.get("carrier", "nova_poshta") == "nova_poshta":
        try:
            shipping = resolve_shipping(city, warehouse_query, area_hint=area_hint)
        except NovaPoshtaAPIError as e:
            print(
                f"[order_router] Проблема з API Нової Пошти для {order['internal_order_id']}: {e}",
                file=sys.stderr,
            )
            shipping = None
        if shipping:
            shipping_fields = {
                "shipping_city_id": shipping["shipping_city_id"],
                "shipping_warehouse_id": shipping["shipping_warehouse_id"],
            }

    first_name, last_name = _split_name(order.get("customer_name", ""))

    moneyback = 0.0
    if order["payment_method"] == "cod":
        moneyback = sum(item.get("price", 0) * item.get("qty", 1) for item in order["items"])

    return {
        "internal_order_id": order["internal_order_id"][:25],
        "items": order["items"],
        "first_name": first_name,
        "last_name": last_name,
        "phone": _normalize_phone_for_toysi(order.get("phone", "")),
        "shipping_city_name": city or "Київ",  # Toysi вимагає непорожнє місто
        # Без NP-резолву адреса лишається вільним текстом np_branch — бажано,
        # ніж порожній рядок (response_code 20 "порожня адреса доставки").
        "shipping_address": order.get("np_branch", "") if not shipping_fields else "",
        "moneyback": moneyback,
        "comment": f"Автоматично: {order['platform']} #{order['order_id']}",
        **shipping_fields,
    }


def _create_ukrposhta_shipment(order: dict) -> dict:
    """Створює відправлення Укрпоштою через ukrposhta_client.py: ТТН + PDF-
    етикетка, збережена локально в UKRPOSHTA_STICKERS_DIR. Повертає None (без
    винятку), якщо не вдалось — виклик вище просто пропускає замовлення до
    наступного циклу, той самий підхід, що й для тимчасових помилок Toysi
    (should_retry у route_order())."""
    first_name, last_name = _split_name(order.get("customer_name", ""))
    city, _, _ = parse_np_branch(order.get("np_branch", ""))

    moneyback = 0.0
    if order["payment_method"] == "cod":
        moneyback = sum(item.get("price", 0) * item.get("qty", 1) for item in order["items"])

    try:
        shipment = create_shipment_with_label(
            recipient_first_name=first_name,
            recipient_last_name=last_name,
            recipient_phone=_normalize_phone_for_toysi(order.get("phone", "")),
            recipient_city=city or "Київ",
            # TODO: Prom delivery_provider_data для Укрпошти може містити індекс
            # отримувача окремим полем — уточнити на першому реальному
            # замовленні з доставкою Укрпоштою (поки не було жодного).
            recipient_postcode="",
            cod_amount=moneyback,
        )
    except UkrposhtaAPIError as e:
        print(
            f"[order_router] Не вдалось створити відправлення Укрпоштою для "
            f"{order['internal_order_id']}: {e}",
            file=sys.stderr,
        )
        return None

    os.makedirs(UKRPOSHTA_STICKERS_DIR, exist_ok=True)
    sticker_path = os.path.join(UKRPOSHTA_STICKERS_DIR, f"{order['internal_order_id']}.pdf")
    with open(sticker_path, "wb") as f:
        f.write(shipment["sticker_pdf"])

    return {"ttn": shipment["ttn"], "sticker_path": sticker_path}


def _update_marketplace_status(order: dict) -> None:
    """
    Задача власниці 2026-07-15: клієнт бачив СТАРИЙ статус на маркетплейсі
    (наприклад, "Оплачено"/"Нове замовлення"), хоча замовлення вже реально
    передане в Toysi й в обробці — статус на самому маркетплейсі ніяк не
    оновлювався в момент forward. Викликається одразу ПІСЛЯ
    mark_forwarded_to_toysi(), лише для реальних (не test_mode) передач.

    Best-effort: помилка тут НЕ повинна відкочувати чи блокувати вже
    успішну передачу в Toysi — замовлення вже реально в обробці незалежно
    від того, чи вдалось оновити видимий клієнту статус. Наступний цикл
    order_status_tracker.py/route_pending_orders() тут нічого не
    повторює автоматично (це одноразова дія в момент forward, не
    ідемпотентний стан у orders_db) — якщо виклик не вдався, статус
    лишиться старим до ручної перевірки.

    ВИПРАВЛЕНО (знайдено власним тестом перед комітом): rozetka_client.
    _login() кидає голий RuntimeError, коли ROZETKA_USERNAME/
    ROZETKA_PASSWORD не задані в .env — НЕ RozetkaAPIError. Без широкого
    except Exception нижче це реально ВАЛИЛО Б route_order() цілком (не
    лише пропускало б оновлення статусу) для КОЖНОГО замовлення, доки
    власниця не додасть ці облікові дані — той самий клас бага, що вже
    закривали для _maybe_push_ttn_to_rozetka()/_maybe_register_ettn() у
    order_status_tracker.py.
    """
    try:
        if order["platform"] == "rozetka":
            # status=2 без ttn — "Комплектується. Дані підтверджені", ТТН
            # додасться пізніше окремо (order_status_tracker.py), коли
            # з'явиться від Toysi.
            rozetka_client.update_order_status(order["order_id"], status=rozetka_client.ORDER_STATUS_PROCESSING)
        elif order["platform"] == "prom":
            update_prom_order_status(order["order_id"])
    except (rozetka_client.RozetkaAPIError, PromAPIError) as e:
        print(
            f"[order_router] Не вдалось оновити статус на {order['platform']} для "
            f"{order['internal_order_id']}: {e}",
            file=sys.stderr,
        )
    except Exception as e:
        print(
            f"[order_router] Неочікувана помилка при оновленні статусу на {order['platform']} для "
            f"{order['internal_order_id']}: {e}",
            file=sys.stderr,
        )


# ВИПРАВЛЕНО (2026-07-17, реальний інцидент — замовлення №415858222):
# клієнтка скасувала замовлення на Prom, поки воно "зависло" непідхопленим
# автоматикою; ручне відновлення (вставка запису в orders.db + запуск
# order-router.service) відправило товар у Toysi, НЕ перевіривши, що
# клієнт тим часом скасував замовлення на Prom — небажана відправка,
# клієнтка сама сплатила доставку (субсидія "Дешева доставка" не
# спрацювала, бо ТТН не було зареєстровано в самому Prom).
_PROM_CANCELLED_STATUSES = {"canceled"}


def _check_prom_not_cancelled(conn, order: dict) -> bool:
    """Живий запит до Prom Orders API ПЕРЕД будь-яким форвардом у Toysi —
    єдина точка виклику всередині route_order(), тож автоматично
    покриває ОБИДВА шляхи, про які йде мова в задачі: звичайний цикл
    (route_pending_orders() -> route_order()) і safety-net
    (service_watchdog.check_unforwarded_orders(), що викликає route_order()
    напряму, P0-6) — а також ручне відновлення (insert в orders.db +
    перезапуск order-router.service, який зрештою так само доходить до
    route_pending_orders()).

    Лише для platform="prom" — Rozetka ще не активна в проді (статус
    реєстрації "Підготовка"), еквівалентної перевірки там поки немає;
    додати за тим самим патерном, коли Rozetka реально стане активною.

    date_from для check_prom_order_status() — початок КАЛЕНДАРНОГО ДНЯ
    перед датою створення замовлення (є в orders.db), не сама
    дата/час створення з невеликим запасом — ПЕРЕВІРЕНО ЖИВО
    (2026-07-17, реальне замовлення №415858222): 2-годинний запас від
    known created_at виявився недостатнім (замовлення НЕ знайшлось у
    вужчому вікні, хоча знайшлось з денним запасом) — найімовірніше,
    orders.db зберігає created_at як момент, коли МИ вперше побачили
    замовлення (наступний цикл опитування), а не точний date_created
    Prom, тож малий запас відносно НАШОГО timestamp ризикує почати
    вікно ПІЗНІШЕ за реальний Prom-timestamp. Початок попереднього дня —
    надійний запас проти цього класу розбіжності, і досі знаходить
    конкретне замовлення за 1-2 сторінки (перевірено), а не сканує весь
    нещодавній список.

    Повертає True (можна продовжувати форвард), якщо живий статус НЕ
    "canceled", АБО якщо перевірити не вдалось (мережа/немає ключа/не
    знайдено у вікні) — той самий fail-open принцип, що й
    _check_toysi_stock() нижче: тимчасова недоступність перевірки не
    повинна зупиняти весь конвеєр лише через відсутність підтвердження.

    Повертає False (форвард НЕ відбувається) лише коли Prom ЖИВО й
    ПОЗИТИВНО підтверджує скасування — ескалює в Telegram і позначає
    status='prom_cancelled_before_forward', щоб get_orders_ready_to_
    forward() більше не повертав це замовлення на кожному циклі (той
    самий фільтр-виняток, що вже діє для 'toysi_error')."""
    if order.get("platform") != "prom":
        return True

    date_from = None
    created_at = order.get("created_at")
    if created_at:
        try:
            created_date = datetime.fromisoformat(created_at).date()
            date_from = (datetime(created_date.year, created_date.month, created_date.day) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            date_from = None

    live_status = check_prom_order_status(order["order_id"], date_from=date_from)
    if live_status not in _PROM_CANCELLED_STATUSES:
        return True

    message = (
        f"🛑 {order['internal_order_id']} (Prom #{order['order_id']}): замовлення СКАСОВАНО на Prom "
        f"(живий статус: {live_status}), поки воно чекало передачі в Toysi.\n"
        f"Клієнт: {order.get('customer_name') or '?'}\n"
        "Форвард у Toysi ЗУПИНЕНО автоматично. Відправити все одно вручну, чи залишити скасованим?"
    )
    print(f"[order_router] {message}", file=sys.stderr)
    send_telegram_message(message)
    update_delivery_status(conn, order["internal_order_id"], status="prom_cancelled_before_forward")
    return False


def route_order(conn, order: dict, test_mode: bool = False, toysi_catalog: dict = None) -> None:
    if not _check_prom_not_cancelled(conn, order):
        return

    # P0-6: якщо викликач не передав каталог (напр. service_watchdog.py's
    # check_unforwarded_orders(), який викликає route_order() напряму) —
    # завантажуємо самі, щоб перевірка діяла незалежно від того, звідки
    # прийшов виклик. Порожній каталог (мережева помилка/немає ключа) не
    # блокує передачу — best-effort: краще ризикнути застарілим
    # припущенням "в наявності", ніж зупинити весь конвеєр через
    # тимчасову недоступність фіда Toysi.
    if toysi_catalog is None:
        toysi_catalog = fetch_toysi_catalog()

    if toysi_catalog:
        in_stock, shortage_detail = _check_toysi_stock(order, toysi_catalog)
        if not in_stock:
            if not order.get("stock_alert_sent_at"):
                message = (
                    f"⚠️ {order['internal_order_id']}: живий залишок Toysi зараз "
                    f"недостатній — {shortage_detail}. Замовлення НЕ передано в Toysi, "
                    "спробуємо знову наступного циклу (можливо, тимчасово)."
                )
                print(f"[order_router] {message}", file=sys.stderr)
                if send_telegram_message(message):
                    mark_stock_alert_sent(conn, order["internal_order_id"])
            else:
                print(
                    f"[order_router] {order['internal_order_id']}: залишок Toysi досі "
                    f"недостатній ({shortage_detail}) — алерт вже надсилався, повторюємо мовчки",
                    file=sys.stderr,
                )
            return
        elif order.get("stock_alert_sent_at"):
            clear_stock_alert(conn, order["internal_order_id"])

    if test_mode:
        # Toysi документує api_mode=test буквально: "заказ не будет обрабатываться
        # менеджером" — не списує депозит, не потрапляє в "Історію замовлень",
        # ефемерний (авто-видалення через 41 день чи одразу при повторній передачі
        # з тим самим internal_order_id в реальному режимі). Реальний випадок
        # (замовлення №414634349, 2026-07-08): продакшн-виклик мовчки йшов у
        # test_mode за замовчуванням тижнями — Toysi відповідав response_code=1,
        # "успіх" виглядав правдоподібно, але жодне замовлення реально не
        # створювалось. Тому test_mode тепер за замовчуванням False, а якщо його
        # все ж передали True — сигналимо голосно, а не мовчки.
        warning = (
            f"⚠️ order_router: {order['internal_order_id']} передається в Toysi з "
            "test_mode=True — Toysi НЕ створить реальне замовлення (не спише депозит, "
            "не з'явиться в Історії замовлень). Якщо це не навмисний ручний тест — "
            "перевір виклик route_order()/route_pending_orders()."
        )
        print(warning, file=sys.stderr)
        if not send_telegram_message(warning):
            print("[order_router] Не вдалося надіслати попередження про test_mode у Telegram", file=sys.stderr)

    carrier = order.get("carrier", "nova_poshta")
    ukrposhta_shipment = None
    if carrier == "ukrposhta":
        # ТТН/етикетку створюємо ДО передачі в Toysi — якщо Укрпошта API
        # недоступне (чи UKRPOSHTA_API_KEY ще не задано), немає сенсу
        # реєструвати замовлення в Toysi, яке нічим відправити.
        ukrposhta_shipment = _create_ukrposhta_shipment(order)
        if ukrposhta_shipment is None:
            return

    toysi_order = build_toysi_order(order)
    if carrier == "ukrposhta":
        # Прийнято Toysi без помилки (перевірено емпірично 2026-07-10), але
        # НЕ створює реальне відправлення на боці Toysi — Укрпошта в них не
        # інтегрована. Це лише інформативне поле для замовлення в їхній системі.
        toysi_order["shipping_carrier_name"] = "Укрпошта"
    result = submit_order(toysi_order, test_mode=test_mode)

    if result["accepted"] and test_mode:
        # Не позначаємо forwarded_to_toysi_at/status='forwarded_to_supplier' — це
        # передбачило б, що order_status_tracker.py/watchdog починають відстежувати
        # РЕАЛЬНЕ виконання замовлення, якого в test_mode не існує.
        print(
            f"[order_router] Тестова відправка {order['internal_order_id']} прийнята "
            f"Toysi (toysi_order_id={result.get('toysi_order_id')}, api_mode=test) — "
            "НЕ позначено як передане, це не реальне замовлення",
            file=sys.stderr,
        )
    elif result["accepted"]:
        toysi_id = result.get("toysi_order_id")
        mark_forwarded_to_toysi(conn, order["internal_order_id"], str(toysi_id) if toysi_id is not None else "")
        _update_marketplace_status(order)
        dup_note = " (дублікат — вже існував у Toysi)" if result["is_duplicate"] else ""
        if ukrposhta_shipment:
            mark_ukrposhta_shipment(
                conn, order["internal_order_id"], ukrposhta_shipment["ttn"], ukrposhta_shipment["sticker_path"],
            )
            print(
                f"[order_router] Укрпошта: {order['internal_order_id']} -> ТТН "
                f"{ukrposhta_shipment['ttn']}, передано Toysi (toysi_order_id={toysi_id}){dup_note} — "
                "ЧЕКАЄ РУЧНОГО внесення ТТН/етикетки в toysi.ua/lk"
            )
        else:
            print(
                f"[order_router] Передано Toysi: {order['internal_order_id']} -> "
                f"toysi_order_id={toysi_id}{dup_note}"
            )
    elif result["should_retry"]:
        print(
            f"[order_router] Тимчасова помилка, спробуємо в наступному циклі: "
            f"{order['internal_order_id']} — {result['message']}",
            file=sys.stderr,
        )
    else:
        update_delivery_status(conn, order["internal_order_id"], status="toysi_error")
        print(
            f"[order_router] ПОМИЛКА даних замовлення {order['internal_order_id']}: "
            f"response_code={result['response_code']} — {result['message']}",
            file=sys.stderr,
        )


def route_pending_orders(test_mode: bool = False) -> None:
    with get_connection() as conn:
        candidates = get_orders_ready_to_forward(conn)
        if not candidates:
            print("[order_router] Немає замовлень, готових до передачі")
            return

        # P0-6: один живий фетч каталогу Toysi на весь цикл, не на кожне
        # замовлення окремо — той самий каталог, свіжий на момент ЦЬОГО
        # прогону order_pipeline.py, а не кешована копія з генерації фіда.
        toysi_catalog = fetch_toysi_catalog()
        for order in candidates:
            route_order(conn, order, test_mode=test_mode, toysi_catalog=toysi_catalog)


if __name__ == "__main__":
    route_pending_orders()
