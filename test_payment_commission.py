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
    chk(f"cost={cost}: підлога Rozetka з еквайрингом ВИЩА, і зростання в межах 1.5–2.1% ({f0:.2f} → {f1:.2f})",
        f1 > f0 and 1.015 <= f1 / f0 <= 1.021)
# репрайсер Rozetka (95% цін фіду) тепер теж враховує еквайринг у флорі
import rozetka_competitor_repricer as rr
item = {"price": 200.0, "stock": 5}
rr.real_toysi_cost = lambda it: 200.0
state = {"commission": {"1": {"commission_pct": 23.76}}, "recommended": {}}
ov, _ = rr.build_overrides({"1": item}, state)
exp = rr._floor(200.0, 0.2376 + cp.PAYMENT_COMMISSION["rozetka"], cp.MIN_PROFIT)
chk(f"репрайсер: ціна без конкурента = флор з еквайрингом ({ov['1']} == {exp})", abs(ov["1"] - round(exp, 2)) < 0.011)
net = ov["1"] * (1 - 0.2376 - cp.PAYMENT_COMMISSION["rozetka"]) / 200.0 - 1
chk(f"чиста маржа після комісії та еквайрингу = {cp.MIN_PROFIT:.0%} ({net:.4f})", abs(net - cp.MIN_PROFIT) < 0.001)
ov2, _ = rr.build_overrides({"1": item}, {"commission": {"1": {"commission_pct": 23.76}},
                                          "recommended": {"1": {"internal": {"recommended": 10.0}}}})
net2 = ov2["1"] * (1 - 0.2376 - cp.PAYMENT_COMMISSION["rozetka"]) / 200.0 - 1
chk(f"конкурентний режим: стоїмо на 5%-флорі ПІСЛЯ еквайрингу ({net2:.4f})", abs(net2 - rr.ROZETKA_COMPETITOR_MARGIN) < 0.002)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ PAYMENT_COMMISSION — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
