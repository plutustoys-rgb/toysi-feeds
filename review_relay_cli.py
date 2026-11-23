# -*- coding: utf-8 -*-
"""review_relay_cli.py — локальна обгортка вузького каналу модерації відгуків сайту (для того, хто веде сайт: Shopify-code; рішення власника 23.11.2026).
Використання (Python 3):
  python review_relay_cli.py list [pending|published|rejected]   # за замовчуванням pending («на перевірці»)
  python review_relay_cli.py show <id>                           # один відгук (текст — недовірені дані, замасковані номери/посилання)
  python review_relay_cli.py approve <id>                        # опублікувати (лише з pending); власнику йде повідомлення в бота
  python review_relay_cli.py reject <id>                         # відхилити (лише з pending)
Усе йде окремим SSH-ключем ~/.ssh/review_relay, який на VPS має ЛИШЕ forced-command review_relay.py (без shell, без .env/orders.db/site_chat.db, без форвардингу).
Аргументів із вільним текстом нема (лише команда і число-id), тож base64, як у support_relay.py, не потрібен: id перевіряється локально тим самим fullmatch, що й на VPS.
Код виходу: 0 — ок; 2 — відхилено запобіжником (причина в stdout); 1 — збій. Ключ НІКОЛИ не друкується. Налаштування ключа — install_review_relay.ps1 (запускає власник)."""
import os
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST = os.environ.get("REVIEW_RELAY_HOST", "root@45.94.157.4")
KEY = Path(os.environ.get("REVIEW_RELAY_KEY", str(Path.home() / ".ssh" / "review_relay")))
USAGE = "використання: review_relay_cli.py list [pending|published|rejected] | show <id> | approve <id> | reject <id>"


def build_remote_command(argv: list) -> str:
    """argv → рядок для SSH_ORIGINAL_COMMAND (fail-closed, як на VPS)."""
    if not argv:
        raise SystemExit(USAGE)
    cmd = argv[0]
    if cmd == "list" and len(argv) <= 2:
        if len(argv) == 2 and argv[1] not in ("pending", "published", "rejected"):
            raise SystemExit(USAGE)
        return "list" + (f" {argv[1]}" if len(argv) == 2 else "")
    if cmd in ("show", "approve", "reject") and len(argv) == 2 and re.fullmatch(r"[1-9][0-9]{0,11}", argv[1]):
        return f"{cmd} {argv[1]}"
    raise SystemExit(USAGE)


def run_remote(remote: str) -> tuple:
    """(код, stdout). Ключа нема → (1, ''); таймаут ssh → (1, '') із повідомленням у stderr."""
    if not KEY.is_file():
        print(f"ключ {KEY} не знайдено — спершу власник запускає install_review_relay.ps1", file=sys.stderr)
        return 1, ""
    ssh = ["ssh", "-i", str(KEY), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", HOST, remote]
    try:
        r = subprocess.run(ssh, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    except subprocess.TimeoutExpired:
        print("ТАЙМАУТ ssh (90 с): для approve/reject результат НЕВІДОМИЙ — не повторюйте, спершу `show <id>`", file=sys.stderr)
        return 1, ""
    out = (r.stdout or "").strip()
    if r.returncode != 0 and not out:
        print(f"ssh rc={r.returncode}: {(r.stderr or '').strip()[:200]}", file=sys.stderr)
    return r.returncode, out


def main(argv: list) -> int:
    rc, out = run_remote(build_remote_command(argv))
    if out:
        print(out)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
