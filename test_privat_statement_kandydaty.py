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

# 5: classify() — призначення СЛОВО В СЛОВО з живого дампу 2026-10-05_privat_kandydaty.json
# (PDF пише кирилицю з латинською "i" — шаблони мають це витримувати).
_P_LIQ = "LIQPAY ID 2931972741 SOID 8- 081911575 PBK i19473252216 DATE 2026-09-28 TYPE acquiring"
_P_RZP = ("Переказ коштiв за операцiї 25.09.2026- 27.09.2026 зг.дог.№3001505450-П вiд 10.07.2026 на "
          "суму 1327.18 грн за виключ. винагор. 21.14 грн за їхпереказ. Без ПДВ.")
_P_COMM = ("Комiсiя за виконання платежiв в нацiональнiй валютi у сумi 379.91 грн вiд 25.09.2026, "
           "згiдно з вiдкритою офертою банку N б/н вiд 01.07.2026 та тарифiв банку, без ПДВ.")
_P_OWN = ("Переказ власних коштiв ФОП Чечетенко О.Ю. з рахунку NovaPay на розрахунковий рахунок у "
          "ПриватБанку. Без ПДВ.")
_P_TOYSI = "Оплата за iграшки вiд ФОП Чечетенко О.Ю."
_P_GUAR = "Гарантiйний платiж Без ПДВ. згiдно рахунку ТP-001037409 вiд 18.09.2026. Без ПДВ"
_P_HOST = "Оплата за послуги хостингу, згiдно рахунку №1404478h вiд 25.09.2026 р. Без ПДВ."


def _t(purpose, amount, date="2026-09-28", cp="", ref="R"):
    return {"ref": ref, "date": date, "amount": amount, "purpose": purpose, "counterparty": cp}


_chk("classify: LiqPay acquiring", ps.classify(_t(_P_LIQ, 425.55)) == "liqpay")
_chk("classify: виплата RozetkaPay (за контрагентом і за шаблоном)",
     ps.classify(_t(_P_RZP, 1306.04, cp='ТОВ "РОЗЕТКА ПЕЙ" 43170392')) == "rozetkapay"
     and ps.classify(_t(_P_RZP, 1306.04)) == "rozetkapay")
_chk("classify: комісія банку (латинська i в 'Комiсiя')", ps.classify(_t(_P_COMM, -5.0)) == "bank_commission")
_chk("classify: переказ власних коштів", ps.classify(_t(_P_OWN, 3600.0)) == "own_transfer")
_chk("classify: депозит Toysi і гарантійний платіж Rozetka — deposit",
     ps.classify(_t(_P_TOYSI, -5000.0)) == "deposit" and ps.classify(_t(_P_GUAR, -2000.0)) == "deposit")
_chk("classify: хостинг — other", ps.classify(_t(_P_HOST, -1013.84)) == "other")
_chk("classify: 'Оплата за iграшки' з ПЛЮСОМ (надходження) — НЕ депозит", ps.classify(_t(_P_TOYSI, 50.0)) == "other")
_chk("_soid: перенос рядка '8- 081911575' склеюється", ps._soid(_P_LIQ) == "8-081911575")

# 6: _has_amount — межі числа
_chk("_has_amount: крапка", ps._has_amount("еквайринг = 5.61. i9", 5.61))
_chk("_has_amount: кома", ps._has_amount("еквайринг 5,61 грн", 5.61))
_chk("_has_amount: НЕ частина 15.61", not ps._has_amount("сума 15.61", 5.61))
_chk("_has_amount: НЕ частина 5.612", not ps._has_amount("сума 5.612", 5.61))


def _row(n, date, money, text):
    return {"row": n, "date": date, "money": money, "text": text}


from datetime import date as _d  # noqa: E402

# 7: reconcile_liqpay
_BOOK_LIQ = [
    _row(142, _d(2026, 9, 27), {1: 431.16, 5: 259.32, 8: 48.73},
         "EVA.ua 8-081911575 ... LiqPay-еквайринг SOID 8-081911575: замовлення 431.16, зараховано 425.55 = 5.61."),
]
liq = _t(_P_LIQ, 425.55)
_chk("liqpay: еквайринг у тексті рядка продажу → in_book", ps.reconcile_liqpay(liq, _BOOK_LIQ)[0] == "in_book")
_BOOK_LIQ_NO_ACQ = [_row(142, _d(2026, 9, 27), {1: 431.16}, "EVA.ua 8-081911575, оплата карткою (LiqPay).")]
st, note = ps.reconcile_liqpay(liq, _BOOK_LIQ_NO_ACQ)
_chk("liqpay: продаж є, еквайрингу немає → missing_acquiring з сумою 5.61 і рядком",
     st == "missing_acquiring" and "5.61" in note and "р.142" in note)
_BOOK_LIQ_NEG = [_row(142, _d(2026, 9, 27), {1: 431.16},
                      "EVA.ua 8-081911575. LiqPay-еквайринг окремо НЕ знайдено, i9 = 43.12")]
_chk("liqpay: заперечна згадка «еквайринг НЕ знайдено» без суми 5.61 → НЕ in_book",
     ps.reconcile_liqpay(liq, _BOOK_LIQ_NEG)[0] == "missing_acquiring")
_BOOK_LIQ_SEP = _BOOK_LIQ_NO_ACQ + [_row(186, _d(2026, 9, 28), {8: 5.61},
                                        "LiqPay-еквайринг за EVA 8-081911575 (431.16 → 425.55 = 5.61)")]
_chk("liqpay: еквайринг ОКРЕМИМ рядком (як р.186) → in_book", ps.reconcile_liqpay(liq, _BOOK_LIQ_SEP)[0] == "in_book")
_chk("liqpay: продажу в книзі немає → not_in_book", ps.reconcile_liqpay(liq, [])[0] == "not_in_book")
_chk("liqpay: коротший SOID 8-08191157 НЕ знаходиться всередині 8-081911575",
     ps.reconcile_liqpay(_t(_P_LIQ.replace("081911575", "08191157"), 425.55), _BOOK_LIQ)[0] == "not_in_book")

# 8: reconcile_rozetkapay — живі числа виплати 28.09 (1327.18 / 21.14 / 1306.04)
_OPS = [
    {"date_pay": "25.09.2026 19:48:20", "sum": 365, "commission": -5.48, "order_id": "905912920"},
    {"date_pay": "25.09.2026 23:00:03", "sum": 142, "commission": -2.13, "order_id": "907059165"},
    {"date_pay": "26.09.2026 10:34:48", "sum": 211, "commission": -3.17, "order_id": "906155961"},
    {"date_pay": "27.09.2026 02:41:26", "sum": 609.18, "commission": -10.36, "order_id": "429649730"},
    {"date_pay": "28.09.2026 19:08:03", "sum": 114, "commission": -1.71, "order_id": "907290403"},
]
_BOOK_RZP = [_row(n, _d(2026, 9, 26), {1: 1.0}, f"Замовлення №{oid}")
             for n, oid in ((1, "905912920"), (2, "907059165"), (3, "906155961"), (4, "429649730"))]
rzp = _t(_P_RZP, 1306.04)
_chk("rozetkapay: X/Y = реєстр за 25–27.09, усі 4 замовлення в книзі → in_book",
     ps.reconcile_rozetkapay(rzp, _OPS, _BOOK_RZP)[0] == "in_book")
st, note = ps.reconcile_rozetkapay(rzp, _OPS, _BOOK_RZP[:3])
_chk("rozetkapay: одного замовлення немає в книзі → not_in_book з номером", st == "not_in_book" and "429649730" in note)
_chk("rozetkapay: реєстр не прочитано (None) → unverified", ps.reconcile_rozetkapay(rzp, None, _BOOK_RZP)[0] == "unverified")
_chk("rozetkapay: реєстру за діапазон немає → unverified", ps.reconcile_rozetkapay(rzp, _OPS[4:], _BOOK_RZP)[0] == "unverified")
_chk("rozetkapay: реєстр не сходиться з X (бракує операції) → unverified",
     ps.reconcile_rozetkapay(rzp, _OPS[1:], _BOOK_RZP)[0] == "unverified")
_chk("rozetkapay: номер замовлення як частина довшого числа НЕ рахується",
     ps.reconcile_rozetkapay(rzp, _OPS, _BOOK_RZP[:3] + [_row(9, None, {}, "№4296497301")])[0] == "not_in_book")

# 9: reconcile_commission — живі кейси р.134 (2 перекази в одному рядку) і р.185 (3×5.00)
_BOOK_COMM = [
    _row(134, _d(2026, 9, 25), {8: 10.0}, "ПриватБанк, комісія за 2 перекази (депозит Toysi 5000.00 + НП 379.91), 2×5.00 = 10.00"),
    _row(185, _d(2026, 10, 2), {8: 15.0}, "ПриватБанк, комісія за 3 платежі від 02.10.2026, 3×5.00 = 15.00"),
]
_chk("commission: базова сума 379.91 у тексті рядка → in_book",
     ps.reconcile_commission(_t(_P_COMM, -5.0, "2026-09-25"), 10.0, _BOOK_COMM)[0] == "in_book")
_P_COMM2 = _P_COMM.replace("379.91", "777.00").replace("25.09.2026", "02.10.2026")
_chk("commission: базової суми немає, але сума комісій за день 15.00 = графа рядка → in_book",
     ps.reconcile_commission(_t(_P_COMM2, -5.0, "2026-10-02"), 15.0, _BOOK_COMM)[0] == "in_book")
_chk("commission: сума за день НЕ збігається і базової суми немає → not_in_book",
     ps.reconcile_commission(_t(_P_COMM2, -5.0, "2026-10-02"), 20.0, _BOOK_COMM)[0] == "not_in_book")
_chk("commission: рядок за межею ±1 день не рахується",
     ps.reconcile_commission(_t(_P_COMM, -5.0, "2026-09-28"), 5.0, _BOOK_COMM)[0] == "not_in_book")

# 10: reconcile_all + label: не P&L не потрапляє в кандидати; ПІБ фізособи не виходить у мітку
txns5 = [_t(_P_OWN, 3600.0, ref="8"), _t(_P_TOYSI, -5000.0, ref="20"),
         _t(_P_HOST, -1013.84, date="2026-10-02", cp="ГОРЬОВА ОЛЕНА ОЛЕКСАНДРIВН А ФОП 3344000361", ref="23")]
ps.reconcile_all(txns5, [], {}, [])
_chk("reconcile_all: власні кошти і депозит Toysi → not_pnl, in_book=False",
     [t["status"] for t in txns5[:2]] == ["not_pnl", "not_pnl"] and not txns5[0]["in_book"])
_chk("reconcile_all: хостинг без книги → not_in_book", txns5[2]["status"] == "not_in_book")
_chk("label: ПІБ ФОП-контрагента НЕ в мітці, номер документа є",
     "ГОРЬОВА" not in txns5[2]["label"] and "1404478h" in txns5[2]["label"])
_chk("label: ПІБ власника з призначення переказу НЕ в мітці", "Чечетенко" not in txns5[0]["label"])
rep = ps.render_report(txns5, "T")
_chk("render_report: розділи «Справді не в книзі» і «Не P&L», без ПІБ",
     "Справді не в книзі — 1" in rep and "Не P&L" in rep and "ГОРЬОВА" not in rep and "Чечетенко" not in rep)

# 11: reconcile_all рахує суму комісій за день по ВСІХ комісіях дня (р.185 = 3×5.00)
txns3 = [_t(_P_COMM2.replace("777.00", f"{b}"), -5.0, "2026-10-02", ref=str(i))
         for i, b in enumerate(("101.00", "102.00", "103.00"))]
ps.reconcile_all(txns3, _BOOK_COMM, {}, [])
_chk("reconcile_all: 3 комісії 02.10 без базових сум у тексті → in_book через суму дня 15.00",
     all(t["status"] == "in_book" for t in txns3))

# 12: РЕГРЕС на знахідки незалежного аудиту PR #617 (кожна відтворена аудитором на живій книзі)
# п.1 — рядок комісії 18.09 (5.00) НЕ закриває невнесену комісію 17.09 (5.00, інша база)
_BOOK_106 = [_row(106, _d(2026, 9, 18), {8: 5.0}, "ПриватБанк, комісія за переказ гарантійного платежу 2000.00")]
_P_C17 = _P_COMM.replace("379.91", "777.00").replace("25.09.2026", "17.09.2026")
_chk("аудит п.1: комісія 17.09 НЕ закривається рядком 18.09 через «суму за день»",
     ps.reconcile_commission(_t(_P_C17, -5.0, "2026-09-17"), 5.0, _BOOK_106)[0] == "not_in_book")
# п.2 — заперечна згадка з сумою; посилання на окремий рядок, якого немає / який є
_B_NEG = [_row(149, _d(2026, 9, 30), {1: 431.16, 8: 16.87},
               "EVA 8-081911575. LiqPay-еквайринг 5.61 ще НЕ внесено — довнести.")]
_chk("аудит п.2: «еквайринг 5.61 ще НЕ внесено» → missing_acquiring",
     ps.reconcile_liqpay(liq, _B_NEG)[0] == "missing_acquiring")
_B_REF = [_row(149, _d(2026, 9, 30), {1: 431.16, 8: 16.87},
               "EVA 8-081911575. LiqPay-еквайринг 5.61 (утримано банком) внесено ОКРЕМИМ рядком 186 датою 02.10")]
_chk("аудит п.2: посилання на рядок 186, якого НЕМАЄ → missing_acquiring",
     ps.reconcile_liqpay(liq, _B_REF)[0] == "missing_acquiring")
_B_REF_OK = _B_REF + [_row(186, _d(2026, 10, 2), {8: 7.0}, "LiqPay-еквайринг за EVA 8-081911575 і 8-082105333")]
st, note = ps.reconcile_liqpay(liq, _B_REF_OK)
_chk("аудит п.2: рядок 186 є, гр.9 непорожня, той самий SOID → in_book з посиланням на р.186",
     st == "in_book" and "р.186" in note)
_B_REF_OTHER = _B_REF + [_row(186, _d(2026, 10, 2), {8: 7.0}, "LiqPay-еквайринг за EVA 8-082105333")]
_chk("аудит п.2: рядок 186 про ІНШИЙ SOID → missing_acquiring",
     ps.reconcile_liqpay(liq, _B_REF_OTHER)[0] == "missing_acquiring")
_B_99 = [_row(99, _d(2026, 9, 16), {1: 431.16, 8: 49.01},
              "EVA 8-081911575 ... комісія LiqPay-еквайрингу 5.61, факт замість заглушки. i9 було 45.10")]
_chk("аудит п.2 (не перестаратись): «факт замість заглушки» (живий р.99) лишається in_book",
     ps.reconcile_liqpay(liq, _B_99)[0] == "in_book")
_B_NO_I9 = [_row(142, _d(2026, 9, 27), {1: 431.16}, "EVA 8-081911575, LiqPay-еквайринг 5.61 = брутто−нетто")]
_chk("аудит п.2: сума в тексті, але гр.9 рядка порожня → missing_acquiring",
     ps.reconcile_liqpay(liq, _B_NO_I9)[0] == "missing_acquiring")
# п.3 — «Оплата за іграшки» з рахунком — пряма закупівля, не депозит
_chk("аудит п.3: «Оплата за iграшки згiдно рахунку №123» → other, не deposit",
     ps.classify(_t("Оплата за iграшки згiдно рахунку №123 вiд 01.10.2026", -900.0)) == "other")
# п.4 — номер замовлення лише в примітці (не в рядку продажу) не рахується
_B_RZP_NOTE = _BOOK_RZP[:3] + [_row(9, _d(2026, 10, 2), {2: 609.18}, "Повернення по №429649730")]
_chk("аудит п.4: номер замовлення лише в рядку повернення → not_in_book",
     ps.reconcile_rozetkapay(rzp, _OPS, _B_RZP_NOTE)[0] == "not_in_book")

if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ Парсер виписки ПриватБанку (реальна таблиця + звірка з книгою) працює")
sys.exit(0)
