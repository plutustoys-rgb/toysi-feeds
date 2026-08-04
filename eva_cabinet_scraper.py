"""
eva_cabinet_scraper.py — headless-читання кабінету EVA (seller.eva.ua):
баланс магазину (Разом / роялті / платформи) + наповненість (best-effort).

НАВІЩО (2026-08-04, пряме рішення власника «проблема запуску аудиторів», частина 2):
щоденний кабінет-аудит через `claude --print` (Task Scheduler о 8:00) структурно
не працює — headless-сесія Claude не має ні інструментів Claude in Chrome (мосту до
залогіненого браузера), ні `send_telegram_message`. Детермінований Playwright-скрейпер
із ЗБЕРЕЖЕНОЮ сесією (storageState) — єдине, що надійно бігає за розкладом без Claude
й без інтерактивної людини. Це той самий Playwright-патерн, що вже є в
`prom_cabinet_scraper.py`, але з двома відмінностями під EVA:
  1. seller.eva.ua автентифікується через OAuth/сесію, а не прямий login/password —
     тому НЕ логінимось програмно, а перевикористовуємо storageState, створений раз
     інтерактивно самим власником (`--login`). Коли сесія протухне — headless-прогін
     це чесно виявить (редірект на логін) і попросить у Telegram оновити стан.
  2. Головна ціль — БАЛАНС (пряме завдання «контроль балансів на площадках... поки
     просто вивід залишків, без порогів» — власник, 2026-08-01). Наповненість —
     best-effort бонус, не валить прогін, якщо не зчиталась.

БЕЗПЕКА: лише навігація + читання тексту сторінки. ЖОДНИХ кліків запису
(зберегти/відправити/змінити). storageState — секрет (кукі сесії), лежить у
.local_secrets/ (gitignore), НІКОЛИ не в спільній Cowork-папці (правило CLAUDE.md).

ЗАПУСК:
    python eva_cabinet_scraper.py --login   # раз: відкриє вікно, власник логіниться, стан зберігається
    python eva_cabinet_scraper.py           # headless-прогін за розкладом (Task Scheduler / timer)

РЕЗУЛЬТАТ: reports/eva_cabinet_YYYY-MM-DD.md + рядок у balance_history.jsonl
(часовий ряд, як catalog_size_history.jsonl) + Telegram-підсумок (лише вивід
залишків, БЕЗ порогів/алертів — накопичуємо тенденцію).
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
REPORT_DIR = BASE_DIR / "reports"
HISTORY_FILE = BASE_DIR / "balance_history.jsonl"

# storageState (кукі сесії EVA) — секрет, поза git і поза Cowork-папкою.
# Перевизначається змінною EVA_CABINET_STATE_FILE (напр. інший шлях на VPS).
STATE_FILE = Path(
    os.environ.get("EVA_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "eva_cabinet_state.json"))
)

MERCHANT_URL = "https://seller.eva.ua/merchant"
LOGIN_START_URL = "https://seller.eva.ua/"
NAV_TIMEOUT_MS = 30000


class EvaCabinetError(Exception):
    pass


def _save_failure_artifacts(page, prefix: str) -> None:
    """Скріншот при непередбаченому кроці — для живого налагодження, а не
    мовчазний крах (той самий принцип, що prom_cabinet_scraper). HTML НЕ
    зберігаємо: сторінки кабінету можуть містити персональні/сесійні дані."""
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        page.screenshot(path=str(REPORT_DIR / f"eva_cabinet_scraper_failure_{prefix}_{ts}.png"), full_page=True)
    except Exception:
        pass


def create_state() -> None:
    """--login: відкриває ВИДИМЕ вікно, власник логіниться сам (OAuth/2FA — що
    завгодно), тоді натискає Enter, і поточна сесія зберігається у STATE_FILE.
    Жодного пароля в коді/логах — вхід повністю в руках власника."""
    print("[EvaCabinet] Відкриваю вікно кабінету EVA. Залогінься повністю (до сторінки кабінету),")
    print("[EvaCabinet] потім повернись сюди й натисни Enter, щоб зберегти сесію...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(LOGIN_START_URL, timeout=NAV_TIMEOUT_MS)
        try:
            input()  # чекаємо, поки власник залогіниться і підтвердить
        except EOFError:
            print("[EvaCabinet] Немає інтерактивного вводу — --login треба запускати вручну в терміналі.",
                  file=sys.stderr)
            browser.close()
            sys.exit(1)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"[EvaCabinet] Сесію збережено: {STATE_FILE}")
    print("[EvaCabinet] Тепер щоденний прогін `python eva_cabinet_scraper.py` працюватиме headless.")


def _parse_amount(text: str, label: str):
    """Витягує суму після мітки (напр. 'Разом', 'роялті', 'платформи'). EVA
    показує число з пробілом як роздільником тисяч і '₴' після нього, часто на
    наступному рядку від мітки: 'На рахунку роялті:\\n581.41 ₴'. Повертає float
    або None, якщо не знайдено (best-effort — жодна мітка не валить прогін)."""
    # після мітки — будь-які пробіли/двокрапка/перенос рядка, тоді число
    # (цифри + пробіли-роздільники тисяч + опційна коп. частина), тоді ₴.
    m = re.search(label + r"[:\s]*?(\d[\d  ]*(?:[.,]\d{1,2})?)\s*₴", text)
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def read_balance(page) -> dict:
    """Читає баланс магазину зі сторінки /merchant. Кидає EvaCabinetError, якщо
    сесія протухла (редірект на логін/oauth) — це головний сигнал 'онови стан'."""
    page.goto(MERCHANT_URL, timeout=NAV_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
    url = page.url
    if "/merchant" not in url:
        raise EvaCabinetError(f"сесію не прийнято — редірект на {url} (storageState протух, треба --login)")
    text = page.inner_text("body")
    if "Баланс магазину" not in text and "роялті" not in text:
        raise EvaCabinetError("на /merchant не знайдено блок балансу (верстка змінилась або сесія неповна)")
    return {
        "total": _parse_amount(text, r"Разом"),
        "royalty": _parse_amount(text, r"роялті"),
        "platform": _parse_amount(text, r"платформи"),
    }


def read_fullness(page) -> dict:
    """Best-effort наповненість: кількість товарів у розрізах Активні / Модерація
    / З помилками. Рахуємо через таб-лічильники сторінки товарів; якщо не
    зчиталось — None (не валимо прогін, баланс важливіший)."""
    result = {"active": None, "moderation": None, "errors": None}
    try:
        page.goto("https://seller.eva.ua/integrations/items/all", timeout=NAV_TIMEOUT_MS)
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
        text = page.inner_text("body")
        # Лічильники EVA рендерить як «Активні (123)» / «Активні 123» біля вкладок.
        for key, label in (("active", "Активні"), ("moderation", "Модерація"), ("errors", "З помилками")):
            m = re.search(label + r"\D{0,4}(\d[\d  ]*)", text)
            if m:
                try:
                    result[key] = int(m.group(1).replace(" ", "").replace(" ", ""))
                except ValueError:
                    pass
    except Exception as e:
        print(f"[EvaCabinet] наповненість не зчитана (best-effort, не критично): {e}", file=sys.stderr)
    return result


def scrape() -> None:
    if not STATE_FILE.exists():
        msg = (f"🚨 eva_cabinet_scraper: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти раз `python eva_cabinet_scraper.py --login` і залогінься.")
        print(f"[EvaCabinet] {msg}", file=sys.stderr)
        try:
            send_telegram_message(msg)
        except Exception:
            pass
        sys.exit(1)

    balance = None
    fullness = {"active": None, "moderation": None, "errors": None}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            try:
                balance = read_balance(page)
            except (PlaywrightTimeoutError, EvaCabinetError) as e:
                _save_failure_artifacts(page, "balance")
                msg = (f"🚨 eva_cabinet_scraper: не вдалось прочитати баланс EVA: {e}. "
                       f"Якщо сесія протухла — `python eva_cabinet_scraper.py --login`.")
                print(f"[EvaCabinet] {msg}", file=sys.stderr)
                send_telegram_message(msg)
                sys.exit(1)
            fullness = read_fullness(page)
        finally:
            browser.close()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Звіт
    report_path = REPORT_DIR / f"eva_cabinet_{today}.md"
    lines = [f"# Кабінет EVA — автоматичне читання, {now.strftime('%Y-%m-%d %H:%M')}", ""]
    lines.append("## Баланс магазину")
    lines.append(f"- Разом: {balance['total']} ₴")
    lines.append(f"- Роялті: {balance['royalty']} ₴")
    lines.append(f"- Платформи: {balance['platform']} ₴")
    lines.append("")
    lines.append("## Наповненість (best-effort)")
    lines.append(f"- Активні: {fullness['active']}")
    lines.append(f"- Модерація: {fullness['moderation']}")
    lines.append(f"- З помилками: {fullness['errors']}")
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[EvaCabinet] Звіт збережено: {report_path}")

    # Часовий ряд (як catalog_size_history.jsonl — накопичуємо тенденцію)
    row = {"ts": now.isoformat(timespec="seconds"), "platform": "eva",
           "balance_total": balance["total"], "balance_royalty": balance["royalty"],
           "balance_platform": balance["platform"], "active": fullness["active"],
           "moderation": fullness["moderation"], "errors": fullness["errors"]}
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[EvaCabinet] не вдалось дописати історію балансів: {e}", file=sys.stderr)

    # Telegram — лише вивід залишків, БЕЗ порогів (рішення власника 2026-08-01)
    summary = (f"💰 Баланс EVA {today}: разом {balance['total']} ₴ "
               f"(роялті {balance['royalty']} + платформи {balance['platform']}).")
    fl = fullness
    if any(v is not None for v in fl.values()):
        summary += f" Наповненість: активні {fl['active']}, модерація {fl['moderation']}, з помилками {fl['errors']}."
    send_telegram_message(summary)
    print(f"[EvaCabinet] {summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless-читання балансу/наповненості кабінету EVA (Playwright + storageState).")
    parser.add_argument("--login", action="store_true",
                        help="Раз: відкрити вікно, залогінитись вручну, зберегти сесію у storageState.")
    args = parser.parse_args()
    if args.login:
        create_state()
    else:
        scrape()


if __name__ == "__main__":
    main()
