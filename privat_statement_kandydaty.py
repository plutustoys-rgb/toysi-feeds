#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""privat_statement_kandydaty.py — кандидати з ЩОДЕННОЇ виписки ПриватБанку (усі рухи по
рахунку), яких НЕ видно в книзі.

НАВІЩО (запит власника 2026-09-19, "виписки з банка приходять кожен день на пошту"): лист
ПриватБанку — це НЕ сама виписка, а нагадування з кнопкою "Отримати виписку", яка веде на
підписане пряме посилання `att.privatbank.ua/efile/<токен>` (через awstrack.me-трекер +
socauth.privatbank.ua/out_click.php-редирект) — PDF, ПІДПИСАНИЙ CAdES (PKCS7-обгортка навколо
`%PDF-…`). Перевірено живо 2026-09-19: посилання скачується БЕЗ логіну в Приват24 (не той шлях,
що заблокований Автоклієнт API — `a6fbc01`, платний тариф). `kodv_mail_archiver.py` витягує це
посилання й зберігає PDF у документи_КОДВ/YYYY-MM/ПриватБанк/ — той самий патерн, що
`_find_rozetkapay_link`/`_download` для RozetkaPay (PR #566).

ЩО: читає ВСІ PDF у документи_КОДВ/*/ПриватБанк/*.pdf ЩОРАЗУ (без власного курсора — навмисно,
див. нижче), парсить таблицю операцій (`pdfplumber.extract_tables()` — НЕ `extract_text()`: для
широкої багаторядкової таблиці текстовий потік плутає колонки, таблична екстракція дає чисті
клітинки). Кожну операцію звіряє з книгою за (сума, дата ±1 день) — той самий принцип, що
`checkbox_registry_sync._match_book`, лише ширше: шукає суму НЕ в одній конкретній графі
(виписка покриває дохід/комісії/інші списання одразу), а в БУДЬ-ЯКІЙ графі СИРИХ операцій книги
(2,3,6,7,8,9,10 — БЕЗ графи 4 й графи 11, це ОБЧИСЛЮВАНІ підсумки, не окремі операції, див.
`_BOOK_MONEY_COLS`) на відповідну дату — бо яка саме графа підходить конкретній операції,
вирішує бухгалтер, не скрипт. Пише кандидатів у документи_КОДВ, книгу НЕ чіпає.

ЧОМУ БЕЗ ВЛАСНОГО КУРСОРА (на відміну від novapay_registry_kandydaty.py): цей скрипт передає
"поточний повний список ще-не-в-книзі" у `kandydaty_registry.sync_open_candidates(resolve=True)`
(патерн `toysi_returns_kandydaty.py`/`vchasno_akty_kandydaty.py`) — реєстр сам закриває кандидата,
щойно він зникає зі списку (бо `_already_in_book` тепер бачить його в книзі). Якби скрипт мав
власний курсор "вже показував — не показуй знову", той самий кандидат випав би зі списку
`unresolved` на ДРУГОМУУ прогоні незалежно від того, чи бухгалтер справді його внесла — і
`resolve=True` хибно закрив би його як "resolved" (рецидив класу бага з КОДВ_журнал
«ДОПОВНЕННЯ 5», Д1). PDF повторно парситься щоразу — дешево (кілька файлів, десятки KB).

Кирилична деталізація операцій (призначення платежу, найменування контрагента) з PDF
екстрактиться КОРЕКТНО (перевірено живо — Windows-консоль просто не вміє її друкувати в
терміналі, це НЕ дефект PDF/шрифту).

КЛАСИ ОПЕРАЦІЙ (запит головного бухгалтера 2026-10-05, КОДВ_CHANNEL.md): звірка «сума ±1 день»
структурно не працює там, де банк зараховує НЕТТО, а книга веде БРУТТО — 33 з 34 операцій
дампу 05.10 були хибним «❗ НЕ в книзі». Тому спершу `classify()` за призначенням платежу, далі
звірка свого класу:
  • liqpay        — «LIQPAY ID … SOID 8-0… TYPE acquiring»: шукаємо рядок продажу за номером
                    замовлення (SOID) у тексті книги; еквайринг = брутто(гр.2) − нетто; вважаємо
                    внесеним, якщо рядок із тим самим SOID містить слово «еквайринг» і саму суму.
  • rozetkapay    — виплата ТОВ «РОЗЕТКА ПЕЙ» «за операції Д1–Д2 на суму X за виключ. винагор. Y»:
                    X/Y звіряємо з реєстрами RozetkaPay (документи_КОДВ/*/RozetkaPay/*.xlsx) за
                    датою оплати в [Д1, Д2], потім кожне замовлення реєстру шукаємо в книзі.
                    Еквайринг по замовленнях веде rozetkapay_registry_kandydaty.py — тут не дублюємо.
  • bank_commission — «Комісія за виконання платежів … у сумі N грн від Д»: рядок книги ±1 день,
                    текст якого містить «комісі» і або базову суму N, або значення = сумі всіх
                    комісій банку за цей день (бухгалтер пише їх одним рядком, напр. 3×5.00).
  • own_transfer / deposit — не P&L (власні кошти ФОП; депозит Toysi — довідник КОДВ §3, лише
                    «Оплата за іграшки» БЕЗ рахунку/накладної; гарантійний платіж Rozetka = застава —
                    рядки книги 34/60/106): у кандидати не потрапляють, але видно окремим розділом звіту.
  • other         — стара звірка: сума в будь-якій сирій графі книги ±1 день.
У звіт .md — лише клас і коротка мітка (номер документа / контрагент-юрособа), без ПІБ фізосіб.

ЗАПУСК: python privat_statement_kandydaty.py
"""
import glob
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import kandydaty_registry
import source_freshness

BASE_DIR = Path(__file__).parent
COWORK_DIR = Path(os.environ.get("PLUTUS_COWORK_DIR",
                                 r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
DOCS_DIR = COWORK_DIR / "документи_КОДВ"
KODV_XLSX = COWORK_DIR / "KODV_PlutusToys_2026.xlsx"
# Грошові графи книги (0-indexed, ряд iter_rows(min_row=7)) — заголовки звірено ЖИВО з
# KODV_PlutusToys_2026.xlsx (рядок 6), не з пам'яті: 1=Графа2 сума доходу за день, 2=Графа3
# повернення коштів, 5=Графа6 витрати на товари, 6=Графа7 оплата праці/ЦПХ, 7=Графа8 ЄСВ/
# податки, 8=Графа9 інші витрати, 9=Графа10 амортизація — усе СИРІ значення операцій.
# СВІДОМО ВИКЛЮЧЕНО (не грошові кандидати для звірки, хоч і числові): 0=Графа1 дата (ключ
# звірки, не сума), 3=Графа4 "дохід за вирахуванням повернень" — ОБЧИСЛЮВАНА (графа2-графа3,
# не сира операція), 4=Графа5 "реквізити підтвердного документа" (текст), 10=Графа11 "чистий
# оподатковуваний дохід за день" — теж ОБЧИСЛЮВАНА (аудит PR #579 знайшов цю; графа4 я знайшла
# сама при виправленні — той самий клас, мій попередній коментар про "3=до перерахування"
# був написаний з пам'яті, не звірений живо). Обчислювані графи не входять: сума операції з
# банку могла б випадково збігтися з денним НЕТТО-підсумком і хибно позначитись "уже в книзі".
# Зараз обидві графи 100% порожні в реальній книзі (0/101 заповнених рядків) — практичного
# ризику сьогодні нема, але структурно вони не мали бути в цьому наборі.
_BOOK_MONEY_COLS = (1, 2, 5, 6, 7, 8, 9)
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_AMOUNT_RE = re.compile(r"^-?[\d\s]+\.\d{2}$")


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[PrivatStmt] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _all_statements() -> list:
    """Усі PDF-виписки в теці — обробляємо ВСІ щоразу (без курсора, див. докстрінг модуля)."""
    return sorted(set(glob.glob(str(DOCS_DIR / "*" / "ПриватБанк" / "*.pdf"))))


def extract_table(pdf_path: str) -> list:
    """Тонка обгортка: `extract_tables()`, не `extract_text()` — таблична екстракція зберігає
    межі клітинок, текстовий потік для широкої багаторядкової таблиці плутає колонки (звірено
    живо на реальній виписці 2026-09-19).

    Файл — CAdES/PKCS7-підписаний (ASN.1-обгортка ПЕРЕД `%PDF-…`, офсет варіюється — звірено
    живо: 72 байти на реальних виписках 18-19.09, але не гарантовано фіксований), тому шукаємо
    маркер і ріжемо звідти, а не довіряємо файлу з байта 0 — pdfplumber інакше падає
    з "No /Root object"."""
    import pdfplumber
    raw = Path(pdf_path).read_bytes()
    idx = raw.find(b"%PDF-")
    if idx == -1:
        raise ValueError(f"{pdf_path}: немає %PDF- маркера — не PDF (протухле посилання/HTML?)")
    import io
    with pdfplumber.open(io.BytesIO(raw[idx:])) as pdf:
        tables = []
        for page in pdf.pages:
            tables += page.extract_tables()
    rows = []
    for t in tables:
        rows.extend(t)
    return rows


def parse_transactions(table: list) -> list:
    """Чиста функція (тестована без PDF): рядок таблиці — операція, якщо колонка 1 (дата) має
    dd.mm.yyyy, і колонка 3 (сума) парситься як число. Решта (заголовки/підсумки) відсіюється
    природно — вони не мають ОБОХ ознак одночасно."""
    out = []
    for row in table:
        if len(row) < 5:
            continue
        date_cell = (row[1] or "").replace("\n", " ")
        amount_cell = (row[3] or "").replace("\n", " ").strip()
        m = _DATE_RE.search(date_cell)
        if not m or not _AMOUNT_RE.match(amount_cell):
            continue
        try:
            amount = float(amount_cell.replace(" ", ""))
        except ValueError:
            continue
        d, mo, y = m.groups()
        ref = (row[0] or "").replace("\n", " ").strip()
        purpose = (row[4] or "").replace("\n", " ").strip() if len(row) > 4 else ""
        counterparty = (row[6] or "").replace("\n", " ").strip() if len(row) > 6 else ""
        out.append({
            "ref": ref,
            "date": f"{y}-{mo}-{d}",
            "amount": round(amount, 2),
            "purpose": purpose,
            "counterparty": counterparty,
        })
    return out


def _book_money_index() -> dict:
    """READ-ONLY: {сума(грн, 2 знаки, БЕЗ знаку) → [дата рядка, ...]} з БУДЬ-ЯКОЇ грошової графи
    книги (не лише графа2, на відміну від checkbox_registry_sync — виписка покриває всі типи
    рухів). Книгу НЕ пише."""
    index: dict = {}
    if not KODV_XLSX.exists():
        return index
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(KODV_XLSX), data_only=True, read_only=True)
        try:
            ws = wb["КОДВ"]
            for row in ws.iter_rows(min_row=7):
                if len(row) <= max(_BOOK_MONEY_COLS):
                    continue
                d = row[0].value
                for col in _BOOK_MONEY_COLS:
                    v = row[col].value
                    if isinstance(v, (int, float)) and v:
                        key = round(abs(float(v)), 2)
                        index.setdefault(key, []).append(_coerce_date(d))
        finally:
            wb.close()
    except Exception as e:  # noqa: BLE001
        print(f"[PrivatStmt] книжковий індекс не побудовано (не критично): {e}", file=sys.stderr)
    return index


def _coerce_date(v):
    if isinstance(v, datetime):
        return v.date()
    if hasattr(v, "year") and hasattr(v, "month"):
        return v
    return None


def _already_in_book(txn: dict, index: dict) -> bool:
    key = round(abs(txn["amount"]), 2)
    dates = index.get(key, [])
    try:
        txn_date = datetime.strptime(txn["date"], "%Y-%m-%d").date()
    except ValueError:
        return False
    return any(d is not None and abs((d - txn_date).days) <= 1 for d in dates)


# ── Класифікація за призначенням платежу ────────────────────────────────────────────────
# PDF ПриватБанку пише кирилицю з ЛАТИНСЬКОЮ "i" ("Комiсiя", "операцiї") — тому [iі] у
# шаблонах, а не нормалізація тексту (вона зіпсувала б латинські "acquiring"/"LIQPAY ID").
_LIQPAY_RE = re.compile(r"LIQPAY\s*ID\s*(\d+).*?SOID\s*(\S+(?:\s+\d+)?)\s+PBK", re.S)
_RZP_RE = re.compile(
    r"операц[iі]\S*\s*(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4}).*?"
    r"на\s*суму\s*([\d.]+)\s*грн.*?винагор\.?\s*([\d.]+)", re.S | re.I)
_COMM_RE = re.compile(
    r"Ком[iі]с[iі]я\s+за\s+виконання\s+платеж.*?у\s+сум[iі]\s+([\d.]+)\s*грн\s+в[iі]д\s+"
    r"(\d{2}\.\d{2}\.\d{4})", re.S | re.I)
_OWN_RE = re.compile(r"Переказ\s+власних\s+кошт", re.I)
_TOYSI_DEPOSIT_RE = re.compile(r"Оплата\s+за\s+[iі]грашки", re.I)
_GUARANTEE_RE = re.compile(r"Гарант[iі]йний\s+плат[iі]ж", re.I)
_INVOICE_RE = re.compile(r"рахун|накл|№|видатков|замовл|\bN\s*\d", re.I)
_DOC_RE = re.compile(r"(?:№|N)\s*([A-Za-zА-Яа-яІіЇїЄєҐґ0-9][A-Za-zА-Яа-яІіЇїЄєҐґ0-9/\-]*)")
_LEGAL_RE = re.compile(r"^\s*(ТОВ|Товариство|АТ|ПрАТ|ПАТ|ДП)\b", re.I)


def classify(txn: dict) -> str:
    """Чиста функція: клас операції за призначенням/контрагентом (див. докстрінг модуля)."""
    p = txn.get("purpose") or ""
    cp = (txn.get("counterparty") or "").upper()
    amt = txn.get("amount") or 0
    if amt > 0 and "LIQPAY" in p and "acquiring" in p.lower():
        return "liqpay"
    if amt > 0 and ("РОЗЕТКА ПЕЙ" in cp or _RZP_RE.search(p)):
        return "rozetkapay"
    if amt < 0 and _COMM_RE.search(p):
        return "bank_commission"
    if _OWN_RE.search(p):
        return "own_transfer"
    if amt < 0 and _GUARANTEE_RE.search(p):
        return "deposit"
    # Аудит PR #617, п.3: «Оплата за іграшки» = депозит Toysi лише БЕЗ рахунку/накладної. З
    # рахунком це може бути пряма закупівля в іншого постачальника (графа 6) — тоді клас «інше»,
    # щоб не зникла мовчки з кандидатів.
    if amt < 0 and _TOYSI_DEPOSIT_RE.search(p) and not _INVOICE_RE.search(p):
        return "deposit"
    return "other"


def _soid(purpose: str) -> str:
    """'SOID 8- 081911575 PBK' (перенос рядка в PDF) → '8-081911575'."""
    m = _LIQPAY_RE.search(purpose or "")
    return re.sub(r"\s+", "", m.group(2)) if m else ""


def _parse_dmy(s: str):
    try:
        return datetime.strptime(s[:10], "%d.%m.%Y").date()
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def label(txn: dict) -> str:
    """Коротка мітка для звіту .md — БЕЗ ПІБ фізосіб (лише номер документа / контрагент-юрособа)."""
    cls = txn.get("cls") or classify(txn)
    p = txn.get("purpose") or ""
    if cls == "liqpay":
        return f"LiqPay SOID {_soid(p) or '?'}"
    if cls == "rozetkapay":
        m = _RZP_RE.search(p)
        if m:
            return f"RozetkaPay: операції {m.group(1)[:5]}–{m.group(2)[:5]}, брутто {m.group(3)}, винагорода {m.group(4)}"
        return "RozetkaPay (призначення не розпізнано)"
    if cls == "bank_commission":
        m = _COMM_RE.search(p)
        return f"Комісія банку за платіж {m.group(1)} від {m.group(2)}" if m else "Комісія банку"
    if cls == "own_transfer":
        return "Переказ власних коштів ФОП між рахунками"
    if cls == "deposit":
        if _GUARANTEE_RE.search(p):
            d = _DOC_RE.search(p)
            return "Гарантійний платіж Rozetka (застава)" + (f", рахунок {d.group(1)}" if d else "")
        return "Поповнення депозиту Toysi"
    cp = (txn.get("counterparty") or "").strip()
    who = cp if _LEGAL_RE.search(cp) else "контрагент — ФОП/фізособа"
    d = _DOC_RE.search(p)
    return f"{who}" + (f", документ №{d.group(1)}" if d else "")


def _has_amount(text: str, value: float) -> bool:
    """Сума як окреме число в тексті ('5.61' або '5,61'), не частина іншого числа."""
    v = f"{abs(value):.2f}"
    for form in (v, v.replace(".", ",")):
        if re.search(r"(?<![\d.,])" + re.escape(form) + r"(?!\d)", text or ""):
            return True
    return False


# Формат запису бухгалтера (живо: р.97/99/115/129/142/144/168-170/173): «i9 було 43.12, стало 48.73».
_I9_STEP_RE = re.compile(r"i9\s*було\s*(\d+(?:[.,]\d+)?)[.,]?\s*,?\s*стало\s*(\d+(?:[.,]\d+)?)", re.I)
# Окремий рядок еквайрингу (живо: р.186 «(112.49 → 111.03 = 1.46, …) і (245.31 → 242.12 = 3.19, …)»).
_EQ_AMOUNT_RE = re.compile(r"→\s*\d+[.,]\d{2}\s*=\s*(\d+[.,]\d{2})")


def _num(s: str):
    try:
        return float(str(s).strip().rstrip(".,").replace(",", "."))
    except ValueError:
        return None


def _id_in(text: str, ident: str) -> bool:
    return bool(ident) and re.search(r"(?<![\d])" + re.escape(ident) + r"(?![\d])", text or "") is not None


def _load_book_rows() -> list:
    """READ-ONLY: рядки книги як {row, date, money{col: float}, text (графа 5 + примітка L)}."""
    rows = []
    if not KODV_XLSX.exists():
        return rows
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(KODV_XLSX), data_only=True, read_only=True)
        try:
            ws = wb["КОДВ"]
            for i, row in enumerate(ws.iter_rows(min_row=7), start=7):
                vals = [c.value for c in row]
                money = {}
                for col in _BOOK_MONEY_COLS:
                    v = vals[col] if len(vals) > col else None
                    if isinstance(v, (int, float)) and v:
                        money[col] = float(v)
                text = " ".join(str(vals[c]) for c in (4, 11) if len(vals) > c and vals[c] is not None)
                if not money and not text:
                    continue
                e_text = str(vals[4]) if len(vals) > 4 and vals[4] is not None else ""
                rows.append({"row": i, "date": _coerce_date(vals[0] if vals else None),
                             "money": money, "text": text, "e_text": e_text})
        finally:
            wb.close()
    except Exception as e:  # noqa: BLE001
        print(f"[PrivatStmt] рядки книги не прочитано (не критично): {e}", file=sys.stderr)
    return rows


def _load_rozetkapay_ops():
    """Усі операції з УСІХ реєстрів RozetkaPay (дедуп за фін-номером). None — якщо реєстр не
    прочитався: тоді виплати RozetkaPay лишаються «не звірено», а не хибно «в книзі»."""
    try:
        import rozetkapay_registry_kandydaty as rzp
        files = sorted(f for f in glob.glob(str(DOCS_DIR / "*" / "RozetkaPay" / "*.xlsx"))
                       if not os.path.basename(f).startswith("~$"))
        ops = {}
        for f in files:
            for r in rzp._parse_registry(Path(f)):
                ops[r["finop"] or f"{f}#{r['seq']}"] = r
        return list(ops.values())
    except Exception as e:  # noqa: BLE001
        print(f"[PrivatStmt] реєстри RozetkaPay не прочитано: {e}", file=sys.stderr)
        return None


def reconcile_liqpay(txn: dict, book_rows: list) -> tuple:
    soid = _soid(txn.get("purpose"))
    if not soid:
        return "unverified", "SOID у призначенні не розпізнано"
    rows = [r for r in book_rows if _id_in(r["text"], soid)]
    sales = [r for r in rows if r["money"].get(1, 0) > 0]
    if not sales:
        return "not_in_book", f"продаж {soid} у книзі не знайдено (зараховано нетто {txn['amount']:.2f})"
    sale = min(sales, key=lambda r: abs(r["money"][1] - txn["amount"]))
    gross = sale["money"][1]
    acq = round(gross - txn["amount"], 2)
    if acq < -0.005:
        return "unverified", f"нетто {txn['amount']:.2f} більше за брутто {gross:.2f} (р.{sale['row']}) — перевір"
    if abs(acq) < 0.005:
        return "in_book", f"р.{sale['row']}: брутто = нетто {gross:.2f}, еквайрингу немає"
    # Аудит PR #617, раунди 1-2: згадка суми в тексті ≠ запис суми в графі 9 («еквайринг 1.46 —
    # довнесу» теж згадка). Вимагати суму САМЕ в гр.9 не можна — бухгалтер веде гр.9 зведеною
    # (р.142 = комісія EVA 43.12 + 5.61). Тому доказ — АРИФМЕТИКА, прив'язана до гр.9:
    for r in rows:
        i9 = r["money"].get(8)
        if not i9 or "еквайринг" not in r["text"].lower():
            continue
        # (А) у рядку записано «i9 було A, стало B»: B − A = еквайринг, а останнє B = поточна гр.9
        steps = [(_num(a), _num(b)) for a, b in _I9_STEP_RE.findall(r["text"])]
        steps = [(a, b) for a, b in steps if a is not None and b is not None]
        if steps and abs(steps[-1][1] - i9) < 0.005 and any(abs((b - a) - acq) < 0.005 for a, b in steps):
            return "in_book", f"р.{sale['row']}: брутто {gross:.2f}, еквайринг {acq:.2f} у р.{r['row']} (i9 +{acq:.2f})"
        # (Б) окремий рядок еквайрингу (лише гр.9, як р.186): гр.9 = сума перелічених «= X»
        if set(r["money"]) == {8}:
            parts = [x for x in (_num(s) for s in _EQ_AMOUNT_RE.findall(r["e_text"])) if x is not None]
            if any(abs(x - acq) < 0.005 for x in parts) and abs(sum(parts) - i9) < 0.005:
                return "in_book", f"р.{sale['row']}: брутто {gross:.2f}, еквайринг {acq:.2f} у р.{r['row']}"
    if any("еквайринг" in r["text"].lower() and _has_amount(r["text"], acq) for r in rows):
        return "unverified", (f"еквайринг {acq:.2f} для р.{sale['row']} згадано в тексті, але не підтверджено "
                              f"графою 9 (нема «i9 було A, стало B» чи окремого рядка з сумою) — перевір")
    return "missing_acquiring", f"еквайринг {acq:.2f} для р.{sale['row']} (брутто {gross:.2f} − нетто {txn['amount']:.2f})"


def reconcile_rozetkapay(txn: dict, ops, book_rows: list) -> tuple:
    m = _RZP_RE.search(txn.get("purpose") or "")
    if not m:
        return "unverified", "призначення виплати RozetkaPay не розпізнано"
    if ops is None:
        return "unverified", "реєстри RozetkaPay не прочитано"
    d1, d2 = _parse_dmy(m.group(1)), _parse_dmy(m.group(2))
    gross, fee = float(m.group(3)), float(m.group(4))
    in_range = [o for o in ops if (dp := _parse_dmy(o.get("date_pay") or "")) and d1 <= dp <= d2]
    if not in_range:
        return "unverified", f"реєстру RozetkaPay за {m.group(1)[:5]}–{m.group(2)[:5]} немає"
    s = round(sum(float(o["sum"] or 0) for o in in_range), 2)
    c = round(sum(abs(float(o["commission"] or 0)) for o in in_range), 2)
    if abs(s - gross) > 0.005 or abs(c - fee) > 0.005 or abs(round(gross - fee, 2) - txn["amount"]) > 0.005:
        return "unverified", (f"реєстр за {m.group(1)[:5]}–{m.group(2)[:5]}: сума {s:.2f}/комісія {c:.2f}, "
                              f"виписка {gross:.2f}/{fee:.2f} — не сходиться")
    orders = [o["order_id"] for o in in_range if float(o["sum"] or 0) > 0]
    # Аудит PR #617, п.4: номер має стояти в РЯДКУ ПРОДАЖУ (графа 2 > 0), не в примітці/поверненні.
    missing = [oid for oid in orders
               if not any(r["money"].get(1, 0) > 0 and _id_in(r["text"], oid) for r in book_rows)]
    if missing:
        return "not_in_book", f"замовлення реєстру не знайдено в книзі: {', '.join(missing)}"
    return "in_book", (f"брутто {gross:.2f} = {len(in_range)} оп. реєстру ({', '.join(orders)}); "
                       f"винагорода {fee:.2f} — еквайринг веде rozetkapay_registry_kandydaty")


def reconcile_commission(txn: dict, day_total: float, book_rows: list) -> tuple:
    m = _COMM_RE.search(txn.get("purpose") or "")
    base = float(m.group(1)) if m else None
    try:
        d = datetime.strptime(txn["date"], "%Y-%m-%d").date()
    except ValueError:
        return "unverified", "дата операції не розпізнана"
    # Аудит PR #617 (раунди 1-2): лише рядок ТІЄЇ САМОЇ дати. З ±1 днем рядок комісії 18.09
    # «закривав» невнесену комісію 17.09 (і за сумою дня, і за тією ж базою 2000.00). Бухгалтер
    # датує рядок комісії днем списання — живо: р.106 18.09, р.134 25.09, р.185 02.10.
    near = [r for r in book_rows if r["date"] == d and "комісі" in r["text"].lower()]
    for r in near:
        if base is not None and _has_amount(r["text"], base):
            return "in_book", f"р.{r['row']} (згадано платіж {base:.2f})"
    for r in near:
        if any(abs(v - day_total) < 0.005 for v in r["money"].values()):
            return "in_book", f"р.{r['row']} (комісії за день разом {day_total:.2f})"
    return "not_in_book", f"комісію за платіж {base if base is not None else '?'} у книзі не знайдено"


def reconcile_all(txns: list, book_rows: list, index: dict, rzp_ops) -> None:
    """Проставляє кожній операції cls/status/note/in_book. in_book=True лише для 'in_book'."""
    day_totals: dict = {}
    for t in txns:
        t["cls"] = classify(t)
        if t["cls"] == "bank_commission":
            day_totals[t["date"]] = round(day_totals.get(t["date"], 0) + abs(t["amount"]), 2)
    for t in txns:
        cls = t["cls"]
        if cls == "liqpay":
            status, note = reconcile_liqpay(t, book_rows)
        elif cls == "rozetkapay":
            status, note = reconcile_rozetkapay(t, rzp_ops, book_rows)
        elif cls == "bank_commission":
            status, note = reconcile_commission(t, day_totals[t["date"]], book_rows)
        elif cls in ("own_transfer", "deposit"):
            status, note = "not_pnl", "не дохід і не витрата — у книгу не вноситься"
        else:
            found = _already_in_book(t, index)
            status, note = ("in_book" if found else "not_in_book"), "звірка за сумою ±1 день"
        t["status"], t["note"], t["in_book"] = status, note, status == "in_book"
        t["label"] = label(t)


_CLASS_UA = {
    "liqpay": "LiqPay", "rozetkapay": "RozetkaPay", "bank_commission": "комісія банку",
    "own_transfer": "власні кошти", "deposit": "депозит/застава", "other": "інше",
}
# Порядок розділів звіту: спершу те, що вимагає дії бухгалтера.
_SECTIONS = {
    "not_in_book": "❗ Справді не в книзі",
    "missing_acquiring": "💳 Внесено, але бракує еквайрингу",
    "unverified": "⚠️ Не звірено автоматично (перевір вручну)",
    "not_pnl": "↔️ Не P&L (власні перекази, депозит Toysi, застава Rozetka)",
    "in_book": "✅ У книзі",
}


def render_report(txns: list, today: str) -> str:
    lines = [
        f"# ПриватБанк — звірка виписки з книгою, {today}",
        "",
        "Звірка за класом операції (див. докстрінг privat_statement_kandydaty.py): LiqPay — за SOID",
        "і брутто−нетто; RozetkaPay — з реєстрами RozetkaPay за датами операцій; комісії банку —",
        "за базовою сумою платежу або сумою комісій за день; решта — за сумою ±1 день.",
        "Повне призначення платежу — у json поруч; тут лише мітка без ПІБ фізосіб.",
    ]
    for status, title in _SECTIONS.items():
        group = [t for t in txns if t["status"] == status]
        lines += ["", f"## {title} — {len(group)}", ""]
        if not group:
            lines.append("_немає_")
            continue
        lines += ["| Дата | Сума | Документ | Клас | Що | Пояснення |", "|---|---|---|---|---|---|"]
        for t in group:
            lines.append(f"| {t['date']} | {t['amount']:+.2f} | {t['ref'] or '—'} | {_CLASS_UA[t['cls']]} "
                         f"| {t['label']} | {t['note']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    # Детектор тиші (аудит Д3, 2026-09-18): ПриватБанк мовчав з 11.08 і про це не було жодного
    # сигналу — той самий клас, що RozetkaPay мав до PR #566. Лист приходить ЩОДНЯ (підтверджено
    # живо 2026-09-19), тому поріг вужчий за RozetkaPay.
    source_freshness.check_and_record(
        "ПриватБанк", str(DOCS_DIR / "*" / "ПриватБанк" / "*.pdf"), max_stale_days=2)
    source_freshness.write_report()

    files = _all_statements()
    if not files:
        print("[PrivatStmt] жодної виписки в документи_КОДВ/*/ПриватБанк/*.pdf ще немає.")
        return

    all_txns = []
    any_file_failed = False
    for path in files:
        try:
            table = extract_table(path)
        except Exception as e:  # noqa: BLE001
            print(f"[PrivatStmt] {path}: не вдалось прочитати ({e})", file=sys.stderr)
            _notify(f"🚨 privat_statement_kandydaty: {os.path.basename(path)} не читається: {e}")
            any_file_failed = True
            continue
        for t in parse_transactions(table):
            t["file"] = os.path.basename(path)
            all_txns.append(t)

    # Дедуп intra-run (та сама операція теоретично може повторитись у двох PDF — напр. якщо
    # виписку перезапросили за той самий день): за (ref, дата, сума). ДО класифікації — інакше
    # дубль комісії подвоїв би денну суму комісій у reconcile_commission.
    seen_this_run = set()
    txns = []
    for t in all_txns:
        key = (t["ref"], t["date"], t["amount"])
        if key in seen_this_run:
            continue
        seen_this_run.add(key)
        txns.append(t)

    book_rows = _load_book_rows()
    index = _book_money_index()
    rzp_ops = _load_rozetkapay_ops() if any(classify(t) == "rozetkapay" for t in txns) else []
    reconcile_all(txns, book_rows, index, rzp_ops)

    # У реєстр кандидатів — усе, що НЕ доведено внесеним (і не «не P&L»). Ключ НЕ змінено
    # ({ref}_{date}_{amount}) — тож 32 хибних «open» з попередніх прогонів закриються самі
    # (resolve=True), щойно їх тут немає.
    unresolved = [
        {
            "key": f"{t['ref']}_{t['date']}_{t['amount']}",
            "summary": f"{t['date']} {t['amount']:+.2f}₴ [{_CLASS_UA[t['cls']]}] {t['label'][:70]} — {t['note'][:90]}",
            "sum": t["amount"],
            "date": t["date"],
        }
        for t in txns if t["status"] in ("not_in_book", "missing_acquiring", "unverified")
    ]
    # resolve=not any_file_failed: якщо бодай один PDF не прочитався, txns НЕ гарантовано повний
    # (той самий принцип, що toysi_returns_kandydaty.py) — не закриваємо кандидатів наосліп.
    sync_result = kandydaty_registry.sync_open_candidates(
        "privat_statement", unresolved, resolve=not any_file_failed)
    if sync_result["newly_opened"] or sync_result["resolved"]:
        print(f"[PrivatStmt] Реєстр: +{len(sync_result['newly_opened'])} нових, "
              f"-{len(sync_result['resolved'])} закритих, {len(sync_result['still_open'])} досі відкриті.")
    kandydaty_registry.write_open_report()

    today = datetime.now().strftime("%Y-%m-%d")
    month_dir = DOCS_DIR / datetime.now().strftime("%Y-%m") / "ПриватБанк"
    month_dir.mkdir(parents=True, exist_ok=True)
    (month_dir / f"{today}_privat_kandydaty.json").write_text(
        json.dumps(txns, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    (month_dir / f"{today}_privat_kandydaty.md").write_text(render_report(txns, today), encoding="utf-8")

    counts = {s: sum(1 for t in txns if t["status"] == s) for s in _SECTIONS}
    print(f"[PrivatStmt] {len(txns)} операцій: " + ", ".join(f"{_SECTIONS[s]} {n}" for s, n in counts.items()))
    if any_file_failed:
        print("[PrivatStmt] ⚠️ бодай один PDF не прочитався — закриття кандидатів пропущено цим прогоном.")


if __name__ == "__main__":
    main()
