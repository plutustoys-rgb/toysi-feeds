#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_chat_relay.py — вузький канал до чату підтримки (site_chat_relay.py + support_relay.py), 21.11.2026.
ІНВАРІАНТИ: (1) дозволено лише list | digest [1..14] | reply <digits> <base64>; будь-що інше (shell-метасимволи, `id`, `cat .env`, зайві аргументи) → ВІДХИЛЕНО до виклику бота; (2) reply лише чату, що писав нам (є 'in'
у site_chat.db); (3) текст: 1..1000, без керівних, посилання лише https://plutustoys.com.ua/…, без ≥13 цифр поспіль; (4) ліміт 12 відповідей/год; (5) кожна спроба reply — рядок у журналі; (6) «відправлено» лише якщо бот
записав 'out' у БД (токен не задано → бот повертає 0, але запису нема → збій); (7) бот викликається СПИСКОМ аргументів (не shell); (8) обгортка support_relay.py кодує кирилицю в base64 і не підставляє текст у команду як є.
Без мережі. `python test_site_chat_relay.py` → exit 0/1."""
import base64
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import site_chat_relay as r  # noqa: E402
import support_relay as w  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


TMP = Path(tempfile.mkdtemp(prefix="relay_"))
DB = TMP / "site_chat.db"
LOG = TMP / "relay.log"
con = sqlite3.connect(DB)
con.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, chat_id INTEGER, direction TEXT, text TEXT, ts TEXT, forwarded INTEGER)")
con.execute("INSERT INTO messages(chat_id,direction,text,ts,forwarded) VALUES(111,'in','привіт','2026-11-21T12:00:00',1)")
con.execute("INSERT INTO messages(chat_id,direction,text,ts,forwarded) VALUES(222,'in','питання','2026-11-21T12:01:00',1)")
con.commit()
con.close()
NOW = datetime(2026, 11, 21, 21, 0, 0)


def b64(t):
    return base64.b64encode(t.encode("utf-8")).decode("ascii")


class Bot:
    """Підміна site_chat_bot.py: записує 'out' у БД як справжній бот; фіксує список аргументів."""
    def __init__(self, record=True, rc=0):
        self.calls, self.record, self.rc = [], record, rc

    def __call__(self, args, timeout=90):
        self.calls.append(list(args))
        if args[0] == "reply" and self.record and self.rc == 0:
            c = sqlite3.connect(DB)
            c.execute("INSERT INTO messages(chat_id,direction,text,ts,forwarded) VALUES(?,?,?,?,1)", (int(args[1]), "out", args[2], "2026-11-21T21:00:00"))
            c.commit()
            c.close()
        return self.rc, ("2 чати" if args[0] == "list-new" else "")


def H(cmd, bot=None, now=NOW):
    return r.handle(cmd, runner=bot or Bot(), db_path=DB, log_path=LOG, now=now)


# 1. парсинг
for bad in ("", "id", "cat /opt/plutustoys/.env", "list; id", "list && id", "list $(id)", "digest 0", "digest 15", "digest x", "digest 1 2", "reply", "reply 111", "reply abc " + b64("x"), "reply 111 не-base64!", "reply 111 " + b64("x") + " extra",
            "reply 1234567890123456 " + b64("x"), "run", "reply 111 `id`"):
    b = Bot()
    code, out = H(bad, b)
    chk(f"відхилено без виклику бота: {bad[:40]!r}", code == 2 and not b.calls and "ВІДХИЛЕНО" in out)
b = Bot()
code, out = H("list", b)
chk("list → бот list-new, код 0", code == 0 and b.calls == [["list-new"]] and "2 чати" in out)
b = Bot()
code, out = H("digest 3", b)
chk("digest 3 → бот digest 3", code == 0 and b.calls == [["digest", "3"]])

# 2. reply: успіх, список аргументів
LOG.unlink(missing_ok=True)
txt = "Добрий день! Каталог: https://plutustoys.com.ua/catalog.html. Дякуємо!"
b = Bot()
code, out = H("reply 111 " + b64(txt), b)
chk("reply відомому чату → код 0, бот викликаний СПИСКОМ [reply, chat_id, текст] (без shell)", code == 0 and b.calls == [["reply", "111", txt]] and "відправлено" in out)
rows = [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines()]
chk("журнал: час, chat_id, текст, результат sent", len(rows) == 1 and rows[0]["chat_id"] == 111 and rows[0]["text"] == txt and rows[0]["result"] == "sent" and rows[0]["ts"].startswith("2026-11-21T21:00"))

# 3. заборонені тексти і чати
for label, text in (("порожній", "   "), ("довгий", "а" * 1001), ("чуже посилання", "див. https://evil.example/x"), ("www без схеми", "зайдіть на www.evil.com"), ("http замість https", "http://plutustoys.com.ua/x"),
                    ("схоже на картку", "картка 4111 1111 1111 1111"), ("картка через дефіси", "4111-1111-1111-1111"), ("керівний символ", "привіт\x07")):
    b = Bot()
    code, out = H("reply 111 " + b64(text), b)
    chk(f"reply відхилено: {label}", code == 2 and not b.calls)
b = Bot()
code, out = H("reply 999 " + b64("Добрий день"), b)
chk("reply невідомому чату (нам не писав) → відхилено, бот не викликаний", code == 2 and not b.calls and "невідомий" in out)
b = Bot()
code, out = H("reply 111 " + b64("Лінк: https://plutustoys.com.ua/podarunky.html, ціна 1250 грн, артикул 123456789"), b)
chk("легітимний текст з 9-значним артикулом і plutustoys-посиланням проходить", code == 0 and len(b.calls) == 1)
log_before = len(LOG.read_text(encoding="utf-8").splitlines())
chk("відхилені спроби reply теж у журналі (result=rejected)", any(json.loads(x)["result"] == "rejected" for x in LOG.read_text(encoding="utf-8").splitlines()))

# 4. «відправлено» лише при записі 'out'
b = Bot(record=False)
code, out = H("reply 222 " + b64("Вітаю!"), b)
chk("бот повернув 0, але не записав 'out' (токен не задано) → НЕ «відправлено», код 1", code == 1 and "відправлено" not in out)
b = Bot(rc=1)
code, out = H("reply 222 " + b64("Вітаю 2!"), b)
chk("бот rc=1 → код 1, у журналі bot_failed", code == 1 and json.loads(LOG.read_text(encoding="utf-8").splitlines()[-1])["result"] == "bot_failed")

# 5. ліміт на годину
LOG.unlink(missing_ok=True)
for i in range(r.MAX_REPLIES_PER_HOUR):
    r._log({"ts": (NOW - timedelta(minutes=10 + i)).isoformat(), "action": "reply", "chat_id": 111, "text": "x", "result": "sent"}, LOG)
b = Bot()
code, out = H("reply 111 " + b64("ще одна"), b)
chk(f"{r.MAX_REPLIES_PER_HOUR} відповідей за годину → 13-та відхилена, бот не викликаний", code == 2 and not b.calls and "ліміт" in out)
code, out = H("reply 111 " + b64("ще одна"), Bot(), now=NOW + timedelta(hours=2))
chk("через 2 години ліміт знову вільний", code == 0)

# 6. локальна обгортка
cmd = w.build_remote_command(["reply", "111", "Привіт", "світ", "'; rm -rf /; echo '"])
parts = cmd.split(" ")
chk("support_relay: reply → «reply <id> <base64>» без підстановки тексту як є", parts[0] == "reply" and parts[1] == "111" and len(parts) == 3 and base64.b64decode(parts[2]).decode("utf-8") == "Привіт світ '; rm -rf /; echo '" and "rm -rf" not in cmd)
chk("support_relay: reply з stdin ('-')", w.build_remote_command(["reply", "5", "-"], "Текст\n").split(" ")[2] == b64("Текст"))
chk("support_relay: list / digest", w.build_remote_command(["list"]) == "list" and w.build_remote_command(["digest", "2"]) == "digest 2" and w.build_remote_command(["digest"]) == "digest")
for bad in ([], ["reply", "1"], ["list", "x"], ["foo"]):
    try:
        w.build_remote_command(bad)
        ok = False
    except SystemExit:
        ok = True
    chk(f"support_relay: некоректні аргументи {bad} → SystemExit", ok)

print("\nПідсумок:", "УСЕ ЗЕЛЕНЕ" if not F else f"ПРОВАЛИ: {F}")
sys.exit(1 if F else 0)
