#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_backup_orders_db.py — бекап orders.db (backup_orders_db.py, VPS orders-db-backup.timer кожні 6 год).

ІНВАРІАНТИ:
  - з живої бази виходить gzip-копія, що розпаковується в валідну sqlite-базу з тими самими замовленнями;
  - оригінал не змінюється (відкривається mode=ro);
  - порожня таблиця orders = збій (код 2), а не «успіх»; тимчасовий файл не лишається;
  - ротація видаляє лише orders_*.db.gz старші за KEEP_DAYS, чужі файли не чіпає.
Тимчасова тека, без мережі й боєвої БД.
"""
import gzip
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import backup_orders_db as b  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


TMP = Path(tempfile.mkdtemp())
SRC = TMP / "orders.db"
DST = TMP / "backups"
b.SRC, b.DST_DIR = str(SRC), str(DST)


def make_db(n):
    if SRC.exists():
        SRC.unlink()
    c = sqlite3.connect(SRC)
    c.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, total REAL)")
    for i in range(n):
        c.execute("INSERT INTO orders VALUES (?, ?)", (f"PT-{i}", 100.0 + i))
    c.commit()
    c.close()


make_db(3)
before = SRC.read_bytes()
chk("бекап живої бази: код 0", b.main() == 0)
files = sorted(p for p in DST.iterdir() if p.name.startswith("orders_") and p.name.endswith(".db.gz"))
chk("рівно один файл orders_*.db.gz, без тимчасових .tmp/.part", len(files) == 1 and not [p for p in DST.iterdir() if p.name.startswith(".tmp") or p.name.endswith(".part")])
if files:
    out = TMP / "restored.db"
    out.write_bytes(gzip.open(files[0], "rb").read())
    c = sqlite3.connect(out)
    chk("копія розпаковується і має 3 замовлення, integrity ok",
        c.execute("SELECT count(*) FROM orders").fetchone()[0] == 3 and c.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    c.close()
    chk("права файлу 600", (files[0].stat().st_mode & 0o777) == 0o600 or os.name == "nt")
chk("оригінал не змінено", SRC.read_bytes() == before)

# порожня таблиця = збій
for p in DST.iterdir():
    p.unlink()
make_db(0)
rc = b.main()
chk("порожня таблиця orders: код 2 (не «успіх»)", rc == 2)
chk("при збої файл копії не створено і тимчасового не лишилось", list(DST.iterdir()) == [])

# ротація
make_db(2)
old = DST / "orders_2020-01-01_0000.db.gz"
fresh = DST / "orders_2099-01-01_0000.db.gz"
alien = DST / "notes_old.txt"
for p in (old, fresh, alien):
    p.write_bytes(b"x")
past = time.time() - (b.KEEP_DAYS + 5) * 86400
for p in (old, alien):
    os.utime(p, (past, past))
chk("ротація: бекап виконується", b.main() == 0)
chk("ротація: старий orders_*.db.gz видалено", not old.exists())
chk("ротація: свіжий і чужий файли лишились", fresh.exists() and alien.exists())

sys.exit(1 if F else 0)
