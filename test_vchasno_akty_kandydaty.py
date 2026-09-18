#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_vchasno_akty_kandydaty.py — регрес-тест виявлення актів Вчасно (vchasno_akty_kandydaty.py,
замовлення незалежного аудитора бухгалтерії, КОДВ_журнал «ДОПОВНЕННЯ 6», 2026-09-17/18: два
реальні хвости (подвійний облік 734,36, пропущений акт 178,97) висіли місяць, бо номер акта
↔ згадка в книзі звірялась ЛИШЕ вручну).

Мережа НЕ потрібна: тестує парсинг імені файлу (реальні назви, звірені живо) і текстовий
пошук у книзі (реальний наратив, звірений живо). `python test_vchasno_akty_kandydaty.py` →
exit 0/1.
"""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import vchasno_akty_kandydaty as va

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


# 1: parse_akt_filename — на РЕАЛЬНИХ назвах файлів (звірено живо 2026-09-18, документи_КОДВ)
_cases = [
    ("2026-08-31_rozetka_akt_royalti_TA00482473_178.97.pdf",
     {"vendor": "rozetka", "doc_id": "TA00482473", "amount": 178.97, "date": "2026-08-31"}),
    ("2026-08-31_rozetka_akt_dostup_TA00482472_324.36.pdf",
     {"vendor": "rozetka", "doc_id": "TA00482472", "amount": 324.36, "date": "2026-08-31"}),
    ("2026-07-31_prom_akt_UA-00011321442_817.47.pdf",
     {"vendor": "prom", "doc_id": "UA-00011321442", "amount": 817.47, "date": "2026-07-31"}),
    ("2026-07-31_prom_akt_UA-00011321442_817.47_ne_nova_vytrata.pdf",
     {"vendor": "prom", "doc_id": "UA-00011321442", "amount": 817.47, "date": "2026-07-31"}),
    ("2026-08-31_allo_akt_AL000389851_120.00.pdf",
     {"vendor": "allo", "doc_id": "AL000389851", "amount": 120.00, "date": "2026-08-31"}),
    ("2026-07-31_rozetkapay_akt_000573566.pdf",
     {"vendor": "rozetkapay", "doc_id": "000573566", "amount": None, "date": "2026-07-31"}),
    ("2026-09-16_eva_akt_RU000154242.pdf",
     {"vendor": "eva", "doc_id": "RU000154242", "amount": None, "date": "2026-09-16"}),
    ("2026-08-13_np_akt_NP-018826846_130.00.xml.json",
     {"vendor": "np", "doc_id": "NP-018826846", "amount": 130.00, "date": "2026-08-13"}),
]
for filename, expected in _cases:
    got = va.parse_akt_filename(filename)
    _chk(f"parse_akt_filename({filename[:45]}...): {expected['doc_id']}", got == expected)

_chk("parse_akt_filename: без розпізнаного номера → None (не падає, не вигадує)",
     va.parse_akt_filename("2026-07-31_novapay_akt_kompensatsii_1175.00_komisia_5.89.pdf") is None)
_chk("parse_akt_filename: файл поза конвенцією → None",
     va.parse_akt_filename("readme.txt") is None)

# 2: _already_in_book — РЕГРЕС на двох реальних бага, знайдених живим прогоном 2026-09-18
# 2a: Нова Пошта — файл латиницею "NP-...", книга кирилицею "№НП-..." (рядок 35)
_BOOK_NP = "Нова Пошта, рахунок №НП-018826846 від 10.08.2026."
_chk("_already_in_book: НП кирилиця в книзі проти латиниці у doc_id — числове ядро рятує",
     va._already_in_book("NP-018826846", _BOOK_NP))

# 2b: ALLO — файл з префіксом+нулями "AL000389851", книга без них "№389851" (рядок 81)
_BOOK_ALLO = "ALLO, Акт надання послуг №389851 від 31.08.2026"
_chk("_already_in_book: ALLO без префікса/нулів у книзі — числове ядро рятує",
     va._already_in_book("AL000389851", _BOOK_ALLO))

# 2c: точний збіг (Rozetka/Prom — бухгалтер копіює номер дослівно)
_BOOK_TA = "Термінал доступу Rozetka, акт TA00482473, 178.97 грн"
_chk("_already_in_book: точний збіг (Rozetka)", va._already_in_book("TA00482473", _BOOK_TA))

# 2d: справді відсутній — не знаходить (RozetkaPay-подібний, свідомо не внесений)
_chk("_already_in_book: справді відсутній — не знаходить",
     not va._already_in_book("000573566", _BOOK_NP + _BOOK_ALLO + _BOOK_TA))

# 2e: захист від коротких випадкових чисел — числове ядро <5 цифр НЕ використовується
_chk("_already_in_book: коротке числове ядро (<5 цифр) не дає хибних збігів",
     not va._already_in_book("AL1234", "щось із 1234 всередині іншого числа 51234"))

# 2f: doc_id без цифр узагалі (гіпотетично) — не падає
_chk("_already_in_book: doc_id без цифр наприкінці — не падає",
     va._already_in_book("ABCDEF", "текст без doc_id") is False)


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Акти Вчасно: парсинг реальних імен файлів, звірка з книгою (точний збіг + "
      "числове ядро для НП/ALLO) — усе коректно")
sys.exit(0)
