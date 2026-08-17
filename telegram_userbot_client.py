"""telegram_userbot_client.py — відправка маркування ROZETKA Delivery у Telegram через ЮЗЕРБОТ.

НАВІЩО (2026-08-17): останній крок ланцюга ROZETKA Delivery — передати «маркування»
(етикетку/номер) у чат підтримки Toysi. Bot API не може написати першим у приватний
чат → шлемо через ЮЗЕРБОТ (сесія окремого службового акаунта, telegram_userbot_login.py).

ДВІ ЦІЛІ (щоб не спамити реальний Toysi під час налагодження):
  - TELEGRAM_USERBOT_TEST_TARGET  — куди йде ТЕСТ (за рішенням власника — його ОСНОВНИЙ
    акаунт: @username АБО номер телефону). Дефолтна ціль, поки формат не звірено.
  - TELEGRAM_USERBOT_TOYSI_TARGET — РЕАЛЬНИЙ чат Toysi (менеджер Chechetenko). Ставиться
    В .env ЛИШЕ ПІСЛЯ того, як власник звірив формат на своєму номері. Порожній →
    відправка в Toysi заблокована (щоб випадково не піти в нікуди/не туди).

БЕЗПЕКА: api_id/api_hash — з .env; StringSession — з .local_secrets/ (gitignore). Сесія
НІКОЛИ не друкується/не логується. Помилка сесії/креденшелів → зрозумілий алерт, не крах.

ЗАПУСК:
    python telegram_userbot_client.py --test "маркування ..."     # → на TEST_TARGET (твій основний)
    python telegram_userbot_client.py --to-toysi "маркування ..." # → реальний Toysi (лише коли TOYSI_TARGET заданий)
API:  send_marking(text, to_toysi=False) -> bool
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

BASE_DIR = Path(__file__).parent
SESSION_FILE = Path(os.environ.get("TELEGRAM_USERBOT_SESSION_FILE",
                                   str(BASE_DIR / ".local_secrets" / "telegram_userbot_session.txt")))

API_ID = os.environ.get("TELEGRAM_USERBOT_API_ID", "").strip()
API_HASH = os.environ.get("TELEGRAM_USERBOT_API_HASH", "").strip()
TEST_TARGET = os.environ.get("TELEGRAM_USERBOT_TEST_TARGET", "").strip()
TOYSI_TARGET = os.environ.get("TELEGRAM_USERBOT_TOYSI_TARGET", "").strip()


class UserbotError(Exception):
    pass


def _resolve_target(target: str):
    """Ціль може бути @username, номером телефону чи числовим id — повертаємо у вигляді,
    придатному для telethon.send_message. Число → int (user id), решта → рядок як є."""
    t = target.strip()
    if t.lstrip("-").isdigit():
        return int(t)
    return t


def _load_session_str() -> str:
    if not SESSION_FILE.exists():
        raise UserbotError(
            f"немає сесії ({SESSION_FILE.name}) — власник має раз залогінитись: "
            f"`python telegram_userbot_login.py`")
    s = SESSION_FILE.read_text(encoding="utf-8").strip()
    if not s:
        raise UserbotError(f"файл сесії {SESSION_FILE.name} порожній — перезапусти telegram_userbot_login.py")
    return s


def send_marking(text: str, to_toysi: bool = False) -> bool:
    """Шле `text` через юзербот. to_toysi=False → на TEST_TARGET (основний акаунт власника);
    True → на реальний чат Toysi (лише якщо TELEGRAM_USERBOT_TOYSI_TARGET заданий).
    Повертає True при успіху; кидає UserbotError із ЗРОЗУМІЛОЮ причиною при збої (сесія/
    креденшели/ціль/мережа) — самодіагностика, не голий стектрейс."""
    if not text or not text.strip():
        raise UserbotError("порожній текст маркування — нема чого слати")
    if not API_ID or not API_HASH:
        raise UserbotError("немає TELEGRAM_USERBOT_API_ID/HASH у .env (my.telegram.org під другим номером)")
    try:
        api_id = int(API_ID)
    except ValueError:
        raise UserbotError("TELEGRAM_USERBOT_API_ID має бути числом")

    target_raw = TOYSI_TARGET if to_toysi else TEST_TARGET
    if not target_raw:
        which = "TELEGRAM_USERBOT_TOYSI_TARGET" if to_toysi else "TELEGRAM_USERBOT_TEST_TARGET"
        raise UserbotError(
            f"не задано ціль {which} у .env — "
            + ("реальний чат Toysi ставиться ЛИШЕ після звірки формату на твоєму номері"
               if to_toysi else "вкажи свій основний @username або номер"))

    session_str = _load_session_str()

    try:
        from telethon.sync import TelegramClient
        from telethon.sessions import StringSession
        from telethon.errors import FloodWaitError
    except ImportError:
        raise UserbotError("немає telethon (pip install telethon)")

    target = _resolve_target(target_raw)
    try:
        with TelegramClient(StringSession(session_str), api_id, api_hash) as client:
            if not client.is_user_authorized():
                raise UserbotError("сесія протухла/відкликана — власнику перелогінитись: telegram_userbot_login.py")
            try:
                client.send_message(target, text)
            except FloodWaitError as e:
                # Telegram сам каже, скільки чекати — не гадаємо.
                raise UserbotError(f"FloodWait: Telegram просить зачекати {e.seconds} сек "
                                   f"(забагато відправок; дренер має спати рівно стільки й ретраїти)")
            except ValueError as e:
                raise UserbotError(f"не вдалось знайти ціль '{target_raw}' ({e}) — перевір @username/номер; "
                                   f"якщо номер, він має бути контактом службового акаунта, або дай @username")
    except UserbotError:
        raise
    except Exception as e:  # мережа/авторизація тощо — теж із причиною
        raise UserbotError(f"збій відправки через юзербот: {type(e).__name__}: {e}")

    dest = "РЕАЛЬНИЙ Toysi" if to_toysi else "тест (основний акаунт)"
    print(f"[userbot] Надіслано ({dest}).")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Відправка маркування ROZETKA Delivery через юзербот (Telethon).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--test", metavar="TEXT", help="надіслати на TEST_TARGET (основний акаунт власника)")
    g.add_argument("--to-toysi", metavar="TEXT", help="надіслати в РЕАЛЬНИЙ чат Toysi (лише якщо TOYSI_TARGET заданий)")
    args = ap.parse_args()
    try:
        if args.test is not None:
            send_marking(args.test, to_toysi=False)
        else:
            send_marking(args.to_toysi, to_toysi=True)
    except UserbotError as e:
        print(f"[userbot] НЕ надіслано — {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
