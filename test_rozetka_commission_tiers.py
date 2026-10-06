# -*- coding: utf-8 -*-
"""Rozetka «Дитячі іграшки» (ID 4265805): тариф rozetka_tariff_4643_2026-10-04.xlsx — 24% (<2000 ₴), 22% (2000–3999), 15% (4000–9999),
10% (10000–19999), 7% (20000+); у коді ×1,08 (ПДВ на доступ уже всередині). Аудитор 2026-10-06 п.3: сходинки <2000 не було (23,76% замість 25,92%)."""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import competitor_pricing as cp

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

t = cp._rozetka_tiered_commission
chk("73 ₴ → 25,92% (живо: комісія 18,92 ₴ у кабінеті)", abs(t(73) - 0.2592) < 1e-9 and abs(73 * t(73) - 18.92) < 0.01)
chk("межі: 1999 → 25,92%, 2000 → 23,76%", abs(t(1999) - 0.2592) < 1e-9 and abs(t(2000) - 0.2376) < 1e-9)
chk("3999 → 23,76%, 4000 → 16,2%", abs(t(3999) - 0.2376) < 1e-9 and abs(t(4000) - 0.162) < 1e-9)
chk("9999 → 16,2%, 10000 → 10,8%, 20000 → 7,56%", abs(t(9999) - 0.162) < 1e-9 and abs(t(10000) - 0.108) < 1e-9 and abs(t(20000) - 0.0756) < 1e-9)
tiers = cp.ROZETKA_CATEGORY_COMMISSION["дитячі іграшки"]
chk("сходинки відсортовані за зростанням, остання = inf", [c for c, _ in tiers] == sorted(c for c, _ in tiers) and tiers[-1][0] == float("inf"))
# флор для дешевого товару: вищий, ніж при 23,76% (комісія зросла)
f_new = cp.compute_floor(50.0, t(120.0) + cp.PAYMENT_COMMISSION["rozetka"], cp.MIN_PROFIT)
f_old = cp.compute_floor(50.0, 0.2376 + cp.PAYMENT_COMMISSION["rozetka"], cp.MIN_PROFIT)
chk(f"флор товару з собівартістю 50: {f_new:.2f} > {f_old:.2f} (дорожча комісія → вища підлога)", f_new > f_old)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ Rozetka сходинки комісії — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
