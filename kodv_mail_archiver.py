"""kodv_mail_archiver.py — ЛОКАЛЬНИЙ архіватор первинних документів КОДВ із пошти.

Задача власника (2026-08-29, канал КОДВ запис 20): 3 headless-«рутини» КОДВ (LLM-сесії
за розкладом) скасовано → переробити на детерміновані скрипти. Цей скрипт замінює ДВІ
архіваторні рутини — `kodv-novapay-archiver` і `kodv-novaposhta-akt-archiver`:
завантажує з пошти `plutustoys.novapay@gmail.com` (та сама скринька для обох, підтвердив
власник) нові первинні документи й розкладає у локальну теку бухгалтера
`документи_КОДВ/YYYY-MM/<Джерело>/`:
  - «Реєстр переказів №XXX» (.xlsx від NovaPay) → `.../NovaPay/`
  - акти звірки / реєстри Нової Пошти (.pdf/.xlsx)   → `.../НоваПошта/`

ЧОМУ ОКРЕМИЙ ЛОКАЛЬНИЙ СКРИПТ, А НЕ ЧАСТИНА novapay_statement.py:
  1. `документи_КОДВ/` — ЛОКАЛЬНА тека (там книга + журнал бухгалтера), а
     `novapay_statement.py` крутиться на VPS і в локальну теку не запише.
  2. `novapay_statement.py` (реконсиляція замовлень) читає ту саму скриньку через
     UNSEEN + ПОЗНАЧАЄ листи прочитаними (\\Seen). Другий UNSEEN-читач їх би не побачив
     (гонка за Seen). Тому цей архіватор READ-ONLY: читає через BODY.PEEK[] (НЕ ставить
     \\Seen) і має ВЛАСНИЙ дедуп-курсор — не конфліктує з реконсиляцією, обидва бачать усе.

ЩО ЦЕЙ СКРИПТ НЕ РОБИТЬ (свідома межа — книгу пише лише бухгалтер, податковий документ):
  не зіставляє з книгою, не вирішує визнання доходу, не пише `KODV_PlutusToys_2026.xlsx`.
  Лише кладе файл-первинку в правильну теку/місяць. Рішення «чи це рядок книги» — за
  роллю «агент-бухгалтер» (як і кандидати Rozetka/EVA-леджерів).

Креди: NOVAPAY_IMAP_EMAIL / NOVAPAY_IMAP_APP_PASSWORD (ті самі, що novapay_statement на VPS —
локально треба додати у .env). Без них скрипт М'ЯКО виходить (код 0), не валить ланцюг.
"""
import email
import imaplib
import json
import os
import sys
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

BASE_DIR = Path(__file__).parent
COWORK_DIR = Path(os.environ.get(
    "PLUTUS_COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
KODV_DOCS_DIR = COWORK_DIR / "документи_КОДВ"

IMAP_EMAIL = os.environ.get("NOVAPAY_IMAP_EMAIL", "")
IMAP_APP_PASSWORD = os.environ.get("NOVAPAY_IMAP_APP_PASSWORD", "")
IMAP_HOST = os.environ.get("NOVAPAY_IMAP_HOST", "imap.gmail.com")
# Теки/мітки для сканування. Тільки ASCII-назви (imaplib вимагає ASCII; локалізована
# Gmail-тека «Вся пошта» — кирилиця, її SELECT падає з UnicodeEncodeError, тому All Mail
# НЕ використовуємо). За замовчуванням INBOX — живий dry-run 2026-08-29 підтвердив, що і
# реєстри NovaPay (від erp-backoffice-mailer@novapay.ua), і акти НП (від automailer@novaposhta.ua)
# приходять у INBOX (окремої мітки «NovaPay» у цій скриньці немає). Дедуп між теками —
# природний через курсор. Якщо документи виявляться під міткою — додати її в KODV_MAIL_FOLDERS.
FOLDERS = [f.strip() for f in os.environ.get("KODV_MAIL_FOLDERS", "INBOX").split(",") if f.strip()]
LOOKBACK_DAYS = int(os.environ.get("KODV_MAIL_LOOKBACK_DAYS", "60"))

CURSOR_FILE = BASE_DIR / ".local_secrets" / "kodv_mail_archiver_cursor.json"
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

# Класифікація вкладень за джерелом. Маркери в НИЖНЬОМУ регістрі; перевіряємо і в імені
# файлу, і в темі листа, і у відправнику — стійкіше до варіацій експорту.
_ATTACH_EXT = (".xlsx", ".xls", ".pdf")
_NOVAPAY_MARKERS = ("реєстр переказів", "реестр переводов", "novapay", "нова пей")
_NP_AKT_MARKERS = ("акт звірки", "акт сверки", "акт звірення", "реєстр нп",
                   "нова пошта", "новапошта", "novaposhta", "nova poshta", "акт-звірка")
# FC/RozetkaPay — щоденний реєстр виплат (обслуговує і Prom-оплату, і Rozetka-картки, один
# процесор). Ім'я файлу: «Реєстр платежів ФОП Чечетенко Олександр Юрійович_YYYY-MM-DD (0).xlsx».
# Відрізняється від NovaPay «реєстр ПЕРЕказів» словом «ПЛАтежів». Запит бухгалтера 2026-08-31
# (сторно 17 днів висіло непоміченим). НЕ дублювати старою назвою «Rozetka» — окрема тека RozetkaPay.
_ROZETKAPAY_MARKERS = ("реєстр платежів", "реестр платежей")


def _log(msg: str) -> None:
    print(f"[KODVmail] {msg}")


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        sys.path.insert(0, str(BASE_DIR))
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001 — сповіщення не критичне
        print(f"[KODVmail] Telegram не надіслано: {e}", file=sys.stderr)


def _decode_mime_words(raw: str) -> str:
    if not raw:
        return ""
    return "".join(
        part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part
        for part, enc in decode_header(raw)
    )


def _load_cursor() -> set:
    try:
        return set(json.loads(CURSOR_FILE.read_text(encoding="utf-8")).get("saved", []))
    except (OSError, ValueError):
        return set()


def _save_cursor(saved: set) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(
        json.dumps({"saved": sorted(saved), "updated": datetime.now(timezone.utc).isoformat()},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def _classify(filename_l: str, subject_l: str, sender_l: str) -> str | None:
    """Джерело для вкладення: 'RozetkaPay' / 'NovaPay' / 'НоваПошта' / None (не наш документ).
    RozetkaPay перевіряємо ПЕРШИМ — його маркер «реєстр платежів» специфічніший і не колізить із
    NovaPay «реєстр переказів»."""
    hay = f"{filename_l} {subject_l} {sender_l}"
    # RozetkaPay = «реєстр платежів [ФОП Чечетенко Олександр Юрійович]». ВИКЛЮЧАЄМО легасі-файли
    # «реєстр платежів КОНТРАГЕНТА Чечетенко О.Ю.» (інші документи), щоб вони не затінили денний
    # реєстр у теці (аудит #464: _newest_registry бере найновіший xlsx).
    if any(m in hay for m in _ROZETKAPAY_MARKERS) and "контрагент" not in hay:
        return "RozetkaPay"
    if any(m in hay for m in _NOVAPAY_MARKERS):
        return "NovaPay"
    if any(m in hay for m in _NP_AKT_MARKERS):
        return "НоваПошта"
    return None


def _message_datetime(msg) -> datetime:
    try:
        dt = parsedate_to_datetime(msg.get("Date"))
        return dt if dt else datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _iter_attachments(msg):
    """(filename, payload_bytes) для кожного вкладення з цікавим розширенням."""
    for part in msg.walk():
        raw_name = part.get_filename()
        if not raw_name:
            continue
        filename = _decode_mime_words(raw_name)
        if not filename.lower().endswith(_ATTACH_EXT):
            continue
        payload = part.get_payload(decode=True)
        if payload:
            yield filename, payload


def archive(dry_run: bool = False) -> dict:
    saved_cursor = _load_cursor()
    newly = {"RozetkaPay": 0, "NovaPay": 0, "НоваПошта": 0}
    skipped_dupe = 0
    unclassified_msgs = 0

    # IMAP SEARCH дата — англ. абревіатура місяця ЖОРСТКО (не strftime("%b"): у cp1251-Windows,
    # якщо будь-який імпорт викличе locale.setlocale(LC_TIME,''), %b стане кирилицею й SEARCH зламається).
    _MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    since_d = datetime.fromordinal(datetime.now(timezone.utc).date().toordinal() - LOOKBACK_DAYS)
    since_str = f"{since_d.day:02d}-{_MON[since_d.month - 1]}-{since_d.year}"

    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    imap.login(IMAP_EMAIL, IMAP_APP_PASSWORD)
    try:
        scanned_any = False
        for folder in FOLDERS:
            if not folder.isascii():
                # imaplib кодує назву теки в ASCII; кирилична (напр. локалізована Gmail-тека)
                # кинула б UnicodeEncodeError. Пропускаємо явно, а не сирим трейсбеком.
                _log(f"тека '{folder}' не-ASCII — imaplib її не підтримує, пропускаю.")
                continue
            if imap.select(folder, readonly=True)[0] != "OK":
                _log(f"тека '{folder}' не вибралась (нема такої мітки?) — пропускаю.")
                continue
            scanned_any = True
            status, data = imap.search(None, f'(SINCE "{since_str}")')
            if status != "OK":
                _log(f"тека '{folder}': SEARCH не вдався ({status}) — пропускаю.")
                continue
            uids = data[0].split()
            _log(f"тека '{folder}': {len(uids)} листів за {LOOKBACK_DAYS} дн — сканую вкладення.")

            for uid in uids:
                # BODY.PEEK[] — НЕ ставить \\Seen (read-only, не заважає novapay_statement).
                status, msg_data = imap.fetch(uid, "(BODY.PEEK[])")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                subject_l = _decode_mime_words(msg.get("Subject", "")).lower()
                sender_l = _decode_mime_words(msg.get("From", "")).lower()
                month = _message_datetime(msg).strftime("%Y-%m")

                had_doc = False
                for filename, payload in _iter_attachments(msg):
                    source = _classify(filename.lower(), subject_l, sender_l)
                    if source is None:
                        continue
                    had_doc = True
                    key = f"{month}/{source}/{filename}"
                    if key in saved_cursor:
                        skipped_dupe += 1
                        continue
                    saved_cursor.add(key)  # дедуп у пам'яті (на диск — лише якщо не dry_run)
                    newly[source] += 1
                    if dry_run:
                        _log(f"[dry-run] БУЛО Б збережено → {key}  "
                             f"(тема: «{subject_l[:50]}», від: {sender_l[:40]})")
                        continue
                    dest_dir = KODV_DOCS_DIR / month / source
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    (dest_dir / filename).write_bytes(payload)
                    _log(f"збережено → {key}")
                if not had_doc and any(m in f"{subject_l} {sender_l}"
                                       for m in ("пошт", "novapay", "novaposhta", "звірк")):
                    unclassified_msgs += 1  # схоже на релевантний лист без розпізнаного вкладення
        if not scanned_any:
            raise RuntimeError(f"жодна з тек {FOLDERS} не вибралась — перевір KODV_MAIL_FOLDERS")
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass

    if not dry_run:
        _save_cursor(saved_cursor)
    return {"newly": newly, "skipped_dupe": skipped_dupe, "unclassified": unclassified_msgs}


def main() -> None:
    if not (IMAP_EMAIL and IMAP_APP_PASSWORD):
        _log("NOVAPAY_IMAP_EMAIL/NOVAPAY_IMAP_APP_PASSWORD не задані в локальному .env — "
             "архіватор пропущено (додай ті самі, що на VPS, щоб увімкнути). Вихід 0, ланцюг не валю.")
        return  # м'який вихід: не ламаємо local_cabinet_audit, доки креди не додані
    dry_run = "--dry-run" in sys.argv
    try:
        r = archive(dry_run=dry_run)
    except (imaplib.IMAP4.error, OSError, RuntimeError) as e:
        _notify(f"🚨 kodv_mail_archiver: помилка архівації первинки КОДВ: {e}")
        _log(f"помилка: {e}")
        sys.exit(1)

    n = r["newly"]
    _log(f"ГОТОВО: RozetkaPay +{n['RozetkaPay']}, NovaPay +{n['NovaPay']}, НоваПошта +{n['НоваПошта']} нових; "
         f"дублів пропущено {r['skipped_dupe']}; "
         f"схожих листів без розпізнаного вкладення {r['unclassified']}.")
    if r["unclassified"]:
        _log("↑ якщо тут >0 — можливо, маркери актів НП треба уточнити за реальним листом "
             "(перевір теку НоваПошта проти пошти вручну після першого прогону).")


if __name__ == "__main__":
    main()
