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

r_missing = {"toysi_order_id": 100447294, "tc_number": "ТС000000860"}
_chk("_already_in_book: РЕАЛЬНА прогалина (перевірено живо в книзі) — не знаходить",
     not tr._already_in_book(r_missing, _BOOK_TEXT))

r_empty = {"toysi_order_id": None, "tc_number": None}
_chk("_already_in_book: немає ідентифікаторів — не падає, повертає False",
     tr._already_in_book(r_empty, _BOOK_TEXT) is False)

# 3: toysi-order-id регекс (для повноти — використовується непрямо через f-рядок у коді,
#    але формат "toysi-<id>" мусить лишатись консистентним з тим, що пишуть інші kandydaty-скрипти)
_chk("формат ключа книги: 'toysi-100449926' міститься в реальному нараті",
     "toysi-100449926" in _BOOK_TEXT)


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Виявлення повернень Toysi: регекси, текстовий пошук у книзі (знайдено/не знайдено/"
      "порожньо) — усе коректно")
sys.exit(0)
