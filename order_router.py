import os
import re
import sys

from orders_db import (
    get_connection, get_orders_ready_to_forward, mark_forwarded_to_toysi,
    mark_ukrposhta_shipment, update_delivery_status,
)
from toysi_order_submit import submit_order
from nova_poshta import resolve_shipping, NovaPoshtaAPIError
from ukrposhta_client import create_shipment_with_label, UkrposhtaAPIError
from telegram_notify import send_telegram_message
import rozetka_client
from orders_watcher import update_prom_order_status, PromAPIError

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


def route_order(conn, order: dict, test_mode: bool = False) -> None:
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

        for order in candidates:
            route_order(conn, order, test_mode=test_mode)


if __name__ == "__main__":
    route_pending_orders()
