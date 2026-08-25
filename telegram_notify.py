import json
import os
import sys
import time
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


# Стан тротлінгу повторюваних алертів: {dedup_key: last_epoch}. Поряд з
# telegram_alerts.md у reports/ (той самий каталог, уже писабельний на VPS).
ALERT_THROTTLE_FILE = Path(__file__).parent / "reports" / ".alert_throttle.json"


def send_throttled_alert(dedup_key: str, text: str, cooldown_sec: int = 3 * 60 * 60) -> bool:
    """Як send_telegram_message, але не частіше ніж раз на cooldown_sec для одного
    dedup_key. Для повторюваних збоїв, які перевіряються щоцикл (напр. протух токен —
    order-pipeline біжить кожні ~15 хв): БЕЗ тротлінгу це 96 однакових алертів/добу
    (власник замутить), а зовсім без алерту — тиха втрата (інцидент 904194938,
    2026-08-25: живе замовлення висіло годинами невидимим). Тротлінг тримає баланс:
    гучно попереджає, але не спамить.

    Стан — reports/.alert_throttle.json. Помилка читання/запису стану НЕ блокує сам
    алерт (best-effort: краще зайвий алерт, ніж пропущений). Повертає True, якщо
    повідомлення реально надіслано цього разу (поза вікном тиші)."""
    now = time.time()
    state = {}
    try:
        with open(ALERT_THROTTLE_FILE, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            state = loaded
    except (OSError, ValueError):
        state = {}

    last = state.get(dedup_key)
    if isinstance(last, (int, float)) and (now - last) < cooldown_sec:
        return False  # у вікні тиші для цього ключа — не шлемо повторно

    sent = send_telegram_message(text)
    state[dedup_key] = now
    try:
        ALERT_THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERT_THROTTLE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError as e:
        print(f"[telegram] не вдалося зберегти стан тротлінгу {ALERT_THROTTLE_FILE}: {e}", file=sys.stderr)
    return sent
