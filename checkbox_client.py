"""
checkbox_client.py — Checkbox API клієнт: фіскалізація чеків для замовлень.

ВИПРАВЛЕНО (2026-07-22, термінове розслідування — 5 реальних замовлень без
чеків, штраф ФОП за невидачу 100-150% вартості товару): register_ettn()
(нижче, ЕТТН-прив'язка до Нової Пошти) структурно НЕ МОЖЕ працювати для цієї
дропшип-моделі — підтверджено, не лише запідозрено:
  1. `InternetDocument.getDocumentList` (наш власний NP_API_KEY, весь
     період 01.07-22.07.2026) повернув 0 документів — ми не створюємо
     ЖОДНОЇ накладної під власним акаунтом НП взагалі.
  2. Офіційна інструкція Checkbox (wiki.checkbox.ua/.../nova-poshta)
     підтверджує: інтеграція вимагає ВЛАСНОГО активного контракту з Nova
     Pay, якого в PlutusToys немає (Toysi — фулфілмент-партнер, створює
     ТТН під СВОЇМ акаунтом НП).
Це саме той ризик, що вже був задокументований нижче (register_ettn()),
тепер підтверджений на 100%, не лише запідозрений.

Систвін фікс: `create_receipt()` — прямий чек продажу через
POST /receipts/sell (офіційний Swagger, api.checkbox.in.ua/api/docs),
ЗОВСІМ БЕЗ прив'язки до ЕТТН/Нової Пошти — потребує лише CHECKBOX_API_KEY +
CHECKBOX_CASHIER_PIN (CHECKBOX_NP_API_KEY НЕ потрібен цій функції).
Тригер — вже наявні сигнали в order_status_tracker.py: payment_confirmed
(передоплата) чи nova_poshta.get_tracking_status(ttn)["delivered"] (накладений
платіж, той самий сигнал, що й _maybe_push_delivered_to_prom, PR #88) —
жодна з цих подій не залежить від того, чиїм токеном НП створено накладну.

⚠️ ЖОДНОГО ТЕСТОВОГО РЕЖИМУ ДЛЯ ЦЬОГО ПОТОКУ НЕ ПЕРЕВІРЕНО: загальний
демо-режим Checkbox існує (wiki.checkbox.ua/uk/portal/test-data, окремі
тестова каса/касир з'являються одразу після реєстрації акаунта), але в
цьому кабінеті (ФОП Чечетенко Олександр Юрійович) такого тестового запису
в розділах "Каси"/"Касири" не знайдено — ПЕРШИЙ живий виклик create_receipt()
створить РЕАЛЬНИЙ, юридично значущий фіскальний чек.

Авторизація (підтверджено офіційним Swagger, не best-effort):
  - CHECKBOX_API_KEY — ліцензійний ключ каси (заголовок X-License-Key).
  - CHECKBOX_CASHIER_PIN — PIN-код касира (POST /cashier/signinPinCode
    {"pin_code": ...} -> JWT access_token для наступних викликів).
  - CHECKBOX_NP_API_KEY — потрібен ЛИШЕ старій register_ettn() (нижче,
    залишена як історичний контекст і задокументований глухий кут — не
    видаляю функцію повністю, щоб не загубити саме дослідження, але
    order_status_tracker.py більше її НЕ викликає).

🔴 register_ettn() нижче — ЗБЕРЕЖЕНО ЛИШЕ як задокументований архітектурний
глухий кут (див. підтвердження вище), НЕ використовується жодним викликом у
проєкті з 2026-07-22. Оригінальний ризик-опис лишається нижче для історії."""

import os
import sys
import time

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

CHECKBOX_API_KEY    = os.environ.get("CHECKBOX_API_KEY", "")
CHECKBOX_CASHIER_PIN = os.environ.get("CHECKBOX_CASHIER_PIN", "")
CHECKBOX_NP_API_KEY  = os.environ.get("CHECKBOX_NP_API_KEY", "")
CHECKBOX_API_URL    = "https://api.checkbox.in.ua/api/v1"  # підтверджено офіційним Swagger (api.checkbox.in.ua/api/docs)
REQUEST_TIMEOUT     = 30

# Допустима похибка при звірці суми чека із заявленою сумою замовлення —
# лише захист від помилок округлення float, НЕ послаблення самої вимоги
# точного збігу.
AMOUNT_TOLERANCE = 0.01

# POST /shifts асинхронний (202 Accepted, підтверджено Swagger) — зміна
# проходить CREATED -> OPENING -> OPENED за лаштунками. Опитуємо
# GET /cashier/shift, доки статус не стане OPENED, з розумним лімітом —
# якщо не відкрилась (протермінований КЕП касира, несплачений рахунок за
# касу — офіційна документація прямо називає ці 2 причини), не висіти
# вічно, кинути явну помилку.
SHIFT_OPEN_POLL_ATTEMPTS = 10
SHIFT_OPEN_POLL_INTERVAL_SEC = 3


class CheckboxAPIError(Exception):
    """Сам запит до Checkbox API не вдався (немає ключа/PIN, мережева
    помилка, невалідна відповідь, чи явна відмова сервера) — аналог
    NovaPoshtaAPIError/UkrposhtaAPIError."""


def _require_credentials() -> None:
    missing = [
        name for name, value in (
            ("CHECKBOX_API_KEY", CHECKBOX_API_KEY),
            ("CHECKBOX_CASHIER_PIN", CHECKBOX_CASHIER_PIN),
            ("CHECKBOX_NP_API_KEY", CHECKBOX_NP_API_KEY),
        ) if not value
    ]
    if missing:
        raise CheckboxAPIError(
            f"не задано: {', '.join(missing)}. Додайте у .env, коли власник надасть "
            "(PIN касира — окремо в чат, не через .env вручну, за домовленістю)."
        )


def _require_receipt_credentials() -> None:
    """create_receipt() (POST /receipts/sell) потребує ЛИШЕ ці два ключі —
    на відміну від _require_credentials() нижче (уся трійка, для старої
    register_ettn()), CHECKBOX_NP_API_KEY тут не задіяний узагалі."""
    missing = [
        name for name, value in (
            ("CHECKBOX_API_KEY", CHECKBOX_API_KEY),
            ("CHECKBOX_CASHIER_PIN", CHECKBOX_CASHIER_PIN),
        ) if not value
    ]
    if missing:
        raise CheckboxAPIError(
            f"не задано: {', '.join(missing)}. Додайте у .env, коли власник надасть "
            "(PIN касира — окремо в чат, не через .env вручну, за домовленістю)."
        )


def _authenticate_cashier() -> str:
    """POST /api/v1/cashier/signinPinCode (підтверджено офіційним Swagger,
    api.checkbox.in.ua/api/docs) — X-License-Key заголовок + {"pin_code":...}
    тіло, повертає access_token (Cashier JWT). Потрібні лише API_KEY+PIN,
    не NP-ключ — виклики цієї функції (create_receipt і стара register_ettn)
    самі перевіряють свій повний набір credentials окремо."""
    _require_receipt_credentials()
    try:
        response = requests.post(
            f"{CHECKBOX_API_URL}/cashier/signinPinCode",
            headers={"X-License-Key": CHECKBOX_API_KEY},
            json={"pin_code": CHECKBOX_CASHIER_PIN},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise CheckboxAPIError(f"помилка з'єднання (автентифікація касира): {e}") from e

    try:
        data = response.json()
    except ValueError:
        raise CheckboxAPIError(f"невалідна відповідь (не JSON) при автентифікації касира: {response.text[:300]}")

    token = data.get("access_token") or data.get("token")
    if not token:
        raise CheckboxAPIError(f"відповідь автентифікації касира без токена: {data}")
    return token


def _ensure_shift_open(token: str) -> None:
    """GET /cashier/shift -> якщо статус уже OPENED, нічого не робити.
    Інакше POST /shifts (асинхронний, 202 Accepted — Swagger підтверджує
    ланцюжок CREATED -> OPENING -> OPENED) і опитуємо GET /cashier/shift,
    доки статус не стане OPENED чи не вичерпається ліміт спроб.

    Офіційна документація Checkbox прямо називає 2 причини, чому зміна може
    НЕ відкритись: несплачений рахунок за касу, протермінований КЕП касира
    (wiki.checkbox.ua/.../nova-poshta, розділ "Контроль статусу відправлень")
    — у цьому разі кидаємо явну помилку з посиланням на обидві причини,
    не мовчазний нескінченний цикл."""
    headers = {"X-License-Key": CHECKBOX_API_KEY, "Authorization": f"Bearer {token}"}

    def _current_status() -> str | None:
        try:
            response = requests.get(f"{CHECKBOX_API_URL}/cashier/shift", headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as e:
            raise CheckboxAPIError(f"помилка з'єднання (GET /cashier/shift): {e}") from e
        if response.status_code == 404:
            return None
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise CheckboxAPIError(f"помилка перевірки зміни касира: {e}") from e
        try:
            data = response.json()
        except ValueError:
            raise CheckboxAPIError(f"невалідна відповідь (не JSON) при перевірці зміни: {response.text[:300]}")
        # ВИПРАВЛЕНО (2026-07-22, живий крах усіх 6 замовлень на першому реальному
        # прогоні — "'NoneType' object has no attribute 'get'"): коли зміни нема,
        # Checkbox повертає HTTP 200 з тілом ЛІТЕРАЛЬНО "null" (json() -> Python
        # None), НЕ 404, як припускав код досі — .get("status") на None валив
        # кожен виклик _ensure_shift_open() без жодного шансу відкрити зміну чи
        # видати чек. Живо підтверджено прямим запитом.
        return data.get("status") if data else None

    if _current_status() == "OPENED":
        return

    try:
        open_response = requests.post(f"{CHECKBOX_API_URL}/shifts", headers=headers, json={}, timeout=REQUEST_TIMEOUT)
        open_response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise CheckboxAPIError(f"помилка з'єднання (POST /shifts, відкриття зміни): {e}") from e

    for _ in range(SHIFT_OPEN_POLL_ATTEMPTS):
        time.sleep(SHIFT_OPEN_POLL_INTERVAL_SEC)
        if _current_status() == "OPENED":
            return

    raise CheckboxAPIError(
        "зміна касира не відкрилась за відведений час — можливі причини (офіційна "
        "документація Checkbox): несплачений рахунок за касу, протермінований КЕП "
        "касира. Перевір розділ \"Каси\"/\"Касири\" в кабінеті Checkbox."
    )


def create_receipt(
    goods: list,
    payment_type: str,
    total_amount: float,
    order_id: str | None = None,
) -> dict:
    """POST /api/v1/receipts/sell (підтверджено офіційним Swagger,
    api.checkbox.in.ua/api/docs) — прямий чек продажу, БЕЗ жодної залежності
    від Нової Пошти/ЕТТН (на відміну від register_ettn() нижче, яка
    структурно не може працювати для цієї дропшип-моделі — див. докстрінг
    файлу). Саме ця функція замінює register_ettn() як основний шлях
    фіскалізації (order_status_tracker.py, _maybe_issue_receipt()).

    goods — [{"code":.., "name":.., "price":.., "qty":..}, ...] у гривнях і
    штуках — конвертація в копійки (Checkbox: "Вартість в копійках за
    quantity = 1000") відбувається тут, не на виклику.
    payment_type — "CASH" (накладений платіж — гроші фактично отримані при
    видачі посилки) чи "CASHLESS" (передоплата карткою/через Prom).
    total_amount — сума чека в гривнях, МАЄ дорівнювати сумі goods —
    перевіряється ДО мережевого запиту (той самий принцип точного збігу,
    що й був у register_ettn(), хоч сама вимога Checkbox для /receipts/sell
    цього явно не документує — зайва перевірка тут не шкодить, лише
    підтверджує, що ми не надсилаємо суму, яка розходиться з тим, що самі
    порахували)."""
    _require_receipt_credentials()

    goods_sum = round(sum(item["price"] * item.get("qty", 1) for item in goods), 2)
    if abs(goods_sum - round(total_amount, 2)) > AMOUNT_TOLERANCE:
        raise CheckboxAPIError(
            f"Сума товарів чека ({goods_sum} грн) не збігається із заявленою сумою "
            f"замовлення ({total_amount} грн) — виклик заблоковано до мережевого запиту."
        )

    token = _authenticate_cashier()
    _ensure_shift_open(token)

    body = {
        "goods": [
            {
                "good": {
                    "code": str(item.get("code") or item["name"])[:50],
                    "name": item["name"][:200],
                    "price": round(item["price"] * 100),
                },
                "quantity": round(item.get("qty", 1) * 1000),
            }
            for item in goods
        ],
        "payments": [{"type": payment_type, "value": round(total_amount * 100)}],
    }
    if order_id:
        body["order_id"] = str(order_id)

    headers = {"X-License-Key": CHECKBOX_API_KEY, "Authorization": f"Bearer {token}"}
    try:
        response = requests.post(
            f"{CHECKBOX_API_URL}/receipts/sell",
            headers=headers,
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise CheckboxAPIError(f"помилка з'єднання (POST /receipts/sell, замовлення {order_id}): {e}") from e

    try:
        return response.json()
    except ValueError:
        raise CheckboxAPIError(f"невалідна відповідь (не JSON) для /receipts/sell: {response.text[:300]}")


def register_ettn(
    carrier: str,
    ttn: str,
    receipt_items: list,
    payment_control_amount: float,
) -> dict:
    """POST /api/v1/ettn — реєструє ЕТТН для КОНКРЕТНОГО відправлення
    (одне замовлення = один виклик, НЕ одноразове налаштування проєкту).

    carrier — "nova_poshta" чи "ukrposhta" (те саме значення, що
    orders.carrier в orders_db.py).
    ttn — номер ТТН перевізника, до якого прив'язується чек.
    receipt_items — [{"name":.., "price":.., "quantity":..}, ...], best-effort
    форма (звір з офіційною документацією перед першим реальним викликом).
    payment_control_amount — сума контролю оплати на ТТН (для carrier=
    nova_poshta — фактично сума накладеного платежу, яку ми передали Toysi
    як moneyback при order_create).

    Сума receipt_items МАЄ ТОЧНО збігатися з payment_control_amount —
    перевіряється ДО мережевого запиту (документація Checkbox: "Сума чека
    зі знижками повинна повністю співпадати із сумою контролю оплати у
    ТТН"), щоб розбіжність не пішла в реальний фіскальний виклик.

    ЗБЕРЕЖЕНО ЛИШЕ як задокументований архітектурний глухий кут (див.
    докстрінг файлу) — жоден виклик у проєкті більше не використовує цю
    функцію з 2026-07-22, замінена на create_receipt() вище."""
    _require_credentials()
    receipt_sum = round(sum(item["price"] * item["quantity"] for item in receipt_items), 2)
    if abs(receipt_sum - round(payment_control_amount, 2)) > AMOUNT_TOLERANCE:
        raise CheckboxAPIError(
            f"Сума чека ({receipt_sum} грн) НЕ збігається із сумою контролю оплати "
            f"ТТН {ttn} ({payment_control_amount} грн) — Checkbox вимагає точного "
            "збігу, виклик заблоковано до мережевого запиту."
        )

    employee_token = _authenticate_cashier()

    body = {
        "provider": carrier,
        "ttn": ttn,
        "np_api_key": CHECKBOX_NP_API_KEY,
        "receipt_body": {
            "goods": receipt_items,
            "payments": [{"type": "ETTN", "value": receipt_sum}],
        },
    }
    try:
        response = requests.post(
            f"{CHECKBOX_API_URL}/ettn",
            headers={
                "X-License-Key": CHECKBOX_API_KEY,
                "Authorization": f"Bearer {employee_token}",
            },
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise CheckboxAPIError(f"помилка з'єднання (POST /ettn, ТТН {ttn}): {e}") from e

    try:
        return response.json()
    except ValueError:
        raise CheckboxAPIError(f"невалідна відповідь (не JSON) для ТТН {ttn}: {response.text[:300]}")


if __name__ == "__main__":
    try:
        _require_receipt_credentials()
        print(
            "[checkbox_client] CHECKBOX_API_KEY/CHECKBOX_CASHIER_PIN задані — "
            "create_receipt() готова. Жодного мережевого запиту не виконано "
            "(викликається лише з order_status_tracker.py, _maybe_issue_receipt(), "
            "на реальному підтвердженні оплати/доставки)."
        )
    except CheckboxAPIError as e:
        print(f"[checkbox_client] {e}", file=sys.stderr)
