#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_rozetka_commission_ledger.py — регрес-тест reserve_warning (rozetka_commission_ledger.py,
аудит КОДВ-автоматики 2026-09-22, черга 2, баг (6): кабінет пише спершу "Резервування суми",
потім, окремим прогоном, фіналізовану "Комісія за продаж" на ТУ САМУ суму. Бухгалтер інколи
вже вносить резерв у поточне I9 вручну з приміткою "(роялті ще «Резервування»..." у графі 5
(звірено живо, рядки 91/92/95/98 книги, 2026-09-23) — якщо скрипт пізніше пропонує додати
Δ (фіналізовану комісію) поверх, виходить подвійний облік. Фікс — НЕ автоматичне вирахування
(парсинг суми резерву з вільного людського тексту крихкий), а явне попередження, коли графа 5
згадує "резервування" — рішення лишається за роллю «агент-бухгалтер».

Мережа/Playwright НЕ потрібні: fetch_royalty_rows/fetch_logistic_rows/_load_cursor/
_lookup_book_row замокано. `python test_rozetka_commission_ledger.py` → exit 0/1.
"""
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import rozetka_commission_ledger as rc
import kandydaty_registry as kr

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


def _royalty_row(log_id, order_id, sum_uah, type_title=rc.SALE_COMMISS_TITLE):
    return {"log_id": log_id, "date": "2026-09-16", "type_title": type_title,
            "order_id": order_id, "debit": sum_uah}


_BOOK = {}  # order_id → {"row": N, "current_i9": X, "e_text": "..."}


def _mock_lookup(order_id):
    return _BOOK.get(order_id, {})


rc._load_cursor = lambda: {"last_royalty_log_id": 100, "last_logistics_operation_id": 100}
rc.fetch_logistic_rows = lambda page: []
rc._lookup_book_row = _mock_lookup

# ── 1: живий кейс — графа 5 явно згадує "резервування" (рядок 91 книги, 2026-09-23) ──
_BOOK.clear()
_BOOK["905803580"] = {
    "row": 91, "current_i9": 21.92,
    "e_text": "Rozetka №905803580 (Ярьоменко), картка, чек Checkbox серіал 67 від 11.09.2026. "
              "i9=19.80 (роялті ще «Резервування», НЕ фінал, seller.rozetka.com.ua, 16.09).",
}
rc.fetch_royalty_rows = lambda page: [_royalty_row(101, "905803580", 19.80)]
candidates, _ = rc.collect(page=None)
_chk("живий кейс 91: candidate знайдено", len(candidates) == 1)
c = candidates[0]
_chk("живий кейс 91: reserve_warning=True (графа 5 згадує 'резервування')", c["reserve_warning"] is True)
_chk("живий кейс 91: delta_i9 і далі рахується (скрипт лише попереджає, не приховує)", c["delta_i9"] == 19.80)
_chk("живий кейс 91: book_proposed_i9 і далі показується (рішення — за бухгалтером)",
     c["book_proposed_i9"] == round(21.92 + 19.80, 2))

# ── 2: звичайне замовлення БЕЗ згадки резерву в графі 5 — reserve_warning=False ──
_BOOK.clear()
_BOOK["906058699"] = {
    "row": 200, "current_i9": 10.0,
    "e_text": "Rozetka №906058699, картка, чек Checkbox серіал 99 від 20.09.2026.",
}
rc.fetch_royalty_rows = lambda page: [_royalty_row(102, "906058699", 25.0)]
candidates2, _ = rc.collect(page=None)
_chk("звичайне замовлення: reserve_warning=False", candidates2[0]["reserve_warning"] is False)

# ── 3: заголовна буква/відмінок — регістронезалежність підрядкового пошуку ──
_BOOK.clear()
_BOOK["906100000"] = {
    "row": 201, "current_i9": 5.0,
    "e_text": "Rozetka №906100000. РЕЗЕРВУВАННЯ суми ще не фіналізоване.",
}
rc.fetch_royalty_rows = lambda page: [_royalty_row(103, "906100000", 12.0)]
candidates3, _ = rc.collect(page=None)
_chk("регістронезалежність: 'РЕЗЕРВУВАННЯ' (капс) теж ловиться", candidates3[0]["reserve_warning"] is True)

# ── 4: замовлення відсутнє в книзі взагалі — reserve_warning=False (нема графи 5, щоб перевіряти) ──
_BOOK.clear()
rc.fetch_royalty_rows = lambda page: [_royalty_row(104, "906200000", 8.0)]
candidates4, _ = rc.collect(page=None)
_chk("немає в книзі: reserve_warning=False (нема e_text)", candidates4[0]["reserve_warning"] is False)
_chk("немає в книзі: book_current_i9=None, book_proposed_i9=None", candidates4[0]["book_current_i9"] is None and candidates4[0]["book_proposed_i9"] is None)


# ── 5-8: реєстр незалежний від курсора (Аудитор, КОДВ_CHANNEL.md, 2026-09-28, п.1) —
# живий інцидент: роялті №906224962 (47,18) було в звіті 19.09, курсор пішов далі, факт зник
# із кожного НАСТУПНОГО звіту назавжди. Фікс: sync_registry() тримає кандидата persistent,
# незалежно від того, чи collect() бачить його як "новий" цього прогону; resolve_against_book()
# закриває ЛИШЕ коли сума з'явилась у Графі 5 книги (критерій Аудитора).
_tmp_reg = Path(tempfile.mktemp())
kr.REGISTRY_PATH = _tmp_reg

_BOOK.clear()
_BOOK["906224962"] = {"row": 110, "current_i9": 1.04, "e_text": "Rozetka №906224962, ще не внесено."}
rc.fetch_royalty_rows = lambda page: [_royalty_row(200, "906224962", 47.18)]
candidates5, _ = rc.collect(page=None)
sync_result = rc.sync_registry(candidates5)
_chk("sync_registry: новий кандидат відкрито", sync_result["newly_opened"] == ["rozetka_commission:906224962"])
reg = kr._load_registry(_tmp_reg)
_chk("реєстр: запис status=open", reg["rozetka_commission:906224962"]["status"] == "open")
_chk("реєстр: sum = delta_i9 (47.18)", reg["rozetka_commission:906224962"]["sum"] == 47.18)

# ПРЕ-ФІКС ВІДТВОРЕННЯ живого інциденту: наступний прогін кабінет уже НЕ показує цей рядок
# (курсор пройшов повз / рядок випав за межу вікна ~20) — collect() мовчить про нього.
# Саме тут факт раніше зникав НАЗАВЖДИ, бо нічого, крім курсора, його не пам'ятало.
rc.fetch_royalty_rows = lambda page: []
candidates6, _ = rc.collect(page=None)
_chk("ПРЕ-ФІКС інваріант: наступний прогін дійсно НЕ бачить кандидата як 'новий' "
     "(це й був корінь бага — без реєстру факт зник би тут безслідно)", candidates6 == [])
reg_after = kr._load_registry(_tmp_reg)
_chk("ФІКС: кандидат УСЕ ОДНО 'open' у реєстрі — мовчання collect() його не стирає",
     reg_after["rozetka_commission:906224962"]["status"] == "open")

# ── 6: resolve_against_book() — сума з'явилась у Графі 5 → закрито ──
_BOOK["906224962"]["e_text"] = "Rozetka №906224962, роялті 47,18 внесено 28.09.2026."
resolved = rc.resolve_against_book()
_chk("resolve_against_book: закрито (сума 47,18 знайдена в Графі 5)",
     resolved["resolved"] == ["rozetka_commission:906224962"])
_chk("реєстр: status=resolved", kr._load_registry(_tmp_reg)["rozetka_commission:906224962"]["status"] == "resolved")

# ── 7: сума в графі 5 ще НЕ з'явилась (звичайний, найчастіший випадок) — лишається open ──
_BOOK.clear()
_BOOK["906267890"] = {"row": 111, "current_i9": 3.13, "e_text": "Rozetka №906267890, ще не внесено."}
rc.fetch_royalty_rows = lambda page: [_royalty_row(201, "906267890", 135.21)]
candidates7, _ = rc.collect(page=None)
rc.sync_registry(candidates7)
resolved7 = rc.resolve_against_book()
_chk("resolve_against_book: НЕ закрито, якщо сума ще не в Графі 5",
     "rozetka_commission:906267890" not in resolved7["resolved"])
_chk("реєстр: 906267890 усе ще open", kr._load_registry(_tmp_reg)["rozetka_commission:906267890"]["status"] == "open")

# ── 8: _delta_applied_in_book — формати кома/крапка, межові випадки ──
_chk("_delta_applied_in_book: кома у книзі, крапка в delta", rc._delta_applied_in_book("сума 47,18 внесена", 47.18))
_chk("_delta_applied_in_book: крапка в обох", rc._delta_applied_in_book("сума 47.18 внесена", 47.18))
_chk("_delta_applied_in_book: сума відсутня в тексті", not rc._delta_applied_in_book("щось інше", 47.18))
_chk("_delta_applied_in_book: delta=None", not rc._delta_applied_in_book("47,18", None))
_chk("_delta_applied_in_book: e_text порожній", not rc._delta_applied_in_book("", 47.18))


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ reserve_warning + реєстр незалежний від курсора (sync_registry/resolve_against_book) — "
      "усі перевірки коректні.")
sys.exit(0)
