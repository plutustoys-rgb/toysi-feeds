import os
import re
import sys
from datetime import datetime

from telegram_notify import send_telegram_message

# Консоль Windows (cp1251) не показує emoji/деякі символи — не критично для
# systemd/journald на VPS (UTF-8), але без цього локальний запуск падає на print().
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEADLINES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deadlines.md")
REMINDER_WINDOW_DAYS = 14

_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


def parse_deadlines(path: str = DEADLINES_FILE) -> list:
    """
    Парсить markdown-таблицю з deadlines.md: | Дата | Сервіс | Сума | Дія |.
    Рядки без дати у форматі DD.MM.YYYY (напр. "уточнити", "—") пропускаються —
    вони довідкові, не беруть участі в нагадуванні.
    """
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    deadlines = []
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue

        date_str, service, amount, action = cells[0], cells[1], cells[2], cells[3]
        if not _DATE_RE.match(date_str):
            continue  # заголовок таблиці, роздільник "---", чи "уточнити"/"—"

        try:
            date = datetime.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            continue

        deadlines.append({"date": date, "service": service, "amount": amount, "action": action})

    return deadlines


def check_deadlines() -> None:
    deadlines = parse_deadlines()
    today = datetime.now()
    today_date = today.date()

    upcoming = []
    for d in deadlines:
        # Порівнюємо календарні дати, а не повні timestamp'и — інакше час доби
        # запуску скрипта зсуває підрахунок днів на ±1 (deadline завжди опівночі).
        days_left = (d["date"].date() - today_date).days
        if days_left <= REMINDER_WINDOW_DAYS:
            upcoming.append((days_left, d))

    upcoming.sort(key=lambda item: item[0])

    if not upcoming:
        message = (
            f"✅ Дедлайни PlutusToys: нічого в межах {REMINDER_WINDOW_DAYS} днів. "
            f"Перевірено {today.strftime('%d.%m.%Y')}."
        )
    else:
        lines = [f"🔔 Дедлайни PlutusToys — перевірено {today.strftime('%d.%m.%Y')}:\n"]
        for days_left, d in upcoming:
            if days_left < 0:
                when = f"⚠️ ПРОСТРОЧЕНО на {-days_left} дн."
            elif days_left == 0:
                when = "🔴 СЬОГОДНІ"
            else:
                when = f"через {days_left} дн."
            lines.append(
                f"\n{when} ({d['date'].strftime('%d.%m.%Y')}) — {d['service']}\n"
                f"  Сума: {d['amount']}\n"
                f"  Якщо не подовжити: {d['action']}"
            )
        message = "".join(lines)

    print(message)
    sent = send_telegram_message(message)
    if not sent:
        print("[deadline_reminder] Не вдалося надіслати в Telegram (див. повідомлення вище)", file=sys.stderr)


if __name__ == "__main__":
    check_deadlines()
