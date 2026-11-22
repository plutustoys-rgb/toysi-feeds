#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_supplier_export_grace.py — експортери постачальників (rt/kv) не падають на ТИМЧАСОВОМУ збої постачальника, поки попередній файл свіжий (22.11.2026: щогодини 504 від RoyalToys / 500 від KIDDISVIT → юніт failed → 🔴 «потрібен власник»).
ІНВАРІАНТИ: ok → 0; збій + файл свіжіший за 2,5 год (менше порога 3 год у stock_snapshot) → 0 (попередження в stderr); збій + файл старший за 6 год → 1; збій + файлу нема → 1; поріг з env SUPPLIER_EXPORT_GRACE_H; kv_supplier_export використовує той самий grace_exit.
`python test_supplier_export_grace.py` → exit 0/1."""
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import rt_supplier_export as rt  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


TMP = Path(tempfile.mkdtemp(prefix="grace_"))
f = TMP / "supplier.json"
f.write_text("{}", encoding="utf-8")
NOW = time.time()


def age(h):
    os.utime(f, (NOW - h * 3600, NOW - h * 3600))


age(0.5)
chk("ok=True → 0 (файл не потрібен)", rt.grace_exit(True, TMP / "nope.json", "t") == 0)
chk("збій, файлу нема → 1", rt.grace_exit(False, TMP / "nope.json", "t", now_ts=NOW) == 1)
age(0.5)
chk("збій, файл 0,5 год → 0", rt.grace_exit(False, f, "t", now_ts=NOW) == 0)
age(2.4)
chk("збій, файл 2,4 год → 0 (можна пережити 2 збої підряд)", rt.grace_exit(False, f, "t", now_ts=NOW) == 0)
age(2.6)
chk("збій, файл 2,6 год → 1 (далі 3 год залишки зникають на сайті — не маскуємо)", rt.grace_exit(False, f, "t", now_ts=NOW) == 1)
age(20)
chk("збій, файл 20 год → 1", rt.grace_exit(False, f, "t", now_ts=NOW) == 1)
age(2)
chk("власний поріг 1 год: файл 2 год → 1", rt.grace_exit(False, f, "t", grace_h=1, now_ts=NOW) == 1)
chk("власний поріг 3 год: файл 2 год → 0", rt.grace_exit(False, f, "t", grace_h=3, now_ts=NOW) == 0)
os.utime(f, (NOW + 3600, NOW + 3600))
chk("mtime у майбутньому (годинник/збій) → 1, не маскуємо", rt.grace_exit(False, f, "t", now_ts=NOW) == 1)
for val, want in (("abc", 2.5), ("", 2.5), ("nan", 2.5), ("inf", 2.5), ("-1", 2.5), ("1", 1.0), ("2", 2.0), ("10", 3.0)):
    os.environ["SUPPLIER_EXPORT_GRACE_H"] = val
    chk(f"SUPPLIER_EXPORT_GRACE_H={val!r} → {want} год (некоректне не ламає імпорт; не більше 3)", rt._grace_hours() == want)
os.environ.pop("SUPPLIER_EXPORT_GRACE_H", None)
chk("без env → 2,5 год за замовчуванням", rt._grace_hours() == 2.5)
import subprocess  # noqa: E402
r = subprocess.run([sys.executable, "-X", "utf8", "-c", "import rt_supplier_export"], cwd=str(HERE), env={**os.environ, "SUPPLIER_EXPORT_GRACE_H": "abc"}, capture_output=True, text=True, timeout=120)
chk("імпорт rt_supplier_export з SUPPLIER_EXPORT_GRACE_H=abc НЕ падає (його імпортують stock_snapshot, np_ttn, rt_order_runner)", r.returncode == 0)
src_kv = (HERE / "kv_supplier_export.py").read_text(encoding="utf-8")
src_rt = (HERE / "rt_supplier_export.py").read_text(encoding="utf-8")
chk("обидва експортери викликають grace_exit у __main__", "rt.grace_exit(" in src_kv and "grace_exit(run_once(" in src_rt)
print("\nПідсумок:", "УСЕ ЗЕЛЕНЕ" if not F else f"ПРОВАЛИ: {F}")
sys.exit(1 if F else 0)
