"""telegram_userbot_login.py — РАЗОВИЙ інтерактивний логін юзербота (Telethon).

НАВІЩО (2026-08-17): маркування ROZETKA Delivery треба доставляти в Telegram-чат
підтримки Toysi. Bot API не може написати першим у приватний чат → потрібен ЮЗЕРБОТ
(сесія живого акаунта). За рішенням власника юзербот працює на ОКРЕМОМУ ДРУГОМУ
акаунті (не основному особистому) — компрометація сесії тоді = компрометація лише
цього службового акаунта, не всього особистого листування власника.

ЦЕЙ СКРИПТ ЗАПУСКАЄ ВЛАСНИК ОСОБИСТО, РАЗ, У ТЕРМІНАЛІ (потрібен код підтвердження
з Telegram + пароль 2FA, якщо увімкнено). Результат — StringSession, збережений у
.local_secrets/telegram_userbot_session.txt.

ПЕРЕДУМОВА (робить власник на my.telegram.org під ДРУГИМ номером):
    у .env додати
        TELEGRAM_USERBOT_API_ID=...
        TELEGRAM_USERBOT_API_HASH=...

БЕЗПЕКА: StringSession — це ПОВНИЙ доступ до акаунта. НІКОЛИ не друкуємо його в
консоль/лог, НІКОЛИ не в git, НІКОЛИ в чат. Файл лягає лише в .local_secrets/
(gitignore). Якщо щось піде не так — сесію можна миттєво відкликати:
Telegram → Налаштування → Пристрої → завершити сеанс. Раджу увімкнути 2FA на акаунті.

ЗАПУСК:  python telegram_userbot_login.py
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
SESSION_FILE = BASE_DIR / ".local_secrets" / "telegram_userbot_session.txt"

API_ID = os.environ.get("TELEGRAM_USERBOT_API_ID", "").strip()
API_HASH = os.environ.get("TELEGRAM_USERBOT_API_HASH", "").strip()


def main() -> None:
    if not API_ID or not API_HASH:
        print("[userbot-login] Немає TELEGRAM_USERBOT_API_ID/TELEGRAM_USERBOT_API_HASH у .env.\n"
              "  Отримай їх на https://my.telegram.org (під ДРУГИМ номером) і додай у .env, тоді запусти знову.",
              file=sys.stderr)
        sys.exit(1)

    try:
        api_id = int(API_ID)
    except ValueError:
        print("[userbot-login] TELEGRAM_USERBOT_API_ID має бути числом.", file=sys.stderr)
        sys.exit(1)

    # Імпорт тут, а не на рівні модуля — щоб відсутність telethon давала зрозумілу пораду,
    # а не крах імпорту.
    try:
        from telethon.sync import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("[userbot-login] Немає telethon. Встанови: pip install telethon", file=sys.stderr)
        sys.exit(1)

    print("[userbot-login] Відкриваю логін. Введи номер ДРУГОГО акаунта, код із Telegram, і 2FA-пароль (якщо є)...")
    # StringSession() порожня → client.start() проведе інтерактивний логін (номер/код/2FA).
    with TelegramClient(StringSession(), api_id, API_HASH) as client:
        me = client.get_me()
        session_str = client.session.save()  # рядок сесії — СЕКРЕТ, не друкуємо

    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(session_str, encoding="utf-8")
    try:
        # На POSIX робимо файл читабельним лише власнику; на Windows no-op — не критично.
        os.chmod(SESSION_FILE, 0o600)
    except OSError:
        pass

    who = f"{getattr(me, 'first_name', '') or ''} (@{getattr(me, 'username', None) or 'без-username'}, id={getattr(me, 'id', '?')})"
    print(f"[userbot-login] Готово. Залогінено акаунт: {who}")
    print(f"[userbot-login] Сесію збережено: {SESSION_FILE} (секрет, не показуй нікому, не в git).")
    print("[userbot-login] Якщо це НЕ той службовий акаунт — відклич сесію в Telegram і перезапусти.")
    print("[userbot-login] Далі: перевір відправку — python telegram_userbot_client.py --test \"тест маркування\"")


if __name__ == "__main__":
    main()
