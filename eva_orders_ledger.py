"""eva_orders_ledger.py — щоденний детермінований збір НЕОФОРМЛЕНИХ замовлень EVA для КОДВ,
без ручного походу в кабінет (задача власника 2026-08-29, той самий запит, що
rozetka_commission_ledger.py — див. докстрінг там для повного контексту автоматизації).

НАВІЩО: жива сесія бухгалтера 2026-08-29 звірила `seller.eva.ua/merchant/orders` (повний
список, без пагінації — ~10 замовлень за весь час) проти книги і знайшла бэклог 7
неоформлених замовлень (КОДВ_журнал.md, запис 66), з них 4 вже готові до внесення (гроші
зібрані/еквайринг сплачено), 3 ще ні (COD не зібрано / надто нове) чи скасовані.

EVA REST API (`eva_orders_client.py`, merchant-api.eva.ua) МІГ БИ дати ці дані програмно, але
потребує EVA_MERCHANT_USERNAME/PASSWORD у .env — власник їх ще не додав (без них
orders_watcher.py тихо працює на моках, підтверджено 2026-08-29). Замість того щоб чекати на ці
креденшели, скрипт читає ТУ Ж сторінку, яку вже дивиться бухгалтер вручну: `seller.eva.ua/merchant/orders`
— це звичайний server-rendered HTML (НЕ Angular SPA як Rozetka), із чистою таблицею
<table><tbody><tr><td> (звірено наживо 2026-08-29), тож читання значно надійніше за DOM Rozetka.
Колонки (0-індекс): [0] "номер\\nдата", [2] "клієнт\\nтелефон", [3] "сума ₴", [4] "оплата
[· статус]", [6] "статус замовлення".

ГОТОВНІСТЬ (правило бухгалтера, запис 66 — відтворено буквально):
  - оплата "При отриманні" + статус замовлення "Отримано"      -> 🟢 готове (гроші зібрані)
  - оплата містить "Оплачено" (LiqPay · Оплачено)               -> 🟢 готове (еквайринг сплачено;
                                                                    дата доходу = ФІСКАЛЬНИЙ ЧЕК,
                                                                    не дата замовлення — §1.2
                                                                    довідника, бухгалтер звіряє сама)
  - статус замовлення містить "Скасовано"                       -> виключено (§3 довідника)
  - інакше (COD, статус ще не "Отримано")                       -> ще не готове, лише інформативно

Скрипт НІКОЛИ не вирішує ЯКА дата йде в графу 1 книги (це вимагає Checkbox-чек для карткових
і NovaPay-реєстр для накладеного платежу — §1 довідника) і НІКОЛИ не пише в xlsx (правило
власника: графу 9/будь-яку графу пише лише роль «агент-бухгалтер»). Він лише знімає ручний похід
у кабінет і готує факти.

ДЕДУП: без окремого курсора — крос-звірка з книгою (пошук order_id, напр. "8-080133259", у графі
5) вже виключає щойно внесені замовлення на наступному прогоні. Це навмисно простіше за
rozetka_commission_ledger.py (там дедуп по logId потрібен, бо рядок КНИГИ вже може існувати з
неповною сумою — тут потрібна лише бінарна "є/нема").

АВТЕНТИФІКАЦІЯ: перевикористовує storageState eva_cabinet_scraper.py (--login раз, той самий
файл — жодних нових креденшелів).

РЕЗУЛЬТАТ: `документи_КОДВ/YYYY-MM/EVA/YYYY-MM-DD_eva_kandydaty.{md,json}` (лише готові И ще
не в книзі; не готові — окремим інформативним рядком, без файлу-кандидата).

ЗАПУСК: python eva_orders_ledger.py   (з local_cabinet_audit.ps1, після eva_cabinet_scraper.py)
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from telegram_notify import send_telegram_message

load_dotenv()

BASE_DIR = Path(__file__).parent
COWORK_DIR = Path(os.environ.get(
    "PLUTUS_COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
KODV_XLSX = COWORK_DIR / "KODV_PlutusToys_2026.xlsx"
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

# Той самий storageState, що eva_cabinet_scraper.py.
STATE_FILE = Path(
    os.environ.get("EVA_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "eva_cabinet_state.json")))

ORDERS_URL = "https://seller.eva.ua/merchant/orders"
NAV_TIMEOUT_MS = 30000


class EvaOrdersError(Exception):
    pass


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        send_telegram_message(msg)
    except Exception as e:
        print(f"[EvaOrders] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _parse_order_id_date(cell: str):
    # "8-080364562\n29.08.2026 01:08" (звірено наживо 2026-08-29)
    m = re.match(r"\s*(\S+)\s*\n\s*(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})", cell)
    if not m:
        return None, None
    return m.group(1), f"{m.group(2)} {m.group(3)}"


def _parse_customer(cell: str):
    parts = [p.strip() for p in cell.split("\n") if p.strip()]
    name = parts[0] if parts else ""
    phone = parts[1] if len(parts) > 1 else ""
    return name, phone


def read_orders(page) -> list:
    page.goto(ORDERS_URL, timeout=NAV_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
    if "/login" in page.url:
        raise EvaOrdersError(f"сесію не прийнято — редірект на {page.url} (треба --login)")
    rows = page.query_selector_all("table tbody tr")
    if not rows:
        raise EvaOrdersError("на /merchant/orders не знайдено жодного рядка таблиці "
                              "(сесія протухла або змінилась верстка)")
    orders = []
    for row in rows:
        cells = [td.inner_text().strip() for td in row.query_selector_all("td")]
        if len(cells) < 7:
            continue
        order_id, date = _parse_order_id_date(cells[0])
        if not order_id:
            continue
        name, phone = _parse_customer(cells[2])
        amount_m = re.search(r"([\d.,]+)", cells[3])
        orders.append({
            "order_id": order_id,
            "date": date,
            "customer_name": name,
            "phone": phone,
            "amount": float(amount_m.group(1).replace(",", ".")) if amount_m else None,
            "payment": cells[4].strip(),
            "status": cells[6].strip(),
        })
    return orders


def _classify(order: dict) -> str:
    status, payment = order["status"], order["payment"]
    if "Скасовано" in status:
        return "excluded"
    if "Оплачено" in payment:
        return "ready"          # еквайринг сплачено (LiqPay) — дата доходу за чеком, звіряє бухгалтер
    if "При отриманні" in payment and status == "Отримано":
        return "ready"          # накладений платіж, гроші зібрані
    return "pending"            # COD ще не зібрано / статус надто ранній / оплата очікується


def _already_in_book(order_id: str) -> bool:
    if not KODV_XLSX.exists():
        return False
    import openpyxl
    try:
        wb = openpyxl.load_workbook(str(KODV_XLSX), data_only=True, read_only=True)
        ws = wb["КОДВ"]
        for row in ws.iter_rows(min_row=7):
            e = row[4].value if len(row) > 4 else None
            if e and order_id in str(e):
                return True
    except Exception as e:
        print(f"[EvaOrders] крос-звірка з книгою не вдалась (не критично): {e}", file=sys.stderr)
    return False


def _write_report(ready: list, pending: list) -> Path:
    today = datetime.now()
    month_dir = COWORK_DIR / "документи_КОДВ" / today.strftime("%Y-%m") / "EVA"
    month_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{today.strftime('%Y-%m-%d')}_eva_kandydaty"

    (month_dir / f"{stem}.json").write_text(
        json.dumps({"ready": ready, "pending_informational": pending}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    lines = [f"# EVA — замовлення поза книгою, знайдені автоматично, {today.strftime('%Y-%m-%d %H:%M')}",
             "",
             "Джерело: `seller.eva.ua/merchant/orders`. Готовність за правилом бухгалтера "
             "(КОДВ_журнал.md, запис 66): накладений платіж+«Отримано», або LiqPay+«Оплачено».",
             "Дату доходу (графа 1) визначає роль «агент-бухгалтер» за §1 довідника "
             "(чек Checkbox для картки, NovaPay/скрін для накладеного платежу) — тут лише факти.",
             "",
             "## 🟢 Готові до внесення (ще не в книзі)",
             "| Замовлення | Дата | Клієнт | Сума | Оплата | Статус |",
             "|---|---|---|---|---|---|"]
    for o in ready:
        lines.append(f"| {o['order_id']} | {o['date']} | {o['customer_name']} ({o['phone']}) | "
                     f"{o['amount']} ₴ | {o['payment']} | {o['status']} |")
    if not ready:
        lines.append("| _немає_ | | | | | |")

    if pending:
        lines.append("")
        lines.append("## 🟡 Ще не готові (інформативно, гроші не зібрані / надто нове)")
        lines.append("| Замовлення | Дата | Сума | Оплата | Статус |")
        lines.append("|---|---|---|---|---|")
        for o in pending:
            lines.append(f"| {o['order_id']} | {o['date']} | {o['amount']} ₴ | {o['payment']} | {o['status']} |")

    (month_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return month_dir / f"{stem}.md"


def run() -> None:
    if not STATE_FILE.exists():
        msg = (f"🚨 eva_orders_ledger: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти `python eva_cabinet_scraper.py --login`.")
        print(f"[EvaOrders] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            orders = read_orders(page)
        except (PlaywrightTimeoutError, EvaOrdersError) as e:
            msg = f"🚨 eva_orders_ledger: {e}"
            print(f"[EvaOrders] {msg}", file=sys.stderr)
            _notify(msg)
            sys.exit(1)
        finally:
            browser.close()

    ready, pending = [], []
    for o in orders:
        cls = _classify(o)
        if cls == "excluded":
            continue
        if _already_in_book(o["order_id"]):
            continue
        (ready if cls == "ready" else pending).append(o)

    if not ready and not pending:
        print("[EvaOrders] Усі замовлення вже в книзі або скасовані — нічого нового.")
        return

    report_path = _write_report(ready, pending)
    summary = (f"📦 EVA: {len(ready)} замовлень готові до внесення, {len(pending)} ще ні. "
               f"Звіт: {report_path.name}")
    print(f"[EvaOrders] {summary}")
    if ready:
        _notify(summary)


if __name__ == "__main__":
    run()
