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
# by_sum тепер {сума: [(дата, серіал_або_None), ...]} (фікс бага (4) черги 2, 2026-09-23)
by_sum = {194.0: [(date(2026, 8, 30), None)], 100.0: [(date(2026, 9, 10), None)],
          50.0: [(date(2026, 9, 10), None), (date(2026, 9, 11), None)]}

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
m4 = cs._match_book({50.0: [(date(2026, 9, 14), None)]}, 50.0, "2026-09-13T21:57:00")
_chk("UTC→Київ: чек 21:57 UTC → київська дата 14.09 → точний збіг", m4["exact"] == 1)
_chk("UTC→Київ: kyiv_date=2026-09-14", m4["kyiv_date"] == "2026-09-14")

# Невалідний created_at → не падає, вважає "не звірено"
m5 = cs._match_book(by_sum, 100.0, "не дата")
_chk("невалідний created_at: не падає, kyiv_date=None", m5["kyiv_date"] is None)

# ── 2b: бага (4) черги 2 — рядок книги ЯВНО прив'язаний до ЧУЖОГО серіала (2026-09-23) ──
# Відтворення живого інциденту: чек 88 (78,00₴, 20.09) отримав "Точний збіг 1" на рядок,
# який графа 5 книги в'яже до серіала 84 (19.09, інший покупець) — не до 88.
book_84 = {78.0: [(date(2026, 9, 19), 84)]}
m6 = cs._match_book(book_84, 78.0, "2026-09-20T10:00:00", serial=88)
_chk("серіал-88 проти рядка-серіала-84: exact=0 (застовплено чужим серіалом)", m6["exact"] == 0)
_chk("серіал-88 проти рядка-серіала-84: sum_only=0 (не показуємо як 'можливо цей')", m6["sum_only"] == 0)

# Той самий рядок, але звіряємо СПРАВЖНІМ власником серіала 84 → genuine exact match лишається
m7 = cs._match_book(book_84, 78.0, "2026-09-19T14:00:00", serial=84)
_chk("серіал-84 проти рядка-серіала-84: exact=1 (свій рядок, збіг лишається)", m7["exact"] == 1)

# Рядок БЕЗ явного серіала (лише дата) — стара логіка сума+дата, нема з чим звіряти жорсткіше
book_no_serial = {78.0: [(date(2026, 9, 19), None)]}
m8 = cs._match_book(book_no_serial, 78.0, "2026-09-20T10:00:00", serial=88)
_chk("рядок без серіала: exact=1 (fallback на суму+дату, як раніше)", m8["exact"] == 1)

# ── 2c: _pair_match_unmatched — баг (5) черги 2, книга об'єднує 2 чеки одного дня/типу
#       оплати в ОДИН рядок (2026-09-23, живо звірено на 8/8 реальних пар) ──
def _mk(serial, sum_uah, kyiv_date, pay_type="CASH", exact=0, sum_only=0):
    return {"serial": serial, "sum_uah": sum_uah, "kyiv_date": kyiv_date, "pay_type": pay_type,
            "book_exact_matches": exact, "book_sum_only_matches": sum_only}


# живий кейс 11+12=834.28→р.23 (той самий день, той самий тип оплати)
book_pair = {834.28: [(date(2026, 8, 4), None)]}
r11, r12 = _mk(11, 630.0, "2026-08-04", "CASHLESS"), _mk(12, 204.28, "2026-08-04", "CASHLESS")
cs._pair_match_unmatched([r11, r12], book_pair)
_chk("pair-match: пара 11+12 знайдена — обидва exact=1", r11["book_exact_matches"] == 1 and r12["book_exact_matches"] == 1)
_chk("pair-match: paired_with_serial проставлено взаємно", r11["paired_with_serial"] == 12 and r12["paired_with_serial"] == 11)

# живий кейс 42+43=437.00→р.67: чек 43 МАВ несвязаний sum_only-збіг деінде (сума 121 теж є в
# іншому рядку іншої дати) — фікс 2026-09-23: sum_only НЕ повинен блокувати участь у парі
r42 = _mk(42, 316.0, "2026-09-01", "CASH")
r43 = _mk(43, 121.0, "2026-09-01", "CASH", sum_only=1)  # sum_only=1 від НЕПОВʼЯЗАНОГО рядка
book_4243 = {437.0: [(date(2026, 9, 1), None)]}
cs._pair_match_unmatched([r42, r43], book_4243)
_chk("pair-match: sum_only≠0 у партнера НЕ блокує пару (живий рецидив 42+43)",
     r42["book_exact_matches"] == 1 and r43["book_exact_matches"] == 1)

# різний тип оплати того самого дня — НЕ пара, навіть якщо сума збігається
r_cash = _mk(60, 200.0, "2026-09-10", "CASH")
r_cashless = _mk(61, 200.0, "2026-09-10", "CASHLESS")
book_diff_pay = {400.0: [(date(2026, 9, 10), None)]}
cs._pair_match_unmatched([r_cash, r_cashless], book_diff_pay)
_chk("pair-match: різний тип оплати — НЕ парується", r_cash["book_exact_matches"] == 0 and r_cashless["book_exact_matches"] == 0)

# рядок ЯВНО прив'язаний до ІНШОГО checkbox-серіала — не кандидат для парного збігу
r_a = _mk(70, 50.0, "2026-09-10")
r_b = _mk(71, 50.0, "2026-09-10")
book_claimed = {100.0: [(date(2026, 9, 10), 99)]}  # рядок уже belongs серіалу 99
cs._pair_match_unmatched([r_a, r_b], book_claimed)
_chk("pair-match: рядок із чужим явним серіалом — виключено з парного пошуку",
     r_a["book_exact_matches"] == 0 and r_b["book_exact_matches"] == 0)

# немає відповідного рядка книги — пара НЕ вигадується, лишається «не в книзі»
r_x = _mk(80, 10.0, "2026-09-10")
r_y = _mk(81, 20.0, "2026-09-10")
cs._pair_match_unmatched([r_x, r_y], {})
_chk("pair-match: немає рядка книги — обидва лишаються exact=0", r_x["book_exact_matches"] == 0 and r_y["book_exact_matches"] == 0)

# той самий рядок НЕ забирають дві різні пари за один прогін (consumed)
r1a, r1b = _mk(90, 100.0, "2026-09-10"), _mk(91, 100.0, "2026-09-10")   # сума пари 200
r2a, r2b = _mk(92, 100.0, "2026-09-10"), _mk(93, 100.0, "2026-09-10")   # та сама сума пари
book_one_row = {200.0: [(date(2026, 9, 10), None)]}  # лише ОДИН такий рядок
cs._pair_match_unmatched([r1a, r1b, r2a, r2b], book_one_row)
_matched_count = sum(1 for r in (r1a, r1b, r2a, r2b) if r["book_exact_matches"] == 1)
_chk("pair-match: один рядок книги забирає РІВНО одну пару (не обидві)", _matched_count == 2)

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
ws.append([date(2026, 9, 19), 78.0, None, None,
           "Rozetka №905828037, чек Checkbox серіал 84 від 19.09.2026 17:41."])  # рядок 10 — явний серіал
wb.save(_tmp_xlsx)
wb.close()

cs.KODV_XLSX = _tmp_xlsx  # monkeypatch шляху для тесту
idx = cs._book_date_sum_index()
_chk("book_date_sum_index: сума 194.0 → [(30.08, None)]", idx.get(194.0) == [(date(2026, 8, 30), None)])
_chk("book_date_sum_index: сума 100.0 → [(05.09, None)]", idx.get(100.0) == [(date(2026, 9, 5), None)])
_chk("book_date_sum_index: нерозпізнана дата → (None, None) у списку (не падає)", idx.get(50.0) == [(None, None)])
_chk("book_date_sum_index: серіал з графи 5 розпізнано", idx.get(78.0) == [(date(2026, 9, 19), 84)])
os.remove(_tmp_xlsx)

# ── 4: інтеграція з kandydaty_registry — main() синхронізує РІВНО ті чеки, де exact==0 ──
_calls = {"sync": [], "report": 0}


def _mock_sync(source, current, resolve=True):
    _calls["sync"].append((source, current, resolve))
    return {"newly_opened": [c["key"] for c in current], "still_open": [], "resolved": []}


def _mock_report():
    _calls["report"] += 1
    return Path(tempfile.mktemp())


_real_sync_open_candidates = kr.sync_open_candidates
kr.sync_open_candidates = _mock_sync
kr.write_open_report = _mock_report
cs.kandydaty_registry = kr  # той самий модуль (bare-name lookup у checkbox_registry_sync)

cs.cb.CHECKBOX_API_KEY = "test-key"
cs.cb.CHECKBOX_CASHIER_PIN = "test-pin"
cs.COWORK_DIR = Path(tempfile.mkdtemp())  # НЕ писати _write_report() у реальну документи_КОДВ/
cs._load_cursor = lambda: {"last_serial": 100}
_saved_cursor = []
cs._save_cursor = lambda s: _saved_cursor.append(s)
cs.fetch_receipts = lambda: ([
    {"serial": 101, "fiscal_code": "F101", "sum_uah": 194.0, "type": "SELL", "pay_type": "CASH",
     "pay_label": "Готівка", "created_at": "2026-09-10T12:00:00"},   # exact=0 (рядок-70-подібний)
    {"serial": 102, "fiscal_code": "F102", "sum_uah": 100.0, "type": "SELL", "pay_type": "CASH",
     "pay_label": "Готівка", "created_at": "2026-09-09T12:00:00"},   # exact=1 (точний збіг)
], False)
# Порожня книга (файл узагалі відсутній): жоден чек не матиме exact-збігу → ОБИДВА йдуть у реєстр.
cs.KODV_XLSX = Path(tempfile.mktemp(suffix=".xlsx"))

_calls["sync"].clear()
_calls["report"] = 0
_saved_cursor.clear()
cs.main()

_chk("main(): sync_open_candidates викликано рівно раз", len(_calls["sync"]) == 1)
if _calls["sync"]:
    src, current, resolve = _calls["sync"][0]
    _chk("main(): source='checkbox'", src == "checkbox")
    _chk("main(): обидва чеки пішли в реєстр (порожня книга → exact=0 для обох)", len(current) == 2)
    _chk("main(): ключі — серіали рядками", {c["key"] for c in current} == {"101", "102"})
    _chk("main(): сторінка НЕ обрізана → resolve=True", resolve is True)
_chk("main(): write_open_report викликано", _calls["report"] == 1)
_chk("main(): курсор просунуто", _saved_cursor == [102])

# ── 5: сторінка обрізана (truncated=True) → main() передає resolve=False, щоб НЕ закрити хибно
#      старого кандидата, який випав за межу вибірки (аудит 2026-09-18, рецидив Д1/Д4) ──
cs.fetch_receipts = lambda: ([
    {"serial": 103, "fiscal_code": "F103", "sum_uah": 50.0, "type": "SELL", "pay_type": "CASH",
     "pay_label": "Готівка", "created_at": "2026-09-10T12:00:00"},
], True)  # truncated=True
cs._load_cursor = lambda: {"last_serial": 102}
_calls["sync"].clear()
_calls["report"] = 0
_saved_cursor.clear()
cs.main()
_chk("truncated: sync_open_candidates усе одно викликано (відкриває нових)", len(_calls["sync"]) == 1)
if _calls["sync"]:
    _, _, resolve = _calls["sync"][0]
    _chk("truncated: resolve=False (закриття пропущено цим прогоном)", resolve is False)

# ── 6: sync_open_candidates(resolve=False) — реальний виклик (не мок) — не закриває "open" ──
_reg_path = Path(tempfile.mktemp())
_real_sync_open_candidates("checkbox", [{"key": "1", "summary": "s", "sum": 1.0, "date": "x"}], path=_reg_path)
r_trunc = _real_sync_open_candidates("checkbox", [], path=_reg_path, resolve=False)
_chk("resolve=False: нічого не закрито, хоч current порожній", r_trunc["resolved"] == [])
reg_after = kr._load_registry(_reg_path)
_chk("resolve=False: запис лишився open у реєстрі", reg_after["checkbox:1"]["status"] == "open")
r_resolve_true = _real_sync_open_candidates("checkbox", [], path=_reg_path, resolve=True)
_chk("resolve=True (наступний повний прогін): тепер закрито", r_resolve_true["resolved"] == ["checkbox:1"])


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Звірка чеків з книгою (сума+дата, UTC→Київ) + інтеграція з реєстром відкритих кандидатів")
sys.exit(0)
