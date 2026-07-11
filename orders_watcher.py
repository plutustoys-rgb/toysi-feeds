import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

from orders_db import get_connection, init_db, insert_order

load_dotenv()

PROM_API_KEY    = os.environ.get("PROM_API_KEY", "")
ROZETKA_API_KEY = os.environ.get("ROZETKA_API_KEY", "")

PROM_API_URL    = "https://my.prom.ua/api/v1"
REQUEST_TIMEOUT = 30

POLL_INTERVAL_SECONDS = 15 * 60  # 10-15 хв за планом (Крок 3)

# Ключові слова, за якими розпізнаємо накладений платіж у вільному тексті
# payment_option.name (Prom Orders API не дає чистого enum для способу оплати).
# Все, що НЕ підпадає під ці слова, вважаємо передоплатою (безпечніший дефолт:
# помилково зачекати підтвердження оплати краще, ніж помилково відправити
# товар без реальної оплати).
_COD_KEYWORDS = ("наклад", "післяплат", "отриманні", "готівк", "наложен")


def fetch_new_orders_prom() -> list:
    """
    Реальний виклик Prom Orders API (https://public-api.docs.prom.ua/, GET /orders/list,
    Authorization: Bearer PROM_API_KEY). Фільтр status=pending — це статус Prom для
    щойно створеного замовлення, яке ще не оброблене продавцем ("Нове").
    Поки ключа немає — мок-замовлення, щоб перевіряти логіку router/orders.db без акаунту.
    """
    if not PROM_API_KEY:
        print("[Prom] PROM_API_KEY не задано — використовую мок-замовлення для перевірки логіки")
        return _mock_prom_orders()

    try:
        response = requests.get(
            f"{PROM_API_URL}/orders/list",
            headers={"Authorization": f"Bearer {PROM_API_KEY}"},
            params={"status": "pending", "limit": 100},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[Prom] Помилка з'єднання: {e}", file=sys.stderr)
        return []

    try:
        data = response.json()
    except ValueError:
        print(f"[Prom] Невалідна відповідь (не JSON): {response.text[:300]}", file=sys.stderr)
        return []

    return [_convert_prom_order(o) for o in data.get("orders", [])]


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
        "payment_confirmed": False,
        "customer_name": customer_name,
        "phone": order.get("phone", ""),
        "np_branch": order.get("delivery_address", ""),
        "carrier": _detect_carrier(order.get("delivery_provider_data"), order.get("delivery_option")),
        "items": items,
        # "portal"/"company_site"/"company_cabinet" (документація Prom Orders API,
        # Order.yaml) — company_site = кошик власного сайту, де діє комісія 10₴
        # за замовлення (pt24). Дефолт "portal", якщо Prom колись поверне
        # замовлення без цього поля — безпечніший фолбек (каталог, комісія
        # 10₴ не рахується), ніж мовчки вважати сайтом.
        "source": order.get("source") or "portal",
        "payment_option_name": (order.get("payment_option") or {}).get("name") or "",
    }


def fetch_new_orders_rozetka() -> list:
    """
    TODO: реальний виклик Rozetka Seller API, авторизація через ROZETKA_API_KEY.
    Поки ключа немає — мок-замовлення для перевірки логіки.
    """
    if not ROZETKA_API_KEY:
        print("[Rozetka] ROZETKA_API_KEY не задано — використовую мок-замовлення для перевірки логіки")
        return _mock_rozetka_orders()

    raise NotImplementedError("Підключити реальний Rozetka Seller API, коли з'явиться ROZETKA_API_KEY")


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
        "order_id":            raw_order["order_id"],
        "platform":            raw_order["platform"],
        "status":              raw_order.get("status", "new"),
        "payment_method":      raw_order["payment_method"],
        "payment_confirmed":   raw_order.get("payment_confirmed", False),
        "customer_name":       raw_order.get("customer_name", ""),
        "phone":               raw_order.get("phone", ""),
        "np_branch":           raw_order.get("np_branch", ""),
        "carrier":             raw_order.get("carrier", "nova_poshta"),
        "items":               raw_order["items"],
        # Rozetka (мок і майбутній реальний Seller API) не має поняття "джерело
        # каталог/сайт" у тому ж сенсі, що Prom — None, а не вигаданий дефолт.
        "source":              raw_order.get("source"),
        "payment_option_name": raw_order.get("payment_option_name", ""),
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
            else:
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
