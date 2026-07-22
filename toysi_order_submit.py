import os
import sys
import time
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

TOYSI_AUTH_USER = os.environ.get("TOYSI_AUTH_USER", "")
TOYSI_API_KEY   = os.environ.get("TOYSI_API_KEY", "")  # той самий ключ, що й для XML-фіда, працює як auth_key
TOYSI_API_URL   = "https://toysi.ua/api.php"
REQUEST_TIMEOUT = 30

# https://toysi.ua/api-doc.php — order_create response_code
RESPONSE_MESSAGES = {
    1:  "Замовлення прийнято",
    2:  "Дублікат internal_order_id (замовлення вже існує в Toysi, дані повертаються без створення нового)",
    3:  "Створення не вдалося — рекомендовано повторити спробу",
    4:  "Відсутні обов'язкові POST-параметри",
    5:  "Невірний auth_user або auth_key",
    6:  "internal_order_id занадто довгий (>25 символів)",
    7:  "Невірна кількість позицій (positions_count)",
    8:  "positions_quantity не масив або порожній",
    9:  "Довжина positions_quantity не збігається з positions_count",
    11: "Коди товарів відсутні в базі Toysi",
    12: "Нецілі ключі в positions_quantity",
    13: "Невірна кількість товару",
    14: "Порожнє ім'я отримувача",
    15: "Порожнє прізвище отримувача",
    16: "Невірний телефон отримувача",
    17: "Невірний час доставки",
    18: "Невірна сума накладеного платежу",
    19: "Порожня назва перевізника",
    20: "Порожня адреса доставки",
    21: "Порожнє місто доставки",
}
# Примітка: код 10 у таблиці response_code для order_create навмисно відсутній
# у Toysi (нумерація стрибає з 9 на 11) — перевірено напряму на toysi.ua/api-doc.php,
# це не пропуск у цьому словнику.

# Це ІНША таблиця — коди поля "status" замовлення (метод order_status/order_positions,
# ще не реалізований окремим order_status_tracker.py — Крок 6 плану). Код 10 тут є
# ("Скасовано") і легко переплутати з response_code вище, хоча це різні поля.
ORDER_STATUS_MESSAGES = {
    0:   "Невизначений (щойно прийняте або між етапами)",
    10:  "Скасовано",
    20:  "Частково зарезервовано (не всі товари на складі — менеджер уточнить кількість)",
    30:  "Повністю зарезервовано",
    40:  "На збиранні",
    50:  "Запаковано",
    60:  "Відвантажено",
    70:  "Доставлено (в розробці на боці Toysi — поки не повертається)",
    80:  "Повернуто (в розробці на боці Toysi — поки не повертається)",
    503: "Замовлення застаріло (>40 днів) — API більше не обслуговує, зупинити опитування",
}

# Статуси, після яких подальше опитування order_status не має сенсу:
# скасовано, повернуто, застаріло.
TERMINAL_ORDER_STATUSES = {10, 80, 503}


def describe_order_status(status_code: int) -> str:
    return ORDER_STATUS_MESSAGES.get(status_code, f"Невідомий status: {status_code}")


def build_order_create_payload(order: dict, test_mode: bool = False) -> dict:
    """
    order — нормалізоване замовлення (orders_db + результат nova_poshta.resolve_shipping()):
      internal_order_id       str, <=25 символів, "{platform}_{order_id}"
      items                   [{"toysi_code": str|int, "qty": int}, ...]
      first_name, last_name   str (обов'язкові за документацією Toysi)
      middle_name             str, optional
      phone                   str, 12 цифр з "380..."
      shipping_city_name      str
      shipping_address        str, можна "" при доставці на відділення
      shipping_city_id        str, optional — CityRef Нової Пошти (nova_poshta.resolve_shipping)
      shipping_warehouse_id   int, за замовчуванням 0 (= доставка за адресою) — завжди
                               надсилається, навіть якщо не задано (API вимагає цього)
      moneyback                float, 0 якщо клієнт уже передоплатив
      delivery_dt             datetime, optional (за замовчуванням — завтра 12:00)
      comment                 str, optional, <=500 символів
      declared_value          int, optional, мінімум 500
    """
    positions_quantity = {str(i["toysi_code"]): int(i["qty"]) for i in order["items"]}

    delivery_dt = order.get("delivery_dt") or (
        datetime.now() + timedelta(days=1)
    ).replace(hour=12, minute=0, second=0, microsecond=0)

    payload = {
        "auth_user":             TOYSI_AUTH_USER,
        "auth_key":              TOYSI_API_KEY,
        "api_version":           1,
        "api_method":            "order_create",
        "internal_order_id":     order["internal_order_id"][:25],
        "positions_count":       len(positions_quantity),
        # ВАЖЛИВО: Toysi приймає лише точний рядок "Новая почта" (рос., як у їх
        # тестовій формі toysi.ua/api-test.html) — "Нова Пошта" відхиляється як
        # невідомий перевізник (response_code 4, без явного пояснення чому).
        "shipping_carrier_name": order.get("shipping_carrier_name", "Новая почта"),
        # ВАЖЛИВО: попри те, що документація описує це поле як optional,
        # реальний API повертає response_code 4, якщо його взагалі немає в POST —
        # завжди передаємо, 0 = доставка за адресою.
        "shipping_warehouse_id": order.get("shipping_warehouse_id", 0),
        "shipping_city":         order["shipping_city_name"],
        "shipping_address":      order.get("shipping_address", ""),
        "shipping_firstname":    order["first_name"],
        "shipping_lastname":     order["last_name"],
        "shipping_phone":        order["phone"],
        "shipping_moneyback":    float(order.get("moneyback", 0)),
        "shipping_dt":           delivery_dt.strftime("%Y-%m-%d %H:%M:%S"),
    }

    if order.get("middle_name"):
        payload["shipping_middlename"] = order["middle_name"]
    if order.get("comment"):
        payload["comment"] = order["comment"][:500]
    if order.get("shipping_city_id"):
        payload["shipping_city_id"] = order["shipping_city_id"]
    if order.get("declared_value"):
        payload["shipping_declared_value"] = max(500, int(order["declared_value"]))
    if test_mode:
        payload["api_mode"] = "test"

    # PHP-стиль масиву в POST: positions_quantity[код]=кількість
    for code, qty in positions_quantity.items():
        payload[f"positions_quantity[{code}]"] = qty

    return payload


def submit_order(order: dict, test_mode: bool = False) -> dict:
    """
    Відправляє замовлення в Toysi через order_create.
    response_code 2 (дублікат) вважається успіхом, а не помилкою (Крок 5 плану).
    response_code 3 треба повторити; 4-21 — помилка в даних замовлення, показати в звіті.

    test_mode=False за замовчуванням навмисно (безпечний дефолт): api_mode=test
    у Toysi означає "заказ не будет обрабатываться менеджером" — не реальне
    замовлення, не списує депозит, не з'являється в Історії замовлень, ефемерне.
    Реальний випадок (замовлення №414634349, 2026-07-08): продакшн-виклик
    order_router.py мовчки йшов у test_mode тижнями через саме такий небезпечний
    дефолт — Toysi відповідав response_code=1, "успіх" виглядав правдоподібно,
    але жодне замовлення реально не створювалось. Викликай з test_mode=True
    лише свідомо, для ручного тестування (як у __main__ нижче).
    """
    if not TOYSI_AUTH_USER or not TOYSI_API_KEY:
        raise RuntimeError(
            "TOYSI_AUTH_USER / TOYSI_API_KEY не задані. "
            "Взяти на toysi.ua/contact_info/?api=info і прописати в .env"
        )

    payload = build_order_create_payload(order, test_mode=test_mode)

    try:
        response = requests.post(TOYSI_API_URL, data=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        return {
            "accepted": False, "response_code": None,
            "message": f"Помилка з'єднання: {e}",
            "toysi_order_id": None, "is_duplicate": False, "should_retry": True, "raw": {},
        }

    try:
        data = response.json()
    except ValueError:
        return {
            "accepted": False, "response_code": None,
            "message": f"Невалідна відповідь (не JSON): {response.text[:300]}",
            "toysi_order_id": None, "is_duplicate": False, "should_retry": True, "raw": {},
        }

    code = int(data.get("response_code", 0))
    message = RESPONSE_MESSAGES.get(code, f"Невідомий response_code: {code}")

    return {
        "accepted": code in (1, 2),
        "response_code": code,
        "message": message,
        "toysi_order_id": data.get("order_id"),
        "is_duplicate": code == 2,
        "should_retry": code == 3,
        "raw": data,
    }


class ToysiAPIError(Exception):
    """Сам запит до order_status не вдався (мережа, невалідна відповідь,
    фатальна помилка API) — на відміну від порожнього результату, коли
    Toysi відповів нормально, але серед переданих id справді немає жодного
    (не існують чи застаріли, >40 днів). Плутати ці два випадки небезпечно:
    check_toysi_reconciliation() у service_watchdog.py трактує "не знайдено"
    як ознаку, що замовлення могло піти в test_mode (як №414634349) — секундний
    мережевий збій не повинен виглядати так само."""


def fetch_order_statuses(toysi_order_ids: list) -> dict:
    """
    Пакетний запит order_status (Крок 6 плану) — до 500 номерів за раз,
    не більше 5 запитів/сек до Toysi (інакше 503, тому паузи між чанками).
    Повертає {toysi_order_id (str): {"order_id":.., "status": int, "TTN": str, ...}}
    лише для ЗНАЙДЕНИХ замовлень — відсутні в результаті id просто не існують
    чи вже застарілі (>40 днів, Toysi перестає їх обслуговувати).

    Піднімає ToysiAPIError, якщо ЖОДЕН чанк не вдалося опитати успішно
    (мережа/невалідна відповідь/фатальна помилка API) — це відрізняється від
    "усі чанки опрацьовано, просто жоден id не знайдено", коли повертається
    порожній dict без винятку.
    """
    if not toysi_order_ids:
        return {}
    if not TOYSI_AUTH_USER or not TOYSI_API_KEY:
        raise RuntimeError(
            "TOYSI_AUTH_USER / TOYSI_API_KEY не задані. "
            "Взяти на toysi.ua/contact_info/?api=info і прописати в .env"
        )

    results = {}
    had_failure = False
    chunk_size = 500
    for i in range(0, len(toysi_order_ids), chunk_size):
        chunk = toysi_order_ids[i : i + chunk_size]
        payload = {
            "auth_user": TOYSI_AUTH_USER,
            "auth_key": TOYSI_API_KEY,
            "api_version": 1,
            "api_method": "order_status",
            "order_id": ",".join(str(oid) for oid in chunk),
        }

        try:
            response = requests.post(TOYSI_API_URL, data=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"[toysi_order_submit] order_status: помилка з'єднання: {e}", file=sys.stderr)
            had_failure = True
            continue

        try:
            data = response.json()
        except ValueError:
            print(f"[toysi_order_submit] order_status: невалідна відповідь (не JSON): {response.text[:300]}", file=sys.stderr)
            had_failure = True
            continue

        if not isinstance(data, dict):
            # Незадокументована форма відповіді (null/список/рядок/число) — trap
            # знайдено незалежним рев'ю: без цієї перевірки results.update(data)
            # нижче впав би з TypeError на дечому на кшталт None, і жоден з
            # викликів (order_status_tracker.py/service_watchdog.py) цього не
            # ловить (лише RuntimeError/ToysiAPIError).
            print(
                f"[toysi_order_submit] order_status: неочікувана форма відповіді "
                f"(не dict): {response.text[:300]}",
                file=sys.stderr,
            )
            had_failure = True
            continue

        # Фатальна помилка на весь чанк (0/4/400/404) — top-level response_code присутній.
        # Якщо знайдено хоч одне замовлення, response_code у відповіді взагалі немає
        # (документація toysi.ua/api-doc.php) — знайдені записи повертаються напряму.
        if "response_code" in data:
            print(
                f"[toysi_order_submit] order_status: {data.get('response_code')} — {data.get('response_msg')}",
                file=sys.stderr,
            )
            had_failure = True
            continue

        results.update(data)

    if had_failure and not results:
        raise ToysiAPIError("не вдалося перевірити жоден chunk — див. лог вище")

    return results


# Пауза між викликами order_positions (2026-07-22, знахідка власниці — Графа 6
# КОДВ) — той самий ліміт "не більше 5 запитів/сек", що вже документований для
# order_status вище, але тут КОЖЕН виклик — це ОКРЕМИЙ HTTP-запит (на відміну
# від order_status, що батчить до 500 id в один запит), бо документація
# toysi.ua/api-doc.php прямо каже: "Для метода order_positions можно указать
# только один номер заказа". 0.25с = 4 запити/сек, безпечно нижче ліміту.
ORDER_POSITIONS_REQUEST_DELAY_SEC = 0.25


def fetch_order_positions(toysi_order_id) -> dict | None:
    """order_positions (2026-07-22, знахідка власниці — Графа 6 КОДВ):
    ЄДИНИЙ спосіб дістати РЕАЛЬНУ суму, що йде на списання з депозиту Toysi
    за конкретне замовлення — на відміну від каталогу (parser.py), який
    віддає лише ПОТОЧНУ базову оптову ціну товару БЕЗ персональної знижки,
    order_positions повертає "sum_with_discount" — фактичну суму ЦЬОГО
    замовлення з ЗАСТОСОВАНОЮ на момент його створення персональною знижкою
    (personal_discount, що змінювалась в часі 5%->15% — саме тому catalog-
    based оцінка структурно не могла бути точною для старих замовлень).

    Живо підтверджено власницею: реальне списання з депозиту (671.92₴ на
    6 замовленнях) = сума sum_with_discount по кожному замовленню + фіксований
    збір Toysi "Збірка" 15₴/замовлення (див. TOYSI_ASSEMBLY_FEE_UAH у
    daily_report.py) — "Збірка" в ЦІЙ відповіді API НЕ присутня (перевірено
    проти документації toysi.ua/api-doc.php — поле відсутнє в списку), видно
    лише в кабінеті ("order_detailed") — тому додається ОКРЕМО в daily_report.py,
    не тут.

    Повертає dict з полями (усі суми — рядки за документацією Toysi, парсити
    на float у виклику): order_id, status, sum, personal_discount,
    sum_with_discount, positions_price, positions_discount_price,
    positions_name, positions_quantity, shipping_moneyback. None — якщо
    запит не вдався, замовлення не знайдено, чи відповідь фатальна
    (response_code)."""
    if not TOYSI_AUTH_USER or not TOYSI_API_KEY:
        raise RuntimeError(
            "TOYSI_AUTH_USER / TOYSI_API_KEY не задані. "
            "Взяти на toysi.ua/contact_info/?api=info і прописати в .env"
        )

    payload = {
        "auth_user":   TOYSI_AUTH_USER,
        "auth_key":    TOYSI_API_KEY,
        "api_version": 1,
        "api_method":  "order_positions",
        "order_id":    str(toysi_order_id),
    }

    try:
        response = requests.post(TOYSI_API_URL, data=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[toysi_order_submit] order_positions({toysi_order_id}): помилка з'єднання: {e}", file=sys.stderr)
        return None
    finally:
        time.sleep(ORDER_POSITIONS_REQUEST_DELAY_SEC)

    try:
        data = response.json()
    except ValueError:
        print(
            f"[toysi_order_submit] order_positions({toysi_order_id}): невалідна відповідь "
            f"(не JSON): {response.text[:300]}", file=sys.stderr,
        )
        return None

    if not isinstance(data, dict):
        print(
            f"[toysi_order_submit] order_positions({toysi_order_id}): неочікувана форма "
            f"відповіді (не dict): {response.text[:300]}", file=sys.stderr,
        )
        return None

    if "response_code" in data:
        print(
            f"[toysi_order_submit] order_positions({toysi_order_id}): "
            f"{data.get('response_code')} — {data.get('response_msg')}", file=sys.stderr,
        )
        return None

    return data.get(str(toysi_order_id))


if __name__ == "__main__":
    fake_order = {
        "internal_order_id": "test_demo_0001",
        "items": [{"toysi_code": "11623", "qty": 1}],
        "first_name": "Тест",
        "last_name": "Тестенко",
        "phone": "380501234567",
        "shipping_city_name": "Київ",
        "shipping_city_id": "8d5a980d-391c-11dd-90d9-001a92567626",
        "shipping_warehouse_id": 1,
        "moneyback": 500.0,
        "comment": "Тестове замовлення для перевірки інтеграції (api_mode=test)",
    }

    if not TOYSI_AUTH_USER or not TOYSI_API_KEY:
        print(
            "[toysi_order_submit] TOYSI_AUTH_USER / TOYSI_API_KEY відсутні в .env — "
            "показую лише payload, який був би відправлений:\n"
        )
        print(build_order_create_payload(fake_order, test_mode=True))
    else:
        result = submit_order(fake_order, test_mode=True)
        print(result)
