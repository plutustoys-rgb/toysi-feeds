import base64
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

# ВИПРАВЛЕНО (2026-07-16): попереднє припущення в _login() ("на відміну
# від Prom, тут немає окремого статичного API-ключа/scoped-токена")
# виявилось хибним — власниця показала скріншот кабінету "Налаштування
# -> Безпека API -> Токени API": окремий, довгоживучий (термін дії до
# 2027-01-12 на показаному прикладі) токен із явно призначеними ролями
# ("Контент-менеджер, Менеджер з обробки замовлень"), призначений
# СПЕЦІАЛЬНО для програмної інтеграції — на відміну від логіну/пароля
# кабінету, який, судячи з підказки на самій сторінці Rozetka, для
# цього не призначений. Це ймовірне пояснення access_denied
# (code=1010), знайденого раніше: /sites-логін успішно повертав токен,
# але без потрібних ролей/дозволів для /orders/search, /v1/balances.
ROZETKA_API_TOKEN = os.environ.get("ROZETKA_API_TOKEN", "")

ROZETKA_USERNAME = os.environ.get("ROZETKA_USERNAME", "")
ROZETKA_PASSWORD = os.environ.get("ROZETKA_PASSWORD", "")

ROZETKA_API_URL = "https://api-seller.rozetka.com.ua"
REQUEST_TIMEOUT = 30

# https://api-seller.rozetka.com.ua/apidoc/#api-Models-GetOrderStatuses
# Лише статуси, потрібні для нашої автоматизації нижче — повний список довший
# (11/12/13/15-20/24/25/... — відмови, скасування, повернення), не перелічуємо
# тут, бо order_router.py/order_status_tracker.py з ними напряму не працюють.
ORDER_STATUS_NEW               = 1   # Нове замовлення — це шукає orders_watcher.py
ORDER_STATUS_PROCESSING        = 2   # Комплектується. Дані підтверджені
ORDER_STATUS_HANDED_TO_DELIVERY = 3  # Передано в службу доставки (ttn обов'язковий)
ORDER_STATUS_DELIVERING        = 4   # Доставляється (авто-статус після ttn)
ORDER_STATUS_DONE              = 6   # Замовлення виконано
ORDER_STATUS_AUTO_TRACKED      = 61  # Авто-статус: Rozetka сама виставляє його,
                                      # коли ttn додано разом зі status=2 нижче

# Термінальні для order_status_tracker.py/аналогічної логіки (успішні й неуспішні
# кінцеві стани, після яких подальше опитування сенсу не має).
TERMINAL_STATUSES = {6, 7, 11, 12, 13, 15, 16, 17, 18, 19, 20, 24, 49}


class RozetkaAPIError(Exception):
    """Запит до Rozetka Seller API не вдався (мережа, невалідна відповідь,
    success=false у тілі відповіді) — включно з помилками авторизації."""


# Кешується лише в межах ОДНОГО запуску процесу (не в файл, не в БД) — токен
# живе 24 год за активного використання (документація api-seller.rozetka.com.ua/
# apidoc/#api-Authorization-PostSites), але просте повторне логінення на кожен
# новий запуск скрипта (orders_watcher.py, daily_report.py тощо) простіше й
# безпечніше, ніж персистити токен на диску VPS — і уникає окремого класу
# багів "протух кеш токена між прогонами".
_cached_token = None


def _login() -> str:
    """
    POST /sites — авторизація логіном і паролем від Особистого кабінету
    продавця. ВИПРАВЛЕНО (2026-07-16): попередній докстрінг стверджував,
    що окремого статичного API-ключа/scoped-токена немає — виявилось
    хибним, є (ROZETKA_API_TOKEN, див. вище) і саме він тепер
    пріоритетний шлях. Ця функція (логін-пароль) лишається лише
    фолбеком, поки ROZETKA_API_TOKEN ще не додано в .env. Пароль
    передається base64-encoded у тілі запиту, як прямо вимагає документація.

    ROZETKA_USERNAME/ROZETKA_PASSWORD зберігаються в .env на тому самому рівні
    довіри, що й TOYSI_API_KEY/PROM_API_KEY — але, на відміну від них, це
    буквально пароль до кабінету продавця, не scoped-ключ.
    """
    if not ROZETKA_USERNAME or not ROZETKA_PASSWORD:
        raise RuntimeError("ROZETKA_USERNAME/ROZETKA_PASSWORD не задані в .env")

    encoded_password = base64.b64encode(ROZETKA_PASSWORD.encode("utf-8")).decode("ascii")

    try:
        response = requests.post(
            f"{ROZETKA_API_URL}/sites",
            json={"username": ROZETKA_USERNAME, "password": encoded_password},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise RozetkaAPIError(f"Помилка з'єднання при авторизації: {e}")

    try:
        data = response.json()
    except ValueError:
        raise RozetkaAPIError(f"Невалідна відповідь при авторизації (не JSON): {response.text[:300]}")

    if not data.get("success"):
        err = data.get("errors", {})
        raise RozetkaAPIError(
            f"Авторизація Rozetka не вдалась: {err.get('message')} (code={err.get('code')})"
        )

    token = (data.get("content") or {}).get("access_token")
    if not token:
        raise RozetkaAPIError("Відповідь авторизації Rozetka без access_token")
    return token


def _get_token(force_refresh: bool = False) -> str:
    """ROZETKA_API_TOKEN (Налаштування -> Безпека API -> Токени API,
    з явно призначеними ролями) — пріоритетний шлях, якщо заданий:
    статичний, не потребує релогіну (force_refresh для нього — no-op,
    немає чого "оновлювати"). Якщо API все ж поверне 401 на цей
    токен — це означає, що сам токен деактивований/протух у кабінеті
    (24 год без використання, за підказкою на сторінці Rozetka), не
    щось, що можна виправити повторним викликом звідси.

    Фолбек на username/password логін (_login()) лишається для
    сумісності, якщо ROZETKA_API_TOKEN ще не додано в .env."""
    global _cached_token
    if ROZETKA_API_TOKEN:
        return ROZETKA_API_TOKEN
    if _cached_token is None or force_refresh:
        _cached_token = _login()
    return _cached_token


def _request(method: str, path: str, **kwargs) -> dict:
    """
    Виконує запит до Rozetka Seller API з Bearer-токеном. При 401 (токен
    протух/недійсний — документація каже 24 год за активного використання,
    але межові випадки можливі) — одна спроба релогіну й повтору, той самий
    підхід, що й had_failure-обробка в toysi_order_submit.fetch_order_statuses().
    """
    token = _get_token()
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {token}"
    headers.setdefault("Content-Language", "uk")

    def _do_request():
        return requests.request(
            method, f"{ROZETKA_API_URL}{path}", headers=headers, timeout=REQUEST_TIMEOUT, **kwargs
        )

    try:
        response = _do_request()
    except requests.exceptions.RequestException as e:
        raise RozetkaAPIError(f"Помилка з'єднання ({method} {path}): {e}")

    if response.status_code == 401:
        headers["Authorization"] = f"Bearer {_get_token(force_refresh=True)}"
        try:
            response = _do_request()
        except requests.exceptions.RequestException as e:
            raise RozetkaAPIError(f"Помилка з'єднання після релогіну ({method} {path}): {e}")

    try:
        data = response.json()
    except ValueError:
        raise RozetkaAPIError(f"Невалідна відповідь (не JSON, {method} {path}): {response.text[:300]}")

    if not data.get("success"):
        err = data.get("errors", {})
        raise RozetkaAPIError(f"{method} {path}: {err.get('message')} (code={err.get('code')})")

    return data.get("content") or {}


def fetch_new_orders() -> list:
    """
    GET /orders/search?status=1 — нові, ще не оброблені замовлення (статус 1,
    "Нове замовлення"). Пагінується через page, зупиняється, коли сторінка
    повертає порожній список (той самий підхід, що й reconcile_revenue.
    fetch_prom_orders_for_period(), де точний розмір сторінки Prom API теж
    не задокументовано явно).
    """
    orders = []
    page = 1
    while True:
        content = _request(
            "get", "/orders/search",
            params={"status": ORDER_STATUS_NEW, "page": page, "expand": "delivery,user,purchases"},
        )
        page_orders = content.get("orders", [])
        if not page_orders:
            break
        orders.extend(page_orders)
        page += 1
    return orders


def fetch_orders_by_date_range(created_from: str, created_to: str) -> list:
    """
    GET /orders/search?created_from=...&created_to=... — УСІ замовлення за
    період, незалежно від статусу (на відміну від fetch_new_orders(), якому
    потрібен лише статус 1) — потрібно для reconcile_revenue.py.

    ⚠️ GetOrderSearch має параметр `type` (1=В обробці, 2=Успішно завершені,
    3=Неуспішно завершені, дефолт 1) — БЕЗ явного `type` документація не
    підтверджує, чи повертаються геть усі замовлення чи лише group 1. Щоб не
    втратити завершені/неуспішні замовлення в звірці виручки, опитуємо всі
    три типи явно й об'єднуємо. Той самий клас невизначеності, що вже
    позначений у reconcile_revenue.fetch_prom_orders_for_period() для Prom —
    перший реальний прогін варто звірити вручну з кабінетом Rozetka.
    """
    orders = []
    for order_type in (1, 2, 3):
        page = 1
        while True:
            content = _request(
                "get", "/orders/search",
                params={
                    "created_from": created_from, "created_to": created_to,
                    "type": order_type, "page": page,
                },
            )
            page_orders = content.get("orders", [])
            if not page_orders:
                break
            orders.extend(page_orders)
            page += 1
    return orders


def get_order_details(order_id) -> dict:
    """GET /orders/{id} — повні деталі одного замовлення."""
    return _request(
        "get", f"/orders/{order_id}",
        params={"expand": "delivery,user,purchases,payment_type_name"},
    )


def update_order_status(order_id, status: int, ttn: str = None, seller_comment: str = None) -> dict:
    """
    PUT /orders/{id} — зміна статусу і/або прикріплення ТТН.

    За документацією Rozetka: якщо ttn передано РАЗОМ зі status=2 —
    замовлення АВТОМАТИЧНО переводиться в статус 61, і Rozetka сама починає
    відстежувати трекінг доставки — далі вручну міняти статуси не потрібно
    (аналог того, що order_status_tracker.py вже робить для читання статусу
    з боку Toysi, але тут — запис статусу/ТТН НА СТОРОНУ Rozetka, чого для
    Prom у цьому репозиторії взагалі не реалізовано — Prom такого API не
    надає в поточній інтеграції).
    """
    body = {"status": status}
    if ttn:
        body["ttn"] = ttn
    if seller_comment:
        body["seller_comment"] = seller_comment
    return _request("put", f"/orders/{order_id}", json=body)


def search_categories(name_query: str = None) -> list:
    """
    GET /market-categories/search — "Вибірка всіх активних категорій"
    (тобто категорій, доступних продавцю прямо зараз) — природний спосіб
    знайти дозволену альтернативу категорії зі стоп-списку програмно,
    замість ручного перегляду "Управління товарами -> Довідники" в кабінеті.

    ⚠️ НЕ підтверджено живим викликом (немає облікових даних на момент
    написання) — apidoc не деталізує тіло відповіді для цього ендпоінту
    так само детально, як для Orders/Balances. Перше використання варто
    звірити з реальною відповіддю (структура списку категорій, чи саме
    `name` — правильний параметр текстового пошуку).
    """
    params = {"name": name_query} if name_query else {}
    content = _request("get", "/market-categories/search", params=params)
    if isinstance(content, list):
        return content
    return content.get("categories") or []


def _fetch_goods_pages(path: str) -> list:
    """Пагінований збір усіх товарів з ApiItems-ендпоінтів (GetGoodsErrors/
    GetGoodsNotValid) — той самий підхід до пагінації (по page, до
    порожньої сторінки), що й fetch_new_orders()."""
    items = []
    page = 1
    while True:
        content = _request("get", path, params={"page": page})
        page_items = content.get("items") or []
        if not page_items:
            break
        items.extend(page_items)
        page += 1
    return items


def fetch_goods_errors() -> list:
    """
    GET /goods/errors — "Товари з помилками" (той самий розділ кабінету,
    що й ручний перегляд вкладки). Кожен елемент має
    `blocked_reason.title` (людський текст причини) — ЖИВЕ, авторитетне
    джерело того, чому саме Rozetka блокує/приховує конкретний товар,
    замість того, щоб ми самі вгадували/хардкодили стоп-списки категорій
    й брендів (задача 2026-07-15, Крок 3: "живі стоп-списки замість
    захардкоджених знімків").

    ⚠️ НЕ підтверджено живим викликом (немає облікових даних на момент
    написання) — структура `blocked_reason`/`error_reason` підтверджена
    лише з полів у самій специфікації apidoc, не з реальної відповіді.
    """
    return _fetch_goods_pages("/goods/errors")


def fetch_goods_not_valid() -> list:
    """GET /goods/not-valid — "Невалідні товари". Разом з
    fetch_goods_errors() це і є API-еквівалент кабінетного інструмента
    "Перевірка XML" — живий, поточний стан валідації каталогу на боці
    Rozetka, без потреби вручну заходити в кабінет чи вгадувати правила.
    Так само не підтверджено живим викликом."""
    return _fetch_goods_pages("/goods/not-valid")


def summarize_blocked_reasons(errors: list) -> dict:
    """
    Групує результат fetch_goods_errors() за blocked_reason.title (людський
    текст причини) -> кількість товарів. Перший крок до "живих стоп-списків
    замість захардкоджених знімків" (задача 2026-07-15, Крок 3) — ЩЕ НЕ
    автоматична заміна ROZETKA_CATEGORY_STOP_LIST/ROZETKA_BRAND_STOP_LIST
    у generate_rozetka_feed.py.

    🔴 НАВМИСНО не намагаюсь тут відрізнити "категорія в стоп-листі" від
    "бренд у стоп-листі" чи від геть іншої причини (напр. поганий опис) —
    я НЕ бачила жодної реальної відповіді цього ендпоінту (немає облікових
    даних), тож вигадувати regex/keyword-розпізнавання конкретних
    формулювань `blocked_reason.title` напевно означало б здогадуватись
    наосліп і, можливо, помилково. Це проміжний, чесний крок: групування
    без класифікації. Коли з'являться ROZETKA_USERNAME/ROZETKA_PASSWORD і
    перший реальний виклик — звір реальні значення `title` тут і допиши
    класифікацію (категорія/бренд/інше) окремим фолоу-апом."""
    counts: dict = {}
    for item in errors:
        reason = (item.get("blocked_reason") or {}).get("title") or "(без причини)"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def get_balance() -> dict:
    """GET /v1/balances/current — поточний баланс магазину (Крок 7 плану)."""
    return _request("get", "/v1/balances/current")


if __name__ == "__main__":
    if not ROZETKA_USERNAME or not ROZETKA_PASSWORD:
        print(
            "[rozetka_client] ROZETKA_USERNAME/ROZETKA_PASSWORD відсутні в .env — "
            "нічого перевірити неможливо.",
            file=sys.stderr,
        )
    else:
        try:
            orders = fetch_new_orders()
            print(f"[rozetka_client] Нових замовлень (статус 1): {len(orders)}")
            balance = get_balance()
            print(f"[rozetka_client] Баланс: {balance}")
        except RozetkaAPIError as e:
            print(f"[rozetka_client] {e}", file=sys.stderr)
