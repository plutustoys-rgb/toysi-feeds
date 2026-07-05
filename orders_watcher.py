import os
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
            "price": float(product.get("price") or 0),
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
        "items": items,
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
            "items": [
                {"toysi_code": "11623", "name": "Конструктор LEGO City", "qty": 1, "price": 450.0},
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
