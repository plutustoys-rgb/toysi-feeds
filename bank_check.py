import os
import sys
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

from orders_db import get_connection, get_orders_awaiting_payment, mark_payment_confirmed, update_delivery_status

load_dotenv()

PRIVAT_AUTOCLIENT_ID    = os.environ.get("PRIVAT_AUTOCLIENT_ID", "")
PRIVAT_AUTOCLIENT_TOKEN = os.environ.get("PRIVAT_AUTOCLIENT_TOKEN", "")
PRIVAT_IBAN             = os.environ.get("PRIVAT_IBAN", "")

PRIVAT_API_URL   = "https://acp.privatbank.ua/api/statements/transactions"
REQUEST_TIMEOUT  = 20
AMOUNT_TOLERANCE = 1.0   # грн, допустиме відхилення суми (округлення/комісії)
LOOKBACK_DAYS    = 3     # за скільки днів назад тягнути виписку

BANK_AVAILABLE = bool(PRIVAT_AUTOCLIENT_ID and PRIVAT_AUTOCLIENT_TOKEN and PRIVAT_IBAN)


def fetch_transactions(start_date: datetime, end_date: datetime, limit: int = 100) -> list:
    """
    Тягне виписку по рахунку через Автоклієнт API Приват24 для бізнесу
    (https://acp.privatbank.ua/api/statements/transactions, POST, заголовки id+token).
    Налаштування Автоклієнта — вручну власником: Приват24 для бізнесу ->
    Каталог послуг -> Інтеграція (Автоклієнт), окремо для потрібного IBAN.
    """
    if not BANK_AVAILABLE:
        return []

    headers = {"id": PRIVAT_AUTOCLIENT_ID, "token": PRIVAT_AUTOCLIENT_TOKEN}
    all_transactions = []
    follow_id = None

    while True:
        params = {
            "acc": PRIVAT_IBAN,
            "startDate": start_date.strftime("%d-%m-%Y"),
            "endDate": end_date.strftime("%d-%m-%Y"),
            "limit": limit,
        }
        if follow_id:
            params["followId"] = follow_id

        try:
            response = requests.post(PRIVAT_API_URL, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"[bank_check] Помилка з'єднання з ПриватБанк API: {e}", file=sys.stderr)
            break

        try:
            data = response.json()
        except ValueError:
            print(f"[bank_check] Невалідна відповідь (не JSON): {response.text[:300]}", file=sys.stderr)
            break

        if data.get("ErrorCode"):
            print(f"[bank_check] ПриватБанк API помилка {data['ErrorCode']}: {data.get('ErrorMessage')}", file=sys.stderr)
            break

        all_transactions.extend(data.get("Transactions", []))

        if data.get("HasPagination") and data.get("NextPageId"):
            follow_id = data["NextPageId"]
        else:
            break

    return all_transactions


def _transaction_amount(tx: dict) -> float:
    for key in ("SUM", "sum", "Sum", "AMOUNT", "amount"):
        if key in tx:
            try:
                return float(str(tx[key]).replace(",", "."))
            except (TypeError, ValueError):
                continue
    return 0.0


def match_payment(order: dict, transactions: list) -> dict:
    """Шукає надходження, що збігається за сумою (± AMOUNT_TOLERANCE) із сумою замовлення."""
    order_total = sum(item.get("price", 0) * item.get("qty", 1) for item in order["items"])
    for tx in transactions:
        if abs(_transaction_amount(tx) - order_total) <= AMOUNT_TOLERANCE:
            return tx
    return None


def check_pending_prepayments() -> None:
    """
    Проходить замовлення зі статусом "очікує передоплати" (payment_method=prepaid,
    payment_confirmed=0) і звіряє з випискою ПриватБанку.

    Якщо Автоклієнт не підключено (немає PRIVAT_* у .env) — заглушка з плану (Крок 4, п.6):
    позначає замовлення 'awaiting_manual_confirmation', щоб потрапило у щоденний звіт.
    """
    with get_connection() as conn:
        pending = get_orders_awaiting_payment(conn)
        if not pending:
            print("[bank_check] Немає замовлень, що очікують передоплати")
            return

        if not BANK_AVAILABLE:
            print(
                "[bank_check] PRIVAT_AUTOCLIENT_ID/TOKEN/IBAN не задані — автоперевірка вимкнена, "
                "позначаю замовлення для ручного підтвердження",
                file=sys.stderr,
            )
            for order in pending:
                update_delivery_status(conn, order["internal_order_id"], status="awaiting_manual_confirmation")
                print(f"[bank_check] {order['internal_order_id']}: очікує ручного підтвердження")
            return

        end_date = datetime.now()
        transactions = fetch_transactions(end_date - timedelta(days=LOOKBACK_DAYS), end_date)

        for order in pending:
            match = match_payment(order, transactions)
            if match:
                transactions.remove(match)  # захист від повторного використання тієї самої транзакції
                mark_payment_confirmed(conn, order["internal_order_id"])
                print(f"[bank_check] Оплату підтверджено: {order['internal_order_id']}")
            else:
                print(f"[bank_check] Оплата ще не знайдена у виписці: {order['internal_order_id']}")


if __name__ == "__main__":
    check_pending_prepayments()
