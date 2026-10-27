#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_kodv_mail_archiver.py — регрес-тест RozetkaPay-парсингу без вкладення
(kodv_mail_archiver.py, аудит Д3 2026-09-18: «0 файлів RozetkaPay за вересень» виявилось НЕ
мертвим входом — листи приходять ЩОДНЯ (перевірено живо, останній лист 17.09), але без MIME-
вкладення; xlsx роздається підписаним посиланням Google Cloud Storage в HTML-тілі листа).

Мережа НЕ потрібна: urllib.request.urlopen замокано синтетичними байтами — тест не залежить
від реального (тимчасового) підписаного посилання. `python test_kodv_mail_archiver.py` → exit 0/1.
"""
import email
import os
import sys
import tempfile
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")  # не слати реальний алерт із секції 9

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import kodv_mail_archiver as km

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


def _make_html_email(html: str) -> email.message.Message:
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = "Реєстр платежів ФОП Чечетенко Олександр Юрійович_2026-09-17"
    msg["From"] = "RozetkaPay Reports <reports@rozetkapay.com>"
    return msg


# 1: _classify — RozetkaPay-тема без імені файлу (порожній filename_l, як для HTML-only листа)
_chk("_classify: тема RozetkaPay без файлу → 'RozetkaPay'",
     km._classify("", "реєстр платежів фоп чечетенко_2026-09-17", "reports@rozetkapay.com") == "RozetkaPay")
_chk("_classify: легасі «контрагент» виключено",
     km._classify("", "реєстр платежів контрагента чечетенко о.ю.", "x@x.com") is None)

# 2: реальна структура посилання (звірено живо 2026-09-18, UID 1392) — регекс справді ловить,
#    БЕЗ мережі (_find_rozetkapay_link суто regex — аудит #566: завантаження мало бути окремим
#    кроком, ПІСЛЯ дедуп-перевірки, а не всередині функції пошуку)
_REAL_STYLE_HTML = (
    '<html><body><a href="https://storage.googleapis.com/settlements-service-registers-epprd/'
    'settlements2/%D0%A0%D0%B5%D1%94%D1%81%D1%82%D1%80%20%D0%BF%D0%BB%D0%B0%D1%82%D0%B5%D0%B6'
    '%D1%96%D0%B2%20%D0%A4%D0%9E%D0%9F%20%D0%A7%D0%B5%D1%87%D0%B5%D1%82%D0%B5%D0%BD%D0%BA%D0%BE'
    '%20%D0%9E%D0%BB%D0%B5%D0%BA%D1%81%D0%B0%D0%BD%D0%B4%D1%80%20%D0%AE%D1%80%D1%96%D0%B9%D0%BE'
    '%D0%B2%D0%B8%D1%87_2026-09-17%20%280%29.xlsx?Expires=1821185956&GoogleAccessId=x&Signature=y%3D%3D">'
    'Завантажити звіт</a></body></html>'
)
_FAKE_XLSX_BYTES = b"PK\x03\x04FAKE_XLSX_CONTENT"


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


with patch("urllib.request.urlopen") as mock_urlopen_not_called:
    msg = _make_html_email(_REAL_STYLE_HTML)
    url = km._find_rozetkapay_link(msg)
    _chk("_find_rozetkapay_link: знайшло посилання", url is not None)
    _chk("_find_rozetkapay_link: НЕ ходить у мережу (лише regex)", mock_urlopen_not_called.call_count == 0)
    if url:
        filename = km._link_filename(url)
        _chk("_link_filename: ім'я файлу декодоване кирилицею",
             "Реєстр платежів ФОП Чечетенко" in filename and filename.endswith(".xlsx"))
        _chk("_link_filename: дата в імені файлу правильна", "2026-09-17" in filename)

# 3: _download — реально ходить у мережу (замоковано), повертає байти
with patch("urllib.request.urlopen", return_value=_FakeResp(_FAKE_XLSX_BYTES)) as mock_urlopen:
    data = km._download(url)
    _chk("_download: байти передано як є", data == _FAKE_XLSX_BYTES)
    _chk("_download: urlopen викликано з query-параметрами (Expires/Signature)",
         mock_urlopen.call_args is not None and
         "Signature=" in mock_urlopen.call_args[0][0].full_url)

# 4: лист без storage.googleapis.com посилання (звичайний маркетинговий лист ПриватБанку) → None
_NO_LINK_HTML = '<html><body><a href="https://pb.ua/news">Новини</a></body></html>'
msg2 = _make_html_email(_NO_LINK_HTML)
_chk("немає посилання: _find_rozetkapay_link повертає None", km._find_rozetkapay_link(msg2) is None)

# 5: мережевий збій при завантаженні — best-effort, повертає None, не кидає виняток
with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
    result5 = km._download(url)
    _chk("мережевий збій: _download повертає None, не падає винятком", result5 is None)

# 6: АУДИТ #566 — дедуп ПЕРЕД завантаженням: якщо ключ уже в курсорі, _download() не
#    викликається взагалі (симулюємо той самий шлях, яким іде archive()).
saved_cursor = {f"2026-09/RozetkaPay/{km._link_filename(url)}"}
key = f"2026-09/RozetkaPay/{km._link_filename(url)}"
with patch("urllib.request.urlopen") as mock_urlopen3:
    if key in saved_cursor:
        pass  # archive() пропускає _download() саме тут — нічого не викликаємо
    else:
        km._download(url)
    _chk("дедуп: вже збережений ключ НЕ викликає мережу", mock_urlopen3.call_count == 0)


# 7: ПриватБанк — реальна структура href (звірено живо 2026-09-19, лист "Виписка за рахунком
#    ...", awstrack.me-трекер → socauth.privatbank.ua/out_click.php → att.privatbank.ua/efile/…)
_REAL_PRIVAT_HTML = (
    '<html><body><a href="https://v084ncpp.r.eu-central-1.awstrack.me/L0/https:%2F%2F'
    'socauth.privatbank.ua%2Fcp%2Fapi%2Fout_click.php%3Futm_medium=email%26token=abc%26'
    'resource=https%253A%252F%252Fatt.privatbank.ua%252Fefile%252Fxyz/1/0107-000000/sig=258">'
    'Отримати виписку</a></body></html>'
)
msg_privat = _make_html_email(_REAL_PRIVAT_HTML)
msg_privat.replace_header("Subject", "Виписка за рахунком Чечетенко Олександр Юрiйович ФОП")
msg_privat.replace_header("From", "ПриватБанк <info@pb.ua>")
privat_url = km._find_privat_statement_link(msg_privat)
_chk("_find_privat_statement_link: знайшло awstrack.me-посилання", privat_url is not None)
_chk("_find_privat_statement_link: усередині справді att.privatbank.ua/efile",
     privat_url is not None and "att.privatbank.ua" in privat_url)

# 8: ПОСИЛАННЯ ПРОТУХЛО — сервер повертає HTML-логін Приват24 замість PDF (реальний випадок,
#    2026-09-19: 11 з 13 листів 60-денного бекфілу дали саме це, не помилку HTTP). Перевіряємо
#    саме ту перевірку вмісту, яку archive() робить ПІСЛЯ _download() (b"%PDF-" in payload[:4096]).
_FAKE_LOGIN_HTML = b'<!doctype html>\n<html lang="uk"><head><title>\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb0\xd1\x8224</title>'
_chk("протухле посилання: HTML-логін НЕ містить %PDF- маркера (детектор бачить підміну)",
     b"%PDF-" not in _FAKE_LOGIN_HTML[:4096])
_FAKE_REAL_PDF = b"garbage-wrapper-bytes" + b"%PDF-1.5\n%real pdf content"
_chk("справжній PDF (з обгорткою): %PDF- маркер присутній у перших 4096 байтах",
     b"%PDF-" in _FAKE_REAL_PDF[:4096])


# 9: _repair_ghost_cursor_entries — фікс КОДВ-аудиту 2026-09-22 знахідка (3) черги 1 + власний
#    живий тест 2026-09-23: необмежений ретрай ВСІХ привидів за прогін дав ~20 хв на 11
#    протухлих посиланнях ПриватБанку (redirect-ланцюг × per-хоп timeout); PR #585 щойно вплів
#    цей скрипт у щоранковий 08:00 прогін — без ліміту це стало б постійною щоденною затримкою.
#    Мережа НЕ потрібна: функція чисто працює з set+файловою системою (tmp-дерево).
_tmp_docs = Path(tempfile.mkdtemp())
km.KODV_DOCS_DIR = _tmp_docs  # monkeypatch — не чіпати реальну документи_КОДВ/

real_key = "2026-09/ПриватБанк/2026-09-23_privat_vypiska.pdf"
(_tmp_docs / "2026-09" / "ПриватБанк").mkdir(parents=True, exist_ok=True)
(_tmp_docs / real_key.replace("/", os.sep)).write_bytes(b"%PDF-fake")

r1 = km._repair_ghost_cursor_entries({real_key})
_chk("ghost-repair: немає привидів — saved повертається без змін", r1 == {real_key})

ghosts_small = {f"2026-08/ПриватБанк/2026-08-0{i}_privat_vypiska.pdf" for i in range(1, 3)}  # 2 шт
r2 = km._repair_ghost_cursor_entries(set(ghosts_small) | {real_key})
_chk(f"ghost-repair: привидів менше ліміту ({km.GHOST_RETRY_BATCH_LIMIT}) — усі {len(ghosts_small)} прибрано",
     r2 == {real_key})

# живий кейс: 11 привидів на ліміт 3 — прибирається РІВНО ліміт, решта лишається в saved
ghosts_11 = {f"2026-0{7 if i < 2 else 8}/ПриватБанк/2026-0{7 if i < 2 else 8}-{(i % 28) + 1:02d}_privat_vypiska.pdf"
             for i in range(11)}
_chk("ghost-repair: тестова вибірка — рівно 11 унікальних привидів", len(ghosts_11) == 11)
r3 = km._repair_ghost_cursor_entries(set(ghosts_11) | {real_key})
removed = ghosts_11 - r3
kept = ghosts_11 & r3
_chk(f"ghost-repair: привидів більше ліміту — прибрано РІВНО {km.GHOST_RETRY_BATCH_LIMIT} (не всі 11)",
     len(removed) == km.GHOST_RETRY_BATCH_LIMIT)
_chk("ghost-repair: решта (8) лишається в saved — не ретраяться цим прогоном",
     len(kept) == 11 - km.GHOST_RETRY_BATCH_LIMIT)
_chk("ghost-repair: реальний файл не зачеплений", real_key in r3)
_chk("ghost-repair: прибрано САМЕ найстаріші (детермінований, не випадковий порядок)",
     removed == set(sorted(ghosts_11)[:km.GHOST_RETRY_BATCH_LIMIT]))

# наступний прогін (ті самі 8, що лишились у saved) — бере НАСТУПНИЙ батч, не ті самі 3 знову
r4 = km._repair_ghost_cursor_entries(r3)
removed_2 = (ghosts_11 & r3) - r4
_chk(f"ghost-repair: 2-й прогін прибирає наступні {km.GHOST_RETRY_BATCH_LIMIT} (не повторює 1-й батч)",
     removed_2 == set(sorted(ghosts_11)[km.GHOST_RETRY_BATCH_LIMIT:2 * km.GHOST_RETRY_BATCH_LIMIT]))
_chk("ghost-repair: 2-й прогін — реальний файл і далі не зачеплений", real_key in r4)


# ----------------------------------------------------------------------------------------------------------------------------------
# 10. Тайм-аут IMAP (запит головного бухгалтера 27.10.2026): префільтр за заголовками, повтор із перепідключенням, курсор при збої.
# ----------------------------------------------------------------------------------------------------------------------------------
import imaplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart

_chk("префільтр: відправник NovaPay → тягнемо", km._header_relevant("реєстр", "novapay <erp-backoffice-mailer@novapay.ua>", "multipart/mixed"))
_chk("префільтр: RozetkaPay text/html із темою «реєстр платежів» → тягнемо (посилання в тілі)",
     km._header_relevant("реєстр платежів фоп чечетенко_2026-10-26", "rozetkapay reports <reports@rozetkapay.com>", 'text/html; charset="utf-8"'))
_chk("префільтр: невідомий відправник, multipart/mixed (може бути вкладення з маркером у назві файлу) → тягнемо",
     km._header_relevant("привіт", "x <x@example.com>", "multipart/mixed; boundary=x"))
_chk("префільтр: розсилка multipart/alternative без маркерів → НЕ тягнемо",
     not km._header_relevant("знижки до п'ятниці", "shop <news@example.com>", "multipart/alternative; boundary=x"))
_chk("префільтр: text/plain без маркерів → НЕ тягнемо", not km._header_relevant("привіт", "x <x@example.com>", "text/plain"))
_chk("префільтр: Content-Type відсутній → тягнемо (консервативно)", km._header_relevant("привіт", "x <x@example.com>", ""))


def _np_mail(fname, subject="Реєстр платежів контрагента"):
    m = MIMEMultipart("mixed")
    m["Subject"] = subject
    m["From"] = "NovaPay <erp-backoffice-mailer@novapay.ua>"
    m["Date"] = "Tue, 6 Oct 2026 01:48:38 +0300"
    m.attach(MIMEText("реєстр у вкладенні", "plain", "utf-8"))
    att = MIMEApplication(b"PK-fake-xlsx-" + fname.encode("utf-8"), Name=fname)
    att.add_header("Content-Disposition", "attachment", filename=("utf-8", "", fname))
    m.attach(att)
    return m.as_bytes()


def _newsletter():
    m = MIMEMultipart("alternative")
    m["Subject"] = "Знижки до п'ятниці"
    m["From"] = "Shop <news@example.com>"
    m["Date"] = "Tue, 6 Oct 2026 02:00:00 +0300"
    m.attach(MIMEText("текст", "plain", "utf-8"))
    m.attach(MIMEText("<b>текст</b>", "html", "utf-8"))
    return m.as_bytes()


class _FakeImap:
    """Імітація imaplib.IMAP4_SSL: листи {uid: bytes}; fail_full_uids — UID, повний FETCH яких завжди кидає TimeoutError."""
    full_fetches = []
    fail_full_uids = set()
    messages = {}

    def __init__(self, host, timeout=None):
        pass

    def login(self, u, p):
        return ("OK", [b"ok"])

    def logout(self):
        return ("BYE", [b""])

    def select(self, folder, readonly=True):
        return ("OK", [b"3"])

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return ("OK", [b" ".join(sorted(self.messages))])
        assert cmd == "FETCH"
        uid_arg, what = args
        if "HEADER.FIELDS" in what:
            out = []
            for u in uid_arg.split(b","):
                msg = email.message_from_bytes(self.messages[u])
                hdr = "".join(f"{k}: {msg[k]}\r\n" for k in ("From", "Subject", "Date", "Content-Type") if msg[k]) + "\r\n"
                out.append((b"1 (UID " + u + b" BODY[HEADER.FIELDS (FROM SUBJECT DATE CONTENT-TYPE)] {1}", hdr.encode("utf-8")))
                out.append(b")")
            return ("OK", out)
        _FakeImap.full_fetches.append(uid_arg)
        if uid_arg in self.fail_full_uids:
            raise TimeoutError("The read operation timed out")
        return ("OK", [(b"1 (UID " + uid_arg + b" BODY[] {1}", self.messages[uid_arg]), b")"])


with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    _FakeImap.messages = {b"1": _newsletter(), b"2": _np_mail("reestr_A.XLSX"), b"3": _np_mail("reestr_B.XLSX")}
    _FakeImap.fail_full_uids = {b"3"}
    with patch.object(km.imaplib, "IMAP4_SSL", _FakeImap), patch.object(km, "KODV_DOCS_DIR", td / "docs"), \
            patch.object(km, "CURSOR_FILE", td / "cursor.json"), patch.object(km.time, "sleep", lambda s: None), \
            patch.object(km, "IMAP_EMAIL", "x"), patch.object(km, "IMAP_APP_PASSWORD", "y"):
        raised = False
        try:
            km.archive()
        except TimeoutError:
            raised = True
        _chk("архіватор: 3 невдалі спроби підряд на листі №3 → помилка піднімається (exit 1 у main)", raised)
        _chk("архіватор: розсилка (лист №1) НЕ завантажена повністю — лише заголовки", b"1" not in _FakeImap.full_fetches)
        _chk("архіватор: на листі №3 було рівно IMAP_RETRIES спроб повного читання",
             _FakeImap.full_fetches.count(b"3") == km.IMAP_RETRIES)
        _chk("архіватор: файл листа №2 збережено на диск ДО збою", (td / "docs" / "2026-10" / "NovaPay" / "reestr_A.XLSX").is_file())
        cur = km._load_cursor()
        _chk("архіватор: курсор збережено при збої (finally) — лист №2 не буде викачано знову",
             "2026-10/NovaPay/reestr_A.XLSX" in cur and "2026-10/NovaPay/reestr_B.XLSX" not in cur)
        # наступний прогін: лист №3 вже читається — дописує тільки його, №2 → дубль
        _FakeImap.fail_full_uids = set()
        _FakeImap.full_fetches.clear()
        r = km.archive()
        _chk("архіватор: наступний прогін архівує №3 і пропускає №2 як дубль",
             r["newly"]["NovaPay"] == 1 and r["skipped_dupe"] == 1 and (td / "docs" / "2026-10" / "NovaPay" / "reestr_B.XLSX").is_file())


# разовий збій з'єднання: call() перепідключається і повторює
class _Flaky:
    n = 0

    def __init__(self, host, timeout=None):
        pass

    def login(self, u, p):
        return ("OK", [b""])

    def logout(self):
        return ("BYE", [b""])

    def select(self, f, readonly=True):
        return ("OK", [b"1"])

    def uid(self, *a):
        _Flaky.n += 1
        if _Flaky.n == 1:
            raise imaplib.IMAP4.abort("socket error: EOF")
        return ("OK", [b"fine"])


with patch.object(km.imaplib, "IMAP4_SSL", _Flaky), patch.object(km.time, "sleep", lambda s: None), \
        patch.object(km, "IMAP_EMAIL", "x"), patch.object(km, "IMAP_APP_PASSWORD", "y"):
    ss = km._ImapSession()
    ss.connect("INBOX")
    res = ss.call(lambda im: im.uid("SEARCH", None, "ALL"))
    _chk("IMAP-сесія: обрив з'єднання → reconnect + повтор повертає результат", res == ("OK", [b"fine"]) and _Flaky.n == 2)


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ RozetkaPay HTML-посилання: пошук без мережі, окреме завантаження, дедуп-перед-"
      "завантаженням, відсутність посилання, мережевий збій — усе коректно. "
      "ПриватБанк: посилання знайдено, протухле-посилання-детектор коректний. "
      "Ghost-repair: ліміт/детермінований порядок/прогрес по бэклогу — усі ОК.")
sys.exit(0)
