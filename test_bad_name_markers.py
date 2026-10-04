# -*- coding: utf-8 -*-
"""Регрес _BAD_NAME_MARKERS: лістинги «розпродаж/распродажа» (сток, не ринкова ціна) не беруться за конкурента.
Живий кейс: Консультант 2026-10-04 — «распродаж» не відсіювався, ціна стоку ставала еталоном."""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import competitor_pricing as cp
import prom_competitor_pricer as pc

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

base = "Плюшевий ведмідь великий 60 см"
def cands(name2):
    raw = [{"company_id": 1, "name": name2, "price": 300.0, "id": 1, "presence": {"isAvailable": True}}]
    return pc._rank_competitor_candidates(raw, base, 300.0) if hasattr(pc, "_rank_competitor_candidates") else None

for bad in ("РАСПРОДАЖА Плюшевий ведмідь великий 60 см", "Розпродаж! Плюшевий ведмідь великий 60 см",
            "Плюшевый медведь большой 60 см распродажа склада", "Плюшевий ведмідь великий 60 см (розпродано)",
            "Уцінка Плюшевий ведмідь великий 60 см"):
    chk(f"відсіяно: {bad}", not cands(bad))
ok = cands("Плюшевий ведмідь великий 60 см")
chk("звичайний лістинг проходить", bool(ok))
chk("нормальна назва без маркерів не зачеплена", not any(m in "плюшевий ведмідь великий 60 см" for m in cp._BAD_NAME_MARKERS))
chk("маркери додані", all(m in cp._BAD_NAME_MARKERS for m in ("распродаж", "розпродаж")))
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ _BAD_NAME_MARKERS — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
