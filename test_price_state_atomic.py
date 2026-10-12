#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_price_state_atomic.py — атомарний запис і безпечне завантаження стану репрайсера (питання Консультанта 12.10: чи безпечно вбивати репрайсер посеред --apply).
Раніше save_prom_price_state писав напряму (write_text), а load на битий JSON мовчки повертав {}: перерваний запис давав ПОРОЖНІЙ стан (губились _delisted_since, ціни, _readded_at).
Інваріанти: повний запис; перерваний запис (виняток у os.replace) лишає попередній ПОВНИЙ файл і не лишає обрізаного; бекап = попередній валідний стан; битий файл → відновлення з бекапу
+ алерт; битий і бекап → {} + алерт; відсутній файл → {} без шуму; битий основний файл не потрапляє в бекап. Мережа/Telegram замокані. `python test_price_state_atomic.py` → exit 0/1."""
import json
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import competitor_pricing as cp

tmp = Path(tempfile.mkdtemp())
cp.PROM_PRICE_STATE_FILE = tmp / "prom_competitor_price_state.json"
alerts = []
cp._alert_price_state = lambda key, text: alerts.append((key, text))
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


f = cp.PROM_PRICE_STATE_FILE
bak = f.with_suffix(".json.bak")
tmpf = f.with_suffix(".json.tmp")

chk("файлу нема → {} без шуму", cp.load_prom_price_state() == {} and not alerts)

s1 = {"_delisted_since": {"1": "2026-10-01T00:00:00"}, "2": {"price": 100}}
cp.save_prom_price_state(s1)
chk("перший запис: файл повний, .tmp не лишився, бекапу ще нема", json.loads(f.read_text(encoding="utf-8")) == s1 and not tmpf.exists() and not bak.exists())

s2 = {**s1, "3": {"price": 200}}
cp.save_prom_price_state(s2)
chk("другий запис: файл = s2, бекап = попередній валідний s1, .tmp нема", json.loads(f.read_text(encoding="utf-8")) == s2 and json.loads(bak.read_text(encoding="utf-8")) == s1 and not tmpf.exists())

# перерваний запис: виняток у момент os.replace (як SIGKILL між записом tmp і підміною)
real_replace = cp.os.replace
cp.os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("перервано"))
try:
    cp.save_prom_price_state({"зламаний": True})
except OSError:
    pass
finally:
    cp.os.replace = real_replace
chk("перерваний запис: основний файл ЛИШИВСЯ повним s2 (не обрізаний)", json.loads(f.read_text(encoding="utf-8")) == s2)
tmpf.unlink(missing_ok=True)

# битий основний файл → відновлення з бекапу
f.write_text('{"_delisted_since": {"1": "2026-10', encoding="utf-8")      # обрізаний JSON
alerts.clear()
got = cp.load_prom_price_state()
chk("битий основний файл → відновлено з бекапу (останній валідний стан s2: перерваний запис лишив бекап = s2) і є попередження", got == s2 and len(alerts) == 1 and "відновлено з резервної копії" in alerts[0][1])

# битий основний НЕ перезаписує бекап добрим-збитим
cp.save_prom_price_state(got)
chk("після відновлення новий запис: бекап не затертий битим файлом (бекап лишився валідним s2), основний = s2", json.loads(bak.read_text(encoding="utf-8")) == s2 and json.loads(f.read_text(encoding="utf-8")) == s2)

# обидва биті → {} + гучний алерт
f.write_text("не json", encoding="utf-8")
bak.write_text("теж не json", encoding="utf-8")
alerts.clear()
chk("основний і бекап биті → {} і гучний алерт «стан ПОРОЖНІЙ»", cp.load_prom_price_state() == {} and len(alerts) == 1 and "ПОРОЖНІЙ" in alerts[0][1])

# основний битий, бекапу нема
bak.unlink()
alerts.clear()
chk("основний битий, бекапу нема → {} і алерт", cp.load_prom_price_state() == {} and len(alerts) == 1)

# крос-перевірка: кирилиця/великий стан
big = {str(i): {"price": i, "назва": "іграшка"} for i in range(5000)}
cp.save_prom_price_state(big)
chk("великий стан з кирилицею: roundtrip без втрат", cp.load_prom_price_state() == big)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
