#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prom_sweep_gone_first.py — чистка «поза топом» (prom_catalog_sync.find_stale_via_ledger): картки, що ЗНИКЛИ з каталогу Toysi, теж кандидати й ідуть ПЕРШИМИ,
але видаляються лише якщо були «зниклими» БЕЗПЕРЕРВНО ≥ GONE_MIN_AGE_HOURS (20 год) і лише за --apply (persist). Аудит PR #680 (3 раунди). Мережа замокана.
Інваріанти: топ не чіпаємо; живий GET на кожного; перший прогін зниклих НЕ видаляє; dry-run стан НЕ пише; повний каталог скидає лічильник (усічення самовиліковується);
тривале усічення <20 год НЕ видаляє; порожній toysi_ids → нічого; guard >600; битий/відсутній файл стану безпечний; підтверджені зниклі не ховаються у sweep-курсорі."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import prom_catalog_sync as pcs

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


T0 = datetime(2026, 10, 12, 7, 30)
STORE = {"gone": {}}
CHECKED = {"c": set()}
pcs._load_gone_seen = lambda: dict(STORE["gone"])
pcs._save_gone_seen = lambda st: STORE.__setitem__("gone", dict(st)) or True
pcs._load_ledger_checked = lambda: set(CHECKED["c"])
pcs._save_ledger_checked = lambda c: CHECKED.__setitem__("c", set(c))
alerts = []
pcs.send_throttled_alert = lambda key, m, cooldown_sec=0: alerts.append(m) or True
LIVE = {}
pcs.fetch_prom_products_by_external_ids = lambda ids: ({k: v for k, v in LIVE.items() if k in ids}, set())


def run(ledger, desired, toysi, hours=0, persist=True):
    pcs.load_ledger = lambda: list(ledger)
    return pcs.find_stale_via_ledger(set(desired), set(toysi), {}, persist=persist, now=T0 + timedelta(hours=hours))


def reset():
    STORE["gone"] = {}
    CHECKED["c"] = set()
    alerts.clear()


ledger = ["1", "2", "3", "4", "5", "6", "7"]
LIVE.update({"2": {"status": "on_display"}, "3": {"status": "on_display"}, "4": {"status": "on_display"}, "5": {"status": "deleted"}, "7": {"status": "on_display"}})
toysi = {"2", "3", "6"}      # 4, 5, 7 — зникли з Toysi; 1 — у топі; 6 — 404 на Prom; 5 — надгробок

reset()
td, tp = run(ledger, {"1"}, toysi, 0)
chk("ПЕРШИЙ прогін: зниклі (4, 7) НЕ видаляються; ті, що ще в Toysi (2, 3) — як і раніше", "4" not in td and "7" not in td and "2" in td and "3" in td)
chk("топ (1) не чіпаємо; 404 (6, ще в Toysi) → to_prune", "1" not in td and "1" not in tp and "6" in tp)
td, tp = run(ledger, {"1"}, toysi, 4)
chk("ДРУГИЙ прогін через 4 год: ще НЕ підтверджені (потрібно ≥20 год безперервно)", "4" not in td and "7" not in td)
td, tp = run(ledger, {"1"}, toysi, 21)
chk("через 21 год безперервно «зниклі» → підтверджені й в to_delist (живі, status != deleted)", "4" in td and "7" in td)
chk("підтверджені зниклі стоять ПЕРЕД тими, що ще в Toysi (під кап 200)", td.index("4") < td.index("2") and td.index("7") < td.index("3"))
chk("надгробок (5) підтвердженого зниклого → to_prune", "5" in tp)

# dry-run не пише стан
reset()
run(ledger, {"1"}, toysi, 0, persist=False)
chk("dry-run (persist=False) стан «зниклих» НЕ пише", STORE["gone"] == {})
run(ledger, {"1"}, toysi, 0, persist=True)
before = dict(STORE["gone"])
run(ledger, {"1"}, toysi, 1, persist=False)
chk("dry-run не оновлює існуючий стан", STORE["gone"] == before)
td, _ = run(ledger, {"1"}, toysi, 1, persist=False)
chk("діагностичний dry-run + одразу --apply НЕ «підтверджують» за хвилини", "4" not in td)

# усічення, що самовиліковується / що триває <20 год
reset()
run(["1", "2", "3", "4", "5"], {"1"}, {"1", "2"}, 0)                       # каталог усічений: 3,4,5 «зниклі»
LIVE.update({k: {"status": "on_display"} for k in "345"})
td, _ = run(["1", "2", "3", "4", "5"], {"1"}, {"1", "2", "3", "4", "5"}, 6)  # каталог повний → лічильник скинуто
chk("повний каталог скидає лічильник: «зниклі» випадають зі стану", STORE["gone"] == {})
td, _ = run(["1", "2", "3", "4", "5"], {"1"}, {"1", "2"}, 12)               # знову усічення — відлік з нуля
td2, _ = run(["1", "2", "3", "4", "5"], {"1"}, {"1", "2"}, 20)              # 8 год від нового першого побачення
chk("тривале усічення <20 год (з відліку після відновлення) НЕ видаляє живе", not ({"3", "4", "5"} & set(td2)))

# порожній toysi_ids, guard, межа
reset()
chk("ПОРОЖНІЙ toysi_ids → ([], []) і стан не чіпається", run(ledger, {"1"}, set(), 0) == ([], []) and STORE["gone"] == {})
big = [str(i) for i in range(2, 2 + 603)]          # 603 − 2 ще в Toysi = 601 зниклих (> guard 600)
LIVE.clear(); LIVE.update({k: {"status": "on_display"} for k in big})
td, _ = run(big, {"1"}, {"2", "3"}, 0)
chk("601 зниклих → guard: 🚨 один раз, шлях зниклих пропущено, стан скинуто", len(alerts) == 1 and STORE["gone"] == {} and set(td) <= {"2", "3"})
reset()
b600 = [str(i) for i in range(2, 2 + 601)]          # 601 − 1 ще в Toysi = рівно 600 зниклих
LIVE.clear(); LIVE.update({k: {"status": "on_display"} for k in b600})
run(b600, {"1"}, {"2"}, 0)
chk("рівно 600 зниклих — guard не спрацьовує", not alerts)

# 343 реальні: 2 прогони з інтервалом ≥20 год, sweep-курсор не ховає підтверджених і залишок після капу
reset()
real = [str(i) for i in range(2, 2 + 343)]
LIVE.clear(); LIVE.update({k: {"status": "on_display"} for k in real})
run(real, {"1"}, {"2"}, 0)
td, _ = run(real, {"1"}, {"2"}, 21)
chk("343 реальні зниклі, підтверджені через 21 год → усі в to_delist, без алерта", len(td) >= 342 and not alerts)
chk("кап 200 не змінено (застосовується в main)", pcs.LEDGER_MAX_DELIST_PER_RUN == 200 and pcs.GONE_MASS_GUARD == 600 and pcs.GONE_MIN_AGE_HOURS == 20)
chk("підтверджені зниклі НЕ потрапили в sweep-курсор (залишок після капу не ховається до кінця циклу)", not (CHECKED["c"] & set(real[1:])))
td_next, _ = run(real, {"1"}, {"2"}, 25)
chk("наступний прогін знову бачить усі підтверджені зниклі (143 понад кап — не загублені)", len(td_next) >= 342)

# файл стану: відсутній/битий/старого формату — безпечно; атомарний запис
real_load = pcs.__dict__.get("_orig_load")
import importlib
pcs2 = importlib.reload(pcs)
tmpd = Path(tempfile.mkdtemp())
pcs2.GONE_SEEN_FILE = tmpd / "prom_gone_seen.json"
chk("відсутній файл стану → {}", pcs2._load_gone_seen() == {})
pcs2.GONE_SEEN_FILE.write_text("{оборваний json", encoding="utf-8")
chk("битий файл стану → {} (нічого не підтверджено)", pcs2._load_gone_seen() == {})
pcs2.GONE_SEEN_FILE.write_text(json.dumps(["1", "2"]), encoding="utf-8")
chk("старий формат (список) → {} (безпечно)", pcs2._load_gone_seen() == {})
ok = pcs2._save_gone_seen({"9": "2026-10-12T07:30:00"})
chk("атомарний запис: файл читається назад, tmp не лишається", ok and pcs2._load_gone_seen() == {"9": "2026-10-12T07:30:00"} and not (tmpd / "prom_gone_seen.tmp").exists())
pcs2.GONE_SEEN_FILE = tmpd / "нема_такої_теки" / "x.json"
chk("запис не вдався → False (без винятку)", pcs2._save_gone_seen({"1": "x"}) is False)
print()
if F:
    print("FAILED:", F)
    sys.exit(1)
print("ALL OK")
