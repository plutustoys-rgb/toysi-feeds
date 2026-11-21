# -*- coding: utf-8 -*-
"""support_relay.py — локальна обгортка вузького каналу до чату підтримки сайту (для того, хто веде чат: Shopify-code).
Використання (з Bash/PowerShell, Python 3):
  python support_relay.py list                       # черга чатів без відповіді людини
  python support_relay.py digest [днів]              # збірка запитів (ПІБ/телефони/e-mail замасковані ботом)
  python support_relay.py reply <chat_id> "текст"    # відповідь клієнту; текст також можна передати через stdin: reply <chat_id> -
Усе йде окремим SSH-ключем ~/.ssh/site_chat_relay, який на VPS має ЛИШЕ forced-command site_chat_relay.py (без shell, без доступу до .env/orders.db).
Текст кодується в base64 (UTF-8) — кирилиця через ssh інакше перетворюється на «????». Кожна відповідь пишеться в журнал на VPS (support_relay.log).
Код виходу: 0 — ок; 2 — відхилено запобіжником (текст причини в stdout); 1 — збій.
Ключ і токен бота НІКОЛИ не друкуються. Налаштування ключа — install_support_relay.ps1 (запускає власник)."""
import base64
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = os.environ.get("SUPPORT_RELAY_HOST", "root@45.94.157.4")
KEY = Path(os.environ.get("SUPPORT_RELAY_KEY", str(Path.home() / ".ssh" / "site_chat_relay")))


def build_remote_command(argv: list, stdin_text=None) -> str:
    """argv (після імені скрипта) → рядок для SSH_ORIGINAL_COMMAND. reply: текст → base64."""
    if not argv:
        raise SystemExit("використання: support_relay.py list | digest [днів] | reply <chat_id> <текст|->")
    cmd = argv[0]
    if cmd == "list" and len(argv) == 1:
        return "list"
    if cmd == "digest" and len(argv) <= 2:
        return "digest" + (f" {argv[1]}" if len(argv) == 2 else "")
    if cmd == "reply" and len(argv) >= 3:
        chat_id = argv[1]
        text = stdin_text if argv[2] == "-" else " ".join(argv[2:])
        if text is None:
            raise SystemExit("reply: порожній текст")
        return f"reply {chat_id} " + base64.b64encode(text.lstrip("﻿").strip().encode("utf-8")).decode("ascii")
    raise SystemExit("використання: support_relay.py list | digest [днів] | reply <chat_id> <текст|->")


def main(argv: list) -> int:
    stdin_text = sys.stdin.buffer.read().decode("utf-8-sig") if (len(argv) >= 3 and argv[0] == "reply" and argv[2] == "-") else None      # utf-8-sig: PowerShell-конвеєр додає BOM
    remote = build_remote_command(argv, stdin_text)
    if not KEY.is_file():
        print(f"ключ {KEY} не знайдено — спершу власник запускає install_support_relay.ps1", file=sys.stderr)
        return 1
    ssh = ["ssh", "-i", str(KEY), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", HOST, remote]
    try:
        r = subprocess.run(ssh, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    except subprocess.TimeoutExpired:
        print("ТАЙМАУТ ssh (180 с): для reply результат НЕВІДОМИЙ — не повторюйте, спершу `list`", file=sys.stderr)
        return 1
    out = (r.stdout or "").strip()
    if out:
        print(out)
    if r.returncode != 0 and not out:
        print(f"ssh rc={r.returncode}: {(r.stderr or '').strip()[:200]}", file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
