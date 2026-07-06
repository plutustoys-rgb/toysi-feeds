import json
import os
import re
import subprocess
import sys
from datetime import datetime

from telegram_notify import send_telegram_message

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

"""
Watchdog для orders_watcher.py/bank_check.py: якщо systemd-сервіс не мав
жодного успішного завершення ("Finished ...", яке journald логує лише при
status=0/SUCCESS — при падінні логується "Failed with result...") довше,
ніж 2x очікуваний інтервал таймера — це рання ознака, що воркер завис/впав,
і без цього ніхто не помітить, поки не гляне логи вручну.

Сповіщає в Telegram лише на ЗМІНУ стану (OK -> ALARM і назад), а не на
кожній перевірці — інакше при тривалому падінні прийшов би окремий алерт
щоразу, коли запускається сам watchdog.
"""

# Назва сервісу -> поріг у хвилинах (2x очікуваний інтервал відповідного таймера).
MONITORED_SERVICES = {
    "orders-watcher": 30,  # таймер кожні 15 хв
    "bank-check": 30,      # таймер кожні 15 хв
}

LOOKBACK = "3 days ago"  # достатньо, щоб знайти останній успіх навіть після тривалого падіння

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchdog_state.json")

# journalctl -o short-iso віддає зсув часового поясу без двокрапки (+0300),
# а datetime.fromisoformat() приймає такий формат лише з Python 3.11+.
# Нормалізуємо самі, щоб парсинг не залежав від версії Python середовища,
# де це виконується.
_TZ_OFFSET_RE = re.compile(r"([+-]\d{2})(\d{2})$")


class WatchdogCheckError(Exception):
    """Сам watchdog не зміг перевірити стан сервісу (journalctl недоступний,
    немає прав, таймаут тощо) — це НЕ те саме, що "сервіс не звітував про
    успіх": тут ми просто не знаємо, і замовчувати цю різницю означало б
    ризикувати або хибним ALARM, або (гірше) тихим "все ОК", коли насправді
    watchdog сам не працює."""


def _parse_journal_timestamp(timestamp_str: str) -> datetime:
    normalized = _TZ_OFFSET_RE.sub(r"\1:\2", timestamp_str)
    return datetime.fromisoformat(normalized)


def get_last_success_time(service: str):
    """Час останнього рядка "Finished <service>.service" у journald. Повертає
    timezone-aware datetime, або None, якщо успішних завершень не знайдено
    за LOOKBACK. Піднімає WatchdogCheckError, якщо сам виклик journalctl
    не вдався — це відрізняється від "успіхів дійсно немає"."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", f"{service}.service", "--since", LOOKBACK, "-o", "short-iso", "--no-pager"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as e:
        raise WatchdogCheckError(f"не вдалося викликати journalctl: {e}") from e

    if result.returncode != 0:
        raise WatchdogCheckError(
            f"journalctl завершився з кодом {result.returncode}: {result.stderr.strip()[:200]}"
        )

    marker = f"Finished {service}.service"
    last_success = None
    for line in result.stdout.splitlines():
        if marker in line:
            timestamp_str = line.split(" ", 1)[0]
            try:
                last_success = _parse_journal_timestamp(timestamp_str)
            except ValueError:
                continue
    return last_success


def _load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def check_services() -> None:
    now = datetime.now().astimezone()
    state = _load_state()
    new_alarms = []
    recoveries = []

    for service, threshold_minutes in MONITORED_SERVICES.items():
        was_alarming = state.get(service, False)

        try:
            last_success = get_last_success_time(service)
        except WatchdogCheckError as e:
            # Не можемо підтвердити ні здоров'я, ні падіння сервісу — сам
            # watchdog зламався. Позначаємо як ALARM (безпечніше помилково
            # насторожити, ніж мовчки пропустити реальну проблему), але з
            # чітко іншим формулюванням, щоб було видно: це не сервіс упав,
            # а сам watchdog не може перевірити.
            state[service] = True
            print(f"[watchdog] {service}: ERROR — {e}", file=sys.stderr)
            if not was_alarming:
                new_alarms.append(f"⚠️ {service}: watchdog не зміг перевірити стан — {e}")
            continue

        if last_success is None:
            is_alarming = True
            detail = f"жодного успішного запуску не знайдено за {LOOKBACK}"
        else:
            elapsed_minutes = (now - last_success).total_seconds() / 60
            is_alarming = elapsed_minutes > threshold_minutes
            detail = (
                f"останній успіх {last_success.strftime('%d.%m.%Y %H:%M')} "
                f"({elapsed_minutes:.0f} хв тому, поріг {threshold_minutes} хв)"
            )

        state[service] = is_alarming
        status_word = "ALARM" if is_alarming else "OK"
        print(f"[watchdog] {service}: {status_word} — {detail}")

        if is_alarming and not was_alarming:
            new_alarms.append(f"⛔ {service}: {detail} — можливо, завис/впав")
        elif not is_alarming and was_alarming:
            recoveries.append(f"✅ {service}: знову працює ({detail})")

    _save_state(state)

    if new_alarms:
        message = "🚨 Watchdog PlutusToys: сервіс(и) не відповідають\n\n" + "\n\n".join(new_alarms)
        print(message)
        if not send_telegram_message(message):
            print("[watchdog] Не вдалося надіслати алерт у Telegram (див. вище)", file=sys.stderr)
    if recoveries:
        message = "✅ Watchdog PlutusToys: відновлено\n\n" + "\n\n".join(recoveries)
        print(message)
        if not send_telegram_message(message):
            print("[watchdog] Не вдалося надіслати повідомлення про відновлення в Telegram", file=sys.stderr)


if __name__ == "__main__":
    check_services()
