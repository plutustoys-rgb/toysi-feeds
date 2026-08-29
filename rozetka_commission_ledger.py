"""rozetka_commission_ledger.py — щоденний детермінований збір роялті+логістики Rozetka
для графи 9 КОДВ, без ручного походу в кабінет (задача власника 2026-08-29: «мені знову
кожне замовлення треба детально розбирати, чи ти вже налагодиш автоматизацію»).

НАВІЩО: жива сесія бухгалтера сьогодні знайшла, що графа 9 роками бракувала реальних сум
роялті («Комісія за продаж») і логістики RZ-Delivery («Організація видачі відправлень
(Спеціальні умови)») — сукупно +238.34 грн у 5 рядках (КОДВ_журнал.md, запис 66). Причина:
- картка замовлення не показує комісію;
- Rozetka Orders API (rozetka_client.py) — invalid_access_token (нема ROZETKA_USERNAME/PASSWORD);
- спливаюче «Деталі по балансу» показує лише СУКУПНИЙ ЗАЛИШОК боргу (майже завжди 0, бо
  кожне нарахування одразу гаситься з депозиту) — читання «0 = нічого не нараховувалось» хибне.

Справжня історія нарахувань — на `/main/balance/history/list` (Роялті) і `/list/logistic`
(Логістика).

ЯК ЧИТАЄМО (з'ясовано наживо 2026-08-29): за таблицею стоїть JSON API
(cabinet-seller.rozetka.com.ua/balances/search і /balance-logistic/search), АЛЕ виклик fetch()
з credentials:'include' у контексті сторінки провалюється з "TypeError: Failed to fetch" —
сервер повертає `Access-Control-Allow-Origin: *` РАЗОМ З `Access-Control-Allow-Credentials: true`,
що за специфікацією CORS браузер зобов'язаний відхилити (вочевидь антибот-фільтр: звичайному
залогіненому браузеру він, вірогідно, віддає ТОЧНИЙ Origin замість "*"; чому цей скрипт
отримує "*" — невідомо, не досліджували глибше). Голий `requests`/`context.request` теж не
підходить: ендпоінт вимагає CSRF-заголовок, який в Angular-додатку виставляє HttpClient
interceptor, а не сирий cookie-jar (invalid_credentials навіть з усіма кукі).

ТОМУ: звичайне читання DOM САМОЇ таблиці (Playwright `table tbody tr`) — перевірено наживо:
обидві вкладки рендерять ЗВИЧАЙНИЙ `<table>` (не CDK virtual-scroll, бо рядків мало), кожен
`page.query_selector_all('table tbody tr')` дає точний список `<td>` по колонках:
  Роялті:    [logId, дата, тип операції, ID замовлення, ID товару, ціна, к-ть,
              загальна вартість, Нарахування боргу, Погашення боргу, Залишок боргу]
  Логістика: [ID операції, дата транзакції, тип операції, ID замовлення, ID товару, TTN,
              Нарахування боргу, Погашення боргу, Залишок боргу за послугу]
Фільтр — за текстом колонки «Тип операції» (не за прихованим числовим кодом — стабільніше
до змін бекенду): "Комісія за продаж" (роялті) і "Організація видачі відправлень (Спеціальні
умови)" (логістика, лише RMP-ТТН). Сума — колонка «Нарахування боргу» (перевірено проти
живого рядка книги: замовлення 904029467 → 59,62, точно як факт). Числа приходять то з
комою (роялті: "59,62"), то з крапкою (логістика: "10.2") — парсер приймає обидва.
Багатотоварні замовлення дають КІЛЬКА рядків з однаковим ID замовлення — сумуємо за ним
(задокументовано власником: 4 товари → 146.41).

Обидві вкладки за замовчуванням показують ОСТАННІ 20 (роялті) / до 20 (логістика) операцій
(page=1, без пагінації в цьому скрипті — page=2 і pageSize>20 віддавали HTTP 504 нестабільно
при прямих API-викликах, а обсяг замовлень малий (<20/день), тож для ЩОДЕННОГО інкременту
page=1 достатньо; глибокий бекфіл — разова ручна дія, вже зроблена 2026-08-29).

АВТЕНТИФІКАЦІЯ: жодних нових креденшелів — перевикористовує storageState
rozetka_cabinet_scraper.py (`--login` раз, якщо протухне).

ДЕДУП: курсор `.local_secrets/rozetka_commission_cursor.json` (найбільший вже оброблений
logId/operation_id) — щоб щодня не перевипускати ті самі кандидати. Перший запуск на чистому
курсорі приймає ПОТОЧНИЙ максимум за базову лінію (не дампить всю історію як "нове" — вона вже
вручну виправлена 2026-08-29, запис 66).

РЕЗУЛЬТАТ: пише кандидатів (НЕ сам рядок книги — графу 9 пише лише роль «агент-бухгалтер»)
у `документи_КОДВ/YYYY-MM/Rozetka/YYYY-MM-DD_rozetka_komisiya_kandydaty.{md,json}` (спільна
Cowork-папка, бухгалтер має доступ лише туди, не в цей репозиторій) — з крос-звіркою проти
KODV_PlutusToys_2026.xlsx (READ-ONLY: шукає "№<order_id>" у графі 5, показує ПОТОЧНЕ значення
графи 9 поруч із пропонованим Δ, як зробила жива сесія в записі 66 вручну). Якщо нічого
нового — файл не створюється (без порожнього шуму щодня).

ЗАПУСК: python rozetka_commission_ledger.py   (з local_cabinet_audit.ps1, після
rozetka_cabinet_scraper.py щоб сесія була свіжою). Одноразовий логін (якщо ще нема):
python rozetka_cabinet_scraper.py --login
"""
import json
import os
import re
import sys
from collections import defaultdict
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

# Той самий storageState, що rozetka_cabinet_scraper.py — одна сесія на всі Rozetka-скрейпери.
STATE_FILE = Path(
    os.environ.get("ROZETKA_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "rozetka_cabinet_state.json")))
CURSOR_FILE = BASE_DIR / ".local_secrets" / "rozetka_commission_cursor.json"

ROYALTY_PAGE_URL = "https://seller.rozetka.com.ua/main/balance/history/list"
LOGISTIC_PAGE_URL = "https://seller.rozetka.com.ua/main/balance/history/logistic"
NAV_TIMEOUT_MS = 30000

SALE_COMMISS_TITLE = "Комісія за продаж"
LOGISTIC_SPECIAL_TITLE = "Організація видачі відправлень (Спеціальні умови)"  # лише RMP-ТТН


class RozetkaCommissionError(Exception):
    pass


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        send_telegram_message(msg)
    except Exception as e:
        print(f"[RzCommission] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _load_cursor() -> dict:
    if not CURSOR_FILE.exists():
        return {}
    try:
        return json.loads(CURSOR_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_cursor(cursor: dict) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(cursor, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_amount(text: str):
    """Приймає і кому, і крапку як десятковий роздільник (роялті-вкладка пише "59,62",
    логістика — "10.2"), "–"/"-"/порожньо -> None. Прибирає пробіли-роздільники тисяч."""
    text = (text or "").strip().replace("\xa0", "").replace(" ", "")
    if text in ("", "-", "–", "—"):
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _read_table(page, url: str) -> list:
    page.goto(url, timeout=NAV_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
    if "/login" in page.url or "/main" not in page.url:
        raise RozetkaCommissionError(f"сесію не прийнято — редірект на {page.url} (треба --login)")
    page.wait_for_selector("table tbody tr", timeout=NAV_TIMEOUT_MS)
    rows = page.query_selector_all("table tbody tr")
    if not rows:
        raise RozetkaCommissionError(f"{url}: у таблиці 0 рядків (верстка змінилась або сесія неповна)")
    return [[c.inner_text().strip() for c in r.query_selector_all("td")] for r in rows]


def fetch_royalty_rows(page) -> list:
    """Колонки: [0]=logId [1]=дата [2]=тип [3]=order_id [4]=item_id [5]=ціна [6]=к-ть
    [7]=заг.вартість [8]=Нарахування боргу [9]=Погашення боргу [10]=Залишок боргу."""
    rows = _read_table(page, ROYALTY_PAGE_URL)
    result = []
    for cells in rows:
        if len(cells) < 9 or not re.match(r"^\d+$", cells[0]):
            continue
        result.append({"log_id": int(cells[0]), "date": cells[1], "type_title": cells[2],
                        "order_id": cells[3], "debit": _parse_amount(cells[8])})
    return result


def fetch_logistic_rows(page) -> list:
    """Колонки: [0]=operation_id [1]=дата [2]=тип [3]=order_id [4]=item_id [5]=TTN
    [6]=Нарахування боргу [7]=Погашення боргу [8]=Залишок боргу за послугу."""
    rows = _read_table(page, LOGISTIC_PAGE_URL)
    result = []
    for cells in rows:
        if len(cells) < 7 or not re.match(r"^\d+$", cells[0]):
            continue
        result.append({"operation_id": int(cells[0]), "date": cells[1], "type_title": cells[2],
                        "order_id": cells[3], "ttn": cells[5], "debit": _parse_amount(cells[6])})
    return result


def _lookup_book_row(order_id: str) -> dict:
    """READ-ONLY крос-звірка з КОДВ: шукає "№<order_id>" у графі 5 (колонка E), повертає
    {row, current_i9} або {} якщо замовлення в книзі ще нема. Ніколи не пише у файл — графу 9
    книги пише лише роль «агент-бухгалтер» (правило власника)."""
    if not KODV_XLSX.exists():
        return {}
    import openpyxl
    try:
        wb = openpyxl.load_workbook(str(KODV_XLSX), data_only=True, read_only=True)
        ws = wb["КОДВ"]
        needle = f"№{order_id}"
        for row in ws.iter_rows(min_row=7):
            e = row[4].value if len(row) > 4 else None
            if e and needle in str(e):
                return {"row": row[0].row, "current_i9": row[8].value if len(row) > 8 else None,
                        "e_text": str(e)}
    except Exception as e:
        print(f"[RzCommission] крос-звірка з книгою не вдалась (не критично): {e}", file=sys.stderr)
    return {}


def collect(page) -> tuple:
    """Повертає (candidates: list, new_cursor: dict). candidates — по одному запису на
    order_id, де є НОВЕ (з часу останнього курсора) нарахування роялті і/або логістики."""
    cursor = _load_cursor()
    is_first_run = "last_royalty_log_id" not in cursor and "last_logistics_operation_id" not in cursor

    royalty_rows = fetch_royalty_rows(page)
    logistic_rows = fetch_logistic_rows(page)

    max_log_id = max((r["log_id"] for r in royalty_rows), default=cursor.get("last_royalty_log_id", 0))
    max_op_id = max((r["operation_id"] for r in logistic_rows), default=cursor.get("last_logistics_operation_id", 0))
    new_cursor = {"last_royalty_log_id": max_log_id, "last_logistics_operation_id": max_op_id,
                  "updated_at": datetime.now().isoformat(timespec="seconds")}

    if is_first_run:
        # Базова лінія: не дампити всю історію як "нове" (вона вже вручну виправлена 2026-08-29).
        print(f"[RzCommission] Перший запуск — беру поточний стан за базову лінію "
              f"(royalty logId≤{max_log_id}, logistics opId≤{max_op_id}), кандидатів не шукаю.")
        return [], new_cursor

    last_log_id = cursor.get("last_royalty_log_id", 0)
    last_op_id = cursor.get("last_logistics_operation_id", 0)

    per_order = defaultdict(lambda: {"royalty": 0.0, "logistics": 0.0, "royalty_dates": [],
                                      "logistics_dates": [], "ttns": []})
    for r in royalty_rows:
        if r["type_title"] != SALE_COMMISS_TITLE or r["log_id"] <= last_log_id:
            continue
        oid = r["order_id"]
        if not oid or oid == "0":
            continue
        per_order[oid]["royalty"] += r["debit"] or 0.0
        per_order[oid]["royalty_dates"].append(r["date"])

    for r in logistic_rows:
        if r["type_title"] != LOGISTIC_SPECIAL_TITLE or r["operation_id"] <= last_op_id:
            continue
        oid = r["order_id"]
        if not oid or oid == "0":
            continue
        per_order[oid]["logistics"] += abs(r["debit"] or 0.0)
        per_order[oid]["logistics_dates"].append(r["date"])
        if r.get("ttn"):
            per_order[oid]["ttns"].append(r["ttn"])

    candidates = []
    for oid, amounts in sorted(per_order.items()):
        book = _lookup_book_row(oid)
        delta = round(amounts["royalty"] + amounts["logistics"], 2)
        candidates.append({
            "order_id": oid,
            "royalty_new": round(amounts["royalty"], 2),
            "logistics_new": round(amounts["logistics"], 2),
            "delta_i9": delta,
            "ttns": amounts["ttns"],
            "dates": sorted(set(amounts["royalty_dates"] + amounts["logistics_dates"])),
            "book_row": book.get("row"),
            "book_current_i9": book.get("current_i9"),
            "book_proposed_i9": (round((book.get("current_i9") or 0) + delta, 2)
                                  if book.get("current_i9") is not None else None),
            "book_e_text": book.get("e_text"),
        })
    return candidates, new_cursor


def _write_report(candidates: list) -> Path:
    today = datetime.now()
    month_dir = COWORK_DIR / "документи_КОДВ" / today.strftime("%Y-%m") / "Rozetka"
    month_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{today.strftime('%Y-%m-%d')}_rozetka_komisiya_kandydaty"

    (month_dir / f"{stem}.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# Rozetka — нові нарахування роялті/логістики, знайдені автоматично, {today.strftime('%Y-%m-%d %H:%M')}",
             "",
             "Джерело: `seller.rozetka.com.ua/main/balance/history/list` (Роялті, тип "
             "«Комісія за продаж») + `/logistic` (тип «Організація видачі відправлень "
             "(Спеціальні умови)», лише RMP-ТТН). Крос-звірка з книгою READ-ONLY — рядок",
             "графи 9 книги НЕ змінено, це лише кандидати для ролі «агент-бухгалтер».",
             "",
             "| Замовлення | Роялті (нове) | Логістика (нове) | Δ графи 9 | Рядок книги | Поточне I9 | Пропоноване I9 |",
             "|---|---|---|---|---|---|---|"]
    for c in candidates:
        row = c["book_row"] if c["book_row"] else "❗ НЕ ЗНАЙДЕНО в книзі"
        cur = c["book_current_i9"] if c["book_current_i9"] is not None else "—"
        prop = c["book_proposed_i9"] if c["book_proposed_i9"] is not None else "—"
        lines.append(f"| №{c['order_id']} | {c['royalty_new']} | {c['logistics_new']} | "
                     f"{c['delta_i9']} | {row} | {cur} | {prop} |")
    if any(c["ttns"] for c in candidates):
        lines.append("")
        lines.append("TTN (для замовлень з логістикою RZ-Delivery): " +
                     ", ".join(t for c in candidates for t in c["ttns"]))
    (month_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return month_dir / f"{stem}.md"


def run() -> None:
    if not STATE_FILE.exists():
        msg = (f"🚨 rozetka_commission_ledger: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти `python rozetka_cabinet_scraper.py --login`.")
        print(f"[RzCommission] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)

    with sync_playwright() as p:
        # channel="chrome" (справжній Chrome, не bundled chromium) — той самий висновок, що
        # rozetka_merchant_agent.py: bundled chromium ловить антибот Rozetka частіше.
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            candidates, new_cursor = collect(page)
        except (PlaywrightTimeoutError, RozetkaCommissionError) as e:
            msg = f"🚨 rozetka_commission_ledger: {e}"
            print(f"[RzCommission] {msg}", file=sys.stderr)
            _notify(msg)
            sys.exit(1)
        finally:
            browser.close()

    if not candidates:
        print("[RzCommission] Нових нарахувань роялті/логістики немає.")
        _save_cursor(new_cursor)
        return

    report_path = _write_report(candidates)
    _save_cursor(new_cursor)
    total_delta = round(sum(c["delta_i9"] for c in candidates), 2)
    summary = (f"💰 Rozetka: {len(candidates)} замовлень з новим роялті/логістикою "
               f"(разом +{total_delta} грн у графу 9). Звіт: {report_path.name}")
    print(f"[RzCommission] {summary}")
    _notify(summary)


if __name__ == "__main__":
    run()
