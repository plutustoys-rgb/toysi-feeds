"""
ukrposhta_client.py — створює відправлення Укрпоштою (ТТН + PDF-етикетка) на
своєму боці, за офіційним PDF Укрпошти "Як почати роботу з API"
(dev.ukrposhta.ua, бізнес-акаунт через КЕП).

КОНТЕКСТ: Toysi не приймає наш API-ключ для доставки Укрпоштою (політика
компанії, не технічна прогалина — повторно не узгоджуємо). Тому створюємо
ТТН і етикетку самі, тут, а не через order_create Toysi (як для Нової
Пошти, де Toysi сама створює відправлення НП на своєму боці).

Послідовність викликів (підтверджено власником з офіційного PDF):
    POST /addresses   — адреса відправника (Toysi, Київ) і отримувача (клієнт)
    POST /clients      — клієнт-отримувач
    POST /shipments     — саме відправлення (посилається на адреси й клієнта)
    GET  /shipments/{uuid}/sticker — PDF-етикетка готового відправлення

⚠️ КАРКАС БЕЗ ТОКЕНА (за дорученням власника — "починай каркас коду вже
зараз, не чекаючи токена"): власник паралельно реєструє бізнес-акаунт
Укрпошти (договір через КЕП). Поки UKRPOSHTA_API_KEY не задано, усі виклики
падають з UkrposhtaAPIError на самому початку (як NP_API_KEY у
nova_poshta.py) — це очікувано, не помилка каркасу.

НЕ ПІДТВЕРДЖЕНО (уточнити з реальним токеном і офіційним PDF під рукою,
скоригувати за потреби — той самий підхід, що resolve_shipping() у
nova_poshta.py):
  - UKRPOSHTA_API_URL нижче — заглушка (dev.ukrposhta.ua — портал
    реєстрації/документації, НЕ обов'язково те саме, що базовий URL
    робочого API).
  - Точні назви полів у тілі запиту POST /addresses, /clients, /shipments
    (тут — best-effort за типовою формою REST API доставки: counterparty/
    recipient/sender, city/street/building, weight/dimensions тощо).
  - Формат авторизації (Bearer-токен передбачено як найімовірніший варіант
    для REST API з бізнес-акаунтом; можливо додатковий Counterparty-Token,
    як у деяких версій цього API).
  - Повна адреса відправника Toysi: підтверджено лише місто (Київ) і індекс
    (02152) — вулиця/будинок/контактна особа/телефон відправника ще
    потрібно уточнити в Toysi перед першим реальним відправленням.

Запуск (лише після появи UKRPOSHTA_API_KEY у .env):
    python ukrposhta_client.py   # тестовий виклик create_shipment_with_label()
"""

import os
import sys

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

UKRPOSHTA_API_KEY = os.environ.get("UKRPOSHTA_API_KEY", "")
UKRPOSHTA_API_URL = "https://dev.ukrposhta.ua/api"  # TODO: звір із офіційним PDF / кабінетом dev.ukrposhta.ua
REQUEST_TIMEOUT   = 30

# Відправник — Toysi (Київ). Підтверджено лише місто й індекс (2026-07-10);
# вулицю/будинок/контактну особу/телефон треба уточнити в Toysi перед
# першим реальним відправленням (без них create_address() для відправника
# піде з явно неповними даними).
TOYSI_SENDER_ADDRESS = {
    "city": "Київ",
    "postcode": "02152",
    "street": "",   # TODO: уточнити в Toysi
    "building": "", # TODO: уточнити в Toysi
    "contact_name": "",  # TODO: уточнити в Toysi (контактна особа складу)
    "contact_phone": "", # TODO: уточнити в Toysi
}


class UkrposhtaAPIError(Exception):
    """Сам запит до Ukrposhta API не вдався (немає ключа, мережева помилка,
    невалідна відповідь, чи явна відмова сервера) — аналог NovaPoshtaAPIError
    у nova_poshta.py."""


def _check_sender_address_complete() -> None:
    """TOYSI_SENDER_ADDRESS має 4 незаповнені поля (TODO вище) — без них
    create_address() для відправника пішов би в Ukrposhta API з порожніми
    street/building/контактними даними і повернувся б криптичною помилкою
    валідації (400) без зрозумілої причини. Явна перевірка тут дає одразу
    зрозумілий UkrposhtaAPIError замість цього."""
    missing = [k for k, v in TOYSI_SENDER_ADDRESS.items() if not (v or "").strip()]
    if missing:
        raise UkrposhtaAPIError(
            f"TOYSI_SENDER_ADDRESS неповна — відсутні поля: {', '.join(missing)}. "
            "Уточни в Toysi (вулиця/будинок/контактна особа/телефон складу) і "
            "заповни константу в ukrposhta_client.py перед першим реальним відправленням."
        )


def _call(method: str, path: str, json_body: dict = None, params: dict = None) -> dict:
    if not UKRPOSHTA_API_KEY:
        raise UkrposhtaAPIError(
            "не заданий UKRPOSHTA_API_KEY. Токен видається після реєстрації "
            "бізнес-акаунту через КЕП на dev.ukrposhta.ua. Додайте "
            "UKRPOSHTA_API_KEY=... у .env, коли власник його отримає."
        )

    url = f"{UKRPOSHTA_API_URL}{path}"
    headers = {"Authorization": f"Bearer {UKRPOSHTA_API_KEY}"}

    try:
        response = requests.request(
            method, url, headers=headers, json=json_body, params=params, timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise UkrposhtaAPIError(f"помилка з'єднання ({method} {path}): {e}") from e

    try:
        return response.json()
    except ValueError:
        raise UkrposhtaAPIError(f"невалідна відповідь (не JSON) від {method} {path}: {response.text[:300]}")


def create_address(city: str, postcode: str, street: str = "", building: str = "") -> str:
    """POST /addresses — реєструє адресу (відправника чи отримувача),
    повертає її ідентифікатор (uuid) для подальшого використання в
    create_client()/create_shipment(). Поля тіла запиту — best-effort,
    звір з офіційним PDF при першому реальному виклику."""
    body = {
        "city": city,
        "postcode": postcode,
        "street": street,
        "building": building,
    }
    data = _call("POST", "/addresses", json_body=body)
    return data.get("uuid") or data.get("id")


def create_client(first_name: str, last_name: str, phone: str, address_uuid: str) -> str:
    """POST /clients — реєструє клієнта-отримувача, прив'язаного до адреси
    з create_address(). Повертає ідентифікатор клієнта (uuid)."""
    body = {
        "firstName": first_name,
        "lastName": last_name,
        "phone": phone,
        "addressUuid": address_uuid,
    }
    data = _call("POST", "/clients", json_body=body)
    return data.get("uuid") or data.get("id")


def create_shipment(
    sender_address_uuid: str,
    recipient_client_uuid: str,
    weight_kg: float = 1.0,
    declared_value: float = 0.0,
    cod_amount: float = 0.0,
) -> dict:
    """POST /shipments — створює саме відправлення. Повертає {"uuid":..,
    "ttn":..} (назви полів у відповіді — best-effort, звір з PDF). cod_amount
    — накладений платіж (0, якщо клієнт уже передоплатив, як і moneyback у
    build_toysi_order() для Нової Пошти)."""
    body = {
        "senderAddressUuid": sender_address_uuid,
        "recipientClientUuid": recipient_client_uuid,
        "weight": weight_kg,
        "declaredValue": declared_value,
        "codAmount": cod_amount,
    }
    data = _call("POST", "/shipments", json_body=body)
    return {
        "shipment_uuid": data.get("uuid") or data.get("id"),
        "ttn": data.get("ttn") or data.get("barcode") or data.get("ekn"),
    }


def get_sticker(shipment_uuid: str) -> bytes:
    """GET /shipments/{uuid}/sticker — PDF-етикетка готового відправлення.
    Повертає сирі байти PDF (для збереження на диск і подальшого ручного/
    Фаза-2 завантаження в кабінет toysi.ua/lk)."""
    if not UKRPOSHTA_API_KEY:
        raise UkrposhtaAPIError(
            "не заданий UKRPOSHTA_API_KEY — див. повідомлення в _call()."
        )
    url = f"{UKRPOSHTA_API_URL}/shipments/{shipment_uuid}/sticker"
    headers = {"Authorization": f"Bearer {UKRPOSHTA_API_KEY}"}
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise UkrposhtaAPIError(f"помилка з'єднання (GET /shipments/{shipment_uuid}/sticker): {e}") from e
    return response.content


def create_shipment_with_label(
    recipient_first_name: str,
    recipient_last_name: str,
    recipient_phone: str,
    recipient_city: str,
    recipient_postcode: str,
    recipient_street: str = "",
    recipient_building: str = "",
    weight_kg: float = 1.0,
    declared_value: float = 0.0,
    cod_amount: float = 0.0,
) -> dict:
    """Повний ланцюжок для одного замовлення: адреса відправника (Toysi) +
    адреса отримувача -> клієнт-отримувач -> відправлення -> етикетка.
    Повертає {"ttn":.., "shipment_uuid":.., "sticker_pdf": bytes}.

    Піднімає UkrposhtaAPIError на будь-якому кроці — виклик з order_router.py
    має ловити цей виняток і НЕ позначати замовлення як оброблене (аналогічно
    NovaPoshtaAPIError у build_toysi_order())."""
    _check_sender_address_complete()
    sender_uuid = create_address(
        TOYSI_SENDER_ADDRESS["city"], TOYSI_SENDER_ADDRESS["postcode"],
        TOYSI_SENDER_ADDRESS["street"], TOYSI_SENDER_ADDRESS["building"],
    )
    recipient_address_uuid = create_address(recipient_city, recipient_postcode, recipient_street, recipient_building)
    recipient_client_uuid = create_client(recipient_first_name, recipient_last_name, recipient_phone, recipient_address_uuid)

    shipment = create_shipment(
        sender_uuid, recipient_client_uuid,
        weight_kg=weight_kg, declared_value=declared_value, cod_amount=cod_amount,
    )
    sticker_pdf = get_sticker(shipment["shipment_uuid"])

    return {
        "ttn": shipment["ttn"],
        "shipment_uuid": shipment["shipment_uuid"],
        "sticker_pdf": sticker_pdf,
    }


if __name__ == "__main__":
    if not UKRPOSHTA_API_KEY:
        print(
            "[ukrposhta_client] UKRPOSHTA_API_KEY відсутній у .env — каркас готовий, "
            "але викликати API поки нічим. Додай токен, коли власник зареєструє "
            "бізнес-акаунт (dev.ukrposhta.ua)."
        )
        sys.exit(0)

    try:
        result = create_shipment_with_label(
            recipient_first_name="Тест",
            recipient_last_name="Тестенко",
            recipient_phone="380501234567",
            recipient_city="Львів",
            recipient_postcode="79000",
        )
        print(f"ТТН: {result['ttn']}, shipment_uuid: {result['shipment_uuid']}, "
              f"етикетка: {len(result['sticker_pdf'])} байт PDF")
    except UkrposhtaAPIError as e:
        print(f"[ukrposhta_client] Помилка: {e}", file=sys.stderr)
