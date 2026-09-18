"""
vchasno_akty_kandydaty.py — кандидати документів Вчасно.ЕДО, яких НЕ згадано в книзі.

НАВІЩО (незалежний аудитор бухгалтерії, КОДВ_журнал.md «ДОПОВНЕННЯ 6», 2026-09-17/18):
звірка документів Вчасно (edo.vchasno.ua — акти маркетплейсів/еквайрингу/логістики) з
книгою робилась ВРУЧНУ, сесія за сесією, і саме тому дві реальні знахідки (подвійний облік
734,36₴ на рядку 80, пропущений акт роялті 178,97₴) провисіли місяць непоміченими. Доступ до
Вчасно — через живу сесію власника (КЕП/особистий підпис, не програмний логін типу EVA/Prom/
Toysi — цей клас автоматизації тут структурно недоступний). АЛЕ первинку вже завантажено
ЛОКАЛЬНО (жива сесія 2026-09-17, `документи_КОДВ/{міс}/{Prom,RozetkaPay,NovaPay,ALLO,
Rozetka,EVA_akty,НоваПошта}/*.pdf`) — і назва файлу вже містить стабільний номер документа
(Вчасно/маркетплейс). Тому звірка "номер акта ↔ згадка в книзі" автоматизується без жодного
живого доступу — читає лише локальні файли.

ЩО РОБИТЬ: сканує ВСІ файли `*_akt_*` у документи_КОДВ/*/*/, витягує номер документа з назви
файлу (регулярка, не PDF-парсинг), звіряє текстовим пошуком (номер документа у графі 5 АБО
графі 12 книги — та сама логіка, що toysi_returns_kandydaty.py, той самий фікс аудиту
2026-09-18: наратив буває в різних графах). Документи, чий номер НЕ знайдено — кандидати.

ЧОМУ НЕ ВИРІШУЄ, ЧИ ЦЕ РЕАЛЬНА ПРОГАЛИНА: деякі класи актів (RozetkaPay, EVA) — це агрегати
вже порядково врахованих комісій, СВІДОМО не вносяться окремим рядком (аудит, «Акти
RozetkaPay — правильно НЕ внесені»). Скрипт цього не знає й не вгадує — лише каже «номер X
не знайдено текстом у книзі», рішення «це прогалина чи очікуване виключення» — за бухгалтером,
так само, як для решти kandydaty-скриптів.

ЗАПУСК: python vchasno_akty_kandydaty.py
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import kandydaty_registry

BASE_DIR = Path(__file__).parent
COWORK_DIR = Path(os.environ.get(
    "PLUTUS_COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
KODV_XLSX = COWORK_DIR / "KODV_PlutusToys_2026.xlsx"
DOCS_DIR = COWORK_DIR / "документи_КОДВ"

# Слова, що можуть стояти МІЖ "_akt_" і реальним номером документа (описові, не частина
# ідентифікатора) — звірено живо на реальних назвах файлів 2026-09-18.
_DESCRIPTOR_WORDS = {"dostup", "royalti", "zvirky", "kompensatsii", "ne", "nova", "vytrata", "komisia"}
# Номер документа: суміш літер+цифр (UA-00011321442, TA00482473, BO0000489750, AL000389851,
# RU000154242, NP-018826846) АБО чисто цифровий довший за 5 знаків (000573566, 1081695) —
# відсікає короткі суми (817.47) і описові слова.
_ID_RE = re.compile(r"^[A-Z]{1,3}-?\d{5,}$|^\d{6,}$")
_AMOUNT_RE = re.compile(r"^\d+\.\d{2}$")


def _log(msg: str) -> None:
    print(f"[VchasnoAkty] {msg}")


def _notify(msg: str) -> None:
    if os.environ.get("AUDIT_NO_TELEGRAM") == "1":
        return
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[VchasnoAkty] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def parse_akt_filename(filename: str) -> dict | None:
    """«2026-08-31_rozetka_akt_royalti_TA00482473_178.97.pdf» →
    {"vendor": "rozetka", "doc_id": "TA00482473", "amount": 178.97, "date": "2026-08-31"}.
    Повертає None, якщо номер документа не розпізнано (файл лишається "unparsed", не
    втрачається мовчки — див. main()).

    ⚠️ «zvirky» (акти звірки НП) — НЕ обробляються тут, ЗАВЖДИ None (аудит PR #571, живо
    знайдено): їхнє ім'я містить лише номер ДОГОВОРУ (напр. 1081695 у
    `2026-08-11_np_akt_zvirky_1081695_12.05-11.08.xlsx`), не унікальний номер документа —
    той самий номер повторюється у КОЖНОМУ акті звірки НП, і сам договір згадується в книзі
    в контексті ІНШИХ, непов'язаних рядків (окремі рахунки НП). Якби взяти цей номер за
    doc_id: (а) два акти звірки за різні періоди дедуп злив би в один кандидат (обидва
    дають однаковий doc_id, різні лише дати в хвості імені); (б) exact-match видав би
    ХИБНЕ «✅ у книзі» для ОБОХ, бо номер договору й так зустрічається в наративі не
    пов'язаних з цим актом рядків. Це money-critical напрямок помилки (хибний ✅ ховає
    реальну прогалину) — тому акти звірки свідомо йдуть в "unparsed" для ручного розбору,
    а не в автоматичний, але хибний, збіг."""
    stem = filename
    for ext in (".xml.json", ".pdf", ".xlsx", ".json", ".xml"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break

    m = re.match(r"^(\d{4}-\d{2}-\d{2})_([a-z]+)_akt_(.+)$", stem)
    if not m:
        return None
    date_str, vendor, tail = m.groups()

    tokens = tail.split("_")
    if "zvirky" in tokens:
        return None

    doc_id = None
    amount = None
    for tok in tokens:
        if tok in _DESCRIPTOR_WORDS:
            continue
        if doc_id is None and _ID_RE.match(tok):
            doc_id = tok
            continue
        if amount is None and _AMOUNT_RE.match(tok):
            amount = float(tok)
    if doc_id is None:
        return None
    return {"vendor": vendor, "doc_id": doc_id, "amount": amount, "date": date_str}


def find_akt_files() -> list:
    """Усі файли *_akt_* у документи_КОДВ/*/*/ (будь-який місяць, будь-яка платформа-тека)."""
    import glob
    pattern = str(DOCS_DIR / "*" / "*" / "*_akt_*")
    return sorted(f for f in glob.glob(pattern) if not os.path.basename(f).startswith("~$"))


def _book_narrative_text() -> str:
    """READ-ONLY: графа 5 ТА графа 12 книги (той самий підхід, що toysi_returns_kandydaty.py,
    аудит 2026-09-18 — наратив буває в різних графах, не лише в графі 5)."""
    if not KODV_XLSX.exists():
        return ""
    import openpyxl
    wb = openpyxl.load_workbook(str(KODV_XLSX), data_only=True, read_only=True)
    try:
        ws = wb["КОДВ"]
        parts = []
        for row in ws.iter_rows(min_row=7):
            if len(row) < 5:
                continue
            v = row[4].value
            if v:
                parts.append(str(v))
            if len(row) > 11:
                v12 = row[11].value
                if v12:
                    parts.append(str(v12))
        return " \n".join(parts)
    finally:
        wb.close()


def _already_in_book(doc_id: str, book_text: str) -> bool:
    """Точний збіг doc_id У ТЕКСТІ книги, а якщо ні — числове ядро (кінцеві цифри doc_id,
    без провідних нулів) як запасний варіант.

    ⚠️ ЖИВО ПЕРЕВІРЕНО 2026-09-18 (перший прогін дав 11/14 "НЕ знайдено", хоча аудитор уже
    підтвердив вручну частину з них): бухгалтер НЕ переносить номер документа дослівно з
    назви файлу.
      • Нова Пошта: файл `NP-018826846` (латиниця, міжнародне позначення), книга пише
        «№**НП**-018826846» (кирилиця, українська абревіатура) — рядок 35, звірено живо.
      • ALLO: файл `AL000389851` (префікс+провідні нулі), книга пише просто «№389851» —
        рядок 81, звірено живо.
    Точний збіг ловить Rozetka/Prom (бухгалтер копіює номер дослівно), а НП/ALLO — ні.
    Числове ядро (мін. 5 цифр, щоб не ловити випадкові короткі числа) закриває обидва
    випадки одним запасним варіантом, не вимагає окремого правила на кожного постачальника."""
    if doc_id in book_text:
        return True
    m = re.search(r"\d+$", doc_id)
    if not m:
        return False
    core = m.group(0).lstrip("0")
    if len(core) < 5:
        return False
    return core in book_text


def main() -> None:
    files = find_akt_files()
    if not files:
        _log("Жодного файлу *_akt_* не знайдено в документи_КОДВ/*/*/.")
        return

    parsed, unparsed = [], []
    seen_ids = {}  # doc_id -> перший запис (дедуп: 2 файли того самого акта, різні анотації)
    for f in files:
        info = parse_akt_filename(os.path.basename(f))
        if info is None:
            unparsed.append(f)
            continue
        if info["doc_id"] in seen_ids:
            continue  # той самий акт, інша анотація файлу — не дублюємо кандидата
        seen_ids[info["doc_id"]] = info
        info["file"] = f
        parsed.append(info)

    if unparsed:
        _log(f"⚠️ {len(unparsed)} файл(ів) *_akt_* без розпізнаного номера документа "
             f"(перевір вручну): {', '.join(os.path.basename(f) for f in unparsed)}")

    book_text = _book_narrative_text()
    for info in parsed:
        info["in_book"] = _already_in_book(info["doc_id"], book_text)

    unresolved = [
        {
            "key": r["doc_id"],
            "summary": f"{r['vendor']} акт {r['doc_id']}" + (f" ({r['amount']} грн)" if r["amount"] else ""),
            "sum": r.get("amount"),
            "date": r.get("date"),
        }
        for r in parsed if not r["in_book"]
    ]
    sync_result = kandydaty_registry.sync_open_candidates("vchasno_akty", unresolved)
    if sync_result["newly_opened"] or sync_result["resolved"]:
        _log(f"Реєстр відкритих кандидатів: +{len(sync_result['newly_opened'])} нових, "
             f"-{len(sync_result['resolved'])} закритих, {len(sync_result['still_open'])} досі відкриті.")
    kandydaty_registry.write_open_report()

    today = datetime.now().strftime("%Y-%m-%d")
    month_dir = DOCS_DIR / datetime.now().strftime("%Y-%m") / "Vchasno"
    month_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{today}_vchasno_akty_kandydaty"
    (month_dir / f"{stem}.json").write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        f"# Вчасно — акти, знайдені локально в документи_КОДВ, {today}",
        "",
        "Джерело: файли `*_akt_*` у документи_КОДВ/*/*/ (завантажені живою сесією з",
        "edo.vchasno.ua — цей скрипт НЕ заходить у Вчасно сам, лише читає вже завантажене).",
        "«У книзі» — номер документа згадано хоч в одному рядку графи 5 АБО графи 12",
        "(текстовий пошук). ⚠️ Не кожен «НЕ в книзі» — прогалина: деякі класи актів",
        "(RozetkaPay, EVA) — агрегати вже порядково врахованих комісій, свідомо не",
        "вносяться окремо (аудит 2026-09-18) — рішення за бухгалтером.",
        "",
        "| Дата | Постачальник | Номер документа | Сума | У книзі? |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(parsed, key=lambda x: x["date"]):
        mark = "✅" if r["in_book"] else "❗ НЕ знайдено"
        lines.append(f"| {r['date']} | {r['vendor']} | {r['doc_id']} | "
                     f"{r.get('amount', '?')} | {mark} |")
    if unparsed:
        lines.append("")
        lines.append("**Не розпізнано номер (перевір вручну):** " +
                     ", ".join(os.path.basename(f) for f in unparsed))
    (month_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    n_open = len(unresolved)
    _log(f"ГОТОВО: {len(parsed)} актів розпізнано ({n_open} не знайдено в книзі) → "
         f"{month_dir / f'{stem}.md'}")
    if n_open:
        _notify(f"📄 Вчасно: {n_open} акт(ів) не знайдено текстом у книзі (з {len(parsed)} "
                f"розпізнаних). Не всі — прогалини (RozetkaPay/EVA — очікувано). "
                f"Див. {stem}.md")


if __name__ == "__main__":
    main()
