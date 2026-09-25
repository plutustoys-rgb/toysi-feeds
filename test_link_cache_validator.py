#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_link_cache_validator.py — регрес-тест на ІНЦИДЕНТ 2026-08-15 (PR #275): валідатор
масово GET-ив НАШУ ВЛАСНУ вітрину plutustoys.com.ua (5354 паралельних запити/прогін) →
Cloudflare rate-limit сплутав живі сторінки з мертвими + троттлив сусідні тули на тому ж IP.

Фікс: _resolve_url_text() (generate_google_feed.py) резолвить канонічний slug ЛИШЕ через
детермінований запит до prom.ua (за стабільним числовим prom_id), НЕ чіпаючи власний сайт.

Цей тест стереже ІМЕННО прибраний шлях, а не щасливий сценарій (Консультант, 2026-09-25:
«тест, що стереже прибраний шлях, вартий більше за тест, що перевіряє щасливий сценарій») —
підміняє requests.get і падає, якщо БУДЬ-ЯКИЙ виклик колись піде на plutustoys.com.ua.

`python test_link_cache_validator.py` → exit 0/1. Мережа не потрібна (requests.get замокано).
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

import generate_google_feed as ggf
import link_cache_validator as lcv

_FAILS = []


def _chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _FAILS.append(name)


class _FakeResponse:
    def __init__(self, location, status_code=301):
        self.status_code = status_code
        self.headers = {"Location": location}


_calls = []


def _fake_get(url, **kwargs):
    _calls.append(url)
    # Живий детермінований редирект prom.ua — той самий формат, що _resolve_url_text парсить
    # (_URL_TEXT_RE = r"/ua/p\d+-([^/]+)\.html", generate_google_feed.py:101).
    return _FakeResponse("/ua/p999-new-slug.html")


# ── 1. _resolve_url_text() НАПРЯМУ — жодного разу не звертається до нашого домену ──
_calls.clear()
with mock.patch.object(ggf.requests, "get", side_effect=_fake_get):
    result = ggf._resolve_url_text(999)
_chk("_resolve_url_text: рівно 1 виклик requests.get", len(_calls) == 1)
_chk("_resolve_url_text: URL містить prom.ua", "prom.ua" in _calls[0])
_chk("_resolve_url_text: URL НЕ містить plutustoys.com.ua (інцидент 15.08)",
     all("plutustoys.com.ua" not in c for c in _calls))
_chk("_resolve_url_text: повернув розпарсений slug", result == "new-slug")


# ── 2. Повний validate() end-to-end на тимчасовому кеші — теж жодного запиту на наш домен ──
with tempfile.TemporaryDirectory() as tmpdir:
    tmp_cache = Path(tmpdir) / "own_product_links_cache.json"
    tmp_cursor = Path(tmpdir) / "cursor.json"
    tmp_cache.write_text(
        json.dumps({
            "111": {"prom_id": 111, "url_text": "old-slug-1"},
            "222": {"prom_id": 222, "url_text": "old-slug-2"},
        }),
        encoding="utf-8",
    )

    _calls.clear()
    with mock.patch.object(lcv, "OWN_PRODUCT_LINKS_CACHE_FILE", tmp_cache), \
         mock.patch.object(lcv, "CURSOR_FILE", tmp_cursor), \
         mock.patch.object(ggf.requests, "get", side_effect=_fake_get):
        stats = lcv.validate(dry_run=True)

    _chk("validate(): перевірено 2 записи (BATCH_SIZE не обмежив крихітний кеш)",
         stats.get("checked") == 2)
    _chk("validate(): рівно 2 виклики requests.get (по одному на запис)", len(_calls) == 2)
    _chk("validate(): ЖОДНОГО запиту на plutustoys.com.ua серед усіх викликів",
         all("plutustoys.com.ua" not in c for c in _calls))
    _chk("validate(): усі запити — до prom.ua", all("prom.ua" in c for c in _calls))
    _chk("validate(): --dry-run НЕ переписав кеш-файл",
         json.loads(tmp_cache.read_text(encoding="utf-8"))["111"]["url_text"] == "old-slug-1")
    _chk("validate(): --dry-run НЕ створив курсор-файл", not tmp_cursor.exists())


# ── 3. ФІКС 2026-09-25: кластер "підтверджено відсутніх" (404) НЕ абортує прогін ──
# Живий інцидент: journalctl підтвердив 3 доби поспіль (23-25.09) ІДЕНТИЧНИЙ абортований
# прогін на курсорі 1000 — кластер ~10 СПРАВДІ делістнутих товарів (404/чужий редирект)
# хибно спрацьовував як "prom.ua нас блокує" (MAX_TRANSIENT_FAILS=12), курсор НЕ рухався,
# і той самий кластер повторювався щоночі. Тест: 15 послідовних 404 (> MAX_TRANSIENT_FAILS=12)
# — прогін МАЄ завершитись успішно (не aborted), бо жоден з них не мережевий виняток.
def _fake_get_confirmed_gone(url, **kwargs):
    _calls.append(url)
    return _FakeResponse("", status_code=404)  # реальний 404: Location порожній, regex не матчить


with tempfile.TemporaryDirectory() as tmpdir:
    tmp_cache = Path(tmpdir) / "own_product_links_cache.json"
    tmp_cursor = Path(tmpdir) / "cursor.json"
    tmp_cache.write_text(
        json.dumps({str(i): {"prom_id": i, "url_text": f"slug-{i}"} for i in range(1, 16)}),
        encoding="utf-8",
    )

    _calls.clear()
    with mock.patch.object(lcv, "OWN_PRODUCT_LINKS_CACHE_FILE", tmp_cache), \
         mock.patch.object(lcv, "CURSOR_FILE", tmp_cursor), \
         mock.patch.object(ggf.requests, "get", side_effect=_fake_get_confirmed_gone):
        stats = lcv.validate(dry_run=True)

    _chk("15 підтверджено-відсутніх (> MAX_TRANSIENT_FAILS=12) НЕ абортують прогін",
         "aborted" not in stats)
    _chk("усі 15 перевірені (checked == 15, курсор дійшов до кінця)", stats.get("checked") == 15)
    _chk("усі 15 у confirmed_gone, 0 у unknown", stats.get("confirmed_gone") == 15
         and stats.get("unknown") == 0)


# ── 4. Справжній мережевий збій — ВСЕ ОДНО абортує (лічильник не зламано, лише звужено) ──
class _TransientError(Exception):
    """Підміна requests.exceptions.RequestException — має бути піймана як мережевий збій."""


def _fake_get_transient(url, **kwargs):
    _calls.append(url)
    raise ggf.requests.exceptions.RequestException("timeout")


with tempfile.TemporaryDirectory() as tmpdir:
    tmp_cache = Path(tmpdir) / "own_product_links_cache.json"
    tmp_cursor = Path(tmpdir) / "cursor.json"
    tmp_cache.write_text(
        json.dumps({str(i): {"prom_id": i, "url_text": f"slug-{i}"} for i in range(1, 16)}),
        encoding="utf-8",
    )

    _calls.clear()
    with mock.patch.object(lcv, "OWN_PRODUCT_LINKS_CACHE_FILE", tmp_cache), \
         mock.patch.object(lcv, "CURSOR_FILE", tmp_cursor), \
         mock.patch.object(ggf.requests, "get", side_effect=_fake_get_transient):
        stats = lcv.validate(dry_run=True)

    _chk("12 справжніх мережевих збоїв поспіль — прогін АБОРТУЄ", stats.get("aborted") == "transient")
    _chk("абортований прогін зупинився рівно на 12-му (не пройшов усі 15)", len(_calls) == 12)


# ── 5. ФІКС (2-й раунд аудиту 2026-09-25): 429/503 — теж транзієнт, не "підтверджено відсутній" ──
# requests НЕ кидає виняток на не-2xx (raise_for_status() тут не викликається) — без явної
# перевірки status_code 429/503 провалювались би в "unexpected" і НІКОЛИ не рахувались би в
# лічильник аборту, маскуючи справжній rate-limit/перевантаження prom.ua.
def _fake_get_ratelimited(url, **kwargs):
    _calls.append(url)
    # Чергуємо 429/503 — обидва мають рахуватись як transient.
    code = 429 if len(_calls) % 2 else 503
    return _FakeResponse("", status_code=code)


with tempfile.TemporaryDirectory() as tmpdir:
    tmp_cache = Path(tmpdir) / "own_product_links_cache.json"
    tmp_cursor = Path(tmpdir) / "cursor.json"
    tmp_cache.write_text(
        json.dumps({str(i): {"prom_id": i, "url_text": f"slug-{i}"} for i in range(1, 16)}),
        encoding="utf-8",
    )

    _calls.clear()
    with mock.patch.object(lcv, "OWN_PRODUCT_LINKS_CACHE_FILE", tmp_cache), \
         mock.patch.object(lcv, "CURSOR_FILE", tmp_cursor), \
         mock.patch.object(ggf.requests, "get", side_effect=_fake_get_ratelimited):
        stats = lcv.validate(dry_run=True)

    _chk("12 відповідей 429/503 поспіль — прогін АБОРТУЄ (не маскується як confirmed_gone)",
         stats.get("aborted") == "transient")
    _chk("абортований на 429/503 прогін зупинився рівно на 12-му", len(_calls) == 12)


if _FAILS:
    print(f"\n❌ ПРОВАЛЕНО: {len(_FAILS)} — {_FAILS}")
    sys.exit(1)
print("\n✅ link_cache_validator НЕ звертається до власної вітрини (інцидент 2026-08-15) "
      "і НЕ застряє на кластері підтверджено-відсутніх товарів (інцидент 2026-09-25)")
sys.exit(0)
