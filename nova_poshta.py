import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

NP_API_KEY  = os.environ.get("NP_API_KEY", "")
NP_API_URL  = "https://api.novaposhta.ua/v2.0/json/"
REQUEST_TIMEOUT = 15


class NovaPoshtaAPIError(Exception):
    """API явно відмовив обслуговувати запит (немає ключа, мережева помилка,
    невалідна відповідь, або success:false — зламаний/протермінований ключ,
    ліміт запитів, некоректний запит). На відміну від порожнього результату
    пошуку (місто/відділення дійсно не знайдено), це не нормальний стан —
    виклик вище має повідомити про проблему з API, а не "не знайдено"."""


def _call(model_name: str, called_method: str, method_properties: dict) -> list:
    if not NP_API_KEY:
        raise NovaPoshtaAPIError(
            "не заданий NP_API_KEY. Безкоштовний ключ реєструється на novaposhta.ua "
            "(особистий кабінет -> Налаштування -> API). Окремий бізнес-акаунт НЕ потрібен. "
            "Додайте NP_API_KEY=... у .env"
        )

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
        raise NovaPoshtaAPIError(f"помилка з'єднання: {e}") from e

    try:
        data = response.json()
    except ValueError:
        raise NovaPoshtaAPIError(f"невалідна відповідь (не JSON): {response.text[:300]}")

    if not data.get("success"):
        errors = data.get("errors") or data.get("errorCodes") or []
        raise NovaPoshtaAPIError(f"API повернув помилку: {errors}")

    return data.get("data", [])


def find_city(city_name: str, limit: int = 5) -> dict:
    """Шукає місто за назвою. Повертає {"ref":.., "name":..} найбільш релевантного збігу,
    або None, якщо міста дійсно немає в довіднику Нової Пошти.
    Піднімає NovaPoshtaAPIError, якщо сам запит до API не вдався (окрема причина від "не знайдено")."""
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
    Піднімає NovaPoshtaAPIError, якщо сам запит до API не вдався (окрема причина від "не знайдено").
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
    try:
        city = find_city(city_name)
    except NovaPoshtaAPIError as e:
        print(f"[NovaPoshta] Проблема з API Нової Пошти (не з даними міста): {e}", file=sys.stderr)
        return None
    if not city:
        print(f"[NovaPoshta] Місто не знайдено: {city_name}", file=sys.stderr)
        return None

    try:
        warehouse = find_warehouse(city["ref"], warehouse_query)
    except NovaPoshtaAPIError as e:
        print(f"[NovaPoshta] Проблема з API Нової Пошти (не з даними відділення): {e}", file=sys.stderr)
        return None
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
