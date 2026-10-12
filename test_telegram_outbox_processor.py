#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_telegram_outbox_processor.py — вихідна скринька агентів → власнику (аудит #696 раунд 2: нова позначка «📋 лише в журналі» не розпізнавалась як
оброблена, блок крутився б на кожному прогоні: дубль у журналі й ще один маркер). Інваріанти: у режимі «лише замовлення» повідомлення агента НЕ йде в Telegram, блок
позначається «📋 лише в журналі» (не «✅ відправлено») і ДРУГИЙ прогін його не чіпає (ідемпотентно: один запис у журналі, один маркер); «🛒 Нове замовлення»
йде й позначається «✅ відправлено»; режим вимкнено (TELEGRAM_ORDERS_ONLY=0) → «✅ відправлено». Мережа замокана. `python test_telegram_outbox_processor.py` → exit 0/1."""
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ.pop("TELEGRAM_ORDERS_ONLY", None)
import telegram_notify as tn
import telegram_outbox_processor as op

tmp = Path(tempfile.mkdtemp())
tn.ALERTS_LOG_FILE = tmp / "telegram_alerts.md"
tn.ALERT_THROTTLE_FILE = tmp / ".t.json"
tn.TELEGRAM_BOT_TOKEN, tn.TELEGRAM_CHAT_ID = "T", "C"
posts = []


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True}


tn.requests.post = lambda url, json=None, timeout=0: (posts.append(json["text"]) or _Resp())
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def outbox(*bodies):
    p = tmp / "outbox.md"
    p.write_text("".join(f"## Повідомлення {i + 1} (тест)\n\n{b}\n\n" for i, b in enumerate(bodies)), encoding="utf-8")
    return str(p)


def log():
    return tn.ALERTS_LOG_FILE.read_text(encoding="utf-8") if tn.ALERTS_LOG_FILE.exists() else ""


path = outbox("Агент: потрібне рішення по Prom", "🛒 Нове замовлення · prom №1 · 100 грн · 1 поз.")
op.process_outbox(path)
txt = Path(path).read_text(encoding="utf-8")
chk("повідомлення агента (не замовлення): у Telegram не пішло", posts == ["🛒 Нове замовлення · prom №1 · 100 грн · 1 поз."])
chk("блок агента позначено «📋 лише в журналі», а не «✅ відправлено»", txt.count("📋 лише в журналі") == 1 and txt.count("✅ відправлено") == 1)
n_log = log().count("Агент: потрібне рішення по Prom")
chk("у журналі рівно один запис повідомлення агента", n_log == 1)

op.process_outbox(path)
op.process_outbox(path)
txt2 = Path(path).read_text(encoding="utf-8")
chk("повторні прогони ідемпотентні: маркерів не додається", txt2 == txt)
chk("повторні прогони: у журналі досі один запис, у Telegram нічого нового", log().count("Агент: потрібне рішення по Prom") == 1 and len(posts) == 1)
chk("find_unprocessed_messages після обробки порожній", op.find_unprocessed_messages(path) == [])

# ── один блок агента, три прогони (регресія аудиту #696: маркер «📋» не розпізнавався як оброблений → дубль у журналі й маркер на кожному прогоні)
posts.clear()
tn.ALERTS_LOG_FILE.unlink()
path1 = outbox("Агент: єдине повідомлення")
for _ in range(3):
    op.process_outbox(path1)
t1 = Path(path1).read_text(encoding="utf-8")
chk("один блок, 3 прогони: рівно один маркер «📋 лише в журналі»", t1.count("📋 лише в журналі") == 1)
chk("один блок, 3 прогони: у журналі рівно один запис, у Telegram нічого", log().count("Агент: єдине повідомлення") == 1 and not posts)

# ── два блоки (агент + замовлення): маркер КОЖНОГО у своєму блоці (раніше зсув позицій після першої вставки клав маркер короткого блоку в сусідній)
posts.clear()
tn.ALERTS_LOG_FILE.unlink()
path3 = outbox("Агент: довге повідомлення " + "текст " * 40, "🛒 Нове замовлення · prom №2 · 10 грн · 1 поз.")
op.process_outbox(path3)
t4 = Path(path3).read_text(encoding="utf-8")
h1, h2 = t4.index("## Повідомлення 1"), t4.index("## Повідомлення 2")
chk("два блоки: «📋» у першому блоці, «✅» у другому (кожен маркер у своєму)", h1 < t4.index("📋 лише в журналі") < h2 < t4.index("✅ відправлено"))
op.process_outbox(path3)
chk("два блоки: другий прогін нічого не змінює", Path(path3).read_text(encoding="utf-8") == t4)

# режим вимкнено
os.environ["TELEGRAM_ORDERS_ONLY"] = "0"
posts.clear()
path2 = outbox("Агент: інше повідомлення")
op.process_outbox(path2)
t3 = Path(path2).read_text(encoding="utf-8")
chk("TELEGRAM_ORDERS_ONLY=0: повідомлення йде й позначається «✅ відправлено»", len(posts) == 1 and "✅ відправлено" in t3 and "📋" not in t3)
os.environ.pop("TELEGRAM_ORDERS_ONLY", None)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
