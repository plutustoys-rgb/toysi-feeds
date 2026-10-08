# -*- coding: utf-8 -*-
"""ProSale вимкнено (08.10.2026, власник): комісія за замовлення Prom = 0 для УСІХ товарів, включно з fallback; решта площадок без змін.
`python test_prosale_off.py` → exit 0/1, мережа не потрібна."""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import competitor_pricing as cp

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

chk("у коді PROSALE_TIER = off, множник off = 0", cp.PROSALE_TIER == "off" and cp.PROSALE_TIER_MULTIPLIER["off"] == 0.0)
chk("Prom: комісія 0 для категорії з таблиці id (антистрес 2656), для категорії за назвою і для невідомої (fallback 0,20 НЕ діє)",
    cp.get_platform_commission("prom", None, None, 2656) == 0.0 and cp.get_platform_commission("prom", "пазли g-toys") == 0.0
    and cp.get_platform_commission("prom", "якась невідома категорія") == 0.0 and cp.get_platform_commission("prom") == 0.0)
chk("Rozetka/EVA без змін (комісія > 0)", cp.get_platform_commission("rozetka", None, 500) > 0 and cp.get_platform_commission("eva") > 0)
chk("комісія оплати Prom ЛИШАЄТЬСЯ (compute_total_commission = лише PAYMENT_COMMISSION)", abs(cp.compute_total_commission("prom", "пазли g-toys") - cp.PAYMENT_COMMISSION["prom"]) < 1e-9)
d_off = cp.decide_price_for_platform(100.0, None, "prom", "пазли g-toys")
cp.PROSALE_TIER = "standard"
d_std = cp.decide_price_for_platform(100.0, None, "prom", "пазли g-toys")
cp.PROSALE_TIER = "off"
chk("без конкурента: floor при off НИЖЧИЙ, ніж при standard (комісія випала з межі), ціна не вища і вища за собівартість (без конкурента діє множник NO_COMPETITOR_MULT)",
    d_off["floor"] < d_std["floor"] and d_off["price"] <= d_std["price"] and d_off["price"] > 100.0)
d_c_off = cp.decide_price_for_platform(100.0, 150.0, "prom", "пазли g-toys")
cp.PROSALE_TIER = "standard"
d_c_std = cp.decide_price_for_platform(100.0, 135.0, "prom", "пазли g-toys")
cp.PROSALE_TIER = "off"
chk("конкурент 150: при off можна підрізати (undercut), при standard конкурент 135 — вже floor/інша категорія", d_c_off["category"] == "undercut" and d_c_std["category"] in ("floor", "undercut"))
chk("floor при off лишається ВИЩИМ за собівартість з урахуванням оплати+3% (захист від збитку)", d_off["floor"] > 100.0 * (1 + 0.03))

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ ProSale off — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
