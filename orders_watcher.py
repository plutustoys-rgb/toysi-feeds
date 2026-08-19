import os
import re
import sys
import time
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

from orders_db import get_connection, init_db, insert_order, mark_payment_confirmed
import rozetka_client
import eva_orders_client

load_dotenv()

PROM_API_KEY     = os.environ.get("PROM_API_KEY", "")
ROZETKA_USERNAME = os.environ.get("ROZETKA_USERNAME", "")
ROZETKA_PASSWORD = os.environ.get("ROZETKA_PASSWORD", "")
EVA_MERCHANT_USERNAME = os.environ.get("EVA_MERCHANT_USERNAME", "")
EVA_MERCHANT_PASSWORD = os.environ.get("EVA_MERCHANT_PASSWORD", "")

PROM_API_URL    = "https://my.prom.ua/api/v1"
REQUEST_TIMEOUT = 30

POLL_INTERVAL_SECONDS = 15 * 60  # 10-15 хв за планом (Крок 3)

# ВИПРАВЛЕНО (2026-07-15, реальний інцидент — замовлення №415858222,
# оплачене через Пром-оплату о 20:41 UTC, підтверджено оплаченим Prom уже
# о 20:42:46 — за 96 секунд): раніше fetch_new_orders_prom() запитував
# ЛИШЕ status=pending. Онлайн-оплата (Пром-оплата/evopay) переводить
# замовлення зі статусу "pending" в "paid" за лічені секунди — набагато
# швидше за 15-хвилинний цикл опитування (POLL_INTERVAL_SECONDS вище).
# Якщо жоден цикл не встигав застати це вузьке "pending"-вікно (живо
# підтверджено журналом: перший прогін після створення замовлення був
# через 9+ хв, замовлення вже мало статус paid) — замовлення випадало з
# поля зору НАЗАВЖДИ, бо статус більше ніколи не повертається в pending.
# Замінено на широкий діапазон дат БЕЗ фільтра статусу (PROM_ORDER_
# LOOKBACK_HOURS) — той самий підхід, що вже перевірений живими
# запитами в reconcile_revenue.fetch_prom_orders_for_period(). Дедуп за
# (order_id, platform) і так уже робить orders_db.insert_order()/
# order_exists() — повторний прихід уже відомого замовлення щоцикл
# безпечний і дешевий (один SELECT), не створює дублів.
PROM_ORDER_LOOKBACK_HOURS = 72

# Ключові слова, за якими розпізнаємо накладений платіж у вільному тексті
# payment_option.name (Prom Orders API не дає чистого enum для способу оплати).
# Все, що НЕ підпадає під ці слова, вважаємо передоплатою (безпечніший дефолт:
# помилково зачекати підтвердження оплати краще, ніж помилково відправити
# товар без реальної оплати).
_COD_KEYWORDS = ("наклад", "післяплат", "отриманні", "готівк", "наложен")


def fetch_new_orders_prom() -> list:
    """
    Реальний виклик Prom Orders API (https://public-api.docs.prom.ua/, GET /orders/list,
    Authorization: Bearer PROM_API_KEY). Поки ключа немає — мок-замовлення,
    щоб перевіряти логіку router/orders.db без акаунту.

    Запитує ВСІ замовлення за PROM_ORDER_LOOKBACK_HOURS (без фільтра
    status=pending — див. коментар біля константи вище) з пагінацією через
    last_id (той самий підхід, що й reconcile_revenue.
    fetch_prom_orders_for_period()). Дедуп — на рівні orders_db, тут
    навмисно немає жодної спроби відрізнити "нове" від "вже баченого" —
    insert_order() сам ігнорує вже наявні (order_id, platform).
    """
    if not PROM_API_KEY:
        print("[Prom] PROM_API_KEY не задано — використовую мок-замовлення для перевірки логіки")
        return _mock_prom_orders()

    date_from = (datetime.now() - timedelta(hours=PROM_ORDER_LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    orders = []
    last_id = None
    while True:
        params = {"date_from": date_from, "limit": 100}
        if last_id is not None:
            params["last_id"] = last_id
        try:
            response = requests.get(
                f"{PROM_API_URL}/orders/list",
                headers={"Authorization": f"Bearer {PROM_API_KEY}"},
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"[Prom] Помилка з'єднання: {e}", file=sys.stderr)
            break

        try:
            data = response.json()
        except ValueError:
            print(f"[Prom] Невалідна відповідь (не JSON): {response.text[:300]}", file=sys.stderr)
            break

        page = data.get("orders", [])
        if not page:
            break
        orders.extend(page)
        if len(page) < 100:
            break
        last_id = page[-1]["id"]

    return [_convert_prom_order(o) for o in orders]


# ВИПРАВЛЕНО (2026-07-17, реальний інцидент — замовлення №415858222):
# клієнтка скасувала замовлення на Prom, поки воно "зависло"
# непідхопленим автоматикою; ручне відновлення (вставка запису в
# orders.db + запуск order-router.service) відправило товар у Toysi, НЕ
# перевіривши живий статус на Prom — небажана відправка, клієнтка сама
# оплатила доставку (субсидія "Дешева доставка" не спрацювала, бо ТТН не
# зареєстровано в самому Prom).
#
# GET /orders/list НЕ підтримує фільтр за конкретним id — перевірено
# живо 2026-07-17: жоден з очевидних варіантів параметра (`ids[]`, `ids`,
# `id`, `order_id`) не фільтрує відповідь, API мовчки повертає той самий
# дефолтний список. Єдиний робочий шлях — той самий, що вже й так
# використовує fetch_new_orders_prom(): `date_from` + пагінація за
# `last_id`, і шукати потрібний `id` серед сторінок. `date_from` тут —
# дата створення САМОГО замовлення (відома з orders.db) мінус невеликий
# запас, а не широкий LOOKBACK — щоб знайти конкретне замовлення за 1-2
# сторінки, не сканувати весь недавній список щоразу.
def check_prom_order_status(order_id, date_from: str = None) -> str | None:
    """Живий статус КОНКРЕТНОГО замовлення на Prom прямо зараз (той самий
    вокабуляр, що й update_prom_order_status(): pending/received/
    delivered/canceled/draft/paid/custom-{id}).

    Повертає None (не помилку), якщо PROM_API_KEY не задано, запит не
    вдався (мережа/невалідна відповідь), чи замовлення не знайдено в
    межах перевіреного вікна — це НЕ доказ, що замовлення не існує, лише
    те, що зараз перевірити не вдалось. Викликач (route_order()) має
    трактувати None як "не вдалось перевірити" і НЕ блокувати форвард
    лише на цій підставі (той самий fail-open принцип, що й
    _check_toysi_stock() у order_router.py — тимчасова недоступність
    перевірки не повинна зупиняти весь конвеєр)."""
    if not PROM_API_KEY:
        return None
    if date_from is None:
        date_from = (datetime.now() - timedelta(hours=PROM_ORDER_LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")

    last_id = None
    for _ in range(50):  # запобіжник проти нескінченної пагінації
        params = {"date_from": date_from, "limit": 100}
        if last_id is not None:
            params["last_id"] = last_id
        try:
            response = requests.get(
                f"{PROM_API_URL}/orders/list",
                headers={"Authorization": f"Bearer {PROM_API_KEY}"},
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            print(
                f"[Prom] Не вдалось перевірити живий статус замовлення {order_id}: {e}",
                file=sys.stderr,
            )
            return None

        page = data.get("orders", [])
        if not page:
            break
        for o in page:
            if str(o.get("id")) == str(order_id):
                return o.get("status")
        if len(page) < 100:
            break
        last_id = page[-1]["id"]

    print(
        f"[Prom] Замовлення {order_id} не знайдено в перевіреному вікні (date_from={date_from}) — "
        "не блокую форвард лише на цій підставі.",
        file=sys.stderr,
    )
    return None


_PRICE_WHITESPACE_RE = re.compile(r"[\s  ]")


def _parse_prom_price(raw) -> float:
    """Prom Orders API повертає product["price"] як число АБО як рядок з
    валютою ("39 грн") — перевірено на реальному замовленні №414634349, де
    саме другий варіант і призвів до ValueError на кожному циклі опитування
    (жоден мок-тест цього не ловив, бо мок-дані завжди були числами).

    Прибираємо ВСІ пробільні символи (включно з NBSP/вузьким NBSP, якими Prom
    групує тисячі, напр. "1 234,50 грн") ПЕРЕД пошуком числа — інакше
    "1 234 грн" мовчки парситься як 1.0 замість 1234.0 (регекс зупиняється на
    першому нецифровому символі, тобто на пробілі-розділювачі тисяч)."""
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = _PRICE_WHITESPACE_RE.sub("", str(raw or "0"))
    match = re.search(r"\d+(?:[.,]\d+)?", cleaned)
    return float(match.group().replace(",", ".")) if match else 0.0


def _parse_qty(raw) -> int:
    """ВИПРАВЛЕНО (2026-07-15, незалежне рев'ю pt19, критична знахідка):
    голий `int(purchase.get("quantity") or 1)` падав на форматах на
    кшталт "2 шт." (типова укр. e-commerce конвенція) — живо
    підтверджено крахом. Оскільки fetch_new_orders_rozetka()/
    fetch_new_orders_prom() будують ВЕСЬ список замовлень одним виразом
    (list comprehension) ДО того, як цикл вставки в БД навіть починається,
    ОДНЕ замовлення з таким форматом обвалювало б poll_once() ЦІЛКОМ —
    втрачаючи одночасно і Prom-, і Rozetka-замовлення того самого циклу,
    і повторюючи крах на КОЖНОМУ наступному 15-хвилинному циклі (реальний
    шлях запуску — systemd oneshot, без try/except навколо poll_once(),
    на відміну від run_forever()).

    Той самий патерн, що вже рятує парсинг ціни (_parse_prom_price()) —
    для чисел/float повертає як є (з fallback на 1, якщо 0/порожньо, той
    самий сенс, що й старий "or 1"); для рядків витягує перше число
    регексом, ігноруючи одиницю виміру ("шт.", "pcs" тощо)."""
    if isinstance(raw, (int, float)):
        qty = int(raw)
    else:
        match = re.search(r"\d+", str(raw or ""))
        qty = int(match.group()) if match else 0
    return qty or 1


# Машинний слаг перевізника з delivery_provider_data.provider -> наш carrier.
# "nova_poshta" підтверджено емпірично на реальному замовленні №414634349.
# "ukrposhta" — best-effort здогад за аналогією (той самий стиль слага, що
# й у Prom), ЩЕ НЕ підтверджено на реальному замовленні з доставкою
# Укрпоштою — звір і скоригуй за першим таким замовленням.
_CARRIER_PROVIDER_SLUGS = {
    "nova_poshta": "nova_poshta",
    "ukrposhta": "ukrposhta",
}


def _detect_carrier(delivery_provider_data: dict | None, delivery_option: dict | None) -> str:
    """Визначає перевізника (carrier) із замовлення Prom. Пріоритет —
    delivery_provider_data.provider (машинний слаг); якщо відсутній/незнайомий
    (напр. для перевізника, для якого Prom ще не заповнює це поле) — фолбек
    на людський текст delivery_option.name. Дефолт — nova_poshta (уся
    історія замовлень до 2026-07-10 — лише Нова Пошта)."""
    provider = ((delivery_provider_data or {}).get("provider") or "").strip().lower()
    if provider in _CARRIER_PROVIDER_SLUGS:
        return _CARRIER_PROVIDER_SLUGS[provider]

    option_name = ((delivery_option or {}).get("name") or "").strip().lower()
    if "укрпошт" in option_name:
        return "ukrposhta"
    if "нова пошт" in option_name or "нову пошту" in option_name:
        return "nova_poshta"

    return "nova_poshta"


def _convert_prom_order(order: dict) -> dict:
    """Приводить замовлення з реального Prom Orders API до сирої структури,
    яку очікує normalize_order() (той самий формат, що й мок-дані нижче)."""
    payment_name = ((order.get("payment_option") or {}).get("name") or "").lower()
    is_cod = any(kw in payment_name for kw in _COD_KEYWORDS)

    # ВИПРАВЛЕНО (2026-07-15, той самий інцидент №415858222): для онлайн-
    # оплати (Пром-оплата/evopay) Prom сам підтверджує факт оплати через
    # payment_data.status == "paid" — довіряємо цьому напряму, НЕ чекаємо
    # bank_check.py/виписку ПриватБанку для таких замовлень. Кошти за
    # Пром-оплату надходять на рахунок продавця лише через ~24 год ПІСЛЯ
    # отримання посилки клієнтом (задокументовано в плані проєкту) — банк-
    # звірка НІКОЛИ не встигне вчасно для цього способу оплати; замовлення
    # 415858222 простояло непереданим саме тому, доки не втрутились
    # вручну. payment_data відсутній (None) для накладеного платежу —
    # is_cod вже покриває цей шлях окремо, тут це не зачіпає.
    payment_data = order.get("payment_data") or {}
    payment_confirmed_by_prom = not is_cod and payment_data.get("status") == "paid"

    customer_name = " ".join(
        part for part in (order.get("client_first_name"), order.get("client_last_name")) if part
    )

    items = [
        {
            "toysi_code": product.get("sku") or product.get("external_id") or "",
            "name": product.get("name", ""),
            "qty": _parse_qty(product.get("quantity")),
            "price": _parse_prom_price(product.get("price")),
        }
        for product in order.get("products", [])
    ]

    return {
        "order_id": str(order["id"]),
        "platform": "prom",
        "status": order.get("status", "pending"),
        "payment_method": "cod" if is_cod else "prepaid",
        "payment_confirmed": payment_confirmed_by_prom,
        "customer_name": customer_name,
        "phone": order.get("phone", ""),
        "np_branch": order.get("delivery_address", ""),
        "carrier": _detect_carrier(order.get("delivery_provider_data"), order.get("delivery_option")),
        "items": items,
    }


class PromAPIError(Exception):
    """Запит до Prom Orders API (set_status) не вдався — мережа, невалідна
    відповідь, чи сам Prom повернув warning_message для частини замовлень."""


# "received" ("Принят") — Prom-статус, що відповідає "прийнято в обробку"
# (public-api.docs.prom.ua, OrderStatus.name enum: pending/received/
# delivered/canceled/draft/paid/custom-{id}). Підтверджено на реальному
# замовленні №414634349: саме цей статус Prom виставляє, коли продавець
# вручну натискає "Прийняти" в кабінеті — той самий сенс тут, лише
# автоматично, одразу після успішної передачі в Toysi.
PROM_ORDER_STATUS_ACCEPTED = "received"


def update_prom_order_status(order_id, status: str = PROM_ORDER_STATUS_ACCEPTED) -> None:
    """
    POST /orders/set_status (public-api.docs.prom.ua, розділ Orders) —
    оновлює статус замовлення на боці Prom, щоб клієнт бачив актуальний
    стан ("прийнято в обробку"), а не старий, одразу після успішної
    передачі в Toysi (order_router.py). Задача власниці 2026-07-15: клієнт
    бачив старий статус, хоча замовлення вже реально в обробці.

    Не підтверджено живим викликом на момент написання (лише читальні
    /orders/list виклики були перевірені раніше) — перший реальний виклик
    варто звірити з кабінетом Prom вручну.
    """
    if not PROM_API_KEY:
        raise PromAPIError("PROM_API_KEY не задано")

    try:
        response = requests.post(
            f"{PROM_API_URL}/orders/set_status",
            headers={"Authorization": f"Bearer {PROM_API_KEY}"},
            json={"status": status, "ids": [int(order_id)]},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise PromAPIError(f"Помилка з'єднання: {e}")

    try:
        data = response.json()
    except ValueError:
        raise PromAPIError(f"Невалідна відповідь (не JSON): {response.text[:300]}")

    warning = data.get("warning_message")
    if warning:
        raise PromAPIError(f"Prom попередив: {warning}")


def attach_prom_declaration_id(order_id, ttn: str, delivery_type: str = "nova_poshta") -> None:
    """
    POST /delivery/save_declaration_id (my.prom.ua/api/v1) — прикріплює
    номер ЕН до замовлення на СТОРОНІ Prom (заповнює
    delivery_provider_data.declaration_number, порожнє досі для КОЖНОГО
    замовлення за всю історію — жоден інший виклик у коді цього не робив,
    підтверджено 2026-07-17 живою перевіркою GET /orders/{id}).

    Це саме той виклик, що потрібен для програми "Дешева доставка" Новою
    Поштою — офіційна довідка Prom (support.prom.ua) вимагає з боку
    продавця РІВНО одне: "додайте ЕН не пізніше дня відправлення; без ЕН
    доставка лишається платною". Раніше ЕН не передавався в Prom ЖОДНОГО
    разу — тобто ця умова не виконувалась НІКОЛИ, незалежно від того, чи
    в клієнта була активна підписка ("Свій"/SMART). Підтверджено живим
    викликом на замовленні №415965259: `has_order_promo_free_delivery`
    лишився `false`, бо ЕН на той момент був зареєстрований заднім числом
    (за 2 дні по відправленню) — тест підтверджує МЕХАНІЗМ, не гарантує
    субсидію заднім числом на вже відправлені замовлення.

    Prom (перевірено живо) повертає 200 OK з `{"status": "error",
    "message": "Этот ЭН уже добавлен к данному заказу"}` при повторному
    виклику з тим самим ЕН — трактуємо як success (мета вже досягнута),
    щоб ідемпотентний повторний виклик (той самий підхід, що й в
    update_prom_order_status()) не заважав щоцикловому опитуванню.
    """
    if not PROM_API_KEY:
        raise PromAPIError("PROM_API_KEY не задано")

    try:
        response = requests.post(
            f"{PROM_API_URL}/delivery/save_declaration_id",
            headers={"Authorization": f"Bearer {PROM_API_KEY}"},
            json={"order_id": int(order_id), "delivery_type": delivery_type, "declaration_id": str(ttn)},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise PromAPIError(f"Помилка з'єднання: {e}")

    try:
        data = response.json()
    except ValueError:
        raise PromAPIError(f"Невалідна відповідь (не JSON): {response.text[:300]}")

    if data.get("status") == "error":
        message = data.get("message", "")
        if "уже добавлен" in message or "уже додан" in message:
            return
        raise PromAPIError(f"Prom відхилив ЕН: {message} ({data.get('errors')})")


def _rozetka_payment_method(order: dict) -> str:
    """Rozetka повертає назву способу оплати текстом (payment_type_name/
    payment_type) — той самий підхід евристики за ключовими словами, що й
    _COD_KEYWORDS для Prom вище, бо чистого enum тут так само немає."""
    name = str(order.get("payment_type_name") or order.get("payment_type") or "").lower()
    return "cod" if any(kw in name for kw in _COD_KEYWORDS) else "prepaid"


def _convert_rozetka_purchase(purchase: dict) -> dict:
    """
    Приводить одну позицію order.purchases[] до формату items у orders_db.

    ⚠️ Точні назви полів purchases[] НЕ підтверджені живим замовленням —
    apidoc Rozetka (api-seller.rozetka.com.ua/apidoc/) документує сам факт
    існування order.purchases як Object[], але не розписує конкретні поля
    моделі PurchaseDetails (порожній стаб у згенерованій документації).
    Назви нижче — best-effort здогад за аналогією з іншими місцями цього ж
    API (напр. order.items_photos використовує id/item_name/item_price) —
    перевір і скоригуй за першим реальним замовленням Rozetka, той самий
    підхід, що вже застосований для _detect_carrier() (ukrposhta-слаг) і
    RUS-варіанту каталогу Prom.
    """
    return {
        # ⭐ Звірено на живому замовленні 903643405 (2026-08-19): toysi_code = article /
        # price_offer_id / uploader_offer_id (= наш offer_id = Toysi id, напр. '16692'), а НЕ
        # item_id (то внутрішній Rozetka-id 611293757, у Toysi не існує → order_create впав би).
        "toysi_code": str(purchase.get("article") or purchase.get("price_offer_id")
                          or purchase.get("uploader_offer_id") or purchase.get("item_id") or "").strip(),
        "name": purchase.get("name") or purchase.get("item_name", ""),
        "qty": _parse_qty(purchase.get("quantity") or purchase.get("amount")),
        "price": _parse_prom_price(purchase.get("price") or purchase.get("item_price")),
    }


def _rozetka_delivery_address(order: dict) -> str:
    """⚠️ Так само не підтверджено живим замовленням — order.delivery є
    Object (DeliveryDetails), точні під-поля (місто/відділення) не розписані
    в apidoc. Фолбек на порожній рядок, якщо структура виявиться іншою —
    безпечніше порожня адреса (Toysi це прийме як вільний текст), ніж
    падіння всього опитування через один незнайомий формат."""
    d = order.get("delivery") or {}
    if not isinstance(d, dict):
        return ""
    city = d.get("city") if isinstance(d.get("city"), dict) else {}
    city_name = (city.get("name_ua") or city.get("name") or d.get("city_name") or "").strip()
    # RZ Delivery pickup (903643405): вулиця/будинок/номер пункту; НП-фолбек: warehouse_name.
    street = " ".join(p for p in (d.get("place_street"), d.get("place_house"),
                                  d.get("place_number")) if p).strip()
    warehouse = (d.get("warehouse_name") or d.get("warehouse") or "").strip()
    return ", ".join(p for p in (city_name, street or warehouse) if p)


def _rozetka_carrier(order: dict) -> str:
    """Спосіб доставки з delivery.delivery_service_name / name_logo. Звірено на 903643405:
    'ROZETKA Delivery' + name_logo='octopus' + is_action_np=False → 'rozetka_delivery'
    (самовивіз у магазин Rozetka). Інакше — 'nova_poshta' (безпечний дефолт, як було)."""
    d = order.get("delivery") or {}
    svc = str(d.get("delivery_service_name") or "").lower()
    if "rozetka" in svc or str(d.get("name_logo") or "").lower() == "octopus":
        return "rozetka_delivery"
    return "nova_poshta"


def _rozetka_customer_name(order: dict) -> str:
    """Ім'я отримувача. На 903643405 order.userName=None → беремо delivery.recipient_title,
    інакше склад recipient_*_name, інакше user.contact_fio."""
    d = order.get("delivery") or {}
    u = order.get("user") or {}
    return (d.get("recipient_title")
            or " ".join(p for p in (d.get("recipient_last_name"), d.get("recipient_first_name"),
                                    d.get("recipient_second_name")) if p).strip()
            or u.get("contact_fio") or order.get("userName") or "").strip()


def _convert_rozetka_order(order: dict) -> dict:
    """Rozetka Seller API (GET /orders/{id}?expand=delivery,user,purchases) → сира структура для
    normalize_order(). Поля ЗВІРЕНІ на живому замовленні 903643405 (2026-08-19), а не здогад по
    apidoc-стабах, як було раніше (див. rozetka.md — «сира структура замовлення»)."""
    purchases = order.get("purchases") or []
    delivery = order.get("delivery") or {}
    return {
        "order_id": str(order["id"]),
        "platform": "rozetka",
        "status": "new",
        "payment_method": _rozetka_payment_method(order),
        "payment_confirmed": False,
        "customer_name": _rozetka_customer_name(order),
        "phone": order.get("user_phone") or delivery.get("recipient_phone", ""),
        "np_branch": _rozetka_delivery_address(order),
        "carrier": _rozetka_carrier(order),
        "items": [_convert_rozetka_purchase(p) for p in purchases] or [
            {"toysi_code": "", "name": "⚠️ order.purchases порожній/незнайомого формату — перевір вручну", "qty": 1, "price": 0.0}
        ],
    }


def fetch_new_orders_rozetka() -> list:
    """
    Реальний виклик Rozetka Seller API (rozetka_client.py): GET /orders/search
    зі статусом 1 ("Нове замовлення"). Авторизація — АБО довгоживучий
    `ROZETKA_API_TOKEN` (пріоритет, `rozetka_client._get_token()`), АБО логін/
    пароль кабінету (`ROZETKA_USERNAME/ROZETKA_PASSWORD`, фолбек `_login()`).
    Мок-замовлення — ЛИШЕ коли НЕМАЄ ЖОДНОЇ авторизації (перевірка логіки без
    акаунту).

    ⚠️ ВИПРАВЛЕНО 2026-08-16: раніше гейт мокав за відсутності USERNAME/PASSWORD,
    ІГНОРУЮЧИ робочий ROZETKA_API_TOKEN → якщо на сервері лише токен (а він
    тепер працює, магазин "Активний"), пайплайн повертав МОК замість РЕАЛЬНИХ
    замовлень → перше справжнє замовлення клієнта мовчки не потрапляло в Toysi.
    Тепер узгоджено з rozetka_client: токен АБО логін/пароль → реальний API.
    """
    if not rozetka_client.ROZETKA_API_TOKEN and not (ROZETKA_USERNAME and ROZETKA_PASSWORD):
        print("[Rozetka] Нема ні ROZETKA_API_TOKEN, ні USERNAME/PASSWORD — мок-замовлення (перевірка логіки)")
        return _mock_rozetka_orders()

    try:
        raw_orders = rozetka_client.fetch_new_orders()
    except rozetka_client.RozetkaAPIError as e:
        print(f"[Rozetka] {e}", file=sys.stderr)
        return []

    return [_convert_rozetka_order(o) for o in raw_orders]


# ── EVA (merchant-api.eva.ua) ────────────────────────────────────────────────
# Способи оплати EVA (payment.method, OrderBase зі схеми /api/schema): рівно два —
# CASH_ON_DELIVERY (накладений) і LIQPAY (онлайн). Все, що не LIQPAY-authorized —
# не вважаємо оплаченим (той самий безпечний дефолт, що й для Prom/Rozetka).
EVA_PAYMENT_COD = "CASH_ON_DELIVERY"
EVA_PAYMENT_LIQPAY = "LIQPAY"


def _eva_payment_method(order: dict) -> str:
    method = str((order.get("payment") or {}).get("method") or "").upper()
    return "cod" if method == EVA_PAYMENT_COD else "prepaid"


def _eva_carrier(order: dict) -> str:
    """shipping.method (enum зі схеми): novaposhta_warehouse/novaposhta_packstation/
    ukrposhta_postoffice. Мапимо на слаги order_router (nova_poshta/ukrposhta)."""
    method = str((order.get("shipping") or {}).get("method") or "").lower()
    return "ukrposhta" if method.startswith("ukrposhta") else "nova_poshta"


def _eva_delivery_address(order: dict) -> str:
    """shipping.address — структуру звірено на ПЕРШОМУ реальному замовленні
    (2026-08-01, novaposhta_warehouse): {city, region, street:{name}, city_id,
    region_id, warehouse_id, settlement_type, warehouse_number,
    settlement_description}. Людське — `city` + `street.name` (напр. "Київ,
    Відділення №253 (до 30 кг) : вул. Данила Щербаківського, 59"); решта *_id —
    технічні UUID-рефи Нової Пошти, у людську адресу НЕ йдуть (перша версія
    зліплювала все підряд і давала кашу з UUID). Фолбек, якщо street порожній —
    "№{warehouse_number}"; далі — рядкова address чи метод доставки."""
    shipping = order.get("shipping") or {}
    address = shipping.get("address")
    if isinstance(address, dict):
        city = str(address.get("city") or "").strip()
        street = address.get("street")
        if isinstance(street, dict):
            street = str(street.get("name") or "").strip()
        else:
            street = str(street or "").strip()
        if not street and address.get("warehouse_number") is not None:
            street = f"№{address.get('warehouse_number')}"
        parts = [p for p in (city, street) if p]
        if parts:
            return ", ".join(parts)
    if isinstance(address, str) and address.strip():
        return address.strip()
    return str(shipping.get("method") or "")


def _eva_customer_name(order: dict) -> str:
    """Отримувач (recipient) у пріоритеті — саме він отримує посилку; фолбек —
    customer (замовник). Формат ПІБ (Прізвище Ім'я По-батькові) — стандарт
    контакту Нової Пошти; middle_name (по батькові) ВКЛЮЧАЄМО (звірено на
    першому реальному замовленні 2026-08-01 — recipient мав middle_name
    'Юрійович', а перша версія його ігнорувала)."""
    for key in ("recipient", "customer"):
        person = order.get(key) or {}
        name = " ".join(
            p for p in (person.get("last_name"), person.get("first_name"), person.get("middle_name")) if p
        )
        if name.strip():
            return name.strip()
    return ""


def _convert_eva_order(order: dict) -> dict:
    """OrderExtended EVA (get_order) -> сира структура для normalize_order().

    ⚠️ Мапінг address (динамічний) і toysi_code (article vs sku) — best-effort за
    OpenAPI-схемою (merchant-api.eva.ua/api/schema, v1.1.0); 0 живих замовлень на
    момент написання. Звірити на ПЕРШОМУ реальному замовленні (той самий підхід,
    що вже застосований для _convert_rozetka_order і RUS-каталогу Prom)."""
    payment = order.get("payment") or {}
    payment_confirmed = (
        str(payment.get("method") or "").upper() == EVA_PAYMENT_LIQPAY
        and str(payment.get("status") or "").lower() == "authorized"
    )
    items = [
        {
            # toysi_code = item["id"] — це offer id, що МИ віддали у фіді
            # (<offer id=...>), тобто код товару Toysi. Звірено на 1-му живому
            # замовленні (2026-08-01): item.id="160515" (наш offer id, 6 цифр,
            # резолвиться в реальний залишок Toysi), тоді як item.sku="2289628" —
            # ВНУТРІШНІЙ ID EVA (7 цифр, не Toysi-код), а item.article=null.
            # ПОПЕРЕДНЯ версія (#205) брала article/sku → шукала залишок за EVA-ID
            # → Toysi повертав "0" → КОЖНЕ EVA-замовлення хибно зависало як OOS і
            # ніколи не форвардилось (+ ризик 72-год штрафу EVA). НЕ падаємо на
            # sku/article — вони EVA-ідентифікатори, не Toysi-коди; порожній id
            # (не має траплятись) → "" (order_router позначить на ручну перевірку).
            "toysi_code": str(product.get("id") or "").strip(),
            "name": product.get("name", ""),
            "qty": _parse_qty(product.get("quantity")),
            "price": _parse_prom_price(product.get("price")),
        }
        for product in (order.get("items") or [])
    ]
    return {
        "order_id": str(order["id"]),
        "platform": "eva",
        "status": "new",
        "payment_method": _eva_payment_method(order),
        "payment_confirmed": payment_confirmed,
        "customer_name": _eva_customer_name(order),
        "phone": (order.get("recipient") or {}).get("phone") or (order.get("customer") or {}).get("phone") or "",
        "np_branch": _eva_delivery_address(order),
        "carrier": _eva_carrier(order),
        "items": items or [
            {"toysi_code": "", "name": "⚠️ items EVA порожній/незнайомого формату — перевір вручну", "qty": 1, "price": 0.0}
        ],
    }


def fetch_new_orders_eva() -> list:
    """EVA Merchant Center API (eva_orders_client.py): нові замовлення (status=1).
    fetch_orders повертає короткі OrderBase (БЕЗ items) — по кожному тягнемо повні
    деталі get_order() (OrderExtended з items/shipping/recipient). Без кредів
    (EVA_MERCHANT_USERNAME/PASSWORD) — мок, як у Prom/Rozetka."""
    if not EVA_MERCHANT_USERNAME or not EVA_MERCHANT_PASSWORD:
        print("[EVA] EVA_MERCHANT_USERNAME/PASSWORD не задано — мок-замовлення для перевірки логіки")
        return _mock_eva_orders()

    try:
        base_orders = eva_orders_client.fetch_orders(status=eva_orders_client.EVA_STATUS_NEW)
    except eva_orders_client.EvaAPIError as e:
        print(f"[EVA] {e}", file=sys.stderr)
        return []

    result = []
    for base in base_orders:
        oid = base.get("id")
        try:
            full = eva_orders_client.get_order(oid)
        except eva_orders_client.EvaAPIError as e:
            print(f"[EVA] не вдалось отримати деталі замовлення {oid}: {e}", file=sys.stderr)
            continue
        result.append(_convert_eva_order(full or base))
    return result


def _mock_eva_orders() -> list:
    return [
        {
            "order_id": "EVA-700001",
            "platform": "eva",
            "status": "new",
            "payment_method": "cod",
            "payment_confirmed": False,
            "customer_name": "Тестовий EVA Клієнт",
            "phone": "380631234567",
            "np_branch": "Київ, відділення №5",
            "carrier": "nova_poshta",
            "items": [
                {"toysi_code": "298094", "name": "Антистрес ROBLOX 3D друк", "qty": 1, "price": 183.64},
            ],
        },
    ]


def _mock_prom_orders() -> list:
    return [
        {
            "order_id": "PROM-100234",
            "platform": "prom",
            "status": "new",
            "payment_method": "cod",            # накладений платіж -> передаємо Toysi одразу (Крок 5, п.1)
            "payment_confirmed": False,
            "customer_name": "Тестовий Клієнт",
            "phone": "380501234567",
            "np_branch": "Київ, відділення №15",
            "carrier": "nova_poshta",
            "items": [
                {"toysi_code": "11623", "name": "Конструктор LEGO City", "qty": 1, "price": 450.0},
            ],
        },
        {
            "order_id": "PROM-100235",
            "platform": "prom",
            "status": "new",
            "payment_method": "cod",
            "payment_confirmed": False,
            "customer_name": "Тестовий Укрпошта Клієнт",
            "phone": "380671234567",
            "np_branch": "м. Львів, вул. Городоцька, 1",
            "carrier": "ukrposhta",              # для перевірки маршруту order_router.py (Крок Х плану)
            "items": [
                {"toysi_code": "11638", "name": "Пазл 500 елементів", "qty": 1, "price": 220.0},
            ],
        },
    ]


def _mock_rozetka_orders() -> list:
    return [
        {
            "order_id": "RZ-998877",
            "platform": "rozetka",
            "status": "new",
            "payment_method": "prepaid",        # передоплата -> чекає bank_check.py (Крок 5, п.2)
            "payment_confirmed": False,
            "customer_name": "Другий Клієнт",
            "phone": "380671112233",
            "np_branch": "Львів, відділення №3",
            "items": [
                {"toysi_code": "11638", "name": "Пазл 500 елементів", "qty": 2, "price": 220.0},
            ],
        },
    ]


def normalize_order(raw_order: dict) -> dict:
    """Приводить сирі дані з API платформи (або мок-дані) до єдиної структури orders.db."""
    return {
        "order_id":          raw_order["order_id"],
        "platform":          raw_order["platform"],
        "status":            raw_order.get("status", "new"),
        "payment_method":    raw_order["payment_method"],
        "payment_confirmed": raw_order.get("payment_confirmed", False),
        "customer_name":     raw_order.get("customer_name", ""),
        "phone":             raw_order.get("phone", ""),
        "np_branch":         raw_order.get("np_branch", ""),
        "carrier":           raw_order.get("carrier", "nova_poshta"),
        "items":             raw_order["items"],
    }


def poll_once() -> None:
    init_db()
    raw_orders = fetch_new_orders_prom() + fetch_new_orders_rozetka() + fetch_new_orders_eva()

    with get_connection() as conn:
        for raw in raw_orders:
            order = normalize_order(raw)
            internal_id = f"{order['platform']}_{order['order_id']}"
            if insert_order(conn, order):
                print(f"[orders_watcher] Нове замовлення збережено: {internal_id}")
                continue

            # ВИПРАВЛЕНО (2026-07-15): insert_order() НЕ оновлює вже наявний
            # рядок — якщо замовлення потрапило в БД РАНІШЕ, ще до
            # підтвердження оплати (напр. зловлене рівно в момент, коли
            # Prom ще показував "pending"), а цей свіжий запит тепер
            # показує payment_confirmed=True (Прom payment_data.status ==
            # "paid"), без цієї перевірки воно лишилось би непідтвердженим
            # НАЗАВЖДИ — bank_check.py теж його не знайде (кошти за
            # Пром-оплату надходять на рахунок продавця з затримкою ~24
            # год після отримання посилки клієнтом).
            # Prom (payment_data.status=="paid") і EVA (LIQPAY authorized) обидва
            # можуть перейти pending->оплачено швидше за цикл опитування — та сама
            # логіка до-підтвердження вже наявного в БД запису.
            if order["platform"] in ("prom", "eva") and order.get("payment_confirmed"):
                existing = conn.execute(
                    "SELECT payment_confirmed FROM orders WHERE internal_order_id = ?", (internal_id,)
                ).fetchone()
                if existing and not existing["payment_confirmed"]:
                    mark_payment_confirmed(conn, internal_id)
                    print(f"[orders_watcher] Оплату підтверджено ({order['platform']}): {internal_id}")
                    continue

            print(f"[orders_watcher] Пропущено (вже є в БД): {internal_id}")


def run_forever() -> None:
    print(f"[orders_watcher] Старт опитування кожні {POLL_INTERVAL_SECONDS // 60} хв")
    while True:
        try:
            poll_once()
        except Exception as e:
            print(f"[orders_watcher] Помилка циклу опитування: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    poll_once()
