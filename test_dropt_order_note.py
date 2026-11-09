#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dropt_order_note.py — «🛒 Нове замовлення» для кошика з dropt- несе примітку про ручне оформлення, поки конвеєр Dropt у пробному режимі (аудит 08.11.2026 перед відкриттям Dropt на сайті).
`python test_dropt_order_note.py` → exit 0/1."""
import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.pop("AUDIT_NO_TELEGRAM", None)
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
import site_order_api as api  # noqa: E402
import telegram_notify as tn  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


got = []
tn.notify_new_order = lambda platform, oid, total, n, pay="", test=False, note="": got.append(note)
api._NO_TELEGRAM = False
dr = {"order_id": "X1", "items": [{"toysi_code": "dropt-55087", "qty": 1, "price": 54}], "payment_method": "prepaid"}
plain = {"order_id": "X2", "items": [{"toysi_code": "123456", "qty": 1, "price": 100}], "payment_method": "prepaid"}
os.environ.pop("DROPT_RUNNER_APPLY", None)
api._notify_new_order(dr, 54)
api._notify_new_order(plain, 100)
chk("dropt- у кошику, конвеєр у пробному режимі → примітка «оформити ВРУЧНУ»", "ВРУЧНУ" in got[0] and "DROPT" in got[0])
chk("звичайний кошик (без dropt-) → примітки нема", "DROPT" not in got[1])
os.environ["DROPT_RUNNER_APPLY"] = "1"
api._notify_new_order(dr, 54)
chk("DROPT_RUNNER_APPLY=1 → «оформить конвеєр», без «ВРУЧНУ»", "конвеєр" in got[2] and "ВРУЧНУ" not in got[2])
api._notify_new_order(dr, 54, test=True)
chk("тестове (sandbox) замовлення — без примітки Dropt", "DROPT" not in got[3])
api._notify_new_order({"order_id": "X3", "items": [{"toysi_code": "dropt-1", "qty": 1, "price": 1}, "junk", None]}, 1, note="ЗМІШАНЕ")
chk("сміття в items не ламає, наявна примітка зберігається: «ЗМІШАНЕ · DROPT…»", got[4].startswith("ЗМІШАНЕ · DROPT"))
api._notify_new_order({"order_id": "X4", "items": [{"toysi_code": "dropt-59334", "qty": 2, "price": 84}, {"toysi_code": "123456", "qty": 1, "price": 100}, {"toysi_code": "dropt-55087", "qty": 1, "price": 54}]}, 1)
chk("артикули Dropt і кількості в повідомленні, не-Dropt позиція не потрапляє", "артикул Dropt: 59334×2, 55087×1" in got[5] and "123456" not in got[5])
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
