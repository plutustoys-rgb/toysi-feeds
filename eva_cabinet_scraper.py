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
# Куди писати звіт+історію. За замовч. локально в репо; AUDIT_REPORT_DIR перевизначає —
# локальний прогін (Task Scheduler) пише ПРЯМО у спільну Cowork-папку (рішення власника
# 2026-08-04, «без телеграма, у папку»). AUDIT_NO_TELEGRAM=1 — без Telegram.
_OUT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (BASE_DIR / "reports"))
REPORT_DIR = _OUT_DIR
HISTORY_FILE = _OUT_DIR / "balance_history.jsonl"
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

# storageState (кукі сесії EVA) — секрет, поза git і поза Cowork-папкою.
# Перевизначається змінною EVA_CABINET_STATE_FILE (напр. інший шлях на VPS).
STATE_FILE = Path(
    os.environ.get("EVA_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "eva_cabinet_state.json"))
)

MERCHANT_URL = "https://seller.eva.ua/merchant"
LOGIN_START_URL = "https://seller.eva.ua/"
NAV_TIMEOUT_MS = 30000

# WRITE-дія: повний імпорт (нові товари → модерація). Наказ власника 2026-08-31 (через SEO):
# EVA не мала автоциклу повного імпорту — робилось руками. Автоімпорт EVA (той, що в кабінеті
# «Автоматичний») тягне ЛИШЕ оновлення цін/залишків (Оновлені N, Нові 0) — новий товар на
# модерацію дає тільки цей повний імпорт за посиланням. URL фіда = опублікований eva_feed.xml.
IMPORTS_NEW_URL = "https://seller.eva.ua/integrations/imports/new"
EVA_FEED_URL = os.environ.get(
    "EVA_FEED_URL",
    "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/eva_feed.xml",
)


class EvaCabinetError(Exception):
    pass


def _save_failure_artifacts(page, prefix: str) -> None:
    """Скріншот при непередбаченому кроці — для живого налагодження, а не
    мовчазний крах (той самий принцип, що prom_cabinet_scraper). HTML НЕ
    зберігаємо: сторінки кабінету можуть містити персональні/сесійні дані."""
    try:
        # ЛОКАЛЬНА reports/, НЕ AUDIT_REPORT_DIR: скрін кабінету містить персональні
        # дані, не кладемо його у спільну Cowork-папку (аудит #220 нит).
        fail_dir = BASE_DIR / "reports"
        fail_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        page.screenshot(path=str(fail_dir / f"eva_cabinet_scraper_failure_{prefix}_{ts}.png"), full_page=True)
    except Exception:
        pass


def _notify(msg: str) -> None:
    """Telegram best-effort — недоступність Telegram НЕ повинна валити прогін
    трейсбеком (звіт+історія вже збережені на диск до цього кроку). AUDIT_NO_TELEGRAM=1
    → взагалі не шлемо (локальна доставка у Cowork-папку замість Telegram)."""
    if _NO_TELEGRAM:
        return
    try:
        send_telegram_message(msg)
    except Exception as e:
        print(f"[EvaCabinet] Telegram не надіслано (не критично): {e}", file=sys.stderr)


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


def _ensure_session(page) -> None:
    """Протухла сесія → EvaCabinetError (щоб автоцикл НЕ мовчав про це, як 10-денний
    мовчазний збій EVA, CODE_LOG 29.08). Спершу ДОЧЕКАТИСЬ клієнтського редіректу на
    логін/oauth (networkidle, best-effort) — інакше гола перевірка URL одразу його не бачить
    (той самий клас бага, що спіймано на ALLO живим тестом 2026-08-31), потім перевірити URL."""
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    u = (page.url or "").lower()
    if "login" in u or "oauth" in u or "/auth" in u or "sign_in" in u:
        raise EvaCabinetError(f"сесію не прийнято — редірект на {page.url} (storageState протух, треба --login)")


def trigger_full_import(page, feed_url: str, apply: bool) -> dict:
    """Частина C автоциклу: повний імпорт EVA за посиланням (нові товари → модерація).
    Звірено живо 2026-08-31 на /integrations/imports/new: radio «через посилання» (за
    замовч. вибраний) + поле «Введіть адресу XML файлу» + чекбокс «Відправити нові товари
    з співставленою категорією на модерацію» + кнопка «Почати».

    БЕЗ apply — DRY-RUN: заповнює форму (radio+url+чекбокс), але НЕ тисне «Почати»."""
    page.goto(IMPORTS_NEW_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    _ensure_session(page)
    res = {"feed_url": feed_url, "moderation_checked": None, "submitted": False}

    # radio «через посилання» (не «через завантаження файлу») — має бути вибраний за замовч.,
    # але явно ставимо, щоб не залежати від дефолта верстки.
    try:
        radio = page.get_by_role("radio", name=re.compile("через посилання"))
        if radio.count() and not radio.first.is_checked():
            radio.first.check()
    except Exception as e:
        print(f"[EvaCabinet] radio 'через посилання' не встановлено ({e})", file=sys.stderr)

    # поле URL
    box = page.get_by_role("textbox", name="Введіть адресу XML файлу")
    if box.count() == 0:
        raise EvaCabinetError("не знайдено поле 'Введіть адресу XML файлу' (верстка змінилась або сесія неповна)")
    box.first.fill(feed_url)

    # чекбокс «Відправити нові товари з співставленою категорією на модерацію» — саме він
    # робить імпорт ПОВНИМ (нові → модерація), а не лише оновленням.
    try:
        cb = page.get_by_role("checkbox")
        if cb.count():
            if not cb.first.is_checked():
                cb.first.check()
            res["moderation_checked"] = cb.first.is_checked()
    except Exception as e:
        print(f"[EvaCabinet] чекбокс модерації не встановлено ({e})", file=sys.stderr)

    if apply:
        btn = page.get_by_role("button", name="Почати")
        if btn.count() == 0 or not btn.first.is_enabled():
            raise EvaCabinetError("кнопка 'Почати' відсутня/неактивна — форму не подано")
        btn.first.click()
        page.wait_for_timeout(4000)  # старт імпорту (запис зʼявляється в «Історія імпорту»)
        res["submitted"] = True
    return res


def run_full_import(apply: bool) -> None:
    """Оркестратор повного імпорту EVA. Одна сесія браузера; протухла сесія/збій → сигнал."""
    if not STATE_FILE.exists():
        msg = (f"🚨 eva_cabinet_scraper: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти раз `python eva_cabinet_scraper.py --login` і залогінься.")
        print(f"[EvaCabinet] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)

    result = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            result = trigger_full_import(page, EVA_FEED_URL, apply)
        except (PlaywrightTimeoutError, EvaCabinetError) as e:
            _save_failure_artifacts(page, "import")
            msg = (f"🚨 eva повний імпорт не вдався: {e}. "
                   f"Якщо сесія протухла — `python eva_cabinet_scraper.py --login`.")
            print(f"[EvaCabinet] {msg}", file=sys.stderr)
            _notify(msg)
        finally:
            browser.close()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    mode = "APPLY" if apply else "DRY-RUN"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"# EVA повний імпорт ({mode}), {now.strftime('%Y-%m-%d %H:%M')}", ""]
    if result is not None:
        lines.append(f"- Фід: {result['feed_url']}")
        lines.append(f"- Чекбокс 'на модерацію': {result['moderation_checked']}")
        lines.append(f"- Подано ('Почати'): {'так' if result['submitted'] else 'ні (dry-run)'}")
    else:
        lines.append("- ❌ Імпорт не запущено (див. сигнал/скрін збою).")
    report_path = REPORT_DIR / f"eva_full_import_{today}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[EvaCabinet] Звіт імпорту: {report_path}")

    if result is not None and result["submitted"]:
        _notify(f"🟣 EVA повний імпорт {today} ({mode}): подано (чекбокс модерації {result['moderation_checked']}).")


def scrape() -> None:
    if not STATE_FILE.exists():
        msg = (f"🚨 eva_cabinet_scraper: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти раз `python eva_cabinet_scraper.py --login` і залогінься.")
        print(f"[EvaCabinet] {msg}", file=sys.stderr)
        _notify(msg)
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
                _notify(msg)
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
    _notify(summary)
    print(f"[EvaCabinet] {summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless-читання балансу/наповненості кабінету EVA (Playwright + storageState).")
    parser.add_argument("--login", action="store_true",
                        help="Раз: відкрити вікно, залогінитись вручну, зберегти сесію у storageState.")
    parser.add_argument("--full-import", action="store_true",
                        help="Повний імпорт EVA за посиланням (нові товари → модерація) — автоцикл.")
    parser.add_argument("--apply", action="store_true",
                        help="Реально подати ('Почати'). Без нього — DRY-RUN: заповнити форму, не подавати.")
    args = parser.parse_args()
    if args.login:
        create_state()
    elif args.full_import:
        run_full_import(apply=args.apply)
    else:
        scrape()


if __name__ == "__main__":
    main()
