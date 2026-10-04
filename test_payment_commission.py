# -*- coding: utf-8 -*-
"""Регрес PAYMENT_COMMISSION (competitor_pricing.py): еквайринг RozetkaPay 1.5% на Rozetka-картках підтверджено
живими реєстрами 2026-10-04 (21 платіж, 3600.00 грн, утримано 54.05 → 1.501%). Було 0.0 → ціни/підлога Rozetka
рахувались на ~1.5-1.8% оптимістично. Тест фіксує значення й напрямок ефекту на підлогу."""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import competitor_pricing as cp

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

chk("PAYMENT_COMMISSION['rozetka'] = 0.015", cp.PAYMENT_COMMISSION["rozetka"] == 0.015)
chk("prom лишився 0.037", cp.PAYMENT_COMMISSION["prom"] == 0.037)
for cost in (50.0, 200.0, 1200.0):
    f0, c0 = cp._resolve_rozetka_floor(cost, cp.MIN_PROFIT_COMPETITOR_FLOOR, 0.0)
    f1, c1 = cp._resolve_rozetka_floor(cost, cp.MIN_PROFIT_COMPETITOR_FLOOR, cp.PAYMENT_COMMISSION["rozetka"])
    chk(f"cost={cost}: підлога Rozetka з еквайрингом ВИЩА, і зростання в межах 1.2–3% ({f0:.2f} → {f1:.2f})",
        f1 > f0 and 1.012 <= f1 / f0 <= 1.03)
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ PAYMENT_COMMISSION — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
