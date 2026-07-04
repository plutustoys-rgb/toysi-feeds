import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

NP_API_KEY  = os.environ.get("NP_API_KEY", "")
NP_API_URL  = "https://api.novaposhta.ua/v2.0/json/"
REQUEST_TIMEOUT = 15


def _call(model_name: str, called_method: str, method_properties: dict) -> list:
    if not NP_API_KEY:
        print(
            "[NovaPoshta] ПОМИЛКА: не заданий NP_API_KEY.\n"
            "  Безкоштовний ключ реєструється на novaposhta.ua (особистий кабінет -> Налаштування -> API).\n"
            "  Окремий бізнес-акаунт НЕ потрібен. Додайте NP_API_KEY=... у .env",
            file=sys.stderr,
        )
        return []

    payload = {
        "apiKey": NP_API_KEY,
        "modelName": model_name,
        "calledMethod": called_method,
        "methodProperties": method_properties,
    }
    try:
        response = requests.post(NP_API_URL, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[NovaPoshta] Помилка з'єднання: {e}", file=sys.stderr)
        return []

    try:
        data = response.json()
    except ValueError:
        print(f"[NovaPoshta] Невалідна відповідь (не JSON): {response.text[:300]}", file=sys.stderr)
        return []

    if not data.get("success"):
        errors = data.get("errors") or data.get("errorCodes") or []
        print(f"[NovaPoshta] API повернув помилку: {errors}", file=sys.stderr)
        return []

    return data.get("data", [])


def find_city(city_name: str, limit: int = 5) -> dict:
    """Шукає місто за назвою. Повертає {"ref":.., "name":..} найбільш релевантного збігу або None."""
    results = _call("Address", "getCities", {"FindByString": city_name, "Limit": str(limit)})
    if not results:
        return None
    top = results[0]
    return {"ref": top.get("Ref"), "name": top.get("Description")}


def find_warehouse(city_ref: str, warehouse_query: str = "") -> dict:
    """
    Шукає відділення/поштомат у місті за CityRef.
    warehouse_query — номер відділення ("15") або частина адреси з тексту замовлення.
    Фільтрація виконується на нашій стороні (по Description/Number), а не через FindByString
    сервера — ця опція для getWarehouses непослідовна між версіями API Нової Пошти.
    """
    warehouses = _call("AddressGeneral", "getWarehouses", {"CityRef": city_ref, "Limit": "500"})
    if not warehouses:
        return None

    if not warehouse_query:
        top = warehouses[0]
        return {"ref": top.get("Ref"), "description": top.get("Description"), "number": top.get("Number")}

    query = warehouse_query.strip().lower()
    for wh in warehouses:
        number = (wh.get("Number") or "").strip()
        description = wh.get("Description") or ""
        if query == number or query in description.lower():
            return {"ref": wh.get("Ref"), "description": description, "number": number}

    return None


def resolve_shipping(city_name: str, warehouse_query: str = "") -> dict:
    """
    Повний резолв "назва міста + запит відділення" -> ідентифікатори для Toysi order_create.

    Примітка щодо мапінгу на поля Toysi (`shipping_city_id`, `shipping_warehouse_id`):
    Toysi документує shipping_warehouse_id як ціле число ("номер відділення перевізника"),
    тому сюди йде звичайний Number ("15"), а не GUID Ref Нової Пошти.
    shipping_city_id документований як рядок-ідентифікатор міста перевізника — сюди йде
    CityRef (GUID) Нової Пошти. Це best-effort мапінг за офіційним описом полів Toysi;
    перевір на першому тестовому замовленні (api_mode=test) і скоригуй за потреби.
    """
    city = find_city(city_name)
    if not city:
        print(f"[NovaPoshta] Місто не знайдено: {city_name}", file=sys.stderr)
        return None

    warehouse = find_warehouse(city["ref"], warehouse_query)
    if not warehouse:
        print(f"[NovaPoshta] Відділення не знайдено: {city_name} / {warehouse_query}", file=sys.stderr)
        return None

    return {
        "city_name": city["name"],
        "shipping_city_id": city["ref"],
        "shipping_warehouse_id": warehouse["number"],
        "warehouse_ref": warehouse["ref"],
        "warehouse_description": warehouse["description"],
    }


if __name__ == "__main__":
    result = resolve_shipping("Київ", "15")
    if result:
        print(f"Місто:     {result['city_name']}")
        print(f"city_id:   {result['shipping_city_id']}")
        print(f"warehouse: №{result['shipping_warehouse_id']} — {result['warehouse_description']}")
    else:
        print("Резолв не вдався (перевір NP_API_KEY у .env)")
