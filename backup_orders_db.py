#!/opt/plutustoys/venv/bin/python
"""Бекап /opt/plutustoys/orders.db: sqlite3 online-backup (безпечно при паралельному записі),
перевірка цілісності копії, gzip, ротація 30 днів. Нічого не пише в оригінал.
Запуск: orders-db-backup.timer (кожні 6 год). Код виходу != 0 -> service failed -> видно в systemctl --failed."""
import gzip, os, shutil, sqlite3, sys, time
from datetime import datetime

SRC = "/opt/plutustoys/orders.db"
DST_DIR = "/opt/plutustoys/backups/orders"
KEEP_DAYS = 30
MIN_ORDERS = 1  # порожня копія = збій, не «успіх»


def main() -> int:
    os.makedirs(DST_DIR, mode=0o700, exist_ok=True)
    os.chmod(DST_DIR, 0o700)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    tmp = os.path.join(DST_DIR, f".tmp_{stamp}.db")
    final = os.path.join(DST_DIR, f"orders_{stamp}.db.gz")
    try:
        src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True, timeout=60)
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        dst.close()
        src.close()
        chk = sqlite3.connect(tmp)
        ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
        n = chk.execute("SELECT count(*) FROM orders").fetchone()[0]
        chk.close()
        if ok != "ok" or n < MIN_ORDERS:
            print(f"[orders-backup] КОПІЯ НЕВАЛІДНА: integrity={ok} orders={n}", file=sys.stderr)
            return 2
        with open(tmp, "rb") as f_in, gzip.open(final + ".part", "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
        os.chmod(final + ".part", 0o600)
        os.replace(final + ".part", final)
        print(f"[orders-backup] ok {final} orders={n} size={os.path.getsize(final)}")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    cutoff = time.time() - KEEP_DAYS * 86400
    for name in os.listdir(DST_DIR):
        p = os.path.join(DST_DIR, name)
        if name.startswith("orders_") and name.endswith(".db.gz") and os.path.getmtime(p) < cutoff:
            os.remove(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
