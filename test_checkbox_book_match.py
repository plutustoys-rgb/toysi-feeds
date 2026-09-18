#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_checkbox_book_match.py — регрес-тест звірки чеків Checkbox з книгою за (сума+дата)
(checkbox_registry_sync.py, аудит Д2 2026-09-18: раніше звіряли ЛИШЕ суму — хибна дата з
правильною сумою читалась як "уже внесено"; UTC/Київ — чек після 21:00 UTC належить наступній
київській даті) + підключення до kandydaty_registry (аудит Д1 — реєстр не залежить від курсора
джерела).

Мережа не потрібна: KODV_XLSX і fetch_receipts замокано. `python test_checkbox_book_match.py`
→ exit 0/1.
"""
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import checkbox_registry_sync as cs
import kandydaty_registry as kr

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


# ── 1: _coerce_date ──
_chk("_coerce_date: datetime → date", cs._coerce_date(datetime(2026, 9, 10, 12, 0)) == date(2026, 9, 10))
_chk("_coerce_date: date → date", cs._coerce_date(date(2026, 9, 10)) == date(2026, 9, 10))
_chk("_coerce_date: ISO-рядок → date", cs._coerce_date("2026-09-10") == date(2026, 9, 10))
_chk("_coerce_date: невалідний рядок → None", cs._coerce_date("не дата") is None)
_chk("_coerce_date: None → None", cs._coerce_date(None) is None)
_chk("_coerce_date: число → None", cs._coerce_date(123) is None)

# ── 2: _match_book — точний збіг (±1 день), лише сума (дата розходиться), UTC→Київ ──
by_sum = {194.0: [date(2026, 8, 30)], 100.0: [date(2026, 9, 10)], 50.0: [date(2026, 9, 10), date(2026, 9, 11)]}

# Інцидент рядка 70, відтворений: сума 194.00, книга=30.08, чек=10.09 → сума збігається, дата НІ
m = cs._match_book(by_sum, 194.0, "2026-09-10T12:00:00")
_chk("рядок-70-інцидент: exact=0 (дата НЕ збігається)", m["exact"] == 0)
_chk("рядок-70-інцидент: sum_only=1 (сума збіглась, попереджаємо)", m["sum_only"] == 1)

# Точний збіг у межах ±1 день
m2 = cs._match_book(by_sum, 100.0, "2026-09-09T12:00:00")  # книга=09-10, чек київська дата теж 09-09 (UTC+3=15:00, той самий день) — у межах 1 дня
_chk("точний збіг (±1 день): exact=1", m2["exact"] == 1)
_chk("точний збіг (±1 день): sum_only=0", m2["sum_only"] == 0)

# Немає жодного запису з такою сумою
m3 = cs._match_book(by_sum, 999.99, "2026-09-10T12:00:00")
_chk("сума відсутня в книзі: exact=0, sum_only=0", m3["exact"] == 0 and m3["sum_only"] == 0)

# UTC→Київ: чек о 21:57 UTC = 00:57 наступного дня в Києві (інцидент "чек 68 = 13.09 UTC = 14.09 Київ")
m4 = cs._match_book({50.0: [date(2026, 9, 14)]}, 50.0, "2026-09-13T21:57:00")
_chk("UTC→Київ: чек 21:57 UTC → київська дата 14.09 → точний збіг", m4["exact"] == 1)
_chk("UTC→Київ: kyiv_date=2026-09-14", m4["kyiv_date"] == "2026-09-14")

# Невалідний created_at → не падає, вважає "не звірено"
m5 = cs._match_book(by_sum, 100.0, "не дата")
_chk("невалідний created_at: не падає, kyiv_date=None", m5["kyiv_date"] is None)

# ── 3: _book_date_sum_index — на реальній структурі xlsx (openpyxl), як КОДВ_PlutusToys_2026.xlsx ──
import openpyxl
_tmp_xlsx = Path(tempfile.mktemp(suffix=".xlsx"))
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "КОДВ"
for _ in range(6):
    ws.append([None, None])  # рядки 1-6 — шапка, дані з рядка 7 (min_row=7 у коді)
ws.append([date(2026, 8, 30), 194.0])   # рядок 7
ws.append([date(2026, 9, 5), 100.0])    # рядок 8
ws.append(["не дата", 50.0])            # рядок 9 — хибна/нерозпізнана дата в книзі
wb.save(_tmp_xlsx)
wb.close()

cs.KODV_XLSX = _tmp_xlsx  # monkeypatch шляху для тесту
idx = cs._book_date_sum_index()
_chk("book_date_sum_index: сума 194.0 → [30.08]", idx.get(194.0) == [date(2026, 8, 30)])
_chk("book_date_sum_index: сума 100.0 → [05.09]", idx.get(100.0) == [date(2026, 9, 5)])
_chk("book_date_sum_index: нерозпізнана дата → None у списку (не падає)", idx.get(50.0) == [None])
os.remove(_tmp_xlsx)

# ── 4: інтеграція з kandydaty_registry — main() синхронізує РІВНО ті чеки, де exact==0 ──
_calls = {"sync": [], "report": 0}


def _mock_sync(source, current):
    _calls["sync"].append((source, current))
    return {"newly_opened": [c["key"] for c in current], "still_open": [], "resolved": []}


def _mock_report():
    _calls["report"] += 1
    return Path(tempfile.mktemp())


kr.sync_open_candidates = _mock_sync
kr.write_open_report = _mock_report
cs.kandydaty_registry = kr  # той самий модуль (bare-name lookup у checkbox_registry_sync)

cs.cb.CHECKBOX_API_KEY = "test-key"
cs.cb.CHECKBOX_CASHIER_PIN = "test-pin"
cs.COWORK_DIR = Path(tempfile.mkdtemp())  # НЕ писати _write_report() у реальну документи_КОДВ/
cs._load_cursor = lambda: {"last_serial": 100}
_saved_cursor = []
cs._save_cursor = lambda s: _saved_cursor.append(s)
cs.fetch_receipts = lambda: [
    {"serial": 101, "fiscal_code": "F101", "sum_uah": 194.0, "type": "SELL", "pay_type": "CASH",
     "pay_label": "Готівка", "created_at": "2026-09-10T12:00:00"},   # exact=0 (рядок-70-подібний)
    {"serial": 102, "fiscal_code": "F102", "sum_uah": 100.0, "type": "SELL", "pay_type": "CASH",
     "pay_label": "Готівка", "created_at": "2026-09-09T12:00:00"},   # exact=1 (точний збіг)
]
# Порожня книга (файл узагалі відсутній): жоден чек не матиме exact-збігу → ОБИДВА йдуть у реєстр.
cs.KODV_XLSX = Path(tempfile.mktemp(suffix=".xlsx"))

_calls["sync"].clear()
_calls["report"] = 0
_saved_cursor.clear()
cs.main()

_chk("main(): sync_open_candidates викликано рівно раз", len(_calls["sync"]) == 1)
if _calls["sync"]:
    src, current = _calls["sync"][0]
    _chk("main(): source='checkbox'", src == "checkbox")
    _chk("main(): обидва чеки пішли в реєстр (порожня книга → exact=0 для обох)", len(current) == 2)
    _chk("main(): ключі — серіали рядками", {c["key"] for c in current} == {"101", "102"})
_chk("main(): write_open_report викликано", _calls["report"] == 1)
_chk("main(): курсор просунуто", _saved_cursor == [102])


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Звірка чеків з книгою (сума+дата, UTC→Київ) + інтеграція з реєстром відкритих кандидатів")
sys.exit(0)
