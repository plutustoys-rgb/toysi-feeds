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
import re
import sys
import urllib.parse
import urllib.request
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
# Таймаут на КОЖНУ IMAP-операцію (сек). Без нього конект/читання може висіти ВІЧНО, якщо gmail
# застопориться — і таск-планувальник вбиває процес (STATUS_CONTROL_C_EXIT / 0xC000013A), як сталося
# 2026-09-02. socket.timeout — підклас OSError, тож ловиться наявним except → алерт+вихід, не хол.
IMAP_TIMEOUT = int(os.environ.get("KODV_MAIL_IMAP_TIMEOUT", "60"))

CURSOR_FILE = BASE_DIR / ".local_secrets" / "kodv_mail_archiver_cursor.json"
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"
LINK_DOWNLOAD_TIMEOUT = int(os.environ.get("KODV_MAIL_LINK_TIMEOUT", "30"))

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
#
# ⚠️ ЖИВО ПЕРЕВІРЕНО 2026-09-18 (аудит Д3 — «0 файлів за вересень» виявився НЕ мертвим входом,
# а прогалиною парсингу): лист RozetkaPay `is_multipart=False`, ОДНА частина text/html, жодного
# MIME-вкладення. Файл роздається через підписане посилання Google Cloud Storage у тілі листа
# (`https://storage.googleapis.com/.../Реєстр платежів….xlsx?Expires=…&Signature=…`), яке треба
# ЗАВАНТАЖИТИ окремим HTTP-запитом — _iter_attachments() тут завжди дасть 0, це не сигнал
# «листа нема». Обробляється в archive() через _find_rozetkapay_link() + _download().
_ROZETKAPAY_MARKERS = ("реєстр платежів", "реестр платежей")
_ROZETKAPAY_LINK_HOST = "storage.googleapis.com"
# ПриватБанк — щоденна виписка (PDF) на ту саму скриньку. Запит бухгалтера 2026-08-31: завести
# заздалегідь, email ще не тече. Маркери BEST-GUESS (відправник @privatbank.ua/@privat24.ua або
# «Приват24 для бізнесу»/«виписка»); ⚠ ЗВІРИТИ за ПЕРШИМ реальним листом (точний формат невідомий —
# якщо не спіймається, лист впаде в лічильник «unclassified» → уточнити маркер). Тека: ПриватБанк.
_PRIVAT_STATEMENT_MARKERS = ("privatbank.ua", "privat24", "приват24", "приватбанк",
                             "приват24 для бізнесу", "приват24 для бизнеса")


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


GHOST_RETRY_BATCH_LIMIT = 3  # див. коментар у _repair_ghost_cursor_entries: чому НЕ всі одразу


def _repair_ghost_cursor_entries(saved: set) -> set:
    """ДОДАНО (аудит КОДВ-автоматики, 2026-09-22, знахідка (3) — черга 1, втрата даних):
    курсор каже "saved", файлу на диску нема — 11 таких записів прожили до 2 місяців
    непоміченими (жоден інший механізм цей клас не ловить: source_freshness.py дивиться
    лише вік НАЙНОВІШОГО файлу теки, не повноту курсора). Походження: бекфіл 19.09 без
    перевірки `%PDF-` зберіг HTML-сторінку логіну як ".pdf" і просунув курсор; биті файли
    прибрали, курсор — ні.

    Живо перевірено: 11/11 знайдено, усі — ПриватБанк (2026-07-30/31, 08-04/07/13/17/31,
    09-01/07/11/15).

    ВИПРАВЛЕНО (живий прогін, 2026-09-23, ДО того, як пішло в продакшн через PR #585):
    перша версія прибирала з курсора ВСІ привиди одразу — живий тест показав, що кожна
    спроба протухлого посилання ПриватБанку може займати до ~90с (`_download()` йде за
    3-хоповим редиректом awstrack.me→socauth→att.privatbank.ua, `LINK_DOWNLOAD_TIMEOUT`
    застосовується НЕЗАЛЕЖНО на кожен хоп, не на весь ланцюг разом) — 11 привидів дали
    ~20 хв фактичного прогону замість очікуваних ~6 хв. Оскільки PR #585 щойно вплів
    цей скрипт у ЩОРАНКОВИЙ 08:00 прогін ПЕРЕД money-critical парсером ПриватБанку —
    без ліміту це стало б постійним +20 хв КОЖЕН РАНОК, бо підписані посилання
    ПриватБанку протухають (retry НІКОЛИ не вдасться для старих листів — сам докстрінг
    нижче це підтверджує).

    Тому: ретраїмо НЕ БІЛЬШЕ GHOST_RETRY_BATCH_LIMIT привидів за прогін (найстаріші
    першими — детермінований порядок, не залежить від порядку ітерації set), решту
    просто алертимо (self-diagnosing), не чіпаючи курсор — вони спробуються іншого дня.
    Обмежує гірший випадок до ~GHOST_RETRY_BATCH_LIMIT×90с, не ростиме з розміром бэклогу.

    Дія на кожен привид з батчу: прибрати з курсора (не файл — файлу й нема) →
    наступний прогін СПРОБУЄ знову (лист і досі в INBOX, BODY.PEEK не ставить \\Seen —
    джерело нікуди не ділось). Якщо посилання протухло (PrivatBank-виписки живуть
    обмежений час) — просто не завантажиться знову, `_download()`'s %PDF-перевірка
    (money-critical фікс той самої сесії) не дасть зберегти биту сторінку вдруге."""
    docs_dir = KODV_DOCS_DIR
    ghosts = sorted(rel for rel in saved if not (docs_dir / rel).is_file())
    if not ghosts:
        return saved
    batch = ghosts[:GHOST_RETRY_BATCH_LIMIT]
    rest = ghosts[GHOST_RETRY_BATCH_LIMIT:]
    _log(f"⚠️ виявлено {len(ghosts)} записів курсора без файлу на диску — прибираю {len(batch)} "
         f"(ліміт {GHOST_RETRY_BATCH_LIMIT}/прогін, найстаріші першими): {batch}"
         + (f"; ще {len(rest)} чекають наступних прогонів: {rest}" if rest else ""))
    _notify(f"⚠️ kodv_mail_archiver: {len(ghosts)} 'привидів' у курсорі (курсор каже saved, файлу "
            f"нема) — цей прогін пробує {len(batch)} з них (ліміт {GHOST_RETRY_BATCH_LIMIT}/прогін), "
            f"решта {len(rest)} чекають наступних прогонів. Перевір документи_КОДВ вручну, якщо "
            f"повторюється: {ghosts[:5]}{'…' if len(ghosts) > 5 else ''}")
    return saved - set(batch)


def _save_cursor(saved: set) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(
        json.dumps({"saved": sorted(saved), "updated": datetime.now(timezone.utc).isoformat()},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def _classify(filename_l: str, subject_l: str, sender_l: str) -> str | None:
    """Джерело для вкладення: 'RozetkaPay' / 'ПриватБанк' / 'NovaPay' / 'НоваПошта' / None.
    RozetkaPay перевіряємо ПЕРШИМ — його маркер «реєстр платежів» специфічніший і не колізить із
    NovaPay «реєстр переказів»."""
    hay = f"{filename_l} {subject_l} {sender_l}"
    # RozetkaPay = «реєстр платежів [ФОП Чечетенко Олександр Юрійович]». ВИКЛЮЧАЄМО легасі-файли
    # «реєстр платежів КОНТРАГЕНТА Чечетенко О.Ю.» (інші документи), щоб вони не затінили денний
    # реєстр у теці (аудит #464: _newest_registry бере найновіший xlsx).
    if any(m in hay for m in _ROZETKAPAY_MARKERS) and "контрагент" not in hay:
        return "RozetkaPay"
    if any(m in hay for m in _PRIVAT_STATEMENT_MARKERS):
        return "ПриватБанк"
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


def _find_rozetkapay_link(msg) -> str | None:
    """RozetkaPay «Реєстр платежів» не додає файл MIME-вкладенням — роздає підписаним
    посиланням Google Cloud Storage у HTML-тілі листа (перевірено живо 2026-09-18, лист
    UID 1392 за 17.09). Шукає `https://storage.googleapis.com/....xlsx?...` у text/html-
    частинах. ЛИШЕ regex, БЕЗ мережі — виклик ізольовано від завантаження, щоб дедуп-
    перевірка (уже збережено?) могла відсіяти вже архівовані листи ДО зайвого HTTP-запиту
    (аудит #566: спершу було навпаки — качало кожен лист щоразу, включно з dry-run)."""
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        m = re.search(
            r'href=["\'](' + re.escape(f"https://{_ROZETKAPAY_LINK_HOST}") + r'[^"\']+\.xlsx\?[^"\']+)["\']',
            html, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _link_filename(url: str) -> str:
    """Ім'я файлу — декодований шлях URL до '?' (query — підпис/термін дії, не частина імені)."""
    path = urllib.parse.unquote(url.split("?", 1)[0])
    return path.rsplit("/", 1)[-1]


def _download(url: str, label: str = "RozetkaPay") -> bytes | None:
    """Best-effort HTTP GET — мережевий збій/протухле посилання не має валити прогін решти
    листів, лише цей кандидат пропускається з повідомленням у stderr. `urlopen` сам іде за
    редиректами (GET) — для ПриватБанку посилання проходить через awstrack.me-трекер →
    socauth.privatbank.ua/out_click.php → att.privatbank.ua/efile/<токен>, підписаний PDF
    (CAdES/PKCS7-обгортка навколо %PDF-…) — перевірено живо 2026-09-19, БЕЗ логіну в Приват24."""
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=LINK_DOWNLOAD_TIMEOUT) as resp:
            return resp.read()
    except Exception as e:  # noqa: BLE001
        print(f"[KODVmail] посилання {label} знайдено, завантаження не вдалось: {e}", file=sys.stderr)
        return None


_PRIVAT_LINK_RE = re.compile(
    r'href=["\'](https://[^"\']*awstrack\.me[^"\']*privatbank\.ua[^"\']*)["\']', re.IGNORECASE)


def _find_privat_statement_link(msg) -> str | None:
    """Лист-нагадування ПриватБанку не містить самої виписки — кнопка «Отримати виписку»
    веде на awstrack.me-трекер (клік-редиректор), що загортає посилання на
    socauth.privatbank.ua/out_click.php → att.privatbank.ua/efile/<токен> (сам PDF).
    ЛИШЕ regex, БЕЗ мережі (той самий принцип, що _find_rozetkapay_link — дедуп ДО завантаження)."""
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        m = _PRIVAT_LINK_RE.search(html)
        if m:
            return m.group(1)
    return None


def archive(dry_run: bool = False) -> dict:
    saved_cursor = _load_cursor()
    saved_cursor = _repair_ghost_cursor_entries(saved_cursor)
    newly = {"RozetkaPay": 0, "ПриватБанк": 0, "NovaPay": 0, "НоваПошта": 0}
    skipped_dupe = 0
    unclassified_msgs = 0

    # IMAP SEARCH дата — англ. абревіатура місяця ЖОРСТКО (не strftime("%b"): у cp1251-Windows,
    # якщо будь-який імпорт викличе locale.setlocale(LC_TIME,''), %b стане кирилицею й SEARCH зламається).
    _MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    since_d = datetime.fromordinal(datetime.now(timezone.utc).date().toordinal() - LOOKBACK_DAYS)
    since_str = f"{since_d.day:02d}-{_MON[since_d.month - 1]}-{since_d.year}"

    imap = imaplib.IMAP4_SSL(IMAP_HOST, timeout=IMAP_TIMEOUT)
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

                # RozetkaPay «Реєстр платежів» не має MIME-вкладення (перевірено живо 2026-09-18) —
                # без цього кожен такий лист впав би тут у "unclassified" і виглядав би мертвим входом.
                if not had_doc and _classify("", subject_l, sender_l) == "RozetkaPay":
                    link_url = _find_rozetkapay_link(msg)  # лише regex, без мережі
                    if link_url:
                        filename = _link_filename(link_url)
                        key = f"{month}/RozetkaPay/{filename}"
                        if key in saved_cursor:
                            # Уже архівовано раніше — НЕ качаємо повторно (аудит #566: до
                            # цього фіксу завантаження йшло ДО дедуп-перевірки, тож кожен
                            # прогін тягнув ~усі листи 60-денного вікна з мережі знову,
                            # включно з dry-run, попри те що файли вже на диску).
                            had_doc = True
                            skipped_dupe += 1
                        else:
                            if dry_run:
                                # dry-run НЕ якає мережу — посилання вже знайдене регексом,
                                # цього досить, щоб показати «було б завантажено».
                                had_doc = True
                                saved_cursor.add(key)
                                newly["RozetkaPay"] += 1
                                _log(f"[dry-run] БУЛО Б завантажено-збережено → {key}  "
                                     f"(тема: «{subject_l[:50]}»)")
                            else:
                                payload = _download(link_url)
                                if payload is not None:
                                    had_doc = True
                                    saved_cursor.add(key)
                                    newly["RozetkaPay"] += 1
                                    dest_dir = KODV_DOCS_DIR / month / "RozetkaPay"
                                    dest_dir.mkdir(parents=True, exist_ok=True)
                                    (dest_dir / filename).write_bytes(payload)
                                    _log(f"завантажено з посилання → {key}")
                                # payload is None: завантаження не вдалось (_download уже
                                # залогувала причину) — had_doc лишається False, лист
                                # спробується знову наступного прогону (курсор не просунуто).

                # ПриватБанк-нагадування теж без MIME-вкладення статi (лише .vcf-візитка
                # персонального банкіра) — той самий клас, що RozetkaPay, звірено живо 2026-09-19
                # на реальному листі "Виписка за рахунком ...".
                if not had_doc and _classify("", subject_l, sender_l) == "ПриватБанк":
                    link_url = _find_privat_statement_link(msg)
                    if link_url:
                        msg_date = _message_datetime(msg).strftime("%Y-%m-%d")
                        filename = f"{msg_date}_privat_vypiska.pdf"
                        key = f"{month}/ПриватБанк/{filename}"
                        if key in saved_cursor:
                            had_doc = True
                            skipped_dupe += 1
                        else:
                            if dry_run:
                                had_doc = True
                                saved_cursor.add(key)
                                newly["ПриватБанк"] += 1
                                _log(f"[dry-run] БУЛО Б завантажено-збережено → {key}  "
                                     f"(тема: «{subject_l[:50]}»)")
                            else:
                                payload = _download(link_url, label="ПриватБанк")
                                # Підписане посилання діє обмежений час (звірено живо
                                # 2026-09-19: 60-денний бекфіл — лише 2 з 13 листів дали
                                # реальний PDF, решта 11 мовчки повернули HTML-сторінку
                                # логіну Приват24-для-бізнесу, `_download` цього не бачить,
                                # бо HTTP-статус 200). Без перевірки вмісту чужий HTML
                                # зберігся б як ".pdf" і виглядав би архівованим документом.
                                if payload is not None and b"%PDF-" not in payload[:4096]:
                                    _log(f"посилання ПриватБанку повернуло НЕ PDF (протухле? "
                                         f"веде на логін) → {key} — не зберігаю")
                                    payload = None
                                if payload is not None:
                                    had_doc = True
                                    saved_cursor.add(key)
                                    newly["ПриватБанк"] += 1
                                    dest_dir = KODV_DOCS_DIR / month / "ПриватБанк"
                                    dest_dir.mkdir(parents=True, exist_ok=True)
                                    (dest_dir / filename).write_bytes(payload)
                                    _log(f"завантажено з посилання → {key}")
                                # payload is None: спробується знову наступного прогону
                                # (курсор не просунуто) — той самий принцип, що RozetkaPay.
                                # Для ВЖЕ протухлого посилання повтор так само не допоможе,
                                # але це принаймні не залишає хибного "архівовано".

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
    _log(f"ГОТОВО: RozetkaPay +{n['RozetkaPay']}, ПриватБанк +{n['ПриватБанк']}, NovaPay +{n['NovaPay']}, "
         f"НоваПошта +{n['НоваПошта']} нових; дублів пропущено {r['skipped_dupe']}; "
         f"схожих листів без розпізнаного вкладення {r['unclassified']}.")
    if r["unclassified"]:
        _log("↑ якщо тут >0 — можливо, маркери актів НП треба уточнити за реальним листом "
             "(перевір теку НоваПошта проти пошти вручну після першого прогону).")


if __name__ == "__main__":
    main()
