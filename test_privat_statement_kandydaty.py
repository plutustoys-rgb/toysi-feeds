#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_privat_statement_kandydaty.py — регрес-тест парсера виписки ПриватБанку
(privat_statement_kandydaty.py). `parse_transactions` тестується на РЕАЛЬНІЙ таблиці,
витягнутій `pdfplumber.extract_tables()` з живої виписки (лист "Виписка за рахунком
Чечетенко Олександр Юрiйович ФОП", 19.09.2026 09:02, PDF завантажено підписаним посиланням
БЕЗ логіну в Приват24 — перевірено живо тієї ж сесії, що й цей тест).

Мережа НЕ потрібна для parse_transactions/_already_in_book (чисті функції). Книжкові тести —
ТИМЧАСОВИЙ workbook (openpyxl), НЕ бойова KODV_PlutusToys_2026.xlsx.
`python test_privat_statement_kandydaty.py` → exit 0/1.
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import privat_statement_kandydaty as ps

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


# 1: parse_transactions на РЕАЛЬНІЙ таблиці (pdfplumber.extract_tables()[0] з живої виписки
# 19.09.2026 — див. докстрінг модуля). Включає підсумкові рядки (мають бути відсіяні) і
# 3 реальні операції.
_REAL_TABLE = [
    ['Вхідний залишок:', None, '5 909.17', None, None, 'Разом за кредитом:', None,
     '105.39 (Операцій: 1)', None],
    ['Вихідний залишок:', None, '4 009.56', None, None, 'Разом за дебетом:', None,
     '-2 005.00 (Операцій: 2)', None],
    ['Номер\nдокумента', 'Дата та\nчас\nоперації', None, 'Сума', 'Призначення платежу', None,
     'Реквізити контрагента', None, None],
    [None, None, None, None, None, None, 'Найменування\nРНОКПП', None, 'Рахунок\nБанк'],
    ['19', '18.09.2026\n16:25', None, '-2 000.00',
     'Гарантiйний платiж Без ПДВ. згiдно рахунку\nТP-001037409 вiд 18.09.2026.\nБез ПДВ', None,
     'ТОВ "Термiнал\nРозетка"\n33584049', None,
     'UA153510050000026002\n194327500\nАТ "УКРСИББАНК"'],
    ['9IOERRYA\nUY', '18.09.2026\n16:25', None, '-5.00',
     'Комiсiя за виконання платежiв в\nнацiональнiй валютi у сумi 2000.00 грн\nвiд 18.09.2026, '
     'згiдно з вiдкритою\nофертою банку N б/н вiд 01.07.2026 та\nтарифiв банку, без ПДВ.', None,
     'ЗА\nДЕБЕТУВАННЯ\nРАХУНКУ(UAH)', None,
     'UA493052990000065105\n918415022\nАТ КБ "ПРИВАТБАНК"\n14360570'],
    ['FC454326', '18.09.2026\n15:28', None, '105.39',
     'Переказ коштiв за операцiї 17.09.2026-\n17.09.2026 зг.дог.№3001505450-П вiд\n'
     '10.07.2026 на суму 107 грн за виключ.\nвинагор. 1.61 грн за їх переказ. Без\nПДВ.', None,
     'ТОВ "РОЗЕТКА\nПЕЙ"\n43170392', None,
     'UA223348510000000000\n002654425\nАТ "ПУМБ"'],
]

txns = ps.parse_transactions(_REAL_TABLE)
_chk("реальна таблиця: рівно 3 операції (підсумки/заголовки відсіяні)", len(txns) == 3)
_chk("операція 1: дата/сума/ref", txns[0]["date"] == "2026-09-18" and txns[0]["amount"] == -2000.0
     and txns[0]["ref"] == "19")
_chk("операція 2: від'ємна комісія -5.00, ref з двох рядків", txns[1]["amount"] == -5.0
     and "9IOERRYA" in txns[1]["ref"])
_chk("операція 3: дохід 105.39, ref FC454326 (RozetkaPay acquiring)",
     txns[2]["amount"] == 105.39 and txns[2]["ref"] == "FC454326")
_chk("операція 3: контрагент 'РОЗЕТКА ПЕЙ' у purpose/counterparty витягнутий",
     "РОЗЕТКА" in txns[2]["counterparty"])

# 2: сума з пробілом-роздільником тисяч
row_thousands = ['X', '01.01.2026', None, '12 345.67', 'p', None, 'c', None, 'a']
t = ps.parse_transactions([row_thousands])
_chk("сума з пробілом-тисяч (12 345.67) парситься як 12345.67", t and t[0]["amount"] == 12345.67)

# 3: рядок без валідної дати/суми — НЕ операція
_chk("рядок без дати не операція", ps.parse_transactions([['X', 'не дата', None, '1.00']]) == [])
_chk("рядок без суми не операція", ps.parse_transactions([['X', '01.01.2026', None, 'не сума']]) == [])
_chk("закороткий рядок (< 5 колонок) не падає", ps.parse_transactions([['a', 'b']]) == [])

# 4: _book_money_index / _already_in_book — тимчасовий workbook, НЕ бойовий
import openpyxl

tmp_xlsx = Path(tempfile.mktemp(suffix=".xlsx"))
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "КОДВ"
for _ in range(6):
    ws.append([None] * 12)  # рядки 1-6 — шапка/заголовки (дані з рядка 7)
# рядок 7: графа1=дата, графа2=дохід=105.39 (наша операція 3)
ws.append([datetime(2026, 9, 18), 105.39, None, None, "джерело", None, None, None, None, None, None, "примітка"])
# рядок 8: графа9(інше списання, індекс 8)=2000.00 — та сама сума, що операція 1 (без знаку в книзі)
ws.append([datetime(2026, 9, 19), None, None, None, "джерело", None, None, None, 2000.00, None, None, None])
# рядок 9: графа4(індекс 3)=777.77 і графа11(індекс 10)=888.88 — ОБЧИСЛЮВАНІ підсумки
# ("дохід за вирахуванням повернень" / "чистий оподатковуваний дохід"), НЕ сирі операції —
# money-critical фікс (мій же попередній коментар про індекси був написаний з пам'яті, не
# звірений живо з реальними заголовками книги; графа11 знайшов аудит PR #579, графа4 — я сама
# при виправленні). Ці суми НЕ мають потрапляти в індекс звірки.
ws.append([datetime(2026, 9, 20), None, None, 777.77, "джерело", None, None, None, None, None, 888.88, None])
wb.save(tmp_xlsx)
wb.close()

ps.KODV_XLSX = tmp_xlsx
idx = ps._book_money_index()

_chk("індекс бачить графа2 (дохід) 105.39", 105.39 in idx)
_chk("індекс бачить графа9 (інше списання, не лише графа2) 2000.0", 2000.0 in idx)
_chk("графа4 (777.77, обчислюваний підсумок) НЕ в індексі", 777.77 not in idx)
_chk("графа11 (888.88, обчислюваний підсумок) НЕ в індексі", 888.88 not in idx)

t_in_book = {"date": "2026-09-18", "amount": 105.39}
_chk("операція 3 (105.39, 18.09) — уже в книзі (точна дата)", ps._already_in_book(t_in_book, idx))

t_neg_in_book = {"date": "2026-09-18", "amount": -2000.0}
_chk("операція 1 (-2000, 18.09) — у книзі 19.09 БЕЗ знаку → знайдено за abs()+±1день",
     ps._already_in_book(t_neg_in_book, idx))

t_not_in_book = {"date": "2026-09-18", "amount": -5.0}
_chk("операція 2 (-5.00) — НЕ в книзі", not ps._already_in_book(t_not_in_book, idx))

t_far_date = {"date": "2026-09-25", "amount": 105.39}
_chk("та сама сума, дата за межею ±1 день — НЕ вважається знайденою",
     not ps._already_in_book(t_far_date, idx))

tmp_xlsx.unlink(missing_ok=True)

if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Парсер виписки ПриватБанку (реальна таблиця + звірка з книгою) працює")
sys.exit(0)
