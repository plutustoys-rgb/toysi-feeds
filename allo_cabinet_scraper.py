"""
allo_cabinet_scraper.py — headless-читання кабінету ALLO (partner.allo.ua):
баланси (ТМ + абонплати + прапорець попередження про блокування) + лічильники
замовлень по статусах. Read-only, той самий Playwright+storageState патерн, що
eva/toysi/rozetka/prom-скрейпери.

НАВІЩО (2026-08-05, завдання Cowork — аудитор ALLO): жива перевірка показала —
у кабінеті ALLO прямим текстом «Поповніть Баланс абонплати, для уникнення
блокування» (Баланс абонплати 120 ₴), 0 замовлень, і 876/1085 товарів НЕ додались
при імпорті. ALLO не має публічного API балансу/статусів — лише кабінет.

ВЕРСТКА ЗВІРЕНА ЖИВО 2026-08-06 (перший --login-прогін): баланси НЕ на дашборді, а
на `/billing/status` («Баланс ТМ:\\n\\n2 500 ₴», «Баланс абонплати:\\n\\n120 ₴»,
«...для уникнення блокування»); замовлення на дашборді картками «N% N СтатусЛейбл»
(число ПЕРЕД міткою). При parse-невдачі зберігається локальний дамп сирого тексту
(allo_cabinet_debug_*.txt) для доналаштування, якщо ALLO змінить верстку.

БЕЗПЕКА: лише навігація + читання тексту. storageState — секрет у .local_secrets/
(gitignore), не в Cowork-папці. (Позначення сповіщень прочитаними — окрема write-дія,
за дозволом власника, ще НЕ реалізовано тут — цей аудитор read-only.)

ЗАПУСК:
    python allo_cabinet_scraper.py --login   # раз: вікно, логін, стан збережено
    python allo_cabinet_scraper.py           # headless (local_cabinet_audit.ps1)

РЕЗУЛЬТАТ: reports/allo_cabinet_YYYY-MM-DD.md + рядок у balance_history.jsonl
(platform="allo") + Telegram (якщо не AUDIT_NO_TELEGRAM).
"""
import argparse
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
_OUT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (BASE_DIR / "reports"))
REPORT_DIR = _OUT_DIR
HISTORY_FILE = _OUT_DIR / "balance_history.jsonl"
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

STATE_FILE = Path(
    os.environ.get("ALLO_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "allo_cabinet_state.json"))
)

DASHBOARD_URL = "https://partner.allo.ua/"           # дашборд = картки замовлень
BILLING_URL = "https://partner.allo.ua/billing/status"  # баланси ТМ/абонплати/Холд + попередження
LOGIN_START_URL = "https://partner.allo.ua/"
NAV_TIMEOUT_MS = 30000

ORDER_LABELS = {"accepted": "Прийнято", "picking": "Комплектується", "delivering": "Доставляється",
                "delivered": "Доставлено", "done": "Виконано", "cancelled": "Скасовано"}
# Попередження ALLO про потребу поповнити абонплату (фінансовий прапорець).
BLOCK_WARN_MARKER = "уникнення блокування"


class AlloCabinetError(Exception):
    pass


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        send_telegram_message(msg)
    except Exception as e:
        print(f"[AlloCabinet] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _save_failure_artifacts(page, prefix: str, text: str = None) -> None:
    """Скріншот + (для доналаштування парсера) сирий текст — у ЛОКАЛЬНУ reports/
    (не в спільну папку: містить персональні/фінансові дані кабінету)."""
    try:
        fail_dir = BASE_DIR / "reports"
        fail_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        page.screenshot(path=str(fail_dir / f"allo_cabinet_failure_{prefix}_{ts}.png"), full_page=True)
        if text is not None:
            (fail_dir / f"allo_cabinet_debug_{prefix}_{ts}.txt").write_text(text, encoding="utf-8")
    except Exception:
        pass


def create_state() -> None:
    print("[AlloCabinet] Відкриваю partner.allo.ua. Залогінься, потім Enter...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(LOGIN_START_URL, timeout=NAV_TIMEOUT_MS)
        try:
            input()
        except EOFError:
            print("[AlloCabinet] Немає інтерактивного вводу — --login запускай вручну в терміналі.", file=sys.stderr)
            browser.close()
            sys.exit(1)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"[AlloCabinet] Сесію збережено: {STATE_FILE}")


def _amount_after(text: str, label: str):
    """Сума (грн) після мітки: «мітка … N ₴» (N з роздільниками тисяч). float|None."""
    m = re.search(re.escape(label) + r"[^\d]{0,20}?(\d[\d\xa0   ]*(?:[.,]\d{1,2})?)", text)
    if not m:
        return None
    raw = (m.group(1).replace("\xa0", "").replace(" ", "").replace(" ", "")
           .replace(" ", "").replace(",", "."))
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def _count_before(text: str, label: str):
    """Ціле число ПЕРЕД міткою (дашборд ALLO: картка «N% N СтатусЛейбл» — число count
    йде перед назвою статусу). Живо звірено 2026-08-06. int|None."""
    m = re.search(r"(\d+)[^\d]{0,6}" + re.escape(label), text)
    return int(m.group(1)) if m else None


def read_cabinet(page) -> dict:
    # 1) Баланси — сторінка /billing/status (на дашборді їх нема: у шапці лише сума ТМ
    #    без мітки, а «Баланс абонплати» + попередження — лише тут). Живо звірено 06.08:
    #    «Баланс ТМ:\n\n2 500 ₴», «Баланс абонплати:\n\n120 ₴», «...для уникнення блокування».
    page.goto(BILLING_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    if "sign_in" in page.url.lower() or "login" in page.url.lower():
        raise AlloCabinetError(f"сесію не прийнято — редірект на {page.url} (треба --login)")
    bill = page.inner_text("body")
    balance_tm = _amount_after(bill, "Баланс ТМ")
    balance_sub = _amount_after(bill, "Баланс абонплати")
    block_warning = BLOCK_WARN_MARKER in bill

    # 2) Замовлення — дашборд «/»: картки «N% N СтатусЛейбл» (число ПЕРЕД міткою).
    page.goto(DASHBOARD_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    dash = page.inner_text("body")
    orders = {k: _count_before(dash, lbl) for k, lbl in ORDER_LABELS.items()}

    if balance_tm is None and balance_sub is None:
        raise AlloCabinetError("на /billing/status не знайдено балансів (сесія протухла "
                               "або змінилась верстка)")
    return {"balance_tm": balance_tm, "balance_sub": balance_sub,
            "block_warning": block_warning, "orders": orders, "_dash": bill}


def scrape() -> None:
    if not STATE_FILE.exists():
        msg = (f"🚨 allo_cabinet_scraper: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти раз `python allo_cabinet_scraper.py --login`.")
        print(f"[AlloCabinet] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)

    dash_text = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            try:
                data = read_cabinet(page)
                dash_text = data.pop("_dash", None)
            except (PlaywrightTimeoutError, AlloCabinetError) as e:
                try:
                    dash_text = page.inner_text("body")
                except Exception:
                    dash_text = None
                _save_failure_artifacts(page, "cabinet", text=dash_text)
                msg = (f"🚨 allo_cabinet_scraper: не вдалось прочитати кабінет ALLO: {e}. "
                       f"Якщо сесія протухла — `python allo_cabinet_scraper.py --login`.")
                print(f"[AlloCabinet] {msg}", file=sys.stderr)
                _notify(msg)
                sys.exit(1)
        finally:
            browser.close()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    o = data["orders"]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    report_path = REPORT_DIR / f"allo_cabinet_{today}.md"
    lines = [f"# Кабінет ALLO — автоматичне читання, {now.strftime('%Y-%m-%d %H:%M')}", ""]
    lines.append("## Баланси")
    lines.append(f"- Баланс ТМ: {data['balance_tm']} ₴")
    lines.append(f"- Баланс абонплати: {data['balance_sub']} ₴")
    if data["block_warning"]:
        lines.append("- 🔴 **Попередження кабінету: поповніть Баланс абонплати для уникнення блокування**")
    lines.append("")
    lines.append("## Замовлення (по статусах)")
    for k, lbl in ORDER_LABELS.items():
        lines.append(f"- {lbl}: {o[k]}")
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[AlloCabinet] Звіт: {report_path}")

    row = {"ts": now.isoformat(timespec="seconds"), "platform": "allo",
           "balance_total": data["balance_tm"], "balance_sub": data["balance_sub"],
           "block_warning": data["block_warning"]}
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[AlloCabinet] не вдалось дописати історію: {e}", file=sys.stderr)

    warn = " 🔴поповнити абонплату" if data["block_warning"] else ""
    summary = (f"🅰️ Баланс ALLO {today}: ТМ {data['balance_tm']} ₴, абонплата {data['balance_sub']} ₴{warn}. "
               f"Замовлень: прийнято {o['accepted']}.")
    _notify(summary)
    print(f"[AlloCabinet] {summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless-читання кабінету ALLO (Playwright + storageState).")
    parser.add_argument("--login", action="store_true", help="Раз: вікно, логін, зберегти сесію.")
    args = parser.parse_args()
    if args.login:
        create_state()
    else:
        scrape()


if __name__ == "__main__":
    main()
