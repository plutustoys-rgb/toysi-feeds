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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import rozetka_commission_ledger as rc

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


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ reserve_warning (резерв↔комісія): живий кейс, звичайне замовлення, регістронезалежність, "
      "відсутність у книзі — усі коректно.")
sys.exit(0)
