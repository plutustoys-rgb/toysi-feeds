"""
prom_notifications_scraper.py — headless-читання сповіщень кабінету Prom
(my.prom.ua/cms/notifications) у звіт: топ найновіших сповіщень (newest-first).

НАВІЩО (2026-08-05, завдання Cowork #5): 46 непрочитаних Prom-сповіщень ніхто не
переглядав, а серед них — прямо-грошові сигнали (жива приклад: «8 позицій у
загальних категоріях з підвищеними ставками ProSale — змініть категорію, щоб
платити менше»). Офіційний Prom API не має ендпоінта сповіщень — лише кабінет.
Той самий Playwright+storageState патерн, що eva/toysi/rozetka-скрейпери.

СВІДОМО БЕЗ per-notification парсингу: сторінка — список різнорідних текстів,
структурний парсинг крихкий і легко дасть хибний результат. Тому вивантажуємо
СИРИЙ текст верхньої частини списку (newest-first) — власник читає найновіші як є.

АВТЕНТИФІКАЦІЯ: storageState (`--login` раз). Протухла сесія → редірект на логін,
скрипт це виявляє і просить `--login`.

БЕЗПЕКА: лише навігація + читання тексту. storageState — секрет у .local_secrets/
(gitignore), не в Cowork-папці.

ЗАПУСК:
    python prom_notifications_scraper.py --login   # раз: вікно, логін, стан збережено
    python prom_notifications_scraper.py           # headless (local_cabinet_audit.ps1)

РЕЗУЛЬТАТ: reports/prom_notifications_YYYY-MM-DD.md (топ сповіщень) у Cowork-папку,
без Telegram (якщо AUDIT_NO_TELEGRAM).
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
    os.environ.get("PROM_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "prom_cabinet_state.json"))
)

NOTIF_URL = "https://my.prom.ua/cms/notifications"
LOGIN_START_URL = "https://my.prom.ua/"
NAV_TIMEOUT_MS = 30000
# Скільки символів верхньої частини списку сповіщень вивантажуємо (newest-first).
NOTIF_TEXT_CHARS = 2500


class PromNotifError(Exception):
    pass


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        send_telegram_message(msg)
    except Exception as e:
        print(f"[PromNotif] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _save_failure_artifacts(page, prefix: str) -> None:
    """Лише скріншот у ЛОКАЛЬНУ reports/ (не в спільну папку — персональні дані)."""
    try:
        fail_dir = BASE_DIR / "reports"
        fail_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        page.screenshot(path=str(fail_dir / f"prom_notifications_failure_{prefix}_{ts}.png"), full_page=True)
    except Exception:
        pass


def create_state() -> None:
    print("[PromNotif] Відкриваю my.prom.ua. Залогінься, потім Enter...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(LOGIN_START_URL, timeout=NAV_TIMEOUT_MS)
        try:
            input()
        except EOFError:
            print("[PromNotif] Немає інтерактивного вводу — --login запускай вручну в терміналі.", file=sys.stderr)
            browser.close()
            sys.exit(1)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"[PromNotif] Сесію збережено: {STATE_FILE}")


def read_notifications(page) -> str:
    # my.prom.ua — важкий SPA з постійним фоновим polling: wait_for_load_state
    # ("networkidle") НІКОЛИ не настає → 30с timeout (живо 2026-08-05). Тому чекаємо
    # лише domcontentloaded + фіксована пауза на SPA-рендер списку сповіщень.
    page.goto(NOTIF_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    if "my.prom.ua" not in page.url or "login" in page.url.lower():
        raise PromNotifError(f"сесію не прийнято — редірект на {page.url} (треба --login)")
    text = page.inner_text("body")
    # Обрізаємо шапку/меню: беремо від слова «Сповіщення», якщо є, інакше — весь body.
    marker = text.find("Сповіщення")
    body = text[marker:] if marker != -1 else text
    # Стискаємо порожні рядки, лишаємо верх (newest-first).
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if len(body) < 20:
        raise PromNotifError("сторінка сповіщень порожня/не завантажилась (сесія протухла або змінилась верстка)")
    return body[:NOTIF_TEXT_CHARS]


def scrape() -> None:
    if not STATE_FILE.exists():
        msg = (f"🚨 prom_notifications_scraper: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти раз `python prom_notifications_scraper.py --login`.")
        print(f"[PromNotif] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            try:
                notif_text = read_notifications(page)
            except (PlaywrightTimeoutError, PromNotifError) as e:
                _save_failure_artifacts(page, "notifications")
                msg = (f"🚨 prom_notifications_scraper: не вдалось прочитати сповіщення Prom: {e}. "
                       f"Якщо сесія протухла — `python prom_notifications_scraper.py --login`.")
                print(f"[PromNotif] {msg}", file=sys.stderr)
                _notify(msg)
                sys.exit(1)
        finally:
            browser.close()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    money_markers = ("ProSale", "підвищеними ставками", "змініть категорію", "переплач", "заблокован")
    flagged = [m for m in money_markers if m in notif_text]

    # СИРИЙ текст сповіщень МОЖЕ містити персональні дані (імена/питання покупців,
    # референси замовлень) — тримаємо його ЛОКАЛЬНО (BASE_DIR/reports, gitignore, не
    # синхронізується), а НЕ у спільній Cowork-папці. Правило CLAUDE.md «без чутливого
    # у спільній папці» (там був реальний витік NovaPay) + узгодженість з failure-
    # скріном тієї ж сторінки, який теж лишається локально (аудит #222).
    local_dir = BASE_DIR / "reports"
    local_dir.mkdir(parents=True, exist_ok=True)
    raw_path = local_dir / f"prom_notifications_{today}.md"
    raw_path.write_text(
        f"# Сповіщення Prom — ПОВНИЙ текст (локально), {now.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"```\n{notif_text}\n```\n", encoding="utf-8")
    print(f"[PromNotif] Повний текст (локально): {raw_path}")

    # У спільну Cowork-папку — лише ЗНЕОСОБЛЕНЕ зведення (які грошові сигнали є +
    # де читати повний текст). Без сирого тексту → без PII у спільній папці.
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = REPORT_DIR / f"prom_notifications_{today}.md"
    summary_path.write_text(
        f"# Сповіщення Prom — зведення, {now.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"- Грошові сигнали у сповіщеннях: {', '.join(flagged) if flagged else 'не знайдено'}\n"
        f"- Повний текст (локально, з міркувань приватності — не в спільній папці): `{raw_path}`\n", encoding="utf-8")
    print(f"[PromNotif] Зведення (спільна папка): {summary_path}")

    # Telegram — лише при грошовому сигналі (щоб не шуміти), без сирого тексту.
    if flagged:
        _notify(f"📨 Prom-сповіщення {today}: є сигнали — {', '.join(flagged)}. Повний текст локально: {raw_path.name}.")
    print(f"[PromNotif] Готово (сигнали: {flagged or 'нема'}).")


def keepalive() -> None:
    """Тримає Prom-кабінетну сесію ТЕПЛОЮ (як Rozetka-keepalive): відкриває кабінет під
    збереженою сесією й ПЕРЕСОХРАНЯЄ storageState (оновлює cookies/токени), щоб не протухала.
    Причина, що сесія падала: scrape() лише ЧИТАВ, ніколи не пересохраняв стан. Запускати по
    таймеру (~30 хв), прихованим запуском (див. задачу PlutusToys_PromCabinetKeepalive)."""
    if not STATE_FILE.exists():
        msg = (f"🚨 prom_notifications_scraper keepalive: нема сесії ({STATE_FILE.name}). "
               f"Запусти раз `python prom_notifications_scraper.py --login`.")
        print(f"[PromNotif] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            page.goto(LOGIN_START_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            if "my.prom.ua" not in page.url or "login" in page.url.lower():
                raise PromNotifError(f"сесію не прийнято — редірект на {page.url} (треба --login)")
            ctx.storage_state(path=str(STATE_FILE))  # пересохраняємо → оновлює сесію (теплою)
            print("[PromNotif] keepalive: сесію оновлено.")
        except (PlaywrightTimeoutError, PromNotifError) as e:
            msg = (f"🚨 prom_notifications_scraper keepalive: сесія протухла/збій ({e}). "
                   f"Перелогінься: `python prom_notifications_scraper.py --login`.")
            print(f"[PromNotif] {msg}", file=sys.stderr)
            _notify(msg)
            sys.exit(1)
        finally:
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless-читання сповіщень кабінету Prom (Playwright + storageState).")
    parser.add_argument("--login", action="store_true", help="Раз: вікно, логін, зберегти сесію.")
    parser.add_argument("--keepalive", action="store_true", help="Тримати сесію теплою (по таймеру ~30 хв).")
    args = parser.parse_args()
    if args.login:
        create_state()
    elif args.keepalive:
        keepalive()
    else:
        scrape()


if __name__ == "__main__":
    main()
