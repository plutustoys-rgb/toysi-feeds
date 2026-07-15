import os
import re
import sys
import time
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

from orders_db import get_connection, init_db, insert_order, mark_payment_confirmed
import rozetka_client

load_dotenv()

PROM_API_KEY     = os.environ.get("PROM_API_KEY", "")
ROZETKA_USERNAME = os.environ.get("ROZETKA_USERNAME", "")
ROZETKA_PASSWORD = os.environ.get("ROZETKA_PASSWORD", "")

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
            "qty": int(product.get("quantity") or 1),
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
        "toysi_code": purchase.get("item_id") or purchase.get("id") or "",
        "name": purchase.get("item_name") or purchase.get("name", ""),
        "qty": int(purchase.get("quantity") or purchase.get("amount") or 1),
        "price": _parse_prom_price(purchase.get("price") or purchase.get("item_price")),
    }


def _rozetka_delivery_address(order: dict) -> str:
    """⚠️ Так само не підтверджено живим замовленням — order.delivery є
    Object (DeliveryDetails), точні під-поля (місто/відділення) не розписані
    в apidoc. Фолбек на порожній рядок, якщо структура виявиться іншою —
    безпечніше порожня адреса (Toysi це прийме як вільний текст), ніж
    падіння всього опитування через один незнайомий формат."""
    delivery = order.get("delivery") or {}
    if isinstance(delivery, dict):
        parts = [
            delivery.get("city_name") or delivery.get("city") or "",
            delivery.get("warehouse_name") or delivery.get("warehouse") or "",
        ]
        return ", ".join(p for p in parts if p)
    return ""


def _convert_rozetka_order(order: dict) -> dict:
    """Приводить замовлення з реального Rozetka Seller API (GET /orders/search)
    до сирої структури, яку очікує normalize_order()."""
    purchases = order.get("purchases") or []
    return {
        "order_id": str(order["id"]),
        "platform": "rozetka",
        "status": "new",
        "payment_method": _rozetka_payment_method(order),
        "payment_confirmed": False,
        "customer_name": order.get("userName") or (order.get("user") or {}).get("full_name", ""),
        "phone": order.get("user_phone", ""),
        "np_branch": _rozetka_delivery_address(order),
        "carrier": "nova_poshta",  # Rozetka API окремо не документує carrier-слаг у search-відповіді
        "items": [_convert_rozetka_purchase(p) for p in purchases] or [
            {"toysi_code": "", "name": "⚠️ order.purchases порожній/незнайомого формату — перевір вручну", "qty": 1, "price": 0.0}
        ],
    }


def fetch_new_orders_rozetka() -> list:
    """
    Реальний виклик Rozetka Seller API (rozetka_client.py): GET /orders/search
    зі статусом 1 ("Нове замовлення"). Авторизація — логін/пароль кабінету
    продавця (ROZETKA_USERNAME/ROZETKA_PASSWORD), не окремий API-ключ, як у
    Prom — див. docstring rozetka_client._login(). Поки облікових даних
    немає — мок-замовлення, щоб перевіряти логіку router/orders.db без акаунту.
    """
    if not ROZETKA_USERNAME or not ROZETKA_PASSWORD:
        print("[Rozetka] ROZETKA_USERNAME/ROZETKA_PASSWORD не задано — використовую мок-замовлення для перевірки логіки")
        return _mock_rozetka_orders()

    try:
        raw_orders = rozetka_client.fetch_new_orders()
    except rozetka_client.RozetkaAPIError as e:
        print(f"[Rozetka] {e}", file=sys.stderr)
        return []

    return [_convert_rozetka_order(o) for o in raw_orders]


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
    raw_orders = fetch_new_orders_prom() + fetch_new_orders_rozetka()

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
            if order["platform"] == "prom" and order.get("payment_confirmed"):
                existing = conn.execute(
                    "SELECT payment_confirmed FROM orders WHERE internal_order_id = ?", (internal_id,)
                ).fetchone()
                if existing and not existing["payment_confirmed"]:
                    mark_payment_confirmed(conn, internal_id)
                    print(f"[orders_watcher] Оплату підтверджено (Prom payment_data): {internal_id}")
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
