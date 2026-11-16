#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_orders_db_busy_timeout.py — orders_db.get_connection чекає блокування запису, а не падає за 5 с.

ЖИВИЙ ВИПАДОК 16-17.11.2026: order-status-tracker тримає запис 25-33 с, order-pipeline стартував у ту саму секунду
і тричі впав «database is locked» (journalctl order-pipeline.service 23:24, 23:54, 00:24).

ІНВАРІАНТИ:
  - сам факт колізії відтворюється: звичайний sqlite3.connect(...) (таймаут 5 с) падає, поки інший процес тримає запис;
  - orders_db.get_connection у тому ж вікні ДОЧЕКАЄТЬСЯ й запише (рядок змінений, коміт відбувся);
  - BUSY_TIMEOUT_S перекриває найдовший відомий прогін трекера (> 35 с);
  - реальна, не блокуюча зміна без конкуренції працює як раніше.
Тимчасова теча, без мережі й боєвої БД (ORDERS_DB_PATH ставиться ДО імпорту orders_db). ~8 с.
"""
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = Path(tempfile.mkdtemp())
DB = str(TMP / "orders.db")
os.environ["ORDERS_DB_PATH"] = DB
sys.path.insert(0, str(Path(__file__).parent))
import orders_db  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


c0 = sqlite3.connect(DB)
c0.execute("CREATE TABLE t (id TEXT PRIMARY KEY, v INTEGER)")
c0.execute("INSERT INTO t VALUES ('a', 0)")
c0.commit()
c0.close()

chk(f"BUSY_TIMEOUT_S = {orders_db.BUSY_TIMEOUT_S} с перекриває найдовший прогін трекера (~35 с)", orders_db.BUSY_TIMEOUT_S >= 60)

HOLD_S = 7.0
started = threading.Event()


def holder():
    """Імітує order-status-tracker: відкриває запис і тримає його довше за 5 с."""
    h = sqlite3.connect(DB, timeout=1)
    h.execute("BEGIN IMMEDIATE")
    h.execute("UPDATE t SET v = 1 WHERE id = 'a'")
    started.set()
    time.sleep(HOLD_S)
    h.commit()
    h.close()


th = threading.Thread(target=holder)
th.start()
started.wait(5)

# 1) відтворення збою: звичайне з'єднання з таймаутом за замовчуванням (5 с) — як було в orders_db до виправлення
plain_err = []


def plain():
    pc = sqlite3.connect(DB)
    try:
        pc.execute("UPDATE t SET v = 100 WHERE id = 'a'")
        pc.commit()
    except sqlite3.OperationalError as e:
        plain_err.append(str(e))
    finally:
        pc.close()


tp = threading.Thread(target=plain)
tp.start()

# 2) виправлення: get_connection чекає в тому ж вікні
t0 = time.time()
try:
    with orders_db.get_connection() as conn:
        conn.execute("UPDATE t SET v = 200 WHERE id = 'a'")
    err = None
except sqlite3.OperationalError as e:
    err = str(e)
waited = time.time() - t0
tp.join()
th.join()

chk(f"колізію відтворено: звичайний connect (5 с) упав: {plain_err}", plain_err == ["database is locked"])
chk(f"get_connection дочекався утримувача (чекав {waited:.1f} с) і не впав: {err}", err is None and waited >= HOLD_S - 1.5)
c = sqlite3.connect(DB)
v = c.execute("SELECT v FROM t WHERE id='a'").fetchone()[0]
c.close()
chk(f"запис виконано й закомічено (v={v}, очікувано 200 — після утримувача)", v == 200)

t1 = time.time()
with orders_db.get_connection() as conn:
    conn.execute("UPDATE t SET v = 300 WHERE id = 'a'")
chk(f"без конкуренції — миттєво ({time.time() - t1:.2f} с)", time.time() - t1 < 1.0)

if F:
    print(f"\n❌ {len(F)} перевірок не пройшло")
    sys.exit(1)
print("\n✅ orders_db.get_connection чекає блокування запису — усі перевірки коректні.")
