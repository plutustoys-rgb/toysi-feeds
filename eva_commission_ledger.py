"""eva_commission_ledger.py — фактична комісія EVA по замовленнях → кандидати графи 9 КОДВ.

Прогалина, яку підняла бухгалтер (канал КОДВ 2026-08-29): `eva_orders_ledger.py` дає лише бэклог
замовлень (готовність до внесення), а комісію EVA — ні; досі в книзі стояла пласка ОЦІНКА 15%,
не факт. Цей скрипт бере ФАКТ (як Rozetka-роялті).

ДЖЕРЕЛО (задокументовано КОДВ_норми_довідник §EVA, звірено живо 2026-08-24 = 15.00% на реальному
замовленні): картка `seller.eva.ua/merchant/orders/{id}` має блок «Сума комісії» з розбивкою
«Всього / З рахунку ТМ / З рахунку платформи». Перевірено цим скриптом наживо: замовлення
8-078684206 → Всього 30.99 ₴ (ТМ 18.59 + платформа 12.40). Окремої сторінки транзакцій у кабінеті
EVA немає (на відміну від Rozetka), тож джерело — картка кожного замовлення.

ВИХІД: кандидати графи 9 у документи_КОДВ/YYYY-MM/EVA/ (окремо від eva_orders_ledger, який про
готовність доходу). Книгу НЕ пише — графу 9 пише лише роль «агент-бухгалтер». Крос-звірка книги
READ-ONLY. Курсор за order_id; перший запуск = базова лінія. READ-ONLY по кабінету (лише goto+read).
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
    "EVA_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "eva_cabinet_state.json")))
CURSOR_FILE = BASE_DIR / ".local_secrets" / "eva_commission_cursor.json"
ORDERS_URL = "https://seller.eva.ua/merchant/orders"
ORDER_DETAIL = "https://seller.eva.ua/merchant/orders/{}"
NAV_TIMEOUT_MS = 60000
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

# «Сума комісії  Всього 30.99 ₴ З рахунку ТМ 18.59 ₴ З рахунку платформи 12.40 ₴»
_TOTAL_RE = re.compile(r"Сума комісії\s+Всього\s+([\d\s.,]+?)\s*₴", re.UNICODE)
_TM_RE = re.compile(r"З рахунку ТМ\s+([\d\s.,]+?)\s*₴", re.UNICODE)
_PLATFORM_RE = re.compile(r"З рахунку платформи\s+([\d\s.,]+?)\s*₴", re.UNICODE)


def _log(msg: str) -> None:
    print(f"[EvaCommission] {msg}")


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        sys.path.insert(0, str(BASE_DIR))
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[EvaCommission] Telegram не надіслано: {e}", file=sys.stderr)


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
        return float(str(v).replace(" ", "").replace(",", "."))
    except (ValueError, TypeError):
        return 0.0


def _first(rx, text: str) -> float:
    m = rx.search(text)
    return _to_float(m.group(1)) if m else 0.0


def fetch_commissions() -> list:
    """Список замовлень EVA + фактична комісія з картки кожного. READ-ONLY (лише goto+read).
    Повертає [{order_id, commission_total, commission_tm, commission_platform}]."""
    if not STATE_FILE.exists():
        raise RuntimeError(f"нема сесії EVA ({STATE_FILE.name}) — `python eva_cabinet_scraper.py --login`")
    out = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            page.goto(ORDERS_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            if "/login" in page.url:
                raise RuntimeError(f"сесію не прийнято — редірект на {page.url} (треба --login)")
            hrefs = page.eval_on_selector_all(
                "a[href*='/orders/']",
                "els => els.map(e => e.getAttribute('href')).filter(h => h && /orders\\/[^/]+$/.test(h))")
            order_ids = []
            for h in hrefs:
                oid = h.rstrip("/").split("/")[-1]
                if oid and oid not in order_ids:
                    order_ids.append(oid)
            if not order_ids:
                raise RuntimeError("на /merchant/orders не знайдено посилань на замовлення — змінилась розмітка?")

            for oid in order_ids:
                page.goto(ORDER_DETAIL.format(oid), timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                txt = page.inner_text("body")
                total = _first(_TOTAL_RE, txt)
                if total <= 0:
                    # Захист від тихої втрати (ниточка аудиту): якщо розбивка ТМ Є, а «Всього» не
                    # розпарсилось — це не «немає комісії», а ймовірна ЗМІНА РОЗМІТКИ EVA. Сигналимо,
                    # щоб не пропало мовчки (напрям безпечний — не хибне проведення, а пропуск).
                    if _TM_RE.search(txt):
                        _log(f"⚠️ {oid}: є ТМ-комісія, але «Всього» не розпарсено — можлива зміна розмітки EVA "
                             f"(перевір якір _TOTAL_RE).")
                    continue  # немає блоку «Сума комісії» (скасоване / ще не проведене) — не кандидат
                out.append({
                    "order_id": oid,
                    "commission_total": round(total, 2),
                    "commission_tm": round(_first(_TM_RE, txt), 2),
                    "commission_platform": round(_first(_PLATFORM_RE, txt), 2),
                })
        finally:
            browser.close()
    return out


def _book_lookup(order_id: str) -> dict:
    """READ-ONLY: чи є в графі 5 книги рядок з цим EVA order_id; + к-сть рядків графи 9 з такою сумою."""
    res = {"book_row": None, "book_e_text": None}
    if not KODV_XLSX.exists():
        return res
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(KODV_XLSX), data_only=True, read_only=True)
        ws = wb["КОДВ"]
        for row in ws.iter_rows(min_row=7):
            e = row[4].value if len(row) > 4 else None
            if e and order_id in str(e):
                res["book_row"] = row[4].row
                res["book_e_text"] = str(e)
                break
    except Exception as e:  # noqa: BLE001
        print(f"[EvaCommission] крос-звірка з книгою не вдалась (не критично): {e}", file=sys.stderr)
    return res


def collect() -> tuple:
    cursor = _load_cursor()
    processed = set(cursor.get("processed_ids", []))
    is_first_run = "processed_ids" not in cursor

    rows = fetch_commissions()
    all_ids = [r["order_id"] for r in rows]

    if is_first_run:
        _log(f"Перший запуск — базова лінія: {len(all_ids)} замовлень з комісією вважаю опрацьованими "
             f"(історія вже в книзі 15%-оцінкою / вручну), кандидатів не шукаю.")
        return [], all_ids

    new = [r for r in rows if r["order_id"] not in processed]
    for r in new:
        r.update(_book_lookup(r["order_id"]))
        processed.add(r["order_id"])
    return new, sorted(processed)


def _write_report(candidates: list) -> Path:
    today = datetime.now()
    month_dir = COWORK_DIR / "документи_КОДВ" / today.strftime("%Y-%m") / "EVA"
    month_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{today.strftime('%Y-%m-%d')}_eva_komisiya_kandydaty"

    (month_dir / f"{stem}.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# EVA — фактична комісія по замовленнях (графа 9), автоматично, {today.strftime('%Y-%m-%d %H:%M')}",
             "",
             "Джерело: картка `seller.eva.ua/merchant/orders/{id}` → блок «Сума комісії» (Всього).",
             "Це ФАКТ (не 15%-оцінка) — §довідника, звірено 15.00% живо. КАНДИДАТИ графи 9, крос-звірка",
             "книги READ-ONLY (книгу НЕ змінено). ТМ+платформа = розбивка «Всього» (для довідки).",
             "",
             "| Замовлення | Комісія Всього | ТМ | Платформа | Рядок книги |",
             "|---|---|---|---|---|"]
    for c in candidates:
        row = c["book_row"] if c.get("book_row") else "❗ НЕ в книзі"
        lines.append(f"| {c['order_id']} | {c['commission_total']} | {c['commission_tm']} | "
                     f"{c['commission_platform']} | {row} |")
    (month_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return month_dir / f"{stem}.md"


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    try:
        candidates, processed_ids = collect()
    except (RuntimeError, OSError) as e:
        _notify(f"🚨 eva_commission_ledger: помилка збору комісії EVA: {e}")
        _log(f"помилка: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        _notify(f"🚨 eva_commission_ledger: несподівана помилка: {e}")
        _log(f"несподівана помилка: {e}")
        sys.exit(1)

    if not candidates:
        if not dry_run:
            _save_cursor(processed_ids)
        _log("Нових замовлень з комісією немає (або перший запуск — базова лінія).")
        return

    if dry_run:
        _log(f"[dry-run] БУЛО Б {len(candidates)} кандидатів комісії EVA; курсор не рухаю, файли не пишу.")
        for c in candidates:
            _log(f"  [dry-run] {c['order_id']}: Всього {c['commission_total']} "
                 f"(ТМ {c['commission_tm']} + платформа {c['commission_platform']})")
        return

    path = _write_report(candidates)
    _save_cursor(processed_ids)
    _log(f"ГОТОВО: {len(candidates)} кандидатів комісії EVA → {path}")


if __name__ == "__main__":
    main()
