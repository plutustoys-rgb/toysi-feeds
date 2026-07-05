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
Проміжна версія (без виручки й балансів — order_router.py/order_status_tracker.py
ще не написані). Раз на день підсумовує: скільки замовлень підхопив
orders_watcher.py за добу (окремо Prom/Rozetka, з позначкою реальні дані
чи мок), і скільки замовлень чекає на bank_check.py.
"""


def _source_label(platform: str) -> str:
    """Реальні дані для платформи є, лише якщо задано відповідний API-ключ —
    інакше orders_watcher.py досі повертає мок-замовлення (Крок 3 плану)."""
    key = PROM_API_KEY if platform == "prom" else ROZETKA_API_KEY
    return "реальні дані" if key else "мок-дані (ключа ще немає)"


def build_report() -> str:
    init_db()
    since = (datetime.now() - timedelta(hours=LOOKBACK_HOURS)).isoformat(timespec="seconds")

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT platform FROM orders WHERE created_at >= ?", (since,)
        ).fetchall()
        awaiting_manual = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status = 'awaiting_manual_confirmation'"
        ).fetchone()[0]
        awaiting_bank_check = len(get_orders_awaiting_payment(conn))

    counts_by_platform = {}
    for row in rows:
        counts_by_platform[row["platform"]] = counts_by_platform.get(row["platform"], 0) + 1

    lines = [f"📋 Щоденний звіт PlutusToys — {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"]
    lines.append(f"\nНових замовлень за останні {LOOKBACK_HOURS} год: {len(rows)}")

    for platform in ("prom", "rozetka"):
        count = counts_by_platform.get(platform, 0)
        lines.append(f"\n  {platform.capitalize()}: {count} ({_source_label(platform)})")

    lines.append("\n\nbank_check.py:")
    lines.append(f"\n  Очікують перевірки оплати (prepaid, ще не підтверджено): {awaiting_bank_check}")
    lines.append(f"\n  Позначено \"очікує ручного підтвердження\": {awaiting_manual}")

    lines.append(
        "\n\n⚠️ Проміжна версія звіту — без виручки й балансів "
        "(з'явиться разом з order_router.py/order_status_tracker.py)."
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
