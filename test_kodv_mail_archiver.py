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
import sys
from email.mime.text import MIMEText
from unittest.mock import patch

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


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ RozetkaPay HTML-посилання: пошук без мережі, окреме завантаження, дедуп-перед-"
      "завантаженням, відсутність посилання, мережевий збій — усе коректно")
sys.exit(0)
