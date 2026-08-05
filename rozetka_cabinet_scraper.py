"""
rozetka_cabinet_scraper.py — headless-читання каталог-здоров'я кабінету Rozetka
(seller.rozetka.com.ua/main/cabinet): статуси товарів (Всього/Активні/Неактивні/
Нові/На модерації) + лічильники блокувань (Стоп-слово/Стоп-бренд/Стоп-категорія/
Прихованих модератором).

НАВІЩО (2026-08-05, завдання Cowork #3): жива перевірка показала — Rozetka НЕ
«Підготовка/1010», а живий канал (9931 товар, 1976 на модерації, реальні
блокування), якого жоден аудитор не бачив. API (`rozetka_client.py`) дає лише
/goods/errors + /goods/not-valid (без повних counts) і вимагає ROZETKA_USERNAME/
PASSWORD; повні counts видно лише в кабінеті. Тому — той самий Playwright+storageState
патерн, що eva_/toysi_cabinet_scraper.py.

АВТЕНТИФІКАЦІЯ: збережений storageState (`--login` раз). Протухла сесія → прогін
виявляє (counts не знайдено) і просить `--login`.

БЕЗПЕКА: лише навігація + читання тексту. storageState — секрет у .local_secrets/
(gitignore), не в Cowork-папці.

ЗАПУСК:
    python rozetka_cabinet_scraper.py --login   # раз: вікно, логін, стан збережено
    python rozetka_cabinet_scraper.py           # headless (Task Scheduler / local_cabinet_audit.ps1)

РЕЗУЛЬТАТ: reports/rozetka_cabinet_YYYY-MM-DD.md + Telegram (якщо не AUDIT_NO_TELEGRAM).
"""
import argparse
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
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

STATE_FILE = Path(
    os.environ.get("ROZETKA_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "rozetka_cabinet_state.json"))
)

CABINET_URL = "https://seller.rozetka.com.ua/main/cabinet"
LOGIN_START_URL = "https://seller.rozetka.com.ua/"
NAV_TIMEOUT_MS = 30000

# Мітки статусів каталогу + причин блокувань (живо бачені Cowork 2026-08-05).
STATUS_LABELS = {"total": "Всього", "active": "Активні", "inactive": "Неактивні",
                 "new": "Нові", "moderation": "На модерації"}
BLOCK_LABELS = {"stop_word": "Стоп-слово", "stop_brand": "Стоп-бренд",
                "stop_category": "Стоп-категорія", "hidden": "Прихован"}


class RozetkaCabinetError(Exception):
    pass


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        send_telegram_message(msg)
    except Exception as e:
        print(f"[RozetkaCabinet] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _save_failure_artifacts(page, prefix: str) -> None:
    """Лише скріншот у ЛОКАЛЬНУ reports/ (не в спільну папку — містить персональні дані)."""
    try:
        fail_dir = BASE_DIR / "reports"
        fail_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        page.screenshot(path=str(fail_dir / f"rozetka_cabinet_scraper_failure_{prefix}_{ts}.png"), full_page=True)
    except Exception:
        pass


def create_state() -> None:
    print("[RozetkaCabinet] Відкриваю seller.rozetka.com.ua. Залогінься, потім Enter...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(LOGIN_START_URL, timeout=NAV_TIMEOUT_MS)
        try:
            input()
        except EOFError:
            print("[RozetkaCabinet] Немає інтерактивного вводу — --login запускай вручну в терміналі.", file=sys.stderr)
            browser.close()
            sys.exit(1)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"[RozetkaCabinet] Сесію збережено: {STATE_FILE}")


def _clean_int(raw: str):
    raw = raw.replace(" ", "").replace(" ", "")
    return int(raw) if raw.isdigit() else None


def _count_near(text: str, label: str, window: int = 60):
    """Ціле число, пов'язане з міткою. ПРІОРИТЕТ — число ОДРАЗУ ПІСЛЯ мітки
    (типова стат-картка «Мітка N» / «Мітка: N»), бо це коректно розводить СУСІДНІ
    лічильники (напр. «Активні 5 Неактивні 7950» — кожна мітка бере своє число
    після себе, а не сусіднє). Фолбек — найближче число ПЕРЕД міткою (layout
    «N Мітка»). None, якщо мітки нема / поряд немає числа.
    ⚠️ Точний layout кабінету Rozetka не звірено живо — валідується першим
    --login-прогоном; якщо числа не ті, підправити напрямок пошуку під реальну
    верстку (те саме, що робили для eva/toysi-скрейперів)."""
    low = text.lower()
    idx = low.find(label.lower())
    if idx == -1:
        return None
    after = text[idx + len(label): idx + len(label) + window]
    m = re.match(r"\D{0,8}(\d[\d  ]*)", after)  # число одразу після мітки
    if m:
        v = _clean_int(m.group(1))
        if v is not None:
            return v
    before = text[max(0, idx - window): idx]   # фолбек: останнє число перед міткою
    nums = re.findall(r"\d[\d  ]*", before)
    return _clean_int(nums[-1]) if nums else None


def read_cabinet(page) -> dict:
    page.goto(CABINET_URL, timeout=NAV_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
    if "/main" not in page.url and "cabinet" not in page.url:
        raise RozetkaCabinetError(f"сесію не прийнято — редірект на {page.url} (треба --login)")
    text = page.inner_text("body")
    status = {k: _count_near(text, lbl) for k, lbl in STATUS_LABELS.items()}
    blocks = {k: _count_near(text, lbl) for k, lbl in BLOCK_LABELS.items()}
    if all(v is None for v in status.values()):
        raise RozetkaCabinetError("на /main/cabinet не знайдено жодного статус-лічильника "
                                  "(сесія протухла або змінилась верстка)")
    return {"status": status, "blocks": blocks}


def scrape() -> None:
    if not STATE_FILE.exists():
        msg = (f"🚨 rozetka_cabinet_scraper: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти раз `python rozetka_cabinet_scraper.py --login`.")
        print(f"[RozetkaCabinet] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            try:
                data = read_cabinet(page)
            except (PlaywrightTimeoutError, RozetkaCabinetError) as e:
                _save_failure_artifacts(page, "cabinet")
                msg = (f"🚨 rozetka_cabinet_scraper: не вдалось прочитати кабінет Rozetka: {e}. "
                       f"Якщо сесія протухла — `python rozetka_cabinet_scraper.py --login`.")
                print(f"[RozetkaCabinet] {msg}", file=sys.stderr)
                _notify(msg)
                sys.exit(1)
        finally:
            browser.close()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    st, bl = data["status"], data["blocks"]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    report_path = REPORT_DIR / f"rozetka_cabinet_{today}.md"
    lines = [f"# Кабінет Rozetka — каталог-здоров'я, {now.strftime('%Y-%m-%d %H:%M')}", ""]
    lines.append("## Статуси товарів")
    lines.append(f"- Всього: {st['total']}")
    lines.append(f"- Активні: {st['active']}")
    lines.append(f"- Неактивні: {st['inactive']}")
    lines.append(f"- Нові: {st['new']}")
    lines.append(f"- На модерації: {st['moderation']}")
    lines.append("")
    lines.append("## Блокування (реальні дії до виконання)")
    lines.append(f"- Стоп-слово: {bl['stop_word']}")
    lines.append(f"- Стоп-бренд: {bl['stop_brand']}")
    lines.append(f"- Стоп-категорія: {bl['stop_category']}")
    lines.append(f"- Прихованих модератором: {bl['hidden']}")
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[RozetkaCabinet] Звіт: {report_path}")

    summary = (f"🛒 Rozetka {today}: всього {st['total']}, активні {st['active']}, "
               f"модерація {st['moderation']}. Блокування: стоп-бренд {bl['stop_brand']}, "
               f"стоп-категорія {bl['stop_category']}, стоп-слово {bl['stop_word']}, приховано {bl['hidden']}.")
    _notify(summary)
    print(f"[RozetkaCabinet] {summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless-читання каталог-здоров'я кабінету Rozetka (Playwright + storageState).")
    parser.add_argument("--login", action="store_true", help="Раз: вікно, логін, зберегти сесію.")
    args = parser.parse_args()
    if args.login:
        create_state()
    else:
        scrape()


if __name__ == "__main__":
    main()
