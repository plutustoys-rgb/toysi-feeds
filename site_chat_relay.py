# -*- coding: utf-8 -*-
"""site_chat_relay.py — ВУЗЬКИЙ канал до чату підтримки сайту (рішення власника 21.11.2026: чат веде Shopify-code, відповіді — її рішення).

Запускається на VPS ЛИШЕ як forced-command окремого SSH-ключа (authorized_keys: `command="…/site_chat_relay.py",restrict ssh-ed25519 … support-relay`). Тож власник ключа не отримує shell,
ні .env, ні orders.db — лише три дії через SSH_ORIGINAL_COMMAND:
  list                      → `site_chat_bot.py list-new` (черга чатів без відповіді людини)
  digest [днів 1..14]       → `site_chat_bot.py digest N` (ПІБ/телефони/e-mail у збірці вже маскуються самим ботом)
  reply <chat_id> <base64>  → `site_chat_bot.py reply <chat_id> <текст>`; текст у base64 (UTF-8), бо кирилиця через stdin/аргументи ssh ламається у «????».
Запобіжники reply: chat_id — лише цифри й ЛИШЕ чат, що вже писав нам (є вхідні повідомлення в site_chat.db); текст 1..1000 символів без керівних; посилання — лише https://plutustoys.com.ua/…; без довгих цифрових рядків
(≥13 цифр: схоже на номер картки); не більше MAX_REPLIES_PER_HOUR відповідей на годину. КОЖНА спроба reply пишеться в support_relay.log (JSON-рядок: час, chat_id, текст, результат). Жодних shell-викликів: subprocess зі списком аргументів.
Код виходу: 0 — ок; 2 — відхилено запобіжником; 1 — збій бота."""
import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
PYTHON = os.environ.get("SITE_CHAT_RELAY_PYTHON", str(HERE / "venv" / "bin" / "python3"))
BOT = HERE / "site_chat_bot.py"
CHAT_DB = Path(os.environ.get("SITE_CHAT_DB", str(HERE / "site_chat.db")))
LOG = Path(os.environ.get("SITE_CHAT_RELAY_LOG", str(HERE / "support_relay.log")))
MAX_REPLIES_PER_HOUR = 12
MAX_TEXT = 1000
MAX_OUT = 6000
ALLOWED_URL_PREFIX = "https://plutustoys.com.ua/"
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_LONG_DIGITS_RE = re.compile(r"\d(?:[\s-]?\d){12,}")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class Rejected(Exception):
    pass


def parse(cmd: str) -> tuple:
    """SSH_ORIGINAL_COMMAND → ('list',) | ('digest', N) | ('reply', chat_id:int, b64:str). Все інше → Rejected."""
    parts = (cmd or "").strip().split()
    if not parts:
        raise Rejected("порожня команда: list | digest [N] | reply <chat_id> <base64>")
    if parts[0] == "list" and len(parts) == 1:
        return ("list",)
    if parts[0] == "digest" and len(parts) <= 2:
        n = 1
        if len(parts) == 2:
            if not parts[1].isdigit() or not 1 <= int(parts[1]) <= 14:
                raise Rejected("digest: днів має бути 1..14")
            n = int(parts[1])
        return ("digest", n)
    if parts[0] == "reply" and len(parts) == 3:
        if not re.fullmatch(r"\d{1,15}", parts[1]):
            raise Rejected("reply: chat_id — лише цифри (до 15)")
        if not re.fullmatch(r"[A-Za-z0-9+/=]{1,6000}", parts[2]):
            raise Rejected("reply: текст має бути base64 (A-Za-z0-9+/=)")
        return ("reply", int(parts[1]), parts[2])
    raise Rejected("невідома команда: дозволено лише list | digest [N] | reply <chat_id> <base64>")


def decode_text(b64: str) -> str:
    try:
        text = base64.b64decode(b64, validate=True).decode("utf-8").strip()
    except Exception:  # noqa: BLE001
        raise Rejected("reply: base64/UTF-8 некоректні") from None
    return text


def check_text(text: str) -> None:
    """Технічні межі тексту (політику відповідей визначає той, хто веде чат)."""
    if not text:
        raise Rejected("reply: порожній текст")
    if len(text) > MAX_TEXT:
        raise Rejected(f"reply: текст {len(text)} символів, дозволено до {MAX_TEXT}")
    if _CTRL_RE.search(text):
        raise Rejected("reply: керівні символи в тексті")
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?)»\"'")
        if not url.startswith(ALLOWED_URL_PREFIX):
            raise Rejected(f"reply: посилання лише на {ALLOWED_URL_PREFIX}… (знайдено інше)")
    if _LONG_DIGITS_RE.search(text):
        raise Rejected("reply: довгий цифровий рядок (схоже на номер картки/рахунку) — не надсилаємо")


def chat_known(chat_id: int, db_path=None) -> bool:
    """Чат має вхідні повідомлення (нам справді писали). Читання mode=ro."""
    p = Path(db_path or CHAT_DB)
    if not p.is_file():
        return False
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            return con.execute("SELECT 1 FROM messages WHERE chat_id=? AND direction='in' LIMIT 1", (chat_id,)).fetchone() is not None
        finally:
            con.close()
    except sqlite3.Error:
        return False


def reply_recorded(chat_id: int, text: str, db_path=None) -> bool:
    """Бот записує успішну відповідь людини як direction='out'. Без запису (напр. токен не задано: бот друкує попередження й повертає 0) «відправлено» НЕ повідомляємо."""
    p = Path(db_path or CHAT_DB)
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            return con.execute("SELECT 1 FROM messages WHERE chat_id=? AND direction='out' AND text=? ORDER BY id DESC LIMIT 1", (chat_id, text)).fetchone() is not None
        finally:
            con.close()
    except sqlite3.Error:
        return False


def _log(entry: dict, log_path=None) -> None:
    p = Path(log_path or LOG)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def replies_last_hour(log_path=None, now=None) -> int:
    p = Path(log_path or LOG)
    if not p.is_file():
        return 0
    cutoff = (now or datetime.now()) - timedelta(hours=1)
    n = 0
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
            if e.get("action") == "reply" and e.get("result") in ("sent", "bot_failed") and datetime.fromisoformat(e["ts"]) >= cutoff:
                n += 1
        except (ValueError, KeyError, TypeError):
            continue
    return n


def _run_bot(args: list, timeout: int = 90) -> tuple:
    r = subprocess.run([PYTHON, str(BOT)] + args, cwd=str(HERE), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or "")[:MAX_OUT]


def handle(cmd: str, runner=None, db_path=None, log_path=None, now=None) -> tuple:
    """→ (код_виходу, текст_для_stdout). runner(args)->(rc,out) — підміна у тестах."""
    run = runner or _run_bot
    ts = (now or datetime.now()).replace(microsecond=0).isoformat()
    try:
        act = parse(cmd)
    except Rejected as e:
        return 2, f"ВІДХИЛЕНО: {e}"
    if act[0] == "list":
        rc, out = run(["list-new"])
        return (0 if rc == 0 else 1), (out or "(черга порожня)")
    if act[0] == "digest":
        rc, out = run(["digest", str(act[1])])
        return (0 if rc == 0 else 1), out
    chat_id, text = act[1], None
    try:
        text = decode_text(act[2])
        check_text(text)
        if not chat_known(chat_id, db_path):
            raise Rejected("reply: чат невідомий (нема вхідних повідомлень від цього chat_id) — відповідати можна лише тим, хто писав")
        if replies_last_hour(log_path, now) >= MAX_REPLIES_PER_HOUR:
            raise Rejected(f"reply: ліміт {MAX_REPLIES_PER_HOUR} відповідей на годину вичерпано")
    except Rejected as e:
        _log({"ts": ts, "action": "reply", "chat_id": chat_id, "text": text, "result": "rejected", "why": str(e)}, log_path)
        return 2, f"ВІДХИЛЕНО: {e}"
    try:
        rc, out = run(["reply", str(chat_id), text])
    except Exception as e:  # noqa: BLE001
        rc, out = 1, f"{type(e).__name__}"
    if rc == 0 and not reply_recorded(chat_id, text, db_path):
        rc, out = 1, "бот завершився без запису відповіді (токен не задано?)"
    _log({"ts": ts, "action": "reply", "chat_id": chat_id, "text": text, "result": "sent" if rc == 0 else "bot_failed", "rc": rc}, log_path)
    if rc == 0:
        return 0, f"відправлено (chat {chat_id}, {len(text)} символів)"
    return 1, f"бот не надіслав (rc={rc}); повторювати лише після перевірки `list`: {out[:200]}"


if __name__ == "__main__":
    code, text = handle(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    print(text)
    sys.exit(code)
