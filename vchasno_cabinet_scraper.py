"""
vchasno_cabinet_scraper.py — headless-читання й завантаження нових документів
кабінету Вчасно.ЕДО (edo.vchasno.ua) → документи_КОДВ, БЕЗ живої присутності агента.

НАВІЩО (власник, 2026-09-19, пряма відмова хибного висновку «інтерактивна перевірка
через Claude in Chrome — достатньо»): «я не планую контролювати тебе щоб ти вчасно
перевіряв нові документи, автоматизуй процес». Живий перегляд через Claude in Chrome
довів ЩО можна прочитати (перевірено 2026-09-19: /app/documents?folder_id=6008 — повний
список 22 зовнішніх документів БЕЗ логіну, сесія жива в браузері власника), але це працює
ЛИШЕ коли агент фізично в інтерактивній сесії — не автоматизація. Цей скрипт — той самий
Playwright+storageState патерн, що eva_cabinet_scraper.py/prom_cabinet_scraper.py:
логін РАЗ інтерактивно власником (`--login`, Google OAuth — Вчасно НЕ КЕП-гейтований на
вході, перевірено живо 2026-09-05/09-19), далі headless за розкладом.

ЩО РОБИТЬ: список зовнішніх документів (дата/тип/номер/статус/контрагент/ЄДРПОУ) →
для кожного, ще не завантаженого локально (за номером документа, крос-звірка з
документи_КОДВ/*/*/), качає файл (page.request.get на /downloads/<id>/print — той самий
безпечний, НЕ-підписний шлях, що перевірено живо; НІКОЛИ не торкається кнопок підпис/
погодження/відхилення) і кладе в правильну підтеку за ЄДРПОУ контрагента.

БЕЗПЕКА: лише навігація + читання + завантаження вже готового файлу за прямим посиланням.
ЖОДНИХ кліків підпису/погодження/відхилення — це юридично зобов'язуючі дії, не для
скрипта. storageState — секрет, .local_secrets/ (gitignore), НІКОЛИ в Cowork.

ЗАПУСК:
    python vchasno_cabinet_scraper.py --login   # раз: відкриє вікно, власник логіниться Google-акаунтом
    python vchasno_cabinet_scraper.py           # headless: нові документи → документи_КОДВ
    python vchasno_cabinet_scraper.py --keepalive
"""
import argparse
import glob
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
COWORK_DIR = Path(os.environ.get("PLUTUS_COWORK_DIR",
                                 r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
DOCS_DIR = COWORK_DIR / "документи_КОДВ"
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

STATE_FILE = Path(
    os.environ.get("VCHASNO_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "vchasno_cabinet_state.json")))

LOGIN_START_URL = "https://edo.vchasno.ua/"
# Тека "Зовнішні документи" — перевірено живо 2026-09-19 (Claude in Chrome, жива сесія
# власника): показує ВСІ зовнішні документи одним списком, найновіші зверху.
DOCS_URL = "https://edo.vchasno.ua/app/documents?folder_id=6008"
NAV_TIMEOUT_MS = 30000

# ЄДРПОУ контрагента → (підтека документи_КОДВ, префікс файлу). Звірено живо 2026-09-19
# на реальному списку документів кабінету.
_COUNTERPARTY_ROUTING = {
    "31316718": ("НоваПошта", "np"),
    "43170392": ("RozetkaPay", "rozetkapay"),
    "32007740": ("EVA_akty", "eva"),
    "30012848": ("ALLO", "allo"),
    "36507036": ("Prom", "prom"),
    "33584049": ("Rozetka", "rozetka"),
    "38324133": ("NovaPay", "novapay"),
    "3731604484": ("HostIQ", "hostiq"),  # Ділай Софія Тарасівна — підписант HostIQ.ua
}


class VchasnoCabinetError(Exception):
    pass


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[VchasnoCabinet] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def create_state() -> None:
    """--login: відкриває ВИДИМЕ вікно, власник логіниться сам (Google OAuth — Вчасно
    НЕ КЕП-гейтований на вході, лише на підпис, перевірено живо), тоді Enter — сесія
    зберігається. Жодного пароля в коді/логах."""
    print("[VchasnoCabinet] Відкриваю вікно кабінету Вчасно. Залогінься повністю (Google-акаунт),")
    print("[VchasnoCabinet] потім повернись сюди й натисни Enter, щоб зберегти сесію...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(LOGIN_START_URL, timeout=NAV_TIMEOUT_MS)
        try:
            input()
        except EOFError:
            print("[VchasnoCabinet] Немає інтерактивного вводу — --login треба запускати вручну в терміналі.",
                  file=sys.stderr)
            browser.close()
            sys.exit(1)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"[VchasnoCabinet] Сесію збережено: {STATE_FILE}")
    print("[VchasnoCabinet] Тепер `python vchasno_cabinet_scraper.py` (без прапорців) працюватиме headless.")


def _ensure_session(page) -> None:
    """Той самий принцип, що eva_cabinet_scraper._ensure_session (аудит PR #567) —
    дочекатись клієнтського редіректу ПЕРЕД перевіркою URL, ширший набір маркерів
    (login/oauth/auth/sign_in), не гола перевірка одразу після goto()."""
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    u = (page.url or "").lower()
    if "login" in u or "oauth" in u or "/auth" in u or "sign_in" in u or "myaccount.vchasno" in u:
        raise VchasnoCabinetError(f"сесію не прийнято — редірект на {page.url} (storageState протух, треба --login)")


def keepalive() -> None:
    """Тримає сесію теплою (той самий патерн, що eva_cabinet_scraper.py --keepalive,
    PR #290/#292/#567) — заходить під збереженою сесією й ПЕРЕСОХРАНЯЄ storageState."""
    if not STATE_FILE.exists():
        msg = (f"🚨 vchasno_cabinet_scraper keepalive: нема сесії ({STATE_FILE.name}). "
               f"Запусти раз `python vchasno_cabinet_scraper.py --login`.")
        print(f"[VchasnoCabinet] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            page.goto(DOCS_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            _ensure_session(page)
            ctx.storage_state(path=str(STATE_FILE))
            print("[VchasnoCabinet] keepalive: сесію оновлено.")
        except (PlaywrightTimeoutError, VchasnoCabinetError) as e:
            msg = (f"🚨 vchasno_cabinet_scraper keepalive: сесія протухла/збій ({e}). "
                   f"Перелогінься: `python vchasno_cabinet_scraper.py --login`.")
            print(f"[VchasnoCabinet] {msg}", file=sys.stderr)
            _notify(msg)
            sys.exit(1)
        finally:
            browser.close()


# Рядок таблиці на сторінці (перевірено живо 2026-09-19, get_page_text()): дата, [тип
# документа], номер, статус, компанія, ЄДРПОУ/ІПН, [зайвий рядок], контакт. Кількість
# полів МІНЛИВА — реальні приклади того самого прогону: (а) запис HostIQ (1375492)
# не має окремого "типу", лише [дата, номер, статус, компанія, єдрпоу, контакт] —
# 6 полів, не 7; (б) запис ALLO (П6231) має ЗАЙВИЙ рядок "Помилка розпізнавання" між
# єдрпоу і контактом. Фіксований офсет ламається на обох — якір тепер СТАТУС (відоме
# з обмеженого словника), а не позиція.
_DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{2}$")
_EDRPOU_RE = re.compile(r"^\d{8,10}$")
_STATUS_VALUES = {
    "Підписаний всіма", "Очікує підпису контрагента", "Отриманий вами",
    "Надісланий контрагенту", "Очікує вашого підпису", "Відхилений",
}
_STATUS_SEARCH_WINDOW = 4   # макс. рядків між датою і статусом (тип+номер, 0-2 рядки)
_EDRPOU_SEARCH_WINDOW = 3   # макс. рядків між компанією і ЄДРПОУ (зазвичай 1)


def parse_document_rows(lines: list) -> list:
    """Чиста функція (тестована без Playwright, на РЕАЛЬНОМУ захопленому тексті —
    test_vchasno_cabinet_scraper.py): рядки тексту сторінки → список документів.
    Якір — РЯДОК-ДАТУ (dd.mm.yy), від нього шукає найближчий рядок-СТАТУС (з відомого
    словника) — усе між ними (0-2 рядки) вважає [тип?, номер] із НОМЕРОМ завжди
    останнім перед статусом (єдине, що важливо для sync_new_documents — тип не
    використовується). Після статусу — компанія, тоді найближчий рядок-ЄДРПОУ
    (8-10 цифр, може бути не одразу — «Помилка розпізнавання» іноді встряє), тоді
    контакт. Запис, де статус чи ЄДРПОУ не знайдені у вікні пошуку — пропускається
    (не вигадує дані), сканування продовжується з наступного рядка."""
    docs = []
    n = len(lines)
    i = 0
    while i < n:
        if not _DATE_RE.match(lines[i].strip()):
            i += 1
            continue
        date = lines[i].strip()
        status_idx = None
        for j in range(i + 1, min(i + 1 + _STATUS_SEARCH_WINDOW, n)):
            if lines[j].strip() in _STATUS_VALUES:
                status_idx = j
                break
        if status_idx is None or status_idx == i + 1:
            i += 1  # немає номера/типу між датою і статусом — не валідний запис
            continue
        number = lines[status_idx - 1].strip()
        status = lines[status_idx].strip()
        if status_idx + 1 >= n:
            i += 1
            continue
        company = lines[status_idx + 1].strip()
        edrpou_idx = None
        for j in range(status_idx + 2, min(status_idx + 2 + _EDRPOU_SEARCH_WINDOW, n)):
            if _EDRPOU_RE.match(lines[j].strip()):
                edrpou_idx = j
                break
        if edrpou_idx is None:
            i = status_idx + 1  # запис без розпізнаного ЄДРПОУ — пропускаємо, не вигадуємо
            continue
        contact = lines[edrpou_idx + 1].strip() if edrpou_idx + 1 < n else ""
        docs.append({
            "date": date, "number": number, "status": status, "company": company,
            "edrpou": lines[edrpou_idx].strip(), "contact": contact,
        })
        i = edrpou_idx + 2
    return docs


def list_external_documents(page) -> list:
    """Живий список — навігація + текст сторінки → parse_document_rows()."""
    page.goto(DOCS_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    _ensure_session(page)
    text = page.inner_text("body")
    lines = [l for l in text.split("\n") if l.strip()]
    return parse_document_rows(lines)


def _already_downloaded(number: str) -> bool:
    """Крос-звірка з локальним архівом за номером документа в назві файлу (той самий
    принцип, що vchasno_akty_kandydaty.py читає для kandydaty)."""
    safe = re.escape(number)
    pattern = str(DOCS_DIR / "*" / "*" / f"*{number}*")
    return len(glob.glob(pattern)) > 0


def _doc_id_from_href(href: str) -> str:
    m = re.search(r"/documents/([0-9a-f-]{36})", href)
    return m.group(1) if m else ""


def sync_new_documents(page) -> dict:
    """Основний прогін: список → фільтр «ще не завантажено» → скачати кожен за
    прямим read-only посиланням /downloads/<id>/print (перевірено живо: PDF- і
    XML-документи обидва віддаються цим шляхом; НЕ /pdf-viewer чи /xml-viewer —
    ті самі URL, які кнопка «Роздрукувати» відкриває, БЕЗ кліку кнопок підпису)."""
    docs = list_external_documents(page)
    downloaded, skipped, failed = [], [], []
    for d in docs:
        number = d["number"]
        if not number or _already_downloaded(number):
            skipped.append(number)
            continue
        route = _COUNTERPARTY_ROUTING.get(d["edrpou"])
        if not route:
            failed.append((number, f"невідомий ЄДРПОУ {d['edrpou']} ({d['company']}) — маршрут не заведено"))
            continue
        subdir, prefix = route
        try:
            href = page.get_by_text(number, exact=False).first.get_attribute("href") or ""
            doc_id = _doc_id_from_href(href)
            if not doc_id:
                # Фолбек: перейти по рядку в UI, взяти doc_id з URL (гілка на випадок,
                # якщо href недоступний напряму з рядка таблиці).
                page.get_by_text(number, exact=False).first.click()
                page.wait_for_timeout(2000)
                doc_id = _doc_id_from_href(page.url)
            if not doc_id:
                failed.append((number, "не вдалось визначити id документа"))
                continue
            resp = page.request.get(f"https://edo.vchasno.ua/downloads/{doc_id}/print", timeout=NAV_TIMEOUT_MS)
            if resp.status != 200:
                failed.append((number, f"HTTP {resp.status}"))
                continue
            body = resp.body()
            ext = ".pdf" if body[:400].find(b"%PDF-") != -1 else ".xml.json"
            try:
                date_iso = datetime.strptime(d["date"], "%d.%m.%y").strftime("%Y-%m-%d")
            except ValueError:
                date_iso = datetime.now().strftime("%Y-%m-%d")
            month_dir = DOCS_DIR / date_iso[:7] / subdir
            month_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{date_iso}_{prefix}_akt_{number}{ext}"
            (month_dir / filename).write_bytes(body)
            downloaded.append(filename)
            list_external_documents  # noqa: (тримаємо посилання на функцію для лінтера — не використання)
            page.goto(DOCS_URL, timeout=NAV_TIMEOUT_MS)  # повернутись до списку для наступної ітерації
            page.wait_for_timeout(1500)
        except Exception as e:  # noqa: BLE001
            failed.append((number, str(e)))
    return {"downloaded": downloaded, "skipped": skipped, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Headless-читання й завантаження нових документів Вчасно (Playwright + storageState).")
    parser.add_argument("--login", action="store_true", help="Раз: відкрити вікно, залогінитись Google-акаунтом, зберегти сесію.")
    parser.add_argument("--keepalive", action="store_true", help="Тримати сесію теплою (по таймеру).")
    args = parser.parse_args()

    if args.login:
        create_state()
        return 0
    if args.keepalive:
        keepalive()
        return 0

    if not STATE_FILE.exists():
        msg = (f"🚨 vchasno_cabinet_scraper: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти раз `python vchasno_cabinet_scraper.py --login` і залогінься.")
        print(f"[VchasnoCabinet] {msg}", file=sys.stderr)
        _notify(msg)
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            result = sync_new_documents(page)
        except (PlaywrightTimeoutError, VchasnoCabinetError) as e:
            msg = (f"🚨 vchasno_cabinet_scraper: збій ({e}). "
                   f"Якщо сесія протухла — `python vchasno_cabinet_scraper.py --login`.")
            print(f"[VchasnoCabinet] {msg}", file=sys.stderr)
            _notify(msg)
            return 1
        finally:
            browser.close()

    print(f"[VchasnoCabinet] завантажено {len(result['downloaded'])}: {result['downloaded']}")
    if result["failed"]:
        print(f"[VchasnoCabinet] ⚠️ не вдалось завантажити {len(result['failed'])}: {result['failed']}", file=sys.stderr)
        _notify(f"⚠️ vchasno_cabinet_scraper: {len(result['failed'])} документів не завантажено — "
                f"перевір документи_КОДВ вручну ({[n for n, _ in result['failed']]}).")
    if result["downloaded"]:
        _notify(f"📄 Вчасно: {len(result['downloaded'])} нових документів завантажено в документи_КОДВ.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
