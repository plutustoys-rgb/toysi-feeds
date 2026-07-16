import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
REQUEST_TIMEOUT     = 15

# ВИПРАВЛЕНО (2026-07-16, задача власниці — прогалина звітності, задача
# #32 мала це закрити для ВСІХ автоматизацій, але торкнулась лише
# конкретних скриптів): усі 10+ скриптів проєкту (service_watchdog.py,
# order_router.py, daily_report.py, prom_catalog_auditor.py,
# prom_competitor_pricer.py, prom_catalog_sync.py, prom_chat_bot.py,
# deadline_reminder.py, telegram_outbox_processor.py,
# generate_rozetka_feed.py) шлють алерти ЛИШЕ в Telegram — жоден рядок
# не дублювався у спільну Windows-папку, тож усе, що не потрапило явним
# файлом-звітом, було видно лише в телефоні власниці, не в терміналі/
# спільній папці, де вона фактично працює з Code Desktop.
#
# Централізовано ТУТ, а не в кожному з 10 скриптів окремо — САМЕ ЦЯ
# функція є єдиною точкою, через яку проходить кожне повідомлення,
# незалежно від того, який скрипт його викликав. Будь-який майбутній
# скрипт, що викличе send_telegram_message(), автоматично отримує це
# дублювання безкоштовно, без окремого патчу.
#
# Пишемо у append-only telegram_alerts.md В /opt/plutustoys/reports/
# (той самий каталог, що вже читає shared-folder-report-sync
# scheduled task) — НЕЗАЛЕЖНО від того, чи сам виклик до Telegram API
# вдався: мережевий збій Telegram не повинен означати "звіту в
# спільній папці теж не буде", інакше саме в момент, коли найважливіше
# щось побачити (Telegram недоступний), запис і туди пропаде.
ALERTS_LOG_FILE = Path(__file__).parent / "reports" / "telegram_alerts.md"


def _log_alert_to_shared_folder(text: str) -> None:
    """Best-effort — збій запису у файл НІКОЛИ не повинен зламати виклик,
    що надсилає реальний Telegram-алерт (той самий принцип, що й
    continue-on-error для допоміжних кроків в update-feeds.yml)."""
    try:
        ALERTS_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        source = os.path.basename(sys.argv[0]) if sys.argv else "?"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(ALERTS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"## {timestamp} — {source}\n\n{text}\n\n---\n\n")
    except OSError as e:
        print(f"[telegram] Не вдалося дописати у {ALERTS_LOG_FILE}: {e}", file=sys.stderr)


def send_telegram_message(text: str) -> bool:
    """Надсилає повідомлення власнику через PlutusToysBot. Повертає True при успіху.

    Дублює КОЖЕН виклик (незалежно від успіху самого надсилання) у
    reports/telegram_alerts.md — див. коментар над ALERTS_LOG_FILE вище."""
    _log_alert_to_shared_folder(text)

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "[telegram] TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID не задані в .env — повідомлення не надіслано",
            file=sys.stderr,
        )
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[telegram] Помилка з'єднання: {e}", file=sys.stderr)
        return False

    try:
        data = response.json()
    except ValueError:
        print(f"[telegram] Невалідна відповідь (не JSON): {response.text[:300]}", file=sys.stderr)
        return False

    if not data.get("ok"):
        print(f"[telegram] Telegram API відхилив повідомлення: {data}", file=sys.stderr)
        return False

    return True
