import os
import sys
import time

from dotenv import load_dotenv

from orders_db import get_connection, init_db, insert_order

load_dotenv()

PROM_API_KEY    = os.environ.get("PROM_API_KEY", "")
ROZETKA_API_KEY = os.environ.get("ROZETKA_API_KEY", "")

POLL_INTERVAL_SECONDS = 15 * 60  # 10-15 хв за планом (Крок 3)


def fetch_new_orders_prom() -> list:
    """
    TODO: реальний виклик Prom Orders API (статус "Нове"/"Очікує підтвердження"),
    авторизація через PROM_API_KEY.
    Поки ключа немає (магазин ще на модерації) — повертає мок-замовлення,
    щоб можна було перевірити логіку router/orders.db без реального акаунту.
    """
    if not PROM_API_KEY:
        print("[Prom] PROM_API_KEY не задано — використовую мок-замовлення для перевірки логіки")
        return _mock_prom_orders()

    raise NotImplementedError("Підключити реальний Prom Orders API, коли з'явиться PROM_API_KEY")


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
