"""
toysi_cabinet_scraper.py — headless-читання депозиту постачальника Toysi
(toysi.ua): сума депозиту + курс (грн/$), які видно в шапці кабінету після логіну.

НАВІЩО (2026-08-05, завдання Cowork #2): Toysi API (`toysi.ua/api.php`) має ЛИШЕ
методи замовлень (order_create/order_status/order_positions) — депозиту/курсу в API
НЕМАЄ (перевірено api-doc.php). Тому депозит читаємо тим самим Playwright+storageState
патерном, що вже є в `eva_cabinet_scraper.py`. Депозит — важливий: списання за кожне
замовлення йде саме з нього, а зміни ніде не логувались (Cowork: 12472 vs 13811 без сліду).

АВТЕНТИФІКАЦІЯ: збережений storageState (створюється раз інтерактивно, `--login`),
не логінимось програмно. Протухла сесія → прогін виявляє (депозит не знайдено) і
чесно пише про потребу `--login`.

БЕЗПЕКА: лише навігація + читання тексту. storageState — секрет у .local_secrets/
(gitignore), не в Cowork-папці (правило CLAUDE.md).

ЗАПУСК:
    python toysi_cabinet_scraper.py --login   # раз: вікно, власник логіниться, стан збережено
    python toysi_cabinet_scraper.py           # headless-прогін (Task Scheduler / local_cabinet_audit.ps1)

РЕЗУЛЬТАТ: reports/toysi_cabinet_YYYY-MM-DD.md + рядок у balance_history.jsonl
(platform="toysi", deposit, rate) + Telegram (якщо не AUDIT_NO_TELEGRAM).
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
# AUDIT_REPORT_DIR → Cowork-папка (локальний прогін); дефолт reports/. AUDIT_NO_TELEGRAM=1 — без Telegram.
_OUT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (BASE_DIR / "reports"))
REPORT_DIR = _OUT_DIR
HISTORY_FILE = _OUT_DIR / "balance_history.jsonl"
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

STATE_FILE = Path(
    os.environ.get("TOYSI_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "toysi_cabinet_state.json"))
)

CABINET_URL = "https://toysi.ua/"
NAV_TIMEOUT_MS = 30000


class ToysiCabinetError(Exception):
    pass


def _notify(msg: str) -> None:
    """Telegram best-effort; AUDIT_NO_TELEGRAM=1 → не шлемо (доставка у папку)."""
    if _NO_TELEGRAM:
        return
    try:
        send_telegram_message(msg)
    except Exception as e:
        print(f"[ToysiCabinet] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _save_failure_artifacts(page, prefix: str) -> None:
    """Лише скріншот (без HTML — сторінка може містити персональні дані)."""
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        page.screenshot(path=str(REPORT_DIR / f"toysi_cabinet_scraper_failure_{prefix}_{ts}.png"), full_page=True)
    except Exception:
        pass


def create_state() -> None:
    """--login: видиме вікно, власник логіниться сам, стан зберігається у STATE_FILE."""
    print("[ToysiCabinet] Відкриваю вікно toysi.ua. Залогінься, потім повернись і натисни Enter...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(CABINET_URL, timeout=NAV_TIMEOUT_MS)
        try:
            input()
        except EOFError:
            print("[ToysiCabinet] Немає інтерактивного вводу — --login запускай вручну в терміналі.", file=sys.stderr)
            browser.close()
            sys.exit(1)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"[ToysiCabinet] Сесію збережено: {STATE_FILE}")


def _find_number_after(text: str, label: str):
    """Перше число (з пробілами-роздільниками тисяч + опційна коп.) після мітки,
    у межах ~25 символів. Повертає float або None."""
    m = re.search(label + r"[^\d\-]{0,25}?(\d[\d  ]*(?:[.,]\d{1,2})?)", text, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def read_deposit(page) -> dict:
    """Читає депозит + курс із шапки toysi.ua. Кидає ToysiCabinetError, якщо сесія
    протухла (депозит не знайдено — найпевніше розлогінило)."""
    page.goto(CABINET_URL, timeout=NAV_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
    text = page.inner_text("body")
    deposit = _find_number_after(text, r"депозит")
    rate = _find_number_after(text, r"курс")
    if deposit is None:
        raise ToysiCabinetError("депозит не знайдено на toysi.ua — сесія протухла (треба --login) "
                                "або змінилась верстка шапки")
    return {"deposit": deposit, "rate": rate}


def scrape() -> None:
    if not STATE_FILE.exists():
        msg = (f"🚨 toysi_cabinet_scraper: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти раз `python toysi_cabinet_scraper.py --login`.")
        print(f"[ToysiCabinet] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            try:
                bal = read_deposit(page)
            except (PlaywrightTimeoutError, ToysiCabinetError) as e:
                _save_failure_artifacts(page, "deposit")
                msg = (f"🚨 toysi_cabinet_scraper: не вдалось прочитати депозит Toysi: {e}. "
                       f"Якщо сесія протухла — `python toysi_cabinet_scraper.py --login`.")
                print(f"[ToysiCabinet] {msg}", file=sys.stderr)
                _notify(msg)
                sys.exit(1)
        finally:
            browser.close()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    report_path = REPORT_DIR / f"toysi_cabinet_{today}.md"
    lines = [f"# Кабінет Toysi (постачальник) — автоматичне читання, {now.strftime('%Y-%m-%d %H:%M')}", ""]
    lines.append("## Депозит")
    lines.append(f"- Депозит: {bal['deposit']} ₴")
    lines.append(f"- Курс: {bal['rate']}")
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[ToysiCabinet] Звіт: {report_path}")

    row = {"ts": now.isoformat(timespec="seconds"), "platform": "toysi",
           "deposit": bal["deposit"], "rate": bal["rate"]}
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[ToysiCabinet] не вдалось дописати історію: {e}", file=sys.stderr)

    summary = f"🏭 Депозит Toysi {today}: {bal['deposit']} ₴ (курс {bal['rate']})."
    _notify(summary)
    print(f"[ToysiCabinet] {summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless-читання депозиту кабінету Toysi (Playwright + storageState).")
    parser.add_argument("--login", action="store_true",
                        help="Раз: відкрити вікно, залогінитись, зберегти сесію.")
    args = parser.parse_args()
    if args.login:
        create_state()
    else:
        scrape()


if __name__ == "__main__":
    main()
