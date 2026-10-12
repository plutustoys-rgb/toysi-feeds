#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_toysi_internal_id.py — id сайтового замовлення для Toysi (12.10.2026, знайдено при відповіді Shopify-code про адресні баги).
internal_order_id сайту «site_PT-20261010-142322-afbf46» = 30 символів, ліміт Toysi = 25; голе [:25] лишало з суфікса xxxxxx лише ПЕРШИЙ hex-символ → колізія двох замовлень однієї секунди ≈ 1/16 (а не 1/16 777 216),
а повтор id = Toysi response_code 2, який submit_order вважає УСПІХОМ (друге замовлення мовчки не створилось би).
Інваріанти: сайт → «site_YYYYMMDDHHMMSSxxxxxx» (рівно 25, суфікс збережено, різні замовлення одної секунди → різні id); Prom/Rozetka/EVA без змін;
невпізнане довге → [:25] як раніше; функція ідемпотентна; і route (build_toysi_order), і payload (build_order_create_payload) дають той самий id.
Мережа не потрібна. `python test_toysi_internal_id.py` → exit 0/1."""
import os
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")

import order_router as orr
import toysi_order_submit as tos

orr.settlement_raion = lambda *a, **k: ""
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


f = tos.toysi_internal_id
a = f("site_PT-20261010-142322-afbf46")
b = f("site_PT-20261010-142322-a1b2c3")   # перший hex-символ суфікса той самий: саме так [:25] давало колізію (з 6 символів суфікса вціляв лише 1)
chk("сайт: рівно 25 символів", len(a) == 25 and a == "site_20261010142322afbf46")
chk("сайт: однакова секунда, різний суфікс → РІЗНІ id (раніше збігались)", a != b)
chk("раніше [:25] давало однаковий id для обох (підтвердження дефекту)", "site_PT-20261010-142322-afbf46"[:25] == "site_PT-20261010-142322-a1b2c3"[:25])
for raw in ("prom_414634349", "rozetka_908234237", "eva_8-081850129", "site_T1", ""):
    chk(f"без змін: {raw!r}", f(raw) == raw)
chk("невпізнане довге → [:25]", f("x" * 40) == "x" * 25)
chk("site_PT з великими hex/іншим форматом → [:25] як раніше (не вгадуємо)", f("site_PT-20261010-142322-AFBF46") == "site_PT-20261010-142322-A")
chk("ідемпотентна", f(a) == a and f(f("x" * 40)) == "x" * 25)

order = dict(internal_order_id="site_PT-20261010-142322-afbf46", order_id="PT-20261010-142322-afbf46", platform="site", carrier="nova_poshta",
             customer_name="Іван Петренко", phone="380671112233", payment_method="cod", np_branch="м. Київ, Відділення №369",
             np_warehouse_number="369", np_city_ref=None, items=[{"toysi_code": "1", "qty": 1, "price": 100, "name": "x"}])
built = orr.build_toysi_order(order)
chk("build_toysi_order: id стиснутий", built is not None and built["internal_order_id"] == "site_20261010142322afbf46")
payload = tos.build_order_create_payload({**built, "internal_order_id": built["internal_order_id"]})
chk("payload: той самий id, ≤25", payload["internal_order_id"] == "site_20261010142322afbf46" and len(payload["internal_order_id"]) <= 25)
payload2 = tos.build_order_create_payload({**built, "internal_order_id": "site_PT-20261010-142322-afbf46"})
chk("payload з нестиснутим вхідним id дає той самий id (подвійний захист)", payload2["internal_order_id"] == payload["internal_order_id"])

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
