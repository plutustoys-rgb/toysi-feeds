#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_chat_watch.py — сторож чату підтримки (site_chat_watch.py), 21.11.2026; перероблено за аудитом #1961 (НЕ ЧИСТО: сито telegram_triage глушило алерт).
ІНВАРІАНТИ: (1) «очікує» = вхідне, новіше за останню відповідь ЛЮДИНИ ('out'), старше 30 хв і молодше 72 год; автовідповідь бота ('auto') відповіддю не вважається; (2) алерт агрегований, БЕЗ тексту клієнтів і БЕЗ chat_id;
(3) відправка — через бота чату (TelegramAPI.sendMessage → SITE_CHAT_OWNER_CHAT_ID), НЕ через telegram_notify; нема токена/власника або збій Telegram → штамп повтору НЕ ставиться (повтор через 10 хв); (4) повтор того самого алерту не частіше ніж раз на 4 год;
(5) вночі (за Києвом) не шлемо; (6) --dry нічого не шле; (7) нема БД / пошкоджена → код 1 (юніт failed); тимчасове блокування БД → код 0; (8) SITE_CHAT_DB з env враховується.
Без мережі. `python test_site_chat_watch.py` → exit 0/1."""
import os
import sqlite3
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
for k in ("SITE_CHAT_DB", "SITE_CHAT_BOT_TOKEN", "SITE_CHAT_OWNER_CHAT_ID"):
    os.environ.pop(k, None)
import site_chat_watch as w  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


TMP = Path(tempfile.mkdtemp(prefix="watch_"))
DB = TMP / "site_chat.db"
ST = TMP / "state.json"
NOW = datetime(2026, 11, 22, 12, 0, 0)


def mk(rows):
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, chat_id INTEGER, direction TEXT, text TEXT, ts TEXT, forwarded INTEGER)")
    for cid, d, t, ts in rows:
        con.execute("INSERT INTO messages(chat_id,direction,text,ts,forwarded) VALUES(?,?,?,?,1)", (cid, d, t, ts))
    con.commit()
    con.close()


mk([(1, "in", "Іван Петренко, тел 0671112233, вул. Шевченка 5", "2026-11-22T11:00:00"),
    (2, "in", "питання", "2026-11-22T11:45:00"),
    (3, "in", "стара", "2026-11-22T09:00:00"), (3, "out", "відповіли", "2026-11-22T09:10:00"),
    (4, "in", "q", "2026-11-22T10:00:00"), (4, "auto", "автовідповідь", "2026-11-22T10:00:05"),
    (5, "in", "q1", "2026-11-22T08:00:00"), (5, "out", "a1", "2026-11-22T08:05:00"), (5, "in", "q2", "2026-11-22T10:30:00"),
    (6, "in", "давнє", "2026-11-18T10:00:00")])                                         # 4 доби тому — поза MAX_AGE_H
rows = w.waiting(DB, NOW, 30)
chk("очікують: чати 1 (60 хв), 4 (120, авто не рахується), 5 (90); 2 — рано; 3 — відповіли; 6 — старше 72 год", {r[1]: r[3] for r in rows} == {1: 60, 4: 120, 5: 90})
key, text = w.build_alert(rows)
chk("алерт агрегований: 3 повідомл., 3 чати, найдовше 120 хв", "3 повідомл. у 3 чат" in text and "120 хв" in text)
chk("алерт БЕЗ тексту клієнтів, ПІБ, телефону, адреси і БЕЗ chat_id", all(x not in text for x in ("Петренко", "0671112233", "Шевченка", "питання", "chat_id", "1, 4, 5")))
chk("ключ алерту прив'язаний до найстарішого очікуваного повідомлення (id 1)", key == "site_chat_unanswered:1")
chk("нема очікуваних → build_alert None", w.build_alert([]) is None)

# ── відправка + штамп + повтор
ST.unlink(missing_ok=True)
sent = []
ok_send = lambda t: (sent.append(t) or True)
rc = w.run(wait_min=30, db_path=DB, now=NOW, send=ok_send, state_path=ST)
chk("run вдень: рівно один алерт, код 0, штамп записано", rc == 0 and len(sent) == 1 and "site_chat_unanswered:1" in ST.read_text(encoding="utf-8"))
rc = w.run(wait_min=30, db_path=DB, now=NOW, send=ok_send, state_path=ST)
chk("повторний run одразу: алерт НЕ повторено (4 год)", rc == 0 and len(sent) == 1)
ST.write_text('{"site_chat_unanswered:1": %f}' % (__import__("time").time() - 5 * 3600), encoding="utf-8")
rc = w.run(wait_min=30, db_path=DB, now=NOW, send=ok_send, state_path=ST)
chk("штамп старший за 4 год → алерт надіслано знову", rc == 0 and len(sent) == 2)
ST.unlink(missing_ok=True)
fails = []
rc = w.run(wait_min=30, db_path=DB, now=NOW, send=lambda t: (fails.append(t) or False), state_path=ST)
chk("відправка не вдалась (False) → штамп НЕ ставиться (повтор наступного циклу)", rc == 0 and len(fails) == 1 and not ST.exists())
rc = w.run(wait_min=30, db_path=DB, now=NOW, send=lambda t: (fails.append(t) or False), state_path=ST)
chk("наступний цикл після невдачі пробує знову", len(fails) == 2)

# ── ніч / dry
sent.clear()
chk("run вночі (03:00): не шле, штампу нема", w.run(wait_min=30, db_path=DB, now=datetime(2026, 11, 22, 3, 0, 0), send=ok_send, state_path=ST) == 0 and not sent and not ST.exists())
chk("run о 22:00: не шле (вікно 08:00–22:00)", w.run(wait_min=30, db_path=DB, now=datetime(2026, 11, 22, 22, 0, 0), send=ok_send, state_path=ST) == 0 and not sent)
chk("run о 21:59 (ще в межах вікна): шле", w.run(wait_min=30, db_path=DB, now=datetime(2026, 11, 22, 21, 59, 0), send=ok_send, state_path=ST) == 0 and len(sent) == 1)
ST.unlink(missing_ok=True)
sent.clear()
chk("--dry: нічого не шле", w.run(wait_min=30, dry=True, db_path=DB, now=NOW, send=ok_send, state_path=ST) == 0 and not sent and not ST.exists())
chk("kyiv_hour(now) → now.hour; kyiv_hour() — число 0..23", w.kyiv_hour(datetime(2026, 1, 1, 7, 0)) == 7 and 0 <= w.kyiv_hour() <= 23)

# ── помилки БД
chk("нема БД → код 1 (юніт failed → health_check)", w.run(wait_min=30, db_path=TMP / "nope.db", now=NOW, send=ok_send, state_path=ST) == 1)
bad = TMP / "bad.db"
bad.write_bytes(b"not a sqlite file")
chk("пошкоджена БД → код 1", w.run(wait_min=30, db_path=bad, now=NOW, send=ok_send, state_path=ST) == 1)
real = sqlite3.connect


def locked(*a, **k):
    raise sqlite3.OperationalError("database is locked")


sqlite3.connect = locked
try:
    rc = w.run(wait_min=30, db_path=DB, now=NOW, send=ok_send, state_path=ST)
finally:
    sqlite3.connect = real
chk("тимчасове блокування БД ботом → код 0 (без failed), алерту нема", rc == 0 and not sent)
mk([(9, "in", "q", "не-дата")])
chk("некоректний ts пропускається без падіння", w.waiting(DB, NOW, 30) == [])
mk([(100 + i, "in", "q", "2026-11-22T10:00:00") for i in range(12)])
chk("12 чатів → алерт «12 повідомл. у 12 чат»", "12 повідомл. у 12 чат" in w.build_alert(w.waiting(DB, NOW, 30))[1])

# ── SITE_CHAT_DB з env
mk([(7, "in", "q", "2026-11-22T10:00:00")])
os.environ["SITE_CHAT_DB"] = str(DB)
chk("SITE_CHAT_DB з env враховується (без явного db_path)", len(w.waiting(None, NOW, 30)) == 1)
os.environ.pop("SITE_CHAT_DB")

# ── send_owner: через бота чату
chk("send_owner без токена/власника → False, без винятку", w.send_owner("x") is False)
calls = []


class FakeAPI:
    def __init__(self, token):
        calls.append(("init", token))

    def call(self, method, **params):
        calls.append((method, params))
        return {"message_id": 1}


fake = types.ModuleType("site_chat_bot")
fake.TelegramAPI = FakeAPI
sys.modules["site_chat_bot"] = fake
os.environ["SITE_CHAT_BOT_TOKEN"] = "TOKEN-SECRET"
os.environ["SITE_CHAT_OWNER_CHAT_ID"] = "123456"
chk("send_owner: sendMessage на SITE_CHAT_OWNER_CHAT_ID через TelegramAPI бота чату", w.send_owner("привіт") is True and calls[-1] == ("sendMessage", {"chat_id": 123456, "text": "привіт"}))


class BoomAPI(FakeAPI):
    def call(self, method, **params):
        raise RuntimeError("telegram down TOKEN-SECRET")


fake.TelegramAPI = BoomAPI
import io  # noqa: E402
from contextlib import redirect_stderr  # noqa: E402
buf = io.StringIO()
with redirect_stderr(buf):
    res = w.send_owner("x")
chk("збій Telegram → False, токен НЕ потрапляє в лог", res is False and "TOKEN-SECRET" not in buf.getvalue())
src = (HERE / "site_chat_watch.py").read_text(encoding="utf-8")
chk("сторож НЕ використовує telegram_notify (сито «лише замовлення» глушить), БД лише mode=ro, без INSERT/UPDATE/DELETE", "import telegram_notify" not in src and "from telegram_notify" not in src and "mode=ro" in src and "INSERT" not in src and "UPDATE" not in src and "DELETE" not in src)

# ── страховка власнику лише після 2 год (рішення власника 22.11: кожне повідомлення будить того, хто веде чат)
chk("WAIT_MIN за замовчуванням = 120 хв", w.WAIT_MIN == 120)
mk([(1, "in", "q", "2026-11-22T11:00:00"), (2, "in", "q", "2026-11-22T09:30:00")])        # 60 хв і 150 хв до NOW=12:00
sent2 = []
ST.unlink(missing_ok=True)
w.run(db_path=DB, now=NOW, send=lambda t: (sent2.append(t) or True), state_path=ST)
chk("за замовчуванням алертить лише чат, що чекає 150 хв (>=120); 60 хв — ні", len(sent2) == 1 and "1 повідомл. у 1 чат" in sent2[0] and "150 хв" in sent2[0] and "страховка" in sent2[0])

print("\nПідсумок:", "УСЕ ЗЕЛЕНЕ" if not F else f"ПРОВАЛИ: {F}")
sys.exit(1 if F else 0)
