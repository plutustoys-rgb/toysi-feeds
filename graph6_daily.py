"""graph6_daily.py — собівартість замовлень Toysi (графа 6 КОДВ) → кандидати.

Задача власника 2026-08-29: «закупівлі Toysi треба зробити». Полагоджує биту Windows-задачу
PlutusToys-Graph6Daily (скрипт graph6_daily.ps1 був відсутній). Методологія — КОДВ_норми_довідник
§«Графа 6» (рядки 251-357): графа 6 = собівартість реалізованих товарів = фактична сума списання
з депозиту Toysi за замовлення. Джерело — «Історія замовлень» кабінету Toysi (звірено 18/18).

ЧОМУ КАБІНЕТ, А НЕ API/orders.db:
  - Toysi API (`order_positions.sum_with_discount`) дає точну суму, але ЛИШЕ по конкретному
    toysi_order_id — методу «список замовлень» в API немає; а локальна orders.db застаріла.
  - Кабінет «Історія замовлень» дає ЖИВИЙ список усіх замовлень із сумою (собівартість) — те саме
    число, що списується з депозиту (§довідника; «Збірка» в цю суму НЕ входить — окремий облік).
  Читаємо через ту саму локальну сесію, що toysi_cabinet_scraper.py (storageState, без нових кредів).

ВИХІД: кандидати у документи_КОДВ/YYYY-MM/Toysi/ (як інші леджери). Книгу НЕ пише — графу 6 пише
лише роль «агент-бухгалтер». Хінт «збігів суми в графі 6 книги» (READ-ONLY) — щоб не задвоїти.
Курсор за toysi-id; перший запуск = базова лінія (історія вже в книзі, не дампимо).
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
COWORK_DIR = Path(os.environ.get(
    "PLUTUS_COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
KODV_XLSX = COWORK_DIR / "KODV_PlutusToys_2026.xlsx"
STATE_FILE = Path(os.environ.get(
    "TOYSI_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "toysi_cabinet_state.json")))
CURSOR_FILE = BASE_DIR / ".local_secrets" / "graph6_cursor.json"
ORDER_HISTORY_URL = "https://toysi.ua/order_history/"
NAV_TIMEOUT_MS = 60000
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

# «toysi-100450057 від 29.08.2026 19:40  Нове   Сума  177.01  Курс  44.90»
_ORDER_RE = re.compile(
    r"toysi-(\d+)\s+від\s+(\d{2}\.\d{2}\.\d{4})\s+[\d:]+\s+(.+?)\s+Сума\s+([\d.,]+)",
    re.UNICODE)


def _log(msg: str) -> None:
    print(f"[Graph6] {msg}")


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        sys.path.insert(0, str(BASE_DIR))
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[Graph6] Telegram не надіслано: {e}", file=sys.stderr)


def _load_cursor() -> dict:
    try:
        return json.loads(CURSOR_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cursor(processed_ids: list) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(
        json.dumps({"processed_ids": sorted(set(processed_ids)),
                    "updated_at": datetime.now().isoformat(timespec="seconds")},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def _to_float(v) -> float:
    try:
        return float(str(v).replace(",", ".").replace(" ", ""))
    except (ValueError, TypeError):
        return 0.0


def fetch_order_history() -> list:
    """Список замовлень із кабінету Toysi: [{toysi_id, date, status, cost}]. READ-ONLY."""
    if not STATE_FILE.exists():
        raise RuntimeError(f"нема сесії Toysi ({STATE_FILE.name}) — `python toysi_cabinet_scraper.py --login`")
    orders = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            page.goto(ORDER_HISTORY_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            if "order_history" not in page.url.lower() or "login" in page.url.lower():
                raise RuntimeError(f"сесію не прийнято — редірект на {page.url} (треба --login)")
            text = page.inner_text("body")
        finally:
            browser.close()
    for m in _ORDER_RE.finditer(text):
        toysi_id, date, status, cost = m.group(1), m.group(2), m.group(3).strip(), _to_float(m.group(4))
        orders.append({"toysi_id": toysi_id, "date": date, "status": status, "cost": cost})
    return orders


def _book_cost_index() -> dict:
    """READ-ONLY: {собівартість(грн,2) → к-сть рядків графи 6 книги}. Хінт (0 = ще не в книзі)."""
    index: dict = {}
    if not KODV_XLSX.exists():
        return index
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(KODV_XLSX), data_only=True, read_only=True)
        ws = wb["КОДВ"]
        for row in ws.iter_rows(min_row=7):
            f = row[5].value if len(row) > 5 else None            # графа 6 = колонка F (індекс 5)
            if isinstance(f, (int, float)) and f:
                key = round(float(f), 2)
                index[key] = index.get(key, 0) + 1
    except Exception as e:  # noqa: BLE001
        print(f"[Graph6] книжковий хінт не побудовано (не критично): {e}", file=sys.stderr)
    return index


# Статуси, за яких собівартість ЩЕ НЕ реалізована (графа 6 визнається лише в день реалізації —
# методологія §«Графа 6» + SKILL бухгалтера: тригер «Відвантажене», «не «Упаковане» — те ще зарано»).
# «Нове»/«Упаковане»/«В обробці» — рано; «Скасоване» — витрати нема. Такі НЕ кандидати і НЕ базимо
# (спливуть, щойно перейдуть у «Відвантажене»).
_NOT_REALIZED = {"нове", "новий", "упаковане", "упаковано", "в обробці", "в обробцi",
                 "скасоване", "скасовано", "відхилене", "відхилено"}


def _is_realized(o: dict) -> bool:
    return o["cost"] > 0 and o["status"].strip().lower() not in _NOT_REALIZED


def collect() -> tuple:
    cursor = _load_cursor()
    processed = set(cursor.get("processed_ids", []))
    is_first_run = "processed_ids" not in cursor

    orders = fetch_order_history()
    if not orders:
        raise RuntimeError("з «Історії замовлень» не розпарсено жодного замовлення — змінилась розмітка?")
    realized = [o for o in orders if _is_realized(o)]
    realized_ids = [o["toysi_id"] for o in realized]

    if is_first_run:
        # Базова лінія: як опрацьовані беремо лише вже РЕАЛІЗОВАНІ (їхня собівартість уже мала б
        # бути в книзі). «Нове» не базимо — спливе кандидатом, коли стане «Відвантажене».
        _log(f"Перший запуск — базова лінія: {len(realized_ids)} реалізованих замовлень вважаю "
             f"опрацьованими, кандидатів не шукаю (нереалізовані спливуть пізніше).")
        return [], realized_ids

    book_idx = _book_cost_index()
    new = [o for o in realized if o["toysi_id"] not in processed]
    for o in new:
        o["book_same_cost_rows"] = book_idx.get(round(o["cost"], 2), 0)
        processed.add(o["toysi_id"])
    return new, sorted(processed)


def _write_report(candidates: list) -> Path:
    today = datetime.now()
    month_dir = COWORK_DIR / "документи_КОДВ" / today.strftime("%Y-%m") / "Toysi"
    month_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{today.strftime('%Y-%m-%d')}_toysi_zakupivli_graph6_kandydaty"

    (month_dir / f"{stem}.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# Toysi — закупівлі (собівартість, графа 6), автоматично, {today.strftime('%Y-%m-%d %H:%M')}",
             "",
             "Джерело: кабінет Toysi «Історія замовлень» (собівартість = сума списання з депозиту,",
             "§довідника «Графа 6», звірено 18/18; «Збірка» в цю суму НЕ входить — окремо). КАНДИДАТИ",
             "графи 6 — крос-звірка з книгою READ-ONLY, книгу НЕ змінено. «Збігів суми в графі 6»:",
             "скільки рядків книги вже мають таку собівартість (0 = ще не в книзі; ≥1 = перевірити).",
             "",
             "| Toysi-замовлення | Дата | Статус | Собівартість, грн | Збігів у графі 6 |",
             "|---|---|---|---|---|"]
    for c in candidates:
        lines.append(f"| toysi-{c['toysi_id']} | {c['date']} | {c['status']} | {c['cost']} | "
                     f"{c['book_same_cost_rows']} |")
    (month_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return month_dir / f"{stem}.md"


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    try:
        candidates, processed_ids = collect()
    except (RuntimeError, OSError) as e:
        _notify(f"🚨 graph6_daily: помилка збору закупівель Toysi: {e}")
        _log(f"помилка: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 — несподіване не має падати тихо
        _notify(f"🚨 graph6_daily: несподівана помилка: {e}")
        _log(f"несподівана помилка: {e}")
        sys.exit(1)

    if not candidates:
        if not dry_run:
            _save_cursor(processed_ids)
        _log("Нових замовлень Toysi немає (або перший запуск — базова лінія).")
        return

    if dry_run:
        _log(f"[dry-run] БУЛО Б {len(candidates)} кандидатів графи 6; курсор не рухаю, файли не пишу.")
        for c in candidates:
            _log(f"  [dry-run] toysi-{c['toysi_id']}: {c['cost']} грн {c['date']} {c['status']} "
                 f"(збігів у графі6: {c['book_same_cost_rows']})")
        return

    path = _write_report(candidates)
    _save_cursor(processed_ids)
    _log(f"ГОТОВО: {len(candidates)} кандидатів графи 6 → {path}")


if __name__ == "__main__":
    main()
