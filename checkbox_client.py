"""
checkbox_client.py — Checkbox API клієнт: автофіскалізація чеків при видачі
товару, доставленого Новою Поштою чи Укрпоштою (ЕТТН), Крок ... плану,
2026-07-09.

Замінює й генералізує те, що раніше було в checkbox_ukrposhta.py (PR #15,
2026-07-09) — тодішнє припущення, що ЕТТН реєструється ОДИН РАЗ як
"проєкт", виявилось неточним. Насправді реєстрація — на КОЖНЕ відправлення
(ТТН) окремо, з сумою чека, що МАЄ ТОЧНО збігатися із сумою контролю оплати
на ТТН (документація Checkbox: "Сума чека зі знижками повинна повністю
співпадати із сумою контролю оплати у ТТН").

⚠️ ЖОДНОГО ТЕСТОВОГО/SANDBOX РЕЖИМУ НЕ ІСНУЄ (підтверджено дослідженням
публічної документації Checkbox, code_report_2026-07-09_pt4.md): "Testing
is only possible on a real fiscal register with a real Nova Poshta
waybill" — тобто ПЕРШИЙ живий виклик register_ettn() СТВОРИТЬ РЕАЛЬНИЙ,
юридично значущий фіскальний чек. За дорученням власника (2026-07-09) —
у ЦІЙ задачі лише каркас, без жодного живого виклику; перший реальний
виклик відбудеться природно на кроці 9 (наскрізний тест власника на
реальному замовленні), НЕ раніше і НЕ автоматично звідси.

Авторизація (best-effort — немає під рукою офіційної документації з
точними назвами полів/заголовків, звір і скоригуй перед першим реальним
викликом):
  - CHECKBOX_API_KEY — ліцензійний ключ каси (X-License-Key чи
    аналогічний заголовок).
  - CHECKBOX_CASHIER_PIN — PIN-код касира, потрібен для автентифікації
    конкретного співробітника/відкриття зміни (Checkbox зазвичай
    вимагає це окремо від ліцензійного ключа каси).
  - CHECKBOX_NP_API_KEY — ключ Нової Пошти, прив'язаний до ФОП у кабінеті
    Checkbox (2026-07-09: "Токен НП успішно прив'язано в Checkbox" —
    власник підтвердив, що це ОКРЕМИЙ від NP_API_KEY ключ/акаунт, не той,
    що використовує nova_poshta.py для пошуку міст/відділень).

Запуск: НЕ передбачений як самостійний скрипт (register_ettn() викликає
лише order_status_tracker.py, коли з'являється реальний ТТН) — прямий
`python checkbox_client.py` нижче лише перевіряє наявність ключів,
жодного мережевого запиту не робить.

🔴 СЕРЙОЗНИЙ АРХІТЕКТУРНИЙ РИЗИК (дослідження, 2026-07-09, НЕ підтверджено
живим тестом — sandbox немає, дивись вище): ТТН для carrier=nova_poshta
СТВОРЮЄ TOYSI (виклик order_create з shipping_carrier_name="Новая почта" —
Toysi сама створює відправлення НП на СВОЄМУ боці, під СВОЇМ акаунтом
Нової Пошти; ми не передаємо Toysi жодного власного NP-ключа чи
контрагента). Публічна документація й форуми користувачів Checkbox
одностайно стверджують: ЕТТН-механізм Checkbox ЯВНО перевіряє, що ТТН
створено ТИМ САМИМ API-токеном НП, який використовується для реєстрації
чека — "накладну, створену на чужий токен", підв'язати НЕ можна
("Не найдено накладной: перевірте номер ТТН або чи не створена вона на
інший API-токен"). Оскільки токен CHECKBOX_NP_API_KEY належить НАШОМУ
(ФОП) акаунту, а не Toysi — register_ettn() для замовлень, де ТТН
створено Toysi, МОЖЕ НЕ СПРАЦЮВАТИ ВЗАГАЛІ через цю саме причину.
Це НЕ підтверджено на 100% (жодного sandbox для перевірки немає), але
достатньо задокументовано в незалежних джерелах, щоб вважати реальним
ризиком. Перед тим, як покладатись на цю автоматизацію — варто уточнити
в підтримки Checkbox, чи є спосіб прив'язати ТТН стороннього
постачальника (дропшип-модель), чи цей шлях у принципі непридатний для
поточної архітектури (Toysi як фулфілмент-партнер, не ми, створює
накладну)."""

import os
import sys

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

CHECKBOX_API_KEY    = os.environ.get("CHECKBOX_API_KEY", "")
CHECKBOX_CASHIER_PIN = os.environ.get("CHECKBOX_CASHIER_PIN", "")
CHECKBOX_NP_API_KEY  = os.environ.get("CHECKBOX_NP_API_KEY", "")
CHECKBOX_API_URL    = "https://api.checkbox.in.ua/api/v1"  # TODO: звір з офіційною документацією/підтримкою
REQUEST_TIMEOUT     = 30

# Допустима похибка при звірці суми чека із сумою контролю оплати ТТН —
# лише захист від помилок округлення float, НЕ послаблення самої вимоги
# точного збігу.
AMOUNT_TOLERANCE = 0.01


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


def _authenticate_cashier() -> str:
    """Автентифікація касира за PIN — best-effort (немає під рукою точної
    назви ендпоінту/полів). Повертає токен/ідентифікатор сесії касира для
    подальших запитів. TODO: звір з офіційною документацією Checkbox."""
    _require_credentials()
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
    ТТН"), щоб розбіжність не пішла в реальний фіскальний виклик."""
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
        _require_credentials()
        print(
            "[checkbox_client] Усі ключі задані — каркас готовий. Жодного мережевого "
            "запиту не виконано (register_ettn() викликається лише з "
            "order_status_tracker.py, на реальному ТТН)."
        )
    except CheckboxAPIError as e:
        print(f"[checkbox_client] {e}", file=sys.stderr)
