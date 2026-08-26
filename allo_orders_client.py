"""allo_orders_client.py — клієнт ALLO Dropship Public API v2 (замовлення + баланс).

НАВІЩО (2026-08-26, наказ власника «по алло можна продовжувати» — креди від ALLO прийшли):
ALLO дала dropship-API (раніше була лише кабінет-скрейпер, `allo_cabinet_scraper.py`). Це
розблоковує авто-обробку замовлень ALLO так само, як EVA merchant-api. Повна специфікація —
у довіднику `технічні_вимоги_маркетплейсів/allo.md` (розділ «ALLO Dropship Public API»), жива
звірка зі спеком 2026-08-26 підтвердила: login=username+apiKey, 4 методи (orderList,
cancelStatuses, update, getBalance).

СКОУП цього модуля (СВІДОМО read-safe): login, get_balance, fetch_orders (читання),
update_order_status (метод API, але тут НІДЕ авто-не-викликається). Авто-форвард замовлення
на Toysi + запис ТТН назад — ОКРЕМА наступна фаза, яку будуємо на РЕАЛЬНОМУ дампі orderList
(правило `test-in-real-environment` / `test-api-converters-against-real-dumps`: конвертер
ALLO-замовлення → внутрішнє замовлення, протестований лише проти вигаданого dict, дає
хибно-зелений тест — двічі обпеклись на структурі Rozetka-замовлення). Тому спершу дістаємо
живий orderList (smoke-test нижче) після того, як ALLO внесе наш IP у whitelist і креди
ляжуть у .env на VPS, і аж тоді пишемо конвертер із цим дампом як фікстурою.

АУТЕНТИФІКАЦІЯ: POST /ua/api/public/login, header api_version:2, body {username, apiKey} →
{sessionId}. Токен живе 3600с; будь-який виклик, що повернув {"error":{"code":5,...}}
(Session expired) → один перелогін і повтор (той самий підхід, що 401-релогін у EVA/Rozetka).

СЕКРЕТИ: ALLO_API_USERNAME / ALLO_API_KEY беруться з .env (VPS), НІКОЛИ не в Cowork-папку
(був реальний витік NovaPay). «Пароль» із листа ALLO — це apiKey.
"""
import os
import sys

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ALLO_API_USERNAME = os.environ.get("ALLO_API_USERNAME", "")
ALLO_API_KEY = os.environ.get("ALLO_API_KEY", "")

# host=allo.ua, basePath=/api; укр-версія віддає ua-контент де можливо (зі спеку info).
ALLO_API_BASE = "https://allo.ua/ua/api/public"
ALLO_API_VERSION = "2"
REQUEST_TIMEOUT = 30

# Коди статусу замовлення ALLO (зі спеку). update приймає 1..5 (+cancel_status_id+note при 5).
ALLO_STATUS_ACCEPTED = 0      # Прийнято (початковий, read-only)
ALLO_STATUS_FORMING = 1       # Формується (комплектується на складі)
ALLO_STATUS_SHIPPED = 2       # Відправлено (доставляється)
ALLO_STATUS_DELIVERED = 3     # Доставлено
ALLO_STATUS_DONE = 4          # Виконано
ALLO_STATUS_CANCELLED = 5     # Скасовано (потребує cancel_status_id + note)
ALLO_STATUS_PAUSED = 6        # Призупинено (read-only)
ALLO_UPDATE_ALLOWED_STATUSES = {1, 2, 3, 4, 5}


class AlloAPIError(Exception):
    """Запит до ALLO Public API не вдався (мережа, не-JSON, HTTP 4xx/5xx, або
    {"error":{...}} у тілі відповіді) — включно з помилками авторизації."""


# Кеш sessionId лише в межах ОДНОГО запуску процесу (не в файл/БД) — той самий
# принцип, що _tokens в eva_orders_client / _cached_token у rozetka_client.
_session = {"id": None}


def _headers() -> dict:
    return {"api_version": ALLO_API_VERSION, "Content-Type": "application/json"}


def _login() -> str:
    """POST /login — {username, apiKey} → sessionId. Кешує й повертає sessionId."""
    if not ALLO_API_USERNAME or not ALLO_API_KEY:
        raise RuntimeError("ALLO_API_USERNAME/ALLO_API_KEY не задані в .env")
    try:
        response = requests.post(
            f"{ALLO_API_BASE}/login",
            json={"username": ALLO_API_USERNAME, "apiKey": ALLO_API_KEY},
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise AlloAPIError(f"Помилка авторизації ALLO: {e}")
    try:
        env = response.json()
    except ValueError:
        raise AlloAPIError(f"Невалідна відповідь login ALLO (не JSON): {response.text[:300]}")
    if isinstance(env, dict) and env.get("error"):
        raise AlloAPIError(f"Авторизація ALLO не вдалась: {env['error']}")
    session_id = (env or {}).get("sessionId")
    if not session_id:
        raise AlloAPIError(f"Відповідь login ALLO без sessionId: {str(env)[:300]}")
    _session["id"] = session_id
    return session_id


def _get_session(force_new: bool = False) -> str:
    if force_new or not _session.get("id"):
        _login()
    return _session["id"]


def _is_session_expired(env) -> bool:
    """ALLO сигналізує протухлий токен через {"error":{"code":5,...}} (Session expired)."""
    return isinstance(env, dict) and isinstance(env.get("error"), dict) \
        and env["error"].get("code") == 5


def _call(api_path: str, args: dict) -> dict:
    """POST /call?apiPath=<method>, body {sessionId, args}. При error.code=5
    (протух токен) — один перелогін і повтор. Кидає AlloAPIError на інші error/HTTP."""
    url = f"{ALLO_API_BASE}/call?apiPath={api_path}"

    def _do(session_id):
        try:
            r = requests.post(
                url, json={"sessionId": session_id, "args": args},
                headers=_headers(), timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            raise AlloAPIError(f"Помилка з'єднання ({api_path}): {e}")
        if r.status_code >= 400:
            raise AlloAPIError(f"{api_path}: HTTP {r.status_code} — {r.text[:300]}")
        try:
            return r.json()
        except ValueError:
            raise AlloAPIError(f"Невалідна відповідь ({api_path}, не JSON): {r.text[:300]}")

    env = _do(_get_session())
    if _is_session_expired(env):
        env = _do(_get_session(force_new=True))
    if isinstance(env, dict) and env.get("error"):
        raise AlloAPIError(f"{api_path}: {env['error']}")
    return env


def get_balance() -> dict:
    """partner.getBalance — баланс партнера (hold / restBalance). Повертає сире тіло
    (структуру звірити на живому виклику — довідник каже [{hold, restBalance}])."""
    return _call("partner.getBalance", {})


def get_cancel_statuses() -> dict:
    """orders.cancelStatuses — довідник причин скасування ({id, name}). Потрібен для
    коректного cancel_status_id при update(main_status=5)."""
    return _call("orders.cancelStatuses", {})


def fetch_orders(accepted_from: str = None, order_id: str = None,
                 limit: int = 50, max_pages: int = 200) -> list:
    """orders.orderList — замовлення з `accepted_from` ("YYYY-MM-DD HH:MM:SS") АБО по
    конкретному `order_id`, з наскрізною пагінацією (offset/limit). Повертає СИРИЙ
    список замовлень ALLO (конвертер у внутрішнє замовлення — окрема фаза на реальному
    дампі). ПРИМІТКА: спек вимагає в args і accepted_from, і orderId (orderId
    пріоритетніший) — передаємо той, що заданий; порожній другий не шлемо."""
    if not accepted_from and not order_id:
        raise ValueError("fetch_orders: треба accepted_from або order_id")
    orders: list = []
    offset = 0
    pages = 0
    while pages < max_pages:
        args = {"offset": offset, "limit": limit}
        if order_id:
            args["orderId"] = order_id
        if accepted_from:
            args["accepted_from"] = accepted_from
        env = _call("orders.orderList", args)
        batch = (env or {}).get("orders") or []
        orders.extend(batch)
        pages += 1
        # orderId → один конкретний заказ, пагінація не потрібна; неповна пачка = кінець.
        if order_id or len(batch) < limit:
            break
        offset += limit
    return orders


def update_order_status(order_id, main_status: int, tracking_number: str = None,
                        cancel_status_id: str = None, note: str = None,
                        updated_date: str = None) -> dict:
    """orders.update — зміна статусу замовлення на боці ALLO (+ запис/заміна ТТН).

    ДОЗВОЛЕНІ main_status: 1..5 (0 Прийнято / 6 Призупинено — read-only, виставляє ALLO).
    При main_status=5 (Скасовано) cancel_status_id і note ОБОВ'ЯЗКОВІ (валідуємо тут).
    tracking_number — створює новий або замінює наявний номер ТТН.

    ⚠️ Це метод API — він РЕАЛЬНО міняє статус замовлення на ALLO. У цьому модулі він
    НІДЕ авто-не-викликається; підключення до order-pipeline (авто-ТТН/статус) —
    окрема фаза з аудитом. Пряме ручне використання — лише свідомо."""
    if main_status not in ALLO_UPDATE_ALLOWED_STATUSES:
        raise ValueError(
            f"ALLO update приймає main_status {sorted(ALLO_UPDATE_ALLOWED_STATUSES)}, "
            f"отримано {main_status} (0/6 read-only)"
        )
    if main_status == ALLO_STATUS_CANCELLED and not (cancel_status_id and note):
        raise ValueError("main_status=5 (Скасовано) вимагає cancel_status_id і note")
    order_obj = {"id": str(order_id), "status": {"main_status": main_status}}
    if tracking_number:
        order_obj["tracking_number"] = str(tracking_number)
    if cancel_status_id:
        order_obj["status"]["cancel_status_id"] = str(cancel_status_id)
    if note:
        order_obj["note"] = note
    if updated_date:
        order_obj["updated_date"] = updated_date
    return _call("orders.update", {"orders": [order_obj], "total_records": 1})


def _smoke_test(days: int = 7) -> None:
    """Живий smoke-test (запускати на VPS ПІСЛЯ: креди в .env + наш IP у whitelist ALLO).
    Друкує БЕЗ PII/секретів: баланс, к-сть замовлень, і по кожному — id/статус/к-сть
    товарів/shipping_id/чи є ТТН. Дає РЕАЛЬНИЙ дамп структури для наступної фази."""
    from datetime import datetime, timedelta
    print(f"[allo smoke] login як {ALLO_API_USERNAME!r}...")
    _login()
    print("[allo smoke] login OK, sessionId отримано")
    try:
        print(f"[allo smoke] getBalance: {get_balance()}")
    except AlloAPIError as e:
        print(f"[allo smoke] getBalance ПОМИЛКА: {e}")
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[allo smoke] orderList від {since} (останні {days} дн)...")
    orders = fetch_orders(accepted_from=since, limit=50)
    print(f"[allo smoke] замовлень: {len(orders)}")
    for o in orders[:10]:
        products = o.get("products") or []
        shipping = o.get("shipping") or {}
        status = (o.get("status") or {}).get("status")
        print(f"  id={o.get('id')} status={status} товарів={len(products)} "
              f"shipping_id={shipping.get('shipping_id')} ТТН={'так' if shipping.get('tracking_number') else 'ні'}")
    if orders:
        print(f"[allo smoke] ключі 1-го замовлення (для конвертера): {sorted(orders[0].keys())}")
        if orders[0].get("products"):
            print(f"[allo smoke] ключі products[0]: {sorted(orders[0]['products'][0].keys())}")


if __name__ == "__main__":
    _smoke_test()
