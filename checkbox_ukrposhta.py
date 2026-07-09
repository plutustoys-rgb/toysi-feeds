"""
checkbox_ukrposhta.py — реєстрація ЕТТН-проєкту Checkbox для доставки
Укрпоштою (POST /api/v1/ettn), одразу після появи UKRPOSHTA_API_KEY і
першого реального відправлення (ukrposhta_client.py, Крок 5 плану).

РУЧНА ДІЯ ВЛАСНИКА (не автоматизується тут, за завданням — "один клік,
без токена"): увімкнути "Доставка Укрпошта" в кабінеті Checkbox ПЕРЕД
викликом register_ettn_project() нижче — це перемикач у веб-інтерфейсі,
без API. Без цього кроку виклик, найімовірніше, поверне помилку
(не перевірено без токена Checkbox).

⚠️ ВІДКРИТЕ ПИТАННЯ — "Контроль оплати" для Укрпошти (перевірено research,
НЕ підтверджено з живим токеном/офіційним акаунтом):
  Дослідження публічної документації Checkbox (wiki.checkbox.ua/api/np,
  checkbox.ua/blog) дає ПОМІРНО ВПЕВНЕНИЙ (не 100%) висновок — "Контроль
  оплати" НЕ є NP-специфічним: документація explicitly перелічує "Спосіб
  оплати: Експрес накладна (НП)/Укрпошта/Meest ПОШТА" як варіанти одного
  й того ж механізму, і вимога "з контролем оплати" сформульована як
  загальна ("в обох випадках" створення ТТН — і через бізнес-кабінет, і
  на відділенні), а не як щось унікальне для НП. Один конкретний рядок
  документації таки супроводжує фразу "Контроль оплати" приміткою
  "(Нова Пошта)" — найімовірніше, це позначає РОЗДІЛ/вкладку кабінету
  Checkbox, звідки взято приклад, а не виключність механізму. Повний
  текст офіційної документації (wiki.checkbox.ua) — SPA-сайт на JS,
  недоступний для автоматичного читання; перевір власноруч на
  wiki.checkbox.ua/api/np або звернись у підтримку Checkbox перед тим,
  як покладатись на однакову поведінку без застережень.

⚠️ Точний шлях ендпоінту ТЕЖ під питанням: дослідження публічних джерел
показало реальний приклад `/api/v1/np/ettn` (із "np" в шляху), а не
голий `/api/v1/ettn` — можливо, шлях історично так називається, але
використовується для БУДЬ-ЯКОГО провайдера через поле payment/type у
тілі запиту (сумісно з тим, що Укрпошта явно підтримується тим самим
механізмом), а можливо, для Укрпошти є окремий шлях. Нижче — обидва
варіанти, звір з реальною документацією/підтримкою, коли буде токен.

Запуск (лише після токена Checkbox і УВІМКНЕННЯ Укрпошти вручну в кабінеті):
    python checkbox_ukrposhta.py
"""

import os
import sys

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

CHECKBOX_API_KEY = os.environ.get("CHECKBOX_API_KEY", "")
CHECKBOX_API_URL = "https://api.checkbox.in.ua/api/v1"  # TODO: звір з кабінетом/підтримкою Checkbox
REQUEST_TIMEOUT  = 30

# За завданням — "/api/v1/ettn"; дослідження публічних джерел натомість
# показало приклад "/api/v1/np/ettn" (з "np" в шляху) — не підтверджено,
# який саме правильний без токена. TODO: звір перед першим реальним викликом.
ETTN_PATH = "/ettn"


class CheckboxAPIError(Exception):
    """Сам запит до Checkbox API не вдався (немає ключа, мережева помилка,
    невалідна відповідь, чи явна відмова сервера)."""


def register_ettn_project(carrier: str = "ukrposhta") -> dict:
    """POST /api/v1/ettn (чи /api/v1/np/ettn — див. застереження вище) —
    реєструє ЕТТН-проєкт для перевізника. Виконати ОДРАЗУ ПІСЛЯ того, як
    власник вручну увімкнув "Доставка Укрпошта" в кабінеті Checkbox.

    Поля тіла запиту — best-effort (немає під рукою офіційної документації
    Checkbox із точними назвами полів) — звір і скоригуй, коли з'явиться
    токен і доступ до кабінету."""
    if not CHECKBOX_API_KEY:
        raise CheckboxAPIError(
            "не заданий CHECKBOX_API_KEY. Додайте CHECKBOX_API_KEY=... у .env, "
            "коли буде доступ до кабінету Checkbox."
        )

    body = {"provider": carrier}
    try:
        response = requests.post(
            f"{CHECKBOX_API_URL}{ETTN_PATH}",
            headers={"Authorization": f"Bearer {CHECKBOX_API_KEY}"},
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise CheckboxAPIError(f"помилка з'єднання (POST {ETTN_PATH}): {e}") from e

    try:
        return response.json()
    except ValueError:
        raise CheckboxAPIError(f"невалідна відповідь (не JSON): {response.text[:300]}")


if __name__ == "__main__":
    if not CHECKBOX_API_KEY:
        print(
            "[checkbox_ukrposhta] CHECKBOX_API_KEY відсутній у .env — каркас готовий, "
            "але викликати API поки нічим. Не забудь спершу вручну увімкнути "
            "\"Доставка Укрпошта\" в кабінеті Checkbox."
        )
        sys.exit(0)
    try:
        result = register_ettn_project()
        print(f"ЕТТН-проєкт зареєстровано: {result}")
    except CheckboxAPIError as e:
        print(f"[checkbox_ukrposhta] Помилка: {e}", file=sys.stderr)
