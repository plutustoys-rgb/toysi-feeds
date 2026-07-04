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


def build_order_create_payload(order: dict, test_mode: bool = True) -> dict:
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
      shipping_warehouse_id   int, optional — номер відділення (0 = доставка за адресою)
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
        "shipping_carrier_name": order.get("shipping_carrier_name", "Нова Пошта"),
        "shipping_city":         order["shipping_city_name"],
        "shipping_address":      order.get("shipping_address", ""),
        "shipping_firstname":    order["first_name"],
        "shipping_lastname":     order["last_name"],
        "shipping_phone":        order["phone"],
        "shipping_moneyback":    float(order.get("moneyback", 0)),
        "shipping_dt":           delivery_dt.strftime("%Y-%m-%d %H:%M"),
    }

    if order.get("middle_name"):
        payload["shipping_middlename"] = order["middle_name"]
    if order.get("comment"):
        payload["comment"] = order["comment"][:500]
    if order.get("shipping_warehouse_id") is not None:
        payload["shipping_warehouse_id"] = order["shipping_warehouse_id"]
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


def submit_order(order: dict, test_mode: bool = True) -> dict:
    """
    Відправляє замовлення в Toysi через order_create.
    response_code 2 (дублікат) вважається успіхом, а не помилкою (Крок 5 плану).
    response_code 3 треба повторити; 4-21 — помилка в даних замовлення, показати в звіті.
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
