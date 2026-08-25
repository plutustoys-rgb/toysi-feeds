"""novapay_imap_heartbeat.py — легка ЛОКАЛЬНА перевірка живості IMAP-авторизації NovaPay.

Тільки login (+select теки) — БЕЗ orders.db й БЕЗ обробки реєстрів (реконсиляція живе на
VPS, де є orders.db). Мета: щоб critical_watch показував плитку NovaPay-звірки САМОВІДНОВНО
на ЦЬОМУ компі, де немає ні orders.db, ні синку heartbeat із VPS. Ловить рівно той клас, що
впав 11.08 (AUTHENTICATIONFAILED — відкликаний Gmail app-password).

Пише `reports/novapay_last_ok.json` при УСПІХУ (той самий файл і формат, що novapay_statement
на VPS); при провалі НЕ пише → плитка лишається 🔴. Використовує ті самі NOVAPAY_IMAP_* з .env.

ВАЖЛИВО: app-password має збігатися з тим, що на VPS. Оновлюєш у Google → онови в ОБОХ .env
(локальному тут і на VPS), інакше локальна перевірка й реальна звірка розійдуться.

Запуск: додано в local_cabinet_audit.ps1 (щоденно, з AUDIT_REPORT_DIR=Cowork).
"""
import imaplib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EMAIL = os.environ.get("NOVAPAY_IMAP_EMAIL", "")
PW = os.environ.get("NOVAPAY_IMAP_APP_PASSWORD", "")
HOST = os.environ.get("NOVAPAY_IMAP_HOST", "imap.gmail.com")
FOLDER = os.environ.get("NOVAPAY_IMAP_FOLDER", "NovaPay")
HEARTBEAT_FILE = Path(os.environ.get("AUDIT_REPORT_DIR") or (Path(__file__).parent / "reports")) / "novapay_last_ok.json"


def main() -> None:
    if not (EMAIL and PW):
        print("[NovaPay-hb] NOVAPAY_IMAP_EMAIL/APP_PASSWORD не задані — пропускаю.", file=sys.stderr)
        return
    try:
        conn = imaplib.IMAP4_SSL(HOST)
        conn.login(EMAIL, PW)
        conn.select(FOLDER)  # переконатись, що й тека реєстрів доступна
        conn.logout()
    except (imaplib.IMAP4.error, OSError) as e:
        print(f"[NovaPay-hb] IMAP-перевірка НЕ пройшла ({HOST}): {e} — пульс НЕ оновлено.", file=sys.stderr)
        sys.exit(1)
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_FILE.write_text(
            json.dumps({"last_ok": datetime.now().isoformat(timespec="seconds"), "check": "imap_login"},
                       ensure_ascii=False),
            encoding="utf-8")
        print(f"[NovaPay-hb] IMAP ок -> пульс {HEARTBEAT_FILE}")
    except OSError as e:
        print(f"[NovaPay-hb] пульс не записано: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
