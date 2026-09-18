#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_toysi_returns_kandydaty.py — регрес-тест виявлення повернень Toysi
(toysi_returns_kandydaty.py, аудит Priority #3, 2026-09-18: повернення — єдиний клас
кандидатів з влучністю 0/7 до цього скрипта; звірено живо — 7/7 повернень знайдено,
2 з них виявились ГЕНУІНО відсутні в книзі під будь-яким форматом цитування).

Мережа НЕ потрібна: тестує чисту логіку (регекси ТС-номера/toysi-id, текстовий пошук у
книзі), не Playwright-навігацію. `python test_toysi_returns_kandydaty.py` → exit 0/1.
"""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import toysi_returns_kandydaty as tr

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


# 1: регекс ТС-номера (звірено живо на реальному тексті документа)
_REAL_DOC = "Повернення товарів від покупця ТС000001089 від 17.09.2026 16:11:54"
m = tr._TS_RE.search(_REAL_DOC)
_chk("_TS_RE: знаходить реальний ТС-номер", m is not None and m.group(0) == "ТС000001089")
_chk("_RETURN_MARKER: підрядок збігається з реальним документом", tr._RETURN_MARKER in _REAL_DOC.lower())

_ORDER_DOC = "Замовлення покупця ТС000248538 від 16.09.2026 9:59:22"
_chk("_RETURN_MARKER: НЕ спрацьовує на звичайному замовленні", tr._RETURN_MARKER not in _ORDER_DOC.lower())

# 2: _already_in_book — текстовий пошук toysi-id / ТС-номера
_BOOK_TEXT = (
    "Toysi — незадоволена якість товару, реалізоване замовлення (28.08.2026, EVA №8-080316052), "
    "toysi-100449926. Toysi здійснила повернення №ТС000001089 від 17.09.2026 16:11:54 (реальна "
    "фіскалізована): собівартість за первинним 692.09, реально Toysi 675.58, невідшкодована "
    "Збірка = 16.51."
)

r_found_by_id = {"toysi_order_id": 100449926, "tc_number": None}
_chk("_already_in_book: знаходить за toysi-id", tr._already_in_book(r_found_by_id, _BOOK_TEXT))

r_found_by_tc = {"toysi_order_id": None, "tc_number": "ТС000001089"}
_chk("_already_in_book: знаходить за ТС-номером", tr._already_in_book(r_found_by_tc, _BOOK_TEXT))

r_missing = {"toysi_order_id": 999999999, "tc_number": "ТС999999999"}
_chk("_already_in_book: справді відсутній id/ТС у наративі — не знаходить",
     not tr._already_in_book(r_missing, _BOOK_TEXT))

r_empty = {"toysi_order_id": None, "tc_number": None}
_chk("_already_in_book: немає ідентифікаторів — не падає, повертає False",
     tr._already_in_book(r_empty, _BOOK_TEXT) is False)

# 2b: РЕГРЕС — знахідка незалежного аудитора 2026-09-18 (рядки книги 26/39): наратив із
# toysi-id/ТС-номером НЕ завжди в графі 5 — інколи графа 5 має лише короткий підпис, а
# повний наратив (з id) — у графі 12 ("Розшифровка, примітки"). До фіксу _book_narrative_text
# читала лише графу 5 → хибне "НЕ в книзі" для реально внесених рядків. Текст нижче — РЕАЛЬНИЙ,
# звірений живо з КОДВ_PlutusToys_2026.xlsx, рядок 26 (графа 5 коротка, графа 12 повна).
_BOOK_TEXT_SPLIT_NARRATIVE = (
    "Toysi — невідшкодована Збірка, повернене замовлення №419272444. \n"  # графа 5 (коротка)
    "№419272444 (Prom) — НЕ дохід... Живо перевірено Toysi «Взаєморозрахунки»... рядок "
    "«Повернення товарів від покупця ТС000000860 від 05.08.2026 17:40:09», кредит 213.70 "
    "грн... (order 100447294 від 03.08.2026)..."  # графа 12 (повна, з id)
)
r_split = {"toysi_order_id": 100447294, "tc_number": "ТС000000860"}
_chk("_already_in_book: наратив розділений між графою 5 і графою 12 — все одно знаходить",
     tr._already_in_book(r_split, _BOOK_TEXT_SPLIT_NARRATIVE))

# 3: toysi-order-id регекс (для повноти — використовується непрямо через f-рядок у коді,
#    але формат "toysi-<id>" мусить лишатись консистентним з тим, що пишуть інші kandydaty-скрипти)
_chk("формат ключа книги: 'toysi-100449926' міститься в реальному нараті",
     "toysi-100449926" in _BOOK_TEXT)

# 3b: _book_narrative_text() на СИНТЕТИЧНІЙ книзі реальної структури (12 колонок, дані з
# рядка 7) — регрес-тест на знахідку аудитора: читає ОБИДВІ графа5/графа12, не лише графу 5.
import tempfile
from pathlib import Path
from unittest.mock import patch
import openpyxl

_tmp_book = Path(tempfile.mktemp(suffix=".xlsx"))
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "КОДВ"
for _ in range(6):
    ws.append([None] * 12)  # рядки 1-6 — шапка, дані з рядка 7
# рядок 7: наратив ЛИШЕ в графі 5 (як рядок 106, Куровська)
ws.append([None, None, None, None, "toysi-111111111 повний наратив тут"] + [None] * 7)
# рядок 8: наратив ЛИШЕ в графі 12 (як рядки 26/39) — графа 5 коротка, без id
ws.append([None, None, None, None, "короткий підпис без id"] + [None] * 6 + ["toysi-222222222 повний наратив у графі 12"])
wb.save(_tmp_book)
wb.close()

_orig_kodv_xlsx = tr.KODV_XLSX
tr.KODV_XLSX = _tmp_book
_synthetic_text = tr._book_narrative_text()
tr.KODV_XLSX = _orig_kodv_xlsx
os_remove_ok = True
try:
    import os
    os.remove(_tmp_book)
except OSError:
    os_remove_ok = False

_chk("_book_narrative_text: знаходить id з графи 5", "toysi-111111111" in _synthetic_text)
_chk("_book_narrative_text: знаходить id з графи 12 (не лише графа 5)",
     "toysi-222222222" in _synthetic_text)

# 4: АУДИТ PR #568 — обрізана вибірка (any_period_failed=True) НЕ закриває кандидатів
#    (той самий клас бага, що вже фіксили для checkbox_registry_sync: resolve=True за
#    замовчуванням хибно "закрило" б реальне повернення, яке просто випало з прогону).

_calls = {"sync": [], "report": 0}


def _mock_sync(source, current, resolve=True):
    _calls["sync"].append((source, current, resolve))
    return {"newly_opened": [c["key"] for c in current], "still_open": [], "resolved": []}


def _mock_report():
    _calls["report"] += 1
    return Path(tempfile.mktemp())


_ONE_ROW = [{
    "doc": _REAL_DOC, "toysi_order_id": 100447294, "tc_number": "ТС000000860",
    "date": "2026-08-05", "sum_debet": -213.7, "period": "test_period",
}]

_TMP_DOCS_DIR = Path(tempfile.mkdtemp())

with patch.object(tr, "_book_narrative_text", return_value=""), \
     patch.object(tr.kandydaty_registry, "sync_open_candidates", _mock_sync), \
     patch.object(tr.kandydaty_registry, "write_open_report", _mock_report), \
     patch.object(tr, "_notify", lambda msg: None), \
     patch.object(tr, "DOCS_DIR", _TMP_DOCS_DIR):  # НЕ писати в реальну документи_КОДВ/ —
                                                     # аудит PR #568: попередня версія тесту
                                                     # затерла справжній звіт дня фікстурою

    with patch.object(tr, "fetch_return_rows", return_value=(_ONE_ROW, False)):
        _calls["sync"].clear()
        tr.main()
        _chk("any_period_failed=False: resolve=True передано", _calls["sync"] and _calls["sync"][0][2] is True)

    with patch.object(tr, "fetch_return_rows", return_value=(_ONE_ROW, True)):
        _calls["sync"].clear()
        tr.main()
        _chk("any_period_failed=True: resolve=False передано (не закриваємо хибно)",
             _calls["sync"] and _calls["sync"][0][2] is False)

_written = list(_TMP_DOCS_DIR.rglob("*_toysi_povernennya_kandydaty.md"))
_chk("звіт написано в ТИМЧАСОВУ теку, не в реальну документи_КОДВ/", len(_written) >= 1)


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Виявлення повернень Toysi: регекси, текстовий пошук у книзі (знайдено/не знайдено/"
      "порожньо) — усе коректно")
sys.exit(0)
