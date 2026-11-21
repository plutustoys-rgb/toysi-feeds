#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_chat_watch.py — сторож чату підтримки (site_chat_watch.py), 21.11.2026.
ІНВАРІАНТИ: (1) «очікує» = вхідне, новіше за останню відповідь ЛЮДИНИ (direction='out'), старше 30 хв; автовідповідь бота (kind='auto') відповіддю не вважається; (2) алерт агрегований, БЕЗ тексту клієнтів;
(3) вночі (до 08:00/після 22:00) алерт не шлемо; (4) --dry нічого не шле; (5) нема БД / збій алерту / БД пошкоджена → код 0, без винятку; (6) ключ алерту прив'язаний до найстарішого очікуваного повідомлення.
Без мережі. `python test_site_chat_watch.py` → exit 0/1."""
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import site_chat_watch as w  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


TMP = Path(tempfile.mkdtemp(prefix="watch_"))
DB = TMP / "site_chat.db"
NOW = datetime(2026, 11, 22, 12, 0, 0)


def mk(rows):
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, chat_id INTEGER, direction TEXT, text TEXT, ts TEXT, forwarded INTEGER)")
    for cid, d, t, ts in rows:
        con.execute("INSERT INTO messages(chat_id,direction,text,ts,forwarded) VALUES(?,?,?,?,1)", (cid, d, t, ts))
    con.commit()
    con.close()


mk([(1, "in", "Іван Петренко, тел 0671112233, вул. Шевченка 5", "2026-11-22T11:00:00"),      # 60 хв — очікує
    (2, "in", "питання", "2026-11-22T11:45:00"),                                            # 15 хв — ще рано
    (3, "in", "стара", "2026-11-22T09:00:00"), (3, "out", "відповіли", "2026-11-22T09:10:00"),  # відповіли
    (4, "in", "q", "2026-11-22T10:00:00"), (4, "auto", "автовідповідь", "2026-11-22T10:00:05"),  # лише авто — все ще чекає
    (5, "in", "q1", "2026-11-22T08:00:00"), (5, "out", "a1", "2026-11-22T08:05:00"), (5, "in", "q2", "2026-11-22T10:30:00")])  # нове після відповіді
rows = w.waiting(DB, NOW)
chk("очікують: чати 1 (60 хв), 4 (120 хв, авто не рахується), 5 (90 хв); 2 — рано; 3 — відповіли", sorted(r[1] for r in rows) == [1, 4, 5] and {r[1]: r[3] for r in rows} == {1: 60, 4: 120, 5: 90})
key, text = w.build_alert(rows)
chk("алерт агрегований: 3 повідомл., 3 чати, найдовше 120 хв, chat_id перелічено", "3 повідомл. у 3 чат" in text and "120 хв" in text and "1, 4, 5" in text)
chk("алерт БЕЗ тексту клієнтів (ПІБ, телефон, адреса)", all(x not in text for x in ("Петренко", "0671112233", "Шевченка", "питання")))
chk("ключ алерту прив'язаний до найстарішого очікуваного повідомлення (id 1)", key == "site_chat_unanswered:1")
chk("нема очікуваних → build_alert None", w.build_alert([]) is None)

sent = []
rc = w.run(db_path=DB, now=NOW, alert=lambda k, t, cooldown_sec=0: sent.append((k, t, cooldown_sec)))
chk("run вдень: один алерт, cooldown 4 год, код 0", rc == 0 and len(sent) == 1 and sent[0][2] == 4 * 3600)
sent.clear()
rc = w.run(db_path=DB, now=datetime(2026, 11, 22, 3, 0, 0), alert=lambda *a, **k: sent.append(a))
chk("run вночі (03:00): алерт НЕ надіслано (до ранку), код 0", rc == 0 and not sent)
rc = w.run(db_path=DB, now=datetime(2026, 11, 22, 22, 0, 0), alert=lambda *a, **k: sent.append(a))
chk("run о 22:00: алерт не шлемо (вікно 08:00–22:00)", rc == 0 and not sent)
rc = w.run(dry=True, db_path=DB, now=NOW, alert=lambda *a, **k: sent.append(a))
chk("--dry: нічого не шле", rc == 0 and not sent)


def boom(*a, **k):
    raise RuntimeError("telegram down")


chk("збій алерту → код 0, без винятку", w.run(db_path=DB, now=NOW, alert=boom) == 0)
chk("нема БД → код 0, алерту нема", w.run(db_path=TMP / "nope.db", now=NOW, alert=lambda *a, **k: sent.append(a)) == 0 and not sent)
bad = TMP / "bad.db"
bad.write_bytes(b"not a sqlite file")
chk("пошкоджена БД → [] і код 0", w.waiting(bad, NOW) == [] and w.run(db_path=bad, now=NOW, alert=lambda *a, **k: sent.append(a)) == 0)
mk([(9, "in", "q", "2026-11-22T11:50:00")])
chk("лише свіже (10 хв) → тиша", w.waiting(DB, NOW) == [])
mk([(9, "in", "q", "не-дата")])
chk("некоректний ts пропускається без падіння", w.waiting(DB, NOW) == [])
many = [(100 + i, "in", "q", "2026-11-22T10:00:00") for i in range(12)]
mk(many)
t = w.build_alert(w.waiting(DB, NOW))[1]
chk("12 чатів: у алерті перші 8 і «+4»", "(+4)" in t)
src = (HERE / "site_chat_watch.py").read_text(encoding="utf-8")
chk("БД відкривається лише для читання (mode=ro), нічого не пише", "mode=ro" in src and "INSERT" not in src and "UPDATE" not in src and "DELETE" not in src)

print("\nПідсумок:", "УСЕ ЗЕЛЕНЕ" if not F else f"ПРОВАЛИ: {F}")
sys.exit(1 if F else 0)
