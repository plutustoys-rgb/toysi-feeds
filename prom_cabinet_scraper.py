"""
prom_cabinet_scraper.py — автоматичне читання кабінету Prom там, де офіційний
API не дає даних (звіт по імпорту з деталями помилок, дзвіночок "Сповіщення").

АРХІТЕКТУРА (2026-07-25, пряме прохання власниці "хочу щоб усі сповіщення
з Прома ми читали автоматично")
Офіційний Prom API (my.prom.ua/api/v1) НЕ документує ендпоінт для деталей
звіту імпорту чи списку сповіщень кабінету (перевірено: Swagger-документація
не містить таких маршрутів) — prom_catalog_auditor.py вже давно має явний
коментар "ОБМЕЖЕННЯ: API не дає точної причини блокування", і досі
покладався на ручну перевірку власницею. Живий приклад цінності такого
читання (2026-07-25): звіт по імпорту показав "Для 662 товарів автоматично
визначена категорія" — виявилось, що generate_prom_feed.py надсилав
Toysi-власний category_id замість реального Prom ID (виправлено окремим
PR). Без автоматичного читання цього звіту такі знахідки трапляються лише
випадково, коли власниця сама щось помічає й питає.

Тому: Playwright (headless Chromium) заходить у кабінет під логіном/паролем
власниці (PROM_CABINET_LOGIN/PROM_CABINET_PASSWORD у .env — той самий
принцип, що вже є для ROZETKA_USERNAME/ROZETKA_PASSWORD, скрипт сам
логіниться, ніхто зі сторони не бачить пароль) і читає:
1. /cms/import — останній звіт по імпорту (текст цілого блоку, без
   структурного парсингу конкретних категорій помилок — формулювання
   Prom може змінитись, сирий текст стійкіший і дає повну картину).
2. Дзвіночок "Сповіщення" (лічильник немає в звіту API) — best-effort,
   селектор може знадобитись підправити після першого живого прогону
   (немає можливості протестувати без реального логіну).

БЕЗПЕКА: жодних дій запису (жодних кліків "зберегти"/"видалити"/змінити
налаштування) — лише навігація й читання тексту сторінки.

Результат: пишеться в reports/prom_cabinet_notifications_YYYY-MM-DD.md +
короткий підсумок у Telegram. При першому падінні (зміна верстки Prom,
невірний пароль тощо) — скріншот і повний HTML зберігаються в
reports/prom_cabinet_scraper_failure_*.png/html для живого налагодження,
а не мовчазний крах.

Запуск:
    python prom_cabinet_scraper.py
"""
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from telegram_notify import send_telegram_message

load_dotenv()

PROM_CABINET_LOGIN = os.environ.get("PROM_CABINET_LOGIN", "")
PROM_CABINET_PASSWORD = os.environ.get("PROM_CABINET_PASSWORD", "")

BASE_DIR = Path(__file__).parent
REPORT_DIR = BASE_DIR / "reports"

NAV_TIMEOUT_MS = 30000


class PromCabinetError(Exception):
    pass


def _save_failure_artifacts(page, prefix: str) -> None:
    """При будь-якому непередбаченому кроці — скріншот+HTML для живого
    налагодження (той самий принцип, що novapay_statement.py: краще
    зберегти діагностику, ніж мовчки впасти без сліду)."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        page.screenshot(path=str(REPORT_DIR / f"prom_cabinet_scraper_failure_{prefix}_{ts}.png"), full_page=True)
    except Exception:
        pass
    try:
        (REPORT_DIR / f"prom_cabinet_scraper_failure_{prefix}_{ts}.html").write_text(
            page.content(), encoding="utf-8"
        )
    except Exception:
        pass


def login(page) -> None:
    page.goto("https://prom.ua/", timeout=NAV_TIMEOUT_MS)
    # Резистентний до дрібних змін верстки пошук — за видимим текстом, не
    # за CSS-класами (які значно частіше змінюються при рефакторингу фронтенду).
    login_trigger = page.get_by_text("Увійти", exact=False).first
    login_trigger.click(timeout=NAV_TIMEOUT_MS)

    email_input = page.locator(
        'input[type="email"], input[name="login"], input[placeholder*="Email" i], input[placeholder*="логін" i]'
    ).first
    email_input.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)
    email_input.fill(PROM_CABINET_LOGIN)

    password_input = page.locator('input[type="password"]').first
    password_input.fill(PROM_CABINET_PASSWORD)

    submit_button = page.get_by_role("button", name="Увійти").first
    submit_button.click(timeout=NAV_TIMEOUT_MS)

    page.wait_for_url("**my.prom.ua/**", timeout=NAV_TIMEOUT_MS)


def read_import_report(page) -> str:
    page.goto("https://my.prom.ua/cms/import", timeout=NAV_TIMEOUT_MS)
    page.wait_for_selector("text=Звіт по імпорту", timeout=NAV_TIMEOUT_MS)
    body_text = page.inner_text("body")

    marker = "Звіт по імпорту"
    start = body_text.find(marker)
    if start == -1:
        raise PromCabinetError("Не знайдено блок 'Звіт по імпорту' на сторінці /cms/import")
    # Один звіт — до наступного входження того самого маркера (наступний
    # звіт в історії) чи до "Завантажити ще" (кінець видимого списку).
    next_marker = body_text.find(marker, start + len(marker))
    end_marker = body_text.find("Завантажити ще", start)
    end_candidates = [x for x in (next_marker, end_marker) if x != -1]
    end = min(end_candidates) if end_candidates else len(body_text)
    return body_text[start:end].strip()


def read_notifications(page) -> str | None:
    """Best-effort — селектор дзвіночка не перевірено живо (немає способу
    протестувати без реального логіну власниці). Повертає None замість
    падіння всього скрипта, якщо цей конкретний крок не спрацював —
    звіт по імпорту вище важливіший і не повинен залежати від цього."""
    try:
        bell = page.locator('[aria-label*="повідомлення" i], [aria-label*="сповіщ" i], [class*="notif" i]').first
        bell.click(timeout=10000)
        page.wait_for_timeout(1000)
        return page.inner_text("body")[:3000]
    except Exception as e:
        print(f"[PromCabinet] Сповіщення (дзвіночок) — не вдалось прочитати (best-effort, не критично): {e}",
              file=sys.stderr)
        return None


def main() -> None:
    if not (PROM_CABINET_LOGIN and PROM_CABINET_PASSWORD):
        print("[PromCabinet] PROM_CABINET_LOGIN/PROM_CABINET_PASSWORD не задані в .env — зупиняюсь.", file=sys.stderr)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            try:
                login(page)
            except (PlaywrightTimeoutError, Exception) as e:
                _save_failure_artifacts(page, "login")
                message = (
                    f"🚨 prom_cabinet_scraper.py: не вдалось увійти в кабінет Prom: {e} — "
                    f"скріншот/HTML збережено в reports/ для налагодження."
                )
                print(f"[PromCabinet] {message}", file=sys.stderr)
                send_telegram_message(message)
                sys.exit(1)

            try:
                import_report = read_import_report(page)
            except (PlaywrightTimeoutError, PromCabinetError) as e:
                _save_failure_artifacts(page, "import_report")
                message = (
                    f"🚨 prom_cabinet_scraper.py: не вдалось прочитати звіт по імпорту: {e} — "
                    f"скріншот/HTML збережено в reports/ для налагодження."
                )
                print(f"[PromCabinet] {message}", file=sys.stderr)
                send_telegram_message(message)
                sys.exit(1)

            notifications = read_notifications(page)
        finally:
            browser.close()

    today = datetime.now().strftime("%Y-%m-%d")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"prom_cabinet_notifications_{today}.md"
    lines = [f"# Кабінет Prom — автоматичне читання, {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    lines.append("## Звіт по імпорту (останній)")
    lines.append("")
    lines.append(import_report)
    lines.append("")
    if notifications:
        lines.append("## Сповіщення (дзвіночок)")
        lines.append("")
        lines.append(notifications)
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[PromCabinet] Звіт збережено: {report_path}")

    # Telegram: лише коротка вказівка на проблемні сигнали (той самий принцип,
    # що й в іншому місці проєкту — результати, не "піди перевір сам").
    warning_markers = ("не завантажені через помилки", "невірні дані", "автоматично визначена категорія")
    flagged = [m for m in warning_markers if m in import_report]
    if flagged:
        summary = (
            f"📋 Кабінет Prom: у звіті по імпорту знайдено сигнали — {', '.join(flagged)}. "
            f"Повний текст: {report_path.name} (спільна папка звітів)."
        )
    else:
        summary = "📋 Кабінет Prom: звіт по імпорту чистий, без сигналів про проблеми."
    send_telegram_message(summary)
    print(f"[PromCabinet] {summary}")


if __name__ == "__main__":
    main()
