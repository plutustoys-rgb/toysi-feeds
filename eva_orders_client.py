"""
eva_orders_client.py — клієнт EVA Merchant Center API (замовлення).

EVA має повноцінний REST API замовлень (підтверджено 2026-07-31 support-ботом
@EVA_UA_Marketplace_Help_Bot). Документація — НЕ в довідковому центрі продавця
(sellersupport.eva.ua, лише операційні статті), а на окремому сайті розробника:
https://merchant-api.eva.ua/api/docs (Swagger, OpenAPI 3.0, v1.1.0). Сира схема —
/api/schema.

Цей модуль — ТІЛЬКИ клієнт (token-auth + get/patch замовлень), дзеркало
rozetka_client.py за структурою. Wiring у пайплайн (orders_watcher /
order_router / order_status_tracker / orders.db міграція) — окремими кроками.

Автентифікація — JWT:
  POST /api/v1/token          {username, password} -> {access, refresh}
  POST /api/v1/token/refresh  {refresh}            -> {access}
  У запитах: заголовок Authorization: Bearer <access>.
EVA_MERCHANT_USERNAME/EVA_MERCHANT_PASSWORD — логін/пароль мерчанта EVA у .env
(секрет того ж рівня, що ROZETKA_USERNAME/PASSWORD — буквально доступ до кабінету).
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

EVA_MERCHANT_USERNAME = os.environ.get("EVA_MERCHANT_USERNAME", "")
EVA_MERCHANT_PASSWORD = os.environ.get("EVA_MERCHANT_PASSWORD", "")

EVA_API_URL = "https://merchant-api.eva.ua/api/v1"
REQUEST_TIMEOUT = 30

# Статуси замовлення EVA (OrderStatusPatchStatusEnum + фільтр кабінету).
# Дозволені переходи (зі схеми): 1->[9,10,11]; 11->[12,9,10]; 12->[9,10,5].
EVA_STATUS_NEW = 1                  # Нове
EVA_STATUS_SHIPPED = 5              # Відправлено (потребує tracking_number)
EVA_STATUS_RECEIVED = 7            # Отримано (кінцевий, лише у фільтрі)
EVA_STATUS_CANCELLED_BY_BUYER = 9  # Скасовано покупцем (потребує reason_id 1000-1015)
EVA_STATUS_CANCELLED_BY_SELLER = 10  # Скасовано продавцем (потребує reason_id 2000-2004)
EVA_STATUS_CONFIRMED_BY_SELLER = 11  # Підтверджене продавцем
EVA_STATUS_CONFIRMED_BY_BUYER = 12   # Підтверджене покупцем

# Reason-коди продавця (ReasonIdEnum, 2000-2004) — для скасувань нашого боку.
EVA_REASON_OUT_OF_STOCK = 2000     # Немає в наявності
EVA_REASON_NO_CONTACT = 2001       # Не додзвонився до клієнта
EVA_REASON_PRICE_CHANGED = 2002    # Змінилась ціна
EVA_REASON_WRONG_SPECS = 2003      # Невірні характеристики на сайті
EVA_REASON_DEFECTIVE = 2004        # Бракований товар

EVA_STATUS_FILTER_VALUES = {1, 9, 10, 11, 12}  # значення, які приймає ?status= у GET /orders


class EvaAPIError(Exception):
    """Запит до EVA Merchant Center API не вдався (мережа, невалідна відповідь,
    HTTP 4xx/5xx) — включно з помилками авторизації."""


# Кеш токенів лише в межах ОДНОГО запуску процесу (не в файл/БД) — той самий
# принцип, що й _cached_token у rozetka_client.py: простіше й безпечніше, ніж
# персистити JWT на диску, і уникає класу багів "протух кеш між прогонами".
_tokens = {"access": None, "refresh": None}


def _login() -> None:
    """POST /token — логін/пароль мерчанта -> {access, refresh}. Кешує обидва."""
    if not EVA_MERCHANT_USERNAME or not EVA_MERCHANT_PASSWORD:
        raise RuntimeError("EVA_MERCHANT_USERNAME/EVA_MERCHANT_PASSWORD не задані в .env")
    try:
        response = requests.post(
            f"{EVA_API_URL}/token",
            json={"username": EVA_MERCHANT_USERNAME, "password": EVA_MERCHANT_PASSWORD},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise EvaAPIError(f"Помилка авторизації EVA: {e}")
    try:
        data = response.json()
    except ValueError:
        raise EvaAPIError(f"Невалідна відповідь авторизації EVA (не JSON): {response.text[:300]}")
    access = data.get("access")
    if not access:
        raise EvaAPIError("Відповідь авторизації EVA без access-токена")
    _tokens["access"] = access
    _tokens["refresh"] = data.get("refresh")


def _refresh_access() -> bool:
    """POST /token/refresh — оновити access за наявним refresh. Повертає True при
    успіху, False — якщо refresh відсутній/недійсний (треба повний _login())."""
    refresh = _tokens.get("refresh")
    if not refresh:
        return False
    try:
        response = requests.post(
            f"{EVA_API_URL}/token/refresh",
            json={"refresh": refresh},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            return False
        access = response.json().get("access")
    except (requests.exceptions.RequestException, ValueError):
        return False
    if not access:
        return False
    _tokens["access"] = access
    return True


def _get_access(force_new: bool = False) -> str:
    """Повертає дійсний access-токен. force_new — спершу пробує refresh, якщо не
    вдалось — повний логін (використовується обробником 401 у _request())."""
    if force_new:
        if not _refresh_access():
            _login()
    elif not _tokens.get("access"):
        _login()
    return _tokens["access"]


def _request(method: str, path: str, **kwargs) -> requests.Response:
    """Bearer-запит до EVA API. При 401 (протух access) — одна спроба
    refresh/relogin і повтор, той самий підхід, що й у rozetka_client._request()."""
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {_get_access()}"

    def _do_request():
        return requests.request(
            method, f"{EVA_API_URL}{path}", headers=headers, timeout=REQUEST_TIMEOUT, **kwargs
        )

    try:
        response = _do_request()
    except requests.exceptions.RequestException as e:
        raise EvaAPIError(f"Помилка з'єднання ({method} {path}): {e}")

    if response.status_code == 401:
        headers["Authorization"] = f"Bearer {_get_access(force_new=True)}"
        try:
            response = _do_request()
        except requests.exceptions.RequestException as e:
            raise EvaAPIError(f"Помилка з'єднання після релогіну ({method} {path}): {e}")

    if response.status_code >= 400:
        raise EvaAPIError(f"{method} {path}: HTTP {response.status_code} — {response.text[:300]}")
    return response


def _request_json(method: str, path: str, **kwargs) -> dict:
    response = _request(method, path, **kwargs)
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        raise EvaAPIError(f"Невалідна відповідь (не JSON, {method} {path}): {response.text[:300]}")


def fetch_orders(status: int = EVA_STATUS_NEW, date_from: str = None,
                 updated_from: str = None, page_limit: int = 100) -> list:
    """GET /orders — усі замовлення заданого статусу (за замовчуванням нові = 1),
    з наскрізною пагінацією (offset/limit, limit до 100). Повертає список коротких
    записів OrderBase; повні деталі (позиції/доставка) — окремо через get_order().

    status має бути одним зі EVA_STATUS_FILTER_VALUES (API приймає лише 1/9/10/11/12).
    date_from/updated_from — ISO 8601 (напр. "2026-07-31T00:00:00Z")."""
    if status not in EVA_STATUS_FILTER_VALUES:
        raise ValueError(
            f"EVA ?status= приймає лише {sorted(EVA_STATUS_FILTER_VALUES)}, отримано {status}"
        )
    limit = max(20, min(100, page_limit))  # API: min=20, max=100
    orders: list = []
    offset = 0
    while True:
        params = {"status": status, "limit": limit, "offset": offset}
        if date_from:
            params["date_from"] = date_from
        if updated_from:
            params["updated_from"] = updated_from
        data = _request_json("GET", "/orders", params=params)
        results = data.get("results") or []
        orders.extend(results)
        # Зупиняємось, коли API більше не дає "next" або сторінка порожня (захист від
        # нескінченного циклу, якщо next віддається помилково).
        if not data.get("next") or not results:
            break
        offset += limit
    return orders


def get_order(order_id) -> dict:
    """GET /orders/{id} — повні деталі замовлення (OrderExtended: customer,
    recipient, shipping, items[], payment, tracking_number, ...)."""
    return _request_json("GET", f"/orders/{order_id}")


def update_order_status(order_id, status: int, tracking_number: str = None,
                        reason_id: int = None) -> dict:
    """PATCH /orders/{id}/status — зміна статусу замовлення на боці EVA.

    reason_id — ОБОВ'ЯЗКОВИЙ для скасувань (status 9 покупцем / 10 продавцем).
    tracking_number — ОБОВ'ЯЗКОВИЙ для status 5 (Відправлено).
    Валідність переходу (1->11->12->5 тощо) перевіряє сам API; тут лише
    формуємо тіло запиту."""
    body: dict = {"status": status}
    if tracking_number is not None:
        body["tracking_number"] = tracking_number
    if reason_id is not None:
        body["reason_id"] = reason_id
    return _request_json("PATCH", f"/orders/{order_id}/status", json=body)
