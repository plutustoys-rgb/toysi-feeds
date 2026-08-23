import base64
import json
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
# ⚠️ ГРАФ ПЕРЕХОДІВ (звірено живо 2026-08-19 через ?expand=status_available + apidoc):
# з НОВОГО(1) дозволені лише 26 (обробка) або скасувальні коди (40/49/…) — код 2
# з нового НЕДОСЯЖНИЙ. Офіційний флоу: 1 → 26 «Обробляється менеджером» →
# 2 «Комплектується. Дані підтверджені» → далі доставка з ТТН. Тому при ФОРВАРДІ
# ставимо 26, а не 2 (старий хардкод 2 падав code=1005 «Наступний статус недоступний»).
ORDER_STATUS_MANAGER_PROCESSING = 26 # Обробляється менеджером — ПЕРШИЙ крок після нового
ORDER_STATUS_PROCESSING        = 2   # Комплектується. Дані підтверджені (крок ПІСЛЯ 26, не з 1)
# Доставкові статуси — звірено 2026-08-19 (apidoc field status_order.<код> + живий
# status_available замовлення 903654095 на 26: [55,47,2,61,42,43,28,33,40,49,20,18,29,17,37,38]).
# 61 «Заплановано передачу перевізникові» досяжний з 26 НАПРЯМУ — ТУДИ прикріплюється ТТН
# (не в status=2, як помилково слав старий код). Після 61 Rozetka+перевізник ведуть далі авто.
ORDER_STATUS_SCHEDULED_HANDOVER = 61 # Заплановано передачу перевізникові — сюди прикріплюємо ТТН
ORDER_STATUS_AUTO_TRACKED      = 61  # DEPRECATED-alias (стара НЕВІРНА назва «авто-статус»; = 61)
# ⚠️ Коди нижче (3/4/6) — НЕ звірені живо. Реальні коди «Передано до служби доставки»(58?)/
# «Комплектується перевізником»(56)/«Виконано» звірити через status_available НА ТІЙ стадії.
ORDER_STATUS_HANDED_TO_DELIVERY = 3  # ⚠️ НЕ звірено — можливо не той код
ORDER_STATUS_DELIVERING        = 4   # ⚠️ НЕ звірено
ORDER_STATUS_DONE              = 6   # «Замовлення виконано» (термінальний). ЗВІРЕНО apidoc
                                     # GetOrderStatuses + ЖИВО 2026-08-23 (get_order_details.status:
                                     # 903654095 COD-отримано=6, 903719616 prepaid-отримано=6, Кравчук
                                     # 903992205 у дорозі=80 «чекає отримання від продавця»).

# Термінальні для order_status_tracker.py/аналогічної логіки (успішні й неуспішні
# кінцеві стани, після яких подальше опитування сенсу не має).
TERMINAL_STATUSES = {6, 7, 11, 12, 13, 15, 16, 17, 18, 19, 20, 24, 49}

# Жорсткий кап сторінок пагінації /orders/search — захист від зациклення, бо API ігнорує
# параметр page (перевірено живо 2026-08-19). За реального обсягу замовлень 50 сторінок з
# запасом вистачає; головний стоп — «сторінка без нових id» у fetch_new_orders/by_date_range.
_MAX_ORDER_PAGES = 50


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
    orders, seen, page = [], set(), 1
    while page <= _MAX_ORDER_PAGES:
        content = _request(
            "get", "/orders/search",
            # ⚠️ payment_type_name ОБОВ'ЯЗКОВО в expand (перевірено живо 2026-08-19): search БЕЗ
            # нього не вертає тип оплати → _rozetka_payment_method бачив порожньо → УСІ замовлення
            # класифікувались як prepaid → жодне COD не форвардилось (трималось на ручному).
            params={"status": ORDER_STATUS_NEW, "page": page,
                    "expand": "delivery,user,purchases,payment_type_name"},
        )
        page_orders = content.get("orders", [])
        if not page_orders:
            break
        # ⚠️ Rozetka /orders/search ІГНОРУЄ page (перевірено живо 2026-08-19: кожна сторінка
        # вертала ТЕ САМЕ замовлення → while True крутив сотні разів, 39с CPU, фетч «зависав»).
        # Зупиняємось, коли сторінка не дає ЖОДНОГО нового id (пагінація не рухається) + кап.
        new = [o for o in page_orders if str(o.get("id")) not in seen]
        if not new:
            break
        for o in new:
            seen.add(str(o.get("id")))
            orders.append(o)
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
        seen, page = set(), 1
        while page <= _MAX_ORDER_PAGES:
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
            # Той самий захист від ігнорованого page (див. fetch_new_orders).
            new = [o for o in page_orders if str(o.get("id")) not in seen]
            if not new:
                break
            for o in new:
                seen.add(str(o.get("id")))
                orders.append(o)
            page += 1
    return orders


def get_order_details(order_id) -> dict:
    """GET /orders/{id} — повні деталі одного замовлення."""
    return _request(
        "get", f"/orders/{order_id}",
        params={"expand": "delivery,user,purchases,payment_type_name"},
    )


def get_payment_status(order_id) -> dict:
    """GET /orders/status-payment/{id} — статус оплати замовлення. Оплачене → name=='paid'
    (title='Оплачено', value=1). Живо звірено на 903719616 (2026-08-20). Об'єкт замовлення
    окремого прапорця «оплачено» НЕ має — оце правильне джерело для payment_confirmed
    prepaid-Rozetka (bank_check/ПРИВАТ Rozetka-оплату не бачить)."""
    return _request("get", f"/orders/status-payment/{order_id}")


def is_order_paid(order_id) -> bool:
    """True, якщо Rozetka каже, що замовлення оплачене (name=='paid'). Помилка/невідомо →
    False (краще тримати непідтвердженим, ніж форварднути неоплачене наосліп).

    ⚠️ ЛИШЕ для PREPAID (оплата через платіжку Rozetka). Для COD НЕ ПРАЦЮЄ — apidoc: у
    payment_type='cash' `payment_status: null`, Rozetka не трекає готівку на пункті платіжним
    статусом. Сигнал завершення COD — get_order_status()==ORDER_STATUS_DONE (див. нижче)."""
    try:
        st = get_payment_status(order_id)
        return isinstance(st, dict) and str(st.get("name", "")).lower() == "paid"
    except Exception:
        # БУДЬ-ЯКА невизначеність (мережа, несподівана форма відповіді) → False: краще тримати
        # непідтвердженим, ніж форварднути неоплачене. Той самий безпечний напрямок.
        return False


def get_order_status(order_id) -> int | None:
    """Поточний order-статус замовлення в Rozetka — поле `status` у GET /orders/{id}
    (звірено живо 2026-08-23: `order_status` у відповіді = None, реальний статус у `status`).
    None на будь-якій помилці/несподіваній формі. Легкий запит без expand."""
    try:
        c = _request("get", f"/orders/{order_id}")
        st = c.get("status") if isinstance(c, dict) else None
        return int(st) if st is not None else None
    except (RozetkaAPIError, ValueError, TypeError):
        return None


def is_order_done(order_id) -> bool:
    """True, якщо Rozetka-статус = 6 «Замовлення виконано» (термінальний; для COD = покупець
    ЗАБРАВ+ОПЛАТИВ на пункті). Це правильний сигнал COD-фіскалізації (не is_order_paid, який для
    COD завжди False). Помилка/невідомо → False (безпечно, не поспішаємо з чеком до підтвердження)."""
    return get_order_status(order_id) == ORDER_STATUS_DONE


def _rz_delivery_sender() -> dict:
    """Реквізити відправника для RZ-Delivery-ТТН — усе через env (легко правити без деплою).
    Дефолти: пункт здачі Toysi «Алматинська, 4» (department=pickup_id, звірено живо 2026-08-20),
    відправник = ФОП Чечетенко О.Ю. (Plutonix — це НАЗВА МАГАЗИНА, а не ПІБ; sender.name для
    natural = ПІБ ФОП). Тел від власника. `info` НЕ може бути порожнім (RZ-модуль вимагає ≥1
    символ). Контракт звірено на живій ТТН RMP-835110782 (903719616)."""
    return {
        "type": os.environ.get("RZ_SENDER_TYPE", "natural"),   # ENUM: natural(физ)/legal(юр). ФОП=natural
        "city": os.environ.get("RZ_SENDER_CITY", "Київ"),
        "address": os.environ.get("RZ_SENDER_ADDRESS", "Алматинська, 4"),
        "department": os.environ.get("RZ_SENDER_DEPARTMENT", "0bc950b0-493f-4afb-bc7e-046d38580df3"),
        "name": os.environ.get("RZ_SENDER_NAME", "Чечетенко О.Ю."),      # ПІБ ФОП (офіц. відправник)
        "phones": [os.environ.get("RZ_SENDER_PHONE", "+380730150815")],
        "info": os.environ.get("RZ_SENDER_INFO", "Plutonix"),            # назва магазина (не порожнє)
    }


def create_delivery_ttn(order_id, weight: float = 0.5, height: int = 20, width: int = 20,
                        length: int = 20, places: int = 1, has_paid: bool = True,
                        cost: float = 0.0) -> dict:
    """POST /delivery-rozetka/create-order-ttn — створює RZ-Delivery-ТТН ІЗ замовлення.

    ⚠️ Для RZ Delivery ТТН створює ПРОДАВЕЦЬ (МИ), не постачальник: у Toysi нема доступу до
    нашого кабінета, і на пункті Rozetka вони не оформлятимуть (пряме уточнення власника +
    відповідь Toysi 2026-08-17). Контракт звірено ЖИВО 2026-08-20 — цей body створив реальну
    ТТН RMP-835110782 для 903719616. Отримувач тягнеться самим замовленням (не передаємо).

    payer='sender' (доставку платить відправник — як у кабінеті). prepaid → has_paid=True,cost=0;
    COD → has_paid=False, cost=сума накладеного. Повертає повну відповідь (ТТН — у track_num,
    діставати через extract_delivery_ttn)."""
    body = {
        "order_id": int(order_id),
        "sender": _rz_delivery_sender(),
        "params": {"weight": weight, "height": height, "width": width, "length": length},
        "places": places,
        "payer": "sender",
        "has_paid": bool(has_paid),
        "cost": cost,
    }
    return _request("post", "/delivery-rozetka/create-order-ttn", json=body)


def extract_delivery_ttn(create_resp: dict):
    """Номер ТТН (track_num) з відповіді create_delivery_ttn. `original_info` — JSON-рядок,
    у ньому track_num (напр. 'RMP-835110782'). Повертає рядок ТТН або None."""
    if not isinstance(create_resp, dict):
        return None
    tn = create_resp.get("carrier_track_num") or create_resp.get("track_num")
    if tn:
        return tn
    oi = create_resp.get("original_info")
    if isinstance(oi, str):
        try:
            oi = json.loads(oi)
        except (ValueError, TypeError):
            oi = {}
    if isinstance(oi, dict):
        return oi.get("track_num") or oi.get("carrier_track_num")
    return None


def _request_raw(method: str, path: str, **kwargs):
    """Як _request, але повертає СИРУ requests.Response (для бінарних файлів, напр. PDF-
    наклейки) — JSON не парситься. 401 → один релогін+повтор (той самий підхід, що _request)."""
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
    return response


def fetch_delivery_label(ttn: str) -> bytes:
    """POST /delivery-rozetka/ttn-print-batch {track_numbers:[ttn]} → PDF-наклейка ТТН (байти).

    Це друкована наклейка, яку Toysi клеїть на посилку й здає на пункт Rozetka. За apidoc
    (api_data.js) Success 200 = File(pdf). Стійко до двох форм відповіді:
      • сирий бінарний PDF (тіло починається з %PDF або Content-Type application/pdf);
      • JSON-обгортка {content:{file:<base64|url>}} — витягуємо base64 (data:...;base64,)
        або лишаємо на потім (url) — тоді кидаємо помітну помилку, щоб не гадати мовчки.
    На помилку API повертає JSON {success:false,errors:{...}} → RozetkaAPIError із причиною.
    ⚠️ Живу форму відповіді ще не знято (токен лише на VPS) — best-effort у роутері, збій не
    валить order flow; перша реальна відправка/проба власника підтвердить форму."""
    if not ttn:
        raise RozetkaAPIError("fetch_delivery_label: порожня ТТН")
    resp = _request_raw("post", "/delivery-rozetka/ttn-print-batch",
                        json={"track_numbers": [str(ttn)]})
    ctype = (resp.headers.get("Content-Type") or "").lower()
    body = resp.content or b""
    if body[:4] == b"%PDF" or "application/pdf" in ctype:
        return body
    # не схоже на сирий PDF — пробуємо JSON (або помилка, або base64/url-обгортка)
    try:
        data = resp.json()
    except ValueError:
        raise RozetkaAPIError(f"ttn-print-batch {ttn}: несподівана відповідь "
                              f"(ctype={ctype}, {resp.text[:200]})")
    if isinstance(data, dict) and not data.get("success", True):
        err = data.get("errors", {}) or {}
        raise RozetkaAPIError(f"ttn-print-batch {ttn}: {err.get('message')} (code={err.get('code')})")
    # success або без прапорця → шукаємо файл усередині content
    content = data.get("content") if isinstance(data, dict) else None
    file_ref = None
    if isinstance(content, dict):
        file_ref = content.get("file") or content.get("pdf") or content.get("label")
    elif isinstance(content, str):
        file_ref = content
    if isinstance(file_ref, str) and file_ref:
        b64 = file_ref.split("base64,", 1)[-1] if "base64," in file_ref else file_ref
        try:
            return base64.b64decode(b64, validate=False)
        except (ValueError, TypeError):
            raise RozetkaAPIError(f"ttn-print-batch {ttn}: content.file не декодується як base64 "
                                  f"(схоже на URL? '{file_ref[:80]}') — звірити форму живо")
    raise RozetkaAPIError(f"ttn-print-batch {ttn}: успіх, але файл не знайдено у відповіді "
                          f"({str(data)[:200]}) — звірити форму живо")


def update_order_status(order_id, status: int, ttn: str = None, seller_comment: str = None) -> dict:
    """
    PUT /orders/{id} — зміна статусу і/або прикріплення ТТН.

    ТТН прикріплюється РАЗОМ зі status=61 «Заплановано передачу перевізникові»
    (ORDER_STATUS_SCHEDULED_HANDOVER) — це той статус, де семантично живе ТТН
    (звірено 2026-08-19: apidoc status_order.61 + status_available 26→61 напряму).
    Після цього Rozetka+перевізник (НП/RZ Delivery) ведуть трекінг доставки самі —
    далі вручну міняти статуси не потрібно. Це запис статусу/ТТН НА СТОРОНУ Rozetka
    (для Prom такого API нема — там ТТН іде окремим POST /delivery/save_declaration_id).
    ⚠️ Стара версія слала status=2 (Комплектується) — ТТН туди не належить; виправлено.
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
