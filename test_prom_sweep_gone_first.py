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
# ── запобіжники (аудит PR #680)
alerts = []
pcs.send_telegram_message = lambda m: alerts.append(m) or True
pcs.load_ledger = lambda: ledger
pcs.fetch_prom_products_by_external_ids = lambda ids: ({k: v for k, v in live.items() if k in ids}, set())
chk("ПОРОЖНІЙ toysi_ids (збій фетчу) → нічого не видаляємо (було б «усе зникло»)", pcs.find_stale_via_ledger(desired, set(), {}) == ([], []))
big_ledger = [str(i) for i in range(2, 2 + 700)]            # 700 «зниклих» > GONE_MASS_GUARD=600 — схоже на усічений каталог
pcs.load_ledger = lambda: big_ledger
pcs.fetch_prom_products_by_external_ids = lambda ids: ({k: {"status": "on_display"} for k in ids}, set())
alerts.clear()
td, tp = pcs.find_stale_via_ledger({"1"}, {"2", "3"}, {})
chk("масовий guard: >600 зниклих → шлях зниклих НЕ виконується, лишається старий (лише e in toysi_ids)", set(td) <= {"2", "3"})
chk("масовий guard кричить 🚨 у Telegram з поясненням «усічений каталог»", len(alerts) == 1 and "УСІЧЕНИЙ" in alerts[0])
small = [str(i) for i in range(2, 2 + 343)]                   # реальне накопичення 343 ≤ 600 — працює
pcs.load_ledger = lambda: small
alerts.clear()
td, tp = pcs.find_stale_via_ledger({"1"}, {"2", "3"}, {})
chk("343 зниклих (реальний випадок) ≤ guard → усі стають to_delist, без алерта", len(td) == 343 and not alerts)
chk("to_delist може бути >200 — кап 200 застосовується вище по стеку (LEDGER_MAX_DELIST_PER_RUN, не змінено)", pcs.LEDGER_MAX_DELIST_PER_RUN == 200 and pcs.GONE_MASS_GUARD == 600)
print()
if F:
    print("FAILED:", F)
    sys.exit(1)
print("ALL OK")
