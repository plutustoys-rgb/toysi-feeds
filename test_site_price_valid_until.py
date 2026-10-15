#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_price_valid_until.py — 15.10.2026 (SEO): priceValidUntil = останній день НАСТУПНОГО календарного місяця (запас ≥ 1 міс., змінюється раз на місяць)."""
import os
import sys
from datetime import date
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


CASES = [(date(2026, 10, 15), date(2026, 11, 30)), (date(2026, 10, 31), date(2026, 11, 30)), (date(2026, 11, 1), date(2026, 12, 31)), (date(2026, 12, 31), date(2027, 1, 31)),
         (date(2027, 1, 31), date(2027, 2, 28)), (date(2027, 12, 5), date(2028, 1, 31)), (date(2027, 1, 15), date(2027, 2, 28)), (date(2027, 12, 31), date(2028, 1, 31))]
for t, exp in CASES:
    got = bs.price_valid_until(t)
    chk(f"{t} → {got} (очікується {exp})", got == exp)
chk("запас ≥ 28 днів від будь-якого дня 2026–2028", all((bs.price_valid_until(date.fromordinal(d)) - date.fromordinal(d)).days >= 28 for d in range(date(2026, 1, 1).toordinal(), date(2028, 12, 31).toordinal())))
src = (HERE / "site" / "build_site.py").read_text(encoding="utf-8")
chk("JSON-LD бере price_valid_until(), не «сьогодні+7»", "else price_valid_until())).isoformat()" in src and "PRICE_VALID_DAYS" not in src)
print()
if F:
    print(f"FAILED ({len(F)}):")
    sys.exit(1)
print("ALL OK")
