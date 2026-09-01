"""check_receipt.py — разова жива звірка ОДНОГО фіскального чека Checkbox за фіскальним кодом.

НАВІЩО (запит бухгалтера КОДВ #3, 2026-08-30): вона зобов'язана живо звіряти кандидатів доходу
(checkbox_registry_sync.py) перед записом у книгу, АЛЕ веб-кабінет Checkbox (my.checkbox.ua)
ненадійний — пошук за номером чека не знаходить навіть завідомо правильний код, фільтр дати не
фільтрує, меню зміни не відкривається. Дані при цьому ПРАВИЛЬНІ (API працює) — ламається лише
ручний веб-шлях. Цей хелпер дає надійну звірку одного чека прямо через API, повз зламаний вебUI.

READ-ONLY: лише GET /receipts/{fiscal_code} (той самий клієнт+авторизація, що create_receipt/
checkbox_registry_sync). Зміну НЕ відкриває, чеків НЕ створює.

Живо звірено 2026-09-01: Checkbox приймає фіскальний код прямо як id у GET /receipts/{code}
(приклад бухгалтера yNI_3LAdfpE → 276.00 грн, DONE).

ЗАПУСК: python check_receipt.py <фіскальний_код>   (напр. yNI_3LAdfpE)
Креди: CHECKBOX_API_KEY + CHECKBOX_CASHIER_PIN у .env (ті самі, що вже вживає каса).
"""
import sys

import requests

import checkbox_client as cb

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TIMEOUT = 25


def _uah(kop) -> str:
    """Копійки Checkbox → грн (27600 → '276.00')."""
    try:
        return f"{int(kop) / 100:.2f}"
    except (TypeError, ValueError):
        return str(kop)


def fetch_receipt(fiscal_code: str) -> dict:
    if not cb.CHECKBOX_API_KEY or not cb.CHECKBOX_CASHIER_PIN:
        raise cb.CheckboxAPIError("нема CHECKBOX_API_KEY/CHECKBOX_CASHIER_PIN у .env — звірка неможлива")
    token = cb._authenticate_cashier()
    headers = {"X-License-Key": cb.CHECKBOX_API_KEY, "Authorization": f"Bearer {token}"}
    try:
        r = requests.get(f"{cb.CHECKBOX_API_URL}/receipts/{fiscal_code}", headers=headers, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise cb.CheckboxAPIError(f"помилка з'єднання (GET /receipts/{fiscal_code}): {e}") from e
    if r.status_code in (404, 422):
        # 404 = нема такого чека; 422 = код не валідного формату (не fiscal_code/uuid)
        raise cb.CheckboxAPIError(f"чек із фіскальним кодом '{fiscal_code}' не знайдено "
                                  f"(HTTP {r.status_code} — перевір, що код правильний)")
    if r.status_code != 200:
        raise cb.CheckboxAPIError(f"GET /receipts/{fiscal_code} → HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError:
        raise cb.CheckboxAPIError(f"невалідна відповідь (не JSON): {r.text[:200]}")


def print_receipt(rec: dict) -> None:
    print("=" * 52)
    print(f"Фіскальний код : {rec.get('fiscal_code') or rec.get('id')}")
    print(f"Статус         : {rec.get('status')}  (тип {rec.get('type')})")
    print(f"Дата/час       : {rec.get('fiscal_date') or rec.get('created_at')}")
    print(f"СУМА ЧЕКА      : {_uah(rec.get('total_sum'))} грн")
    # оплати
    pays = rec.get("payments") or []
    if pays:
        parts = []
        for p in pays:
            label = {"CASH": "готівка", "CASHLESS": "картка"}.get(str(p.get("type")), str(p.get("type")))
            parts.append(f"{label} {_uah(p.get('value'))}")
        print(f"Оплата         : {', '.join(parts)}")
    # позиції
    goods = rec.get("goods") or []
    if goods:
        print("Позиції:")
        for g in goods:
            good = g.get("good") or {}
            name = good.get("name") or good.get("code") or "?"
            qty = g.get("quantity")
            qty_h = (int(qty) / 1000) if isinstance(qty, int) else qty  # Checkbox qty у тисячних
            print(f"   • {str(name)[:48]:48}  x{qty_h}  = {_uah(g.get('sum'))} грн")
    print("=" * 52)
    print("(READ-ONLY звірка з каси — книгу пише лише бухгалтер)")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print("Вжиток: python check_receipt.py <фіскальний_код>   (напр. yNI_3LAdfpE)", file=sys.stderr)
        return 2
    fiscal_code = sys.argv[1].strip()
    try:
        rec = fetch_receipt(fiscal_code)
    except cb.CheckboxAPIError as e:
        print(f"[check-receipt] {e}", file=sys.stderr)
        return 1
    print_receipt(rec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
