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

# Статуси замовлення EVA. PATCH-абельні мерчантом (OrderStatusPatchStatusEnum зі
# схеми /api/schema) — РІВНО {1,9,10,11,12}. Потік (звірено зі схемою + статтею
# obrobka-zamovlen): 1 -> 11 (підтв. продавцем) -> 12 (підтв. покупцем, ОБОВ'ЯЗКОВО
# з tracking_number) -> EVA САМА виставляє 5 (Відправлено) з трекінгу перевізника.
EVA_STATUS_NEW = 1                    # Нове
EVA_STATUS_CONFIRMED_BY_SELLER = 11   # Підтверджене продавцем
EVA_STATUS_CONFIRMED_BY_BUYER = 12    # Підтверджене покупцем (ТУТ прикріплюється ТТН)
EVA_STATUS_CANCELLED_BY_BUYER = 9     # Скасовано покупцем (потребує reason_id 1000-1015)
EVA_STATUS_CANCELLED_BY_SELLER = 10   # Скасовано продавцем (потребує reason_id 2000-2004)
# READ-ONLY статуси кабінету — їх виставляє САМА EVA, PATCH-ити НЕ можна (немає в
# OrderStatusPatchStatusEnum → API відхилить):
EVA_STATUS_SHIPPED = 5    # Відправлено — EVA виставляє автоматично з трекінгу перевізника
                          # після прикріплення ТТН на статусі 12 (НЕ merchant-PATCH).
EVA_STATUS_RECEIVED = 7   # Отримано — кінцевий, read-only.

# Значення, які приймає PATCH /orders/{id}/status (OrderStatusPatchStatusEnum).
EVA_STATUS_PATCHABLE = {1, 9, 10, 11, 12}

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
        env = response.json()
    except ValueError:
        raise EvaAPIError(f"Невалідна відповідь авторизації EVA (не JSON): {response.text[:300]}")
    if not env.get("success", True):
        raise EvaAPIError(f"Авторизація EVA не вдалась: {env.get('message') or 'success=false'}")
    data = env.get("data") or {}
    access = data.get("access")
    if not access:
        raise EvaAPIError("Відповідь авторизації EVA без data.access")
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
        access = (response.json().get("data") or {}).get("access")
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


def _request_env(method: str, path: str, **kwargs) -> dict:
    """Bearer-запит + розгортання КОНВЕРТА EVA. EVA обгортає ВСІ відповіді
    (перевірено живо 2026-07-31): `{success, data, message, pagination}`. Корисне
    навантаження — у `data`; метадані сторінок — у `pagination` ({count, page,
    page_size}); текст помилки — у `message` при `success:false`. Повертає ВЕСЬ
    конверт (виклик сам бере `data`/`pagination`). Кидає EvaAPIError на не-JSON або
    success:false. Порожнє тіло (204) -> конверт-заглушка з data=None."""
    response = _request(method, path, **kwargs)
    if not response.content:
        return {"success": True, "data": None, "message": "", "pagination": None}
    try:
        env = response.json()
    except ValueError:
        raise EvaAPIError(f"Невалідна відповідь (не JSON, {method} {path}): {response.text[:300]}")
    if isinstance(env, dict) and not env.get("success", True):
        raise EvaAPIError(f"{method} {path}: {env.get('message') or 'success=false'}")
    return env


def fetch_orders(status: int = EVA_STATUS_NEW, date_from: str = None,
                 updated_from: str = None, page_limit: int = 100) -> list:
    """GET /orders — усі замовлення заданого статусу (за замовчуванням нові = 1),
    з наскрізною СТОРІНКОВОЮ пагінацією (page/page_size, page_size 20-100 — реальний
    контракт конверта; OpenAPI-схема декларує limit/offset, розбіжність під TODO нижче).
    Повертає список коротких OrderBase; повні деталі — окремо через get_order().

    status має бути одним зі EVA_STATUS_FILTER_VALUES (API приймає лише 1/9/10/11/12).
    date_from/updated_from — ISO 8601 (напр. "2026-07-31T00:00:00Z")."""
    if status not in EVA_STATUS_FILTER_VALUES:
        raise ValueError(
            f"EVA ?status= приймає лише {sorted(EVA_STATUS_FILTER_VALUES)}, отримано {status}"
        )
    page_size = max(20, min(100, page_limit))  # API: min=20, max=100
    orders: list = []
    page = 1
    max_pages = 1000  # запобіжник від нескінченного циклу
    while page <= max_pages:
        params = {"status": status, "page": page, "page_size": page_size}
        if date_from:
            params["date_from"] = date_from
        if updated_from:
            params["updated_from"] = updated_from
        env = _request_env("GET", "/orders", params=params)
        batch = env.get("data") or []
        orders.extend(batch)
        count = (env.get("pagination") or {}).get("count")
        # Потрійний захист від зациклення: порожня сторінка / зібрано всі за count /
        # неповна сторінка. ПРИМІТКА: точний контракт пагінації (page/page_size проти
        # limit/offset зі схеми) звірити на першому реальному наборі >page_size
        # замовлень — зараз замовлень 0, тож на практиці це 1 сторінка.
        if not batch:
            break
        if isinstance(count, int) and len(orders) >= count:
            break
        if len(batch) < page_size:
            break
        page += 1
    return orders


def get_order(order_id) -> dict:
    """GET /orders/{id} — повні деталі замовлення (OrderExtended: customer,
    recipient, shipping, items[], payment, tracking_number, ...) з поля `data`."""
    return _request_env("GET", f"/orders/{order_id}").get("data") or {}


def update_order_status(order_id, status: int, tracking_number: str = None,
                        reason_id: int = None) -> dict:
    """PATCH /orders/{id}/status — зміна статусу замовлення на боці EVA.

    ДОЗВОЛЕНІ статуси (OrderStatusPatchStatusEnum зі схеми): 1/9/10/11/12. Статуси
    5 (Відправлено) і 7 (Отримано) — READ-ONLY, їх виставляє САМА EVA (5 —
    автоматично з трекінгу перевізника після прикріплення ТТН на статусі 12);
    PATCH status:5 API відхилить, тому забороняємо його тут явно (ValueError).

    Потік: 1 -> 11 (підтв. продавцем) -> 12 (підтв. покупцем, з tracking_number) ->
    EVA сама -> 5 (Відправлено). tracking_number ОБОВ'ЯЗКОВИЙ для статусу 12
    (прикріплення ТТН). reason_id ОБОВ'ЯЗКОВИЙ для скасувань (9/10)."""
    if status not in EVA_STATUS_PATCHABLE:
        raise ValueError(
            f"EVA PATCH status приймає лише {sorted(EVA_STATUS_PATCHABLE)} "
            f"(5/7 — read-only, виставляє EVA), отримано {status}"
        )
    body: dict = {"status": status}
    if tracking_number is not None:
        body["tracking_number"] = tracking_number
    if reason_id is not None:
        body["reason_id"] = reason_id
    return _request_env("PATCH", f"/orders/{order_id}/status", json=body).get("data") or {}
