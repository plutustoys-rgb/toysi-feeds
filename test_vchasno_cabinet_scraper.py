#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_vchasno_cabinet_scraper.py — регрес-тест парсера списку документів Вчасно
(vchasno_cabinet_scraper.py). `parse_document_rows` тестується на РЕАЛЬНОМУ тексті,
захопленому живо (Claude in Chrome, edo.vchasno.ua/app/documents?folder_id=6008,
2026-09-19) — жоден рядок не вигаданий.

Мережа/Playwright НЕ потрібні: parse_document_rows — чиста функція рядок→список.
`python test_vchasno_cabinet_scraper.py` → exit 0/1.
"""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import vchasno_cabinet_scraper as vc

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


# Реальний фрагмент (перші 4 записи + сторонні рядки шапки/фільтрів до і після) — захоплено
# живо get_page_text() 2026-09-19, edo.vchasno.ua/app/documents?folder_id=6008.
_REAL_TEXT = """Пошук
Оплатити тариф
3001505450
Чечетенко Олександр Юрійович
5+
ОЧ
Фільтри таблиці
Дата
Документ
Номер
Статус
Компанія
ЄДРПОУ/ІПН
Дані для експорту
Контрагент
18.09.26
1375492
Підписаний всіма
Ділай Софія Тарасівна
3731604484
vchasno@hostiq.ua
16.09.26
AKTVYKONANYKHROBIT
RU000154243
Підписаний всіма
ТОВ "РУШ"
32007740
juabrosimova@eva.dp.ua
15.09.26
Акт виконаних робiт
НП-019130332
Підписаний всіма
ТОВ "НОВА ПОШТА"
31316718
pb@novaposhta.ua
27.07.26
Договір з направлення Маркетплейс 1287 Чечетенко Олександр Юрійович
1287
Підписаний всіма
ТОВ "РУШ"
32007740
juabrosimova@eva.dp.ua
1 - 22 з 22
Показувати документів"""

lines = [l for l in _REAL_TEXT.split("\n") if l.strip()]
docs = vc.parse_document_rows(lines)

_chk("реальний текст: рівно 4 документи розпізнано (сторонні рядки відсіяні)", len(docs) == 4)

# Регрес: реальний ALLO-запис (П6231) має ЗАЙВИЙ рядок "Помилка розпізнавання" між
# ЄДРПОУ і контактом — захоплено живо 2026-09-19, той самий прогін.
_ALLO_QUIRK = """27.07.26
ЗаяваПроПриєднанняДоДоговоруСпівробітництва№П6231-pdf
П6231
Очікує підпису контрагента
ТОВ "АЛЛО"
30012848
Помилка розпізнавання
edo_doc@allo.ua"""
allo_docs = vc.parse_document_rows([l for l in _ALLO_QUIRK.split("\n") if l.strip()])
_chk("ALLO-запис із зайвим рядком 'Помилка розпізнавання' — все одно розпізнано",
     len(allo_docs) == 1 and allo_docs[0]["number"] == "П6231" and allo_docs[0]["edrpou"] == "30012848")

# Документ "1375492" не має окремого "типу" в реальних даних (число саме в позиції номера,
# тип поля "документ" збігається з номером для цього конкретного HostIQ-запису) — перевіряємо
# ЩО ФАКТИЧНО важливо для sync_new_documents: number + edrpou коректні.
numbers = [d["number"] for d in docs]
edrpous = [d["edrpou"] for d in docs]
_chk("номери документів розпізнані: 1375492, RU000154243, НП-019130332, 1287",
     numbers == ["1375492", "RU000154243", "НП-019130332", "1287"])
_chk("ЄДРПОУ/ІПН розпізнані коректно (8-10 цифр)",
     edrpous == ["3731604484", "32007740", "31316718", "32007740"])
_chk("дати у форматі dd.mm.yy",
     [d["date"] for d in docs] == ["18.09.26", "16.09.26", "15.09.26", "27.07.26"])
_chk("компанії розпізнані", [d["company"] for d in docs] ==
     ['Ділай Софія Тарасівна', 'ТОВ "РУШ"', 'ТОВ "НОВА ПОШТА"', 'ТОВ "РУШ"'])

# Порожній список / без валідних рядків-дат — не падає, повертає []
_chk("немає рядків-дат → порожній список, не падає", vc.parse_document_rows(["a", "b", "c"]) == [])
_chk("порожній вхід → порожній список", vc.parse_document_rows([]) == [])

# _EDRPOU_RE / _DATE_RE — межові випадки
_chk("_DATE_RE не ловить повну дату (dd.mm.yyyy)", vc._DATE_RE.match("18.09.2026") is None)
_chk("_DATE_RE ловить коротку (dd.mm.yy)", vc._DATE_RE.match("18.09.26") is not None)
_chk("_EDRPOU_RE не ловить email як ЄДРПОУ", vc._EDRPOU_RE.match("vchasno@hostiq.ua") is None)

# _COUNTERPARTY_ROUTING — усі ЄДРПОУ з реального списку заведені (регрес проти "невідомий ЄДРПОУ")
for edrpou in ("31316718", "43170392", "32007740", "30012848", "36507036",
               "33584049", "38324133", "3731604484"):
    _chk(f"маршрут заведено для ЄДРПОУ {edrpou}", edrpou in vc._COUNTERPARTY_ROUTING)

# _doc_id_from_href — витяг UUID з реального href-патерну
_chk("_doc_id_from_href витягує UUID",
     vc._doc_id_from_href("/app/documents/d043d934-3b41-4441-be89-a5fe680c0a30") ==
     "d043d934-3b41-4441-be89-a5fe680c0a30")
_chk("_doc_id_from_href без UUID → порожній рядок", vc._doc_id_from_href("/app/documents") == "")


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Парсер списку документів Вчасно (реальний текст) + маршрутизація + doc_id — усе коректно")
sys.exit(0)
