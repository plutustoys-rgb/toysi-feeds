#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prom_sweep_gone_first.py — чистка «поза топом» (prom_catalog_sync.find_stale_via_ledger): картки, що ЗНИКЛИ з каталогу Toysi, теж кандидати й ідуть ПЕРШИМИ (12.10.2026).
Раніше `e in toysi_ids` виключав їх назавжди (332+11 мертвих карток у слотах 5 878/6 000). Інваріанти: кожен кандидат усе одно звіряється НАЖИВО (лише status != deleted → to_delist);
тих, що в топі (desired_ids), не чіпаємо; зниклі з Toysi — першими; 404/надгробки → to_prune. Самодостатній (мережа замокана)."""
import sys

import prom_catalog_sync as pcs

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


ledger = ["1", "2", "3", "4", "5", "6", "7"]
desired = {"1"}                  # у топі — не чіпаємо
toysi = {"2", "3", "6"}          # ще є в каталозі Toysi
# 4, 5, 7 — зникли з Toysi
pcs.load_ledger = lambda: ledger
saved = {}
pcs._load_ledger_checked = lambda: set()
pcs._save_ledger_checked = lambda c: saved.update(c=set(c))
live = {"2": {"status": "on_display"}, "3": {"status": "on_display"}, "4": {"status": "on_display"}, "5": {"status": "deleted"}, "7": {"status": "on_display"}}   # 6 — 404, 5 — надгробок
pcs.fetch_prom_products_by_external_ids = lambda ids: ({k: v for k, v in live.items() if k in ids}, set())
to_delist, to_prune = pcs.find_stale_via_ledger(desired, toysi, {})
chk("у топі (1) не чіпаємо", "1" not in to_delist and "1" not in to_prune)
chk("зниклі з Toysi (4, 7) тепер кандидати й підтверджені живими → в to_delist", "4" in to_delist and "7" in to_delist)
chk("зниклі з Toysi стоять ПЕРЕД тими, що ще є в Toysi (під кап 200)", to_delist.index("4") < to_delist.index("2") and to_delist.index("7") < to_delist.index("3"))
chk("ті, що ще в Toysi (2, 3) — як і раніше в to_delist", "2" in to_delist and "3" in to_delist)
chk("надгробок (5, deleted) і 404 (6) → to_prune, НЕ to_delist", "5" in to_prune and "6" in to_prune and "5" not in to_delist and "6" not in to_delist)
chk("sweep-курсор записав усіх перевірених кандидатів (крім топа)", saved.get("c") == {"2", "3", "4", "5", "6", "7"})
# індетермінований (мережевий збій) не видаляється й не чиститься з журналу
pcs.fetch_prom_products_by_external_ids = lambda ids: ({}, set(ids))
td, tp = pcs.find_stale_via_ledger(desired, toysi, {})
chk("мережевий збій (indeterminate) → нічого не видаляємо і не чистимо журнал", td == [] and tp == [])
# порожній журнал
pcs.load_ledger = lambda: []
chk("порожній журнал → ([], [])", pcs.find_stale_via_ledger(desired, toysi, {}) == ([], []))
print()
if F:
    print("FAILED:", F)
    sys.exit(1)
print("ALL OK")
