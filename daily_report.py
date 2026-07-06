import json
import os
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

from orders_db import get_connection, get_orders_awaiting_payment, init_db
from telegram_notify import send_telegram_message

# Консоль Windows (cp1251) не показує emoji — не заважає systemd/journald на VPS,
# але без цього локальний тестовий запуск падає на print().
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

PROM_API_KEY    = os.environ.get("PROM_API_KEY", "")
ROZETKA_API_KEY = os.environ.get("ROZETKA_API_KEY", "")

LOOKBACK_HOURS = 24

"""
Крок 8 плану. Не вистачає лише балансів Rozetka/Prom (Крок 7 — окрема
інтеграція з балансовими API маркетплейсів, ще не написана, не пов'язана
з order_router.py/order_status_tracker.py) — усе інше з оригінального
переліку Кроку 8 тепер доступне: нові замовлення по платформах, виручка,
скільки передано Toysi, скільки чекає оплати, повернення/відмови,
замовлення з помилками Toysi.
"""


def _source_label(platform: str) -> str:
    """Реальні дані для платформи є, лише якщо задано відповідний API-ключ —
    інакше orders_watcher.py досі повертає мок-замовлення (Крок 3 плану)."""
    key = PROM_API_KEY if platform == "prom" else ROZETKA_API_KEY
    return "реальні дані" if key else "мок-дані (ключа ще немає)"


def _order_total(order_items: list) -> float:
    return sum(item.get("price", 0) * item.get("qty", 1) for item in order_items)


def build_report() -> str:
    init_db()
    since = (datetime.now() - timedelta(hours=LOOKBACK_HOURS)).isoformat(timespec="seconds")

    with get_connection() as conn:
        new_rows = conn.execute(
            "SELECT platform, items FROM orders WHERE created_at >= ?", (since,)
        ).fetchall()

        forwarded_today = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE forwarded_to_toysi_at >= ?", (since,)
        ).fetchone()[0]

        awaiting_bank_check = len(get_orders_awaiting_payment(conn))

        awaiting_manual = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status = 'awaiting_manual_confirmation'"
        ).fetchone()[0]

        toysi_errors = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status = 'toysi_error'"
        ).fetchone()[0]

        returns_cancellations = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE delivery_status IN ('returned', 'cancelled')"
        ).fetchone()[0]

    counts_by_platform = {}
    revenue_by_platform = {}
    for row in new_rows:
        items = json.loads(row["items"])
        counts_by_platform[row["platform"]] = counts_by_platform.get(row["platform"], 0) + 1
        revenue_by_platform[row["platform"]] = revenue_by_platform.get(row["platform"], 0) + _order_total(items)

    total_revenue = sum(revenue_by_platform.values())

    lines = [f"📋 Щоденний звіт PlutusToys — {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"]
    lines.append(f"\nНових замовлень за останні {LOOKBACK_HOURS} год: {len(new_rows)}")

    for platform in ("prom", "rozetka"):
        count = counts_by_platform.get(platform, 0)
        revenue = revenue_by_platform.get(platform, 0)
        lines.append(f"\n  {platform.capitalize()}: {count} на {revenue:.2f} грн ({_source_label(platform)})")

    lines.append(f"\n\nВиручка нових замовлень за {LOOKBACK_HOURS} год: {total_revenue:.2f} грн")
    lines.append(
        "\n  (сума замовлень за собівартістю Toysi + націнка, як у полі \"price\" замовлення — "
        "не фактично отримані гроші, лише вартість оформлених замовлень)"
    )

    lines.append(f"\n\nПередано Toysi за {LOOKBACK_HOURS} год: {forwarded_today}")
    lines.append(f"\nОчікують перевірки оплати (prepaid, ще не підтверджено): {awaiting_bank_check}")
    lines.append(f"\nПозначено \"очікує ручного підтвердження\": {awaiting_manual}")

    if toysi_errors:
        lines.append(f"\n\n🔴 Замовлення з помилками Toysi (потребують уваги): {toysi_errors}")
    if returns_cancellations:
        lines.append(f"\n⚠️ Повернень/скасувань (поточний стан, не лише за добу): {returns_cancellations}")

    lines.append(
        "\n\nℹ️ Баланси Rozetka/Prom — ще не реалізовано (Крок 7 плану, окрема "
        "інтеграція, не пов'язана з тим, що вже готово)."
    )

    return "".join(lines)


def send_daily_report() -> None:
    message = build_report()
    print(message)
    sent = send_telegram_message(message)
    if not sent:
        print("[daily_report] Не вдалося надіслати в Telegram (див. повідомлення вище)", file=sys.stderr)


if __name__ == "__main__":
    send_daily_report()
