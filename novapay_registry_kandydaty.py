"""novapay_registry_kandydaty.py — кандидати на ВНЕСЕННЯ У КНИГУ невнесених COD-платежів NovaPay.

НАВІЩО (запит бухгалтера 2026-09-12, 3-тя дірка): COD-платіж EVA №8-080364562 просидів в
архіві непоміченим кілька днів. Симетрія з іншими *_kandydaty порушена: у бухгалтера нема
candidate-файлу по NovaPay, який вона щодня переглядає (як RozetkaPay/EVA/Checkbox).

ЧОМУ ОКРЕМИЙ ФАЙЛ (а не novapay_statement.py — дубль-варта перевірена):
`novapay_statement.py` (VPS-юніт, кожні 30 хв) ВЖЕ парсить NovaPay-реєстри й звіряє — але з
`orders_db` (чи є замовлення в НАШОМУ пайплайні), пише лінійний `kodv_ledger.jsonl`. Це ІНША
звірка й ІНШИЙ вихід, ніж потрібно бухгалтеру: (1) книга КОДВ (`KODV_PlutusToys_2026.xlsx`) —
чи ЗАПИСАНО платіж у бухоблік, не чи є він у orders_db; (2) VPS не має доступу до локальної
книги. Тому book-check — десктопний, а парсер РЕЮЗАЄМО з novapay_statement (без дублювання).

ЩО: читає заархівовані реєстри `документи_КОДВ/*/NovaPay/*.XLSX` (їх кладе
`novapay_registry_archiver.ps1`), парсить `novapay_statement.parse_registry_xlsx`
(з обхідом SharedStrings-casing), крос-звіряє з книгою READ-ONLY за № замовлення АБО ТТН у
графі 5. Платіж, якого В КНИЗІ НЕМА → кандидат. Пише кандидатів у документи_КОДВ/*/NovaPay/,
книгу НЕ чіпає (графу заповнює лише роль бухгалтера). Курсор — за ТТН (унікальний на посилку),
обробляє ВСІ реєстри (не лише найновіший), щоб не загубити старіші.

ЗАПУСК: python novapay_registry_kandydaty.py [--file <xlsx>]
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from novapay_statement import parse_registry_xlsx  # реюз парсера (обхід SharedStrings-casing)

BASE_DIR = Path(__file__).parent
COWORK_DIR = Path(os.environ.get("PLUTUS_COWORK_DIR",
                                 r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
KODV_XLSX = COWORK_DIR / "KODV_PlutusToys_2026.xlsx"
DOCS_DIR = COWORK_DIR / "документи_КОДВ"
CURSOR_FILE = BASE_DIR / ".local_secrets" / "novapay_registry_cursor.json"
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[NovaPayReg] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _load_cursor() -> set:
    try:
        return set(json.loads(CURSOR_FILE.read_text(encoding="utf-8")).get("seen_ttn", []))
    except (ValueError, OSError):
        return set()


def _save_cursor(seen: set) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps({"seen_ttn": sorted(seen),
                                       "updated_at": datetime.now().isoformat(timespec="seconds")},
                                      ensure_ascii=False, indent=2), encoding="utf-8")


def _all_registries() -> list:
    """Усі реєстри NovaPay (курсор не дасть повторів). Обробляємо ВСІ, а не лише найновіший —
    щоб не загубити старіші, якщо за день прийшло кілька файлів."""
    files = []
    for ext in ("*.XLSX", "*.xlsx"):
        files += glob.glob(str(DOCS_DIR / "*" / "NovaPay" / ext))
    files = [f for f in files if not os.path.basename(f).startswith("~$")]
    return sorted(set(files), key=os.path.getmtime)


def _bare_order(internal_order_id: str) -> tuple:
    """`eva_8-080968012` → ('eva', '8-080968012'); `rozetka_90548630` → ('rozetka', '90548630').
    Без префікса-платформи — повертає ('?', сам рядок). Голий № потрібен для пошуку в книзі
    (книга тримає і 'EVA.ua №8-...', і 'eva_8-...', і 'Rozetka №90...')."""
    s = (internal_order_id or "").strip()
    if "_" in s:
        plat, order = s.split("_", 1)
        return plat.strip().lower(), order.strip()
    return "?", s


def _book_has(order_id: str, ttn: str) -> dict:
    """READ-ONLY: чи є замовлення в книзі (графа 5 / колонка E) за голим № замовлення АБО ТТН.
    {row, e_text} якщо знайдено, інакше {}. Книгу НЕ пише (правило власника)."""
    if not KODV_XLSX.exists() or (not order_id and not ttn):
        return {}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(KODV_XLSX), data_only=True, read_only=True)
        ws = wb["КОДВ"]
        for row in ws.iter_rows(min_row=7):
            e = row[4].value if len(row) > 4 else None
            if not e:
                continue
            es = str(e)
            if (order_id and order_id in es) or (ttn and ttn in es):
                return {"row": row[4].row, "e_text": es}
    except Exception as e:  # noqa: BLE001
        print(f"[NovaPayReg] крос-звірка з книгою не вдалась (не критично): {e}", file=sys.stderr)
    return {}


def _rows_from(paths: list) -> list:
    """Усі рядки-платежі з реєстрів через реюзаний parse_registry_xlsx (bytes)."""
    out = []
    for p in paths:
        try:
            parsed = parse_registry_xlsx(Path(p).read_bytes())
        except Exception as e:  # noqa: BLE001
            print(f"[NovaPayReg] {os.path.basename(p)}: не прочитано ({e}) — пропуск.", file=sys.stderr)
            continue
        for r in parsed["rows"]:
            r["_src"] = os.path.basename(p)
            out.append(r)
    return out


def collect(rows: list) -> tuple:
    """(candidates, seen_ttn_this_batch). Кандидати — по НОВИХ (не в курсорі за ТТН) платежах,
    яких НЕМА в книзі. Перший запуск (порожній курсор) = базова лінія без кандидатів."""
    seen = _load_cursor()
    is_first = not seen
    this_batch = {str(r.get("ttn")).strip() for r in rows if r.get("ttn")}

    if is_first:
        print(f"[NovaPayReg] Перший запуск — {len(this_batch)} платежів за базову лінію, кандидатів не шукаю.")
        return [], this_batch

    candidates = []
    seen_this_run = set()  # intra-run дедуп: той самий реєстр буває заархівований у двох теках
    for r in rows:
        ttn = str(r.get("ttn") or "").strip()
        if ttn and ttn in seen:
            continue  # вже бачили (між прогонами, курсор)
        if ttn and ttn in seen_this_run:
            continue  # уже додано цього ж прогону (дубль-реєстр) — не подвоюємо
        seen_this_run.add(ttn)
        plat, order = _bare_order(r.get("internal_order_id") or "")
        book = _book_has(order, ttn)
        if book:
            continue  # уже в книзі — не кандидат
        candidates.append({
            "order_id": order,
            "platform": plat,
            "ttn": ttn,
            "date": r.get("date"),
            "sum": r.get("amount_received"),
            "fee": r.get("commission"),
            "net": r.get("amount_net"),
            "pib": r.get("buyer_name"),
            "raw_order_ref": r.get("raw_order_ref"),
            "note": (f"COD-платіж НЕ в книзі: замовлення {order} ({plat}), ТТН {ttn}, дата {r.get('date')}, "
                     f"прийнято {r.get('amount_received')}, винагорода НП {r.get('commission')}, "
                     f"зараховано {r.get('amount_net')} ({r.get('buyer_name')}). "
                     f"Внеси у книгу графу 5/6 (дата = зарахування NovaPay)."),
        })
    return candidates, this_batch


def _write_report(candidates: list, srcs: list) -> Path:
    today = datetime.now()
    month_dir = DOCS_DIR / today.strftime("%Y-%m") / "NovaPay"
    month_dir.mkdir(parents=True, exist_ok=True)
    stamp = today.strftime("%Y-%m-%d")
    md = month_dir / f"{stamp}_novapay_kandydaty.md"
    js = month_dir / f"{stamp}_novapay_kandydaty.json"
    lines = [f"# NovaPay реєстри — COD-платежі НЕ в книзі, {today.strftime('%Y-%m-%d %H:%M')}", "",
             f"Джерел оброблено: {len(srcs)}",
             f"**Кандидатів: {len(candidates)}** (книгу НЕ чіпаю — це роль бухгалтера).", ""]
    for c in candidates:
        lines.append(f"## 📦 COD не в книзі — замовлення {c['order_id']} ({c['platform']})")
        lines.append(f"- {c['note']}")
        lines.append(f"- ТТН {c['ttn']}, призначення {c['raw_order_ref']!r}")
        lines.append("")
    md.write_text("\n".join(lines), encoding="utf-8")
    js.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    return md


def main() -> int:
    ap = argparse.ArgumentParser(description="Кандидати на внесення невнесених COD-платежів NovaPay.")
    ap.add_argument("--file", help="Конкретний xlsx (для тесту); без нього — УСІ в документи_КОДВ/*/NovaPay/.")
    args = ap.parse_args()

    srcs = [args.file] if args.file else _all_registries()
    srcs = [s for s in srcs if Path(s).exists()]
    if not srcs:
        print("[NovaPayReg] Реєстрів не знайдено (ні --file, ні в документи_КОДВ/*/NovaPay/).", file=sys.stderr)
        return 1

    rows = _rows_from(srcs)
    print(f"[NovaPayReg] Реєстрів {len(srcs)}, рядків-платежів {len(rows)}.")

    candidates, this_batch = collect(rows)
    if candidates:
        report = _write_report(candidates, srcs)
        print(f"[NovaPayReg] Кандидатів {len(candidates)} → {report}")
        _notify(f"📦 NovaPay: {len(candidates)} COD-платіж(ів) БЕЗ запису в книзі — перевір графу 5/6. "
                f"Див. {report.name}")
    else:
        print("[NovaPayReg] Нових невнесених платежів немає.")

    seen = _load_cursor() | this_batch
    _save_cursor(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
