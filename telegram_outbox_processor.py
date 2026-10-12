import os
import re
import sys
from datetime import datetime

import telegram_triage
from telegram_notify import send_telegram_message

# Файл живе у спільній папці PlutusToys_avtonomiya (Cowork і automation-сесія
# пишуть/читають той самий фізичний файл на цій Windows-машині) — це НЕ шлях
# у /opt/plutustoys на VPS, тож обробка йде тут, локально, не через systemd.
OUTBOX_FILE = os.environ.get(
    "TELEGRAM_OUTBOX_FILE",
    r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\telegram_outbox.md",
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Кожен блок повідомлення: "## Повідомлення N (...)" — текст — до наступного
# "## Повідомлення" або кінця файлу. Вже оброблені блоки містять рядок
# "✅ відправлено ...". Перевіряємо саме це (не mtime файлу) — так скрипт
# природно ідемпотентний і сам "доганяє" все неопрацьоване незалежно від
# того, скільки повідомлень назбиралось між перевірками.
_HEADER_RE = re.compile(r"^## Повідомлення \d+.*$", re.MULTILINE)
_SENT_MARKER_RE = re.compile(r"^(?:✅ відправлено|📋 лише в журналі)", re.MULTILINE)   # обидві позначки = блок оброблено (📋 — режим «лише замовлення», рішення власника 12.10)


def find_unprocessed_messages(path: str = OUTBOX_FILE) -> list:
    with open(path, encoding="utf-8") as f:
        content = f.read()

    headers = list(_HEADER_RE.finditer(content))
    blocks = []
    for i, match in enumerate(headers):
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(content)
        body = content[start:end]
        if not _SENT_MARKER_RE.search(body):
            blocks.append({"header": match.group().strip(), "body": body.strip(), "end": end})

    return blocks


def process_outbox(path: str = OUTBOX_FILE) -> None:
    unprocessed = find_unprocessed_messages(path)
    if not unprocessed:
        print("[telegram_outbox] Нових повідомлень немає")
        return

    with open(path, encoding="utf-8") as f:
        content = f.read()

    marks = []   # (позиція вставки, маркер): вставляємо ПІСЛЯ циклу й від кінця до початку — інакше зсув від попередньої вставки переносив маркер короткого блоку в сусідній
    for block in unprocessed:
        text = block["body"]
        sent = send_telegram_message(text)
        if not sent:
            print(f"[telegram_outbox] Не вдалося надіслати ({block['header']}) — лишаю непозначеним", file=sys.stderr)
            continue

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if telegram_triage.orders_only_enabled() and not telegram_triage.is_order_arrival(text):
            marker = f"\n\n📋 лише в журналі {timestamp} (Telegram: режим «лише замовлення»)"   # не вводимо в оману «відправлено»
        else:
            marker = f"\n\n✅ відправлено {timestamp}"
        marks.append((block["end"], marker + chr(10)))   # маркер завершуємо переносом рядка: інакше він приклеюється до заголовка наступного блоку («…12:48:49## Повідомлення 2»), той перестає розпізнаватись і повідомлення губиться при збої відправки
        print(f"[telegram_outbox] Оброблено: {block['header']}")

    for insert_at, marker in sorted(marks, key=lambda m: m[0], reverse=True):
        content = content[:insert_at] + marker + content[insert_at:]

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


if __name__ == "__main__":
    process_outbox()
