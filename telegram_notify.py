import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
REQUEST_TIMEOUT     = 15


def send_telegram_message(text: str) -> bool:
    """Надсилає повідомлення власнику через PlutusToysBot. Повертає True при успіху."""
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
