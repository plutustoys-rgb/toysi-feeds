"""
gmc_scraper.py — headless-читання Google Merchant Center (merchants.google.com):
статуси товарів (активні / схвалені / відхилені / очікують) + топ причин відхилення.
Read-only, той самий Playwright+storageState патерн, що кабінет-скрейпери.

НАВІЩО (2026-08-06, прохання власника): GMC дає трафік на сайт plutustoys.com.ua, і
його наповнення НЕ збігається з сайтом — треба бачити, скільки товарів Google схвалив
vs відхилив і ЧОМУ (disapprovals = «некоректність товару»). Наш бік уже проаналізовано:
у GMC йде ~4204 товари (лише ті, для яких резолвнулось посилання на сторінку сайту);
~1800 Prom-топу не потрапляють (self-match ~70%). Але СТАТУС у GMC (active/disapproved)
ми ніде не читаємо — цей скрейпер це закриває.

⚠️ ВЕРСТКА GMC НЕ ЗВІРЕНА (складний Google-SPA, доступу не було при побудові). Перший
--login-прогін ЗАВЖДИ зберігає локальний дамп сирого тексту сторінки (gmc_debug_*.txt)
— по ньому фіналізується парсер (як робили для allo/rozetka). Best-effort парсинг
поки за загальними мітками; точні мітки/URL — після першого дампу.

БЕЗПЕКА: лише навігація + читання тексту. storageState — секрет у .local_secrets/
(gitignore), не в Cowork-папці.

ЗАПУСК:
    python gmc_scraper.py --login   # раз: вікно, логін у Google Merchant, стан збережено
    python gmc_scraper.py           # headless (local_cabinet_audit.ps1)

РЕЗУЛЬТАТ: reports/gmc_YYYY-MM-DD.md + Telegram (якщо не AUDIT_NO_TELEGRAM).
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
# Перший прогін ЗАВЖДИ зберігає сирий текст локально, щоб фіналізувати парсер під
# реальну верстку GMC. Вимикається (ALLO_DEBUG-стиль) після налаштування.
_ALWAYS_DUMP = os.environ.get("GMC_NO_DUMP") != "1"

STATE_FILE = Path(
    os.environ.get("GMC_STATE_FILE", str(BASE_DIR / ".local_secrets" / "gmc_state.json"))
)

GMC_URL = "https://merchants.google.com/"
LOGIN_START_URL = "https://merchants.google.com/"
NAV_TIMEOUT_MS = 45000

# Best-effort мітки статусів (укр. інтерфейс GMC). Уточнити після першого дампу.
STATUS_LABELS = {"active": "Активн", "pending": "Очікує", "disapproved": "Відхилен",
                 "expiring": "Термін дії"}


class GmcError(Exception):
    pass


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        send_telegram_message(msg)
    except Exception as e:
        print(f"[GMC] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _dump_local(prefix: str, text: str, page=None) -> None:
    """Сирий текст (+скрін) у ЛОКАЛЬНУ reports/, НЕ в спільну папку (може містити
    дані акаунта). Для фіналізації парсера під реальну верстку GMC."""
    try:
        d = BASE_DIR / "reports"
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if text is not None:
            (d / f"gmc_debug_{prefix}_{ts}.txt").write_text(text, encoding="utf-8")
        if page is not None:
            page.screenshot(path=str(d / f"gmc_debug_{prefix}_{ts}.png"), full_page=True)
    except Exception:
        pass


def create_state() -> None:
    print("[GMC] Відкриваю merchants.google.com. Залогінься в Google Merchant (може бути 2FA),")
    print("[GMC] дійди до дашборда, потім повернись сюди й натисни Enter...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(LOGIN_START_URL, timeout=NAV_TIMEOUT_MS)
        try:
            input()
        except EOFError:
            print("[GMC] Немає інтерактивного вводу — --login запускай вручну в терміналі.", file=sys.stderr)
            browser.close()
            sys.exit(1)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"[GMC] Сесію збережено: {STATE_FILE}")


def _count_near(text: str, label: str, window: int = 40):
    """Best-effort: ціле число поблизу мітки (до або після, у вікні ±window).
    GMC-верстка невідома — після першого дампу заміню на точний парсер."""
    idx = text.find(label)
    if idx == -1:
        return None
    seg = text[max(0, idx - window): idx + len(label) + window]
    lbl_pos = min(window, idx)
    best, best_dist = None, 10 ** 9
    for m in re.finditer(r"\d[\d\xa0    ,]*", seg):
        raw = re.sub(r"[^\d]", "", m.group(0))
        if not raw:
            continue
        dist = min(abs(m.start() - lbl_pos), abs(m.end() - (lbl_pos + len(label))))
        if dist < best_dist:
            best_dist, best = dist, int(raw)
    return best


def read_gmc(page) -> dict:
    page.goto(GMC_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)  # GMC — важкий SPA
    url = page.url
    if "accounts.google.com" in url or "signin" in url.lower() or "ServiceLogin" in url:
        raise GmcError(f"сесію не прийнято — редірект на логін Google ({url}) (треба --login)")
    text = page.inner_text("body")
    if _ALWAYS_DUMP:
        _dump_local("overview", text, page=page)
    statuses = {k: _count_near(text, lbl) for k, lbl in STATUS_LABELS.items()}
    return {"statuses": statuses, "_text": text}


def scrape() -> None:
    if not STATE_FILE.exists():
        msg = (f"🚨 gmc_scraper: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти раз `python gmc_scraper.py --login`.")
        print(f"[GMC] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            try:
                data = read_gmc(page)
            except (PlaywrightTimeoutError, GmcError) as e:
                try:
                    t = page.inner_text("body")
                except Exception:
                    t = None
                _dump_local("failure", t, page=page)
                msg = (f"🚨 gmc_scraper: не вдалось прочитати GMC: {e}. "
                       f"Якщо сесія протухла — `python gmc_scraper.py --login`.")
                print(f"[GMC] {msg}", file=sys.stderr)
                _notify(msg)
                sys.exit(1)
        finally:
            browser.close()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    st = data["statuses"]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"gmc_{today}.md"
    lines = [f"# Google Merchant Center — {now.strftime('%Y-%m-%d %H:%M')}", "",
             "_Best-effort парсинг (верстка GMC ще уточнюється). Сирий текст — локально._", ""]
    lines.append(f"- Активні: {st['active']}")
    lines.append(f"- Очікують: {st['pending']}")
    lines.append(f"- Відхилені: {st['disapproved']}")
    lines.append(f"- Спливає термін: {st['expiring']}")
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[GMC] Звіт: {report_path}")

    summary = (f"🔵 GMC {today}: активні {st['active']}, відхилені {st['disapproved']}, "
               f"очікують {st['pending']}.")
    _notify(summary)
    print(f"[GMC] {summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Headless-читання Google Merchant Center (Playwright + storageState).")
    parser.add_argument("--login", action="store_true", help="Раз: вікно, логін у Google Merchant, зберегти сесію.")
    args = parser.parse_args()
    if args.login:
        create_state()
    else:
        scrape()


if __name__ == "__main__":
    main()
