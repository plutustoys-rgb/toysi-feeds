import os
import sys
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


def fetch_order_statuses(toysi_order_ids: list) -> dict:
    """
    Пакетний запит order_status (Крок 6 плану) — до 500 номерів за раз,
    не більше 5 запитів/сек до Toysi (інакше 503, тому паузи між чанками).
    Повертає {toysi_order_id (str): {"order_id":.., "status": int, "TTN": str, ...}}
    лише для ЗНАЙДЕНИХ замовлень — відсутні в результаті id просто не існують
    чи вже застарілі (>40 днів, Toysi перестає їх обслуговувати).
    """
    if not toysi_order_ids:
        return {}
    if not TOYSI_AUTH_USER or not TOYSI_API_KEY:
        raise RuntimeError(
            "TOYSI_AUTH_USER / TOYSI_API_KEY не задані. "
            "Взяти на toysi.ua/contact_info/?api=info і прописати в .env"
        )

    results = {}
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
            continue

        try:
            data = response.json()
        except ValueError:
            print(f"[toysi_order_submit] order_status: невалідна відповідь (не JSON): {response.text[:300]}", file=sys.stderr)
            continue

        # Фатальна помилка на весь чанк (0/4/400/404) — top-level response_code присутній.
        # Якщо знайдено хоч одне замовлення, response_code у відповіді взагалі немає
        # (документація toysi.ua/api-doc.php) — знайдені записи повертаються напряму.
        if isinstance(data, dict) and "response_code" in data:
            print(
                f"[toysi_order_submit] order_status: {data.get('response_code')} — {data.get('response_msg')}",
                file=sys.stderr,
            )
            continue

        results.update(data)

    return results


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
