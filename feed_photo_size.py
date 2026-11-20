# -*- coding: utf-8 -*-
"""feed_photo_size.py — Google/Meta/Bing: фото Prom < 500 px → оригінал Toysi, КОЛИ він більший (запит SEO 20.11.2026, «так, роби PR»).

ЧОМУ: GMC «image_too_small_for_high_resolution» (дедлайн Google 31.01.2027). Заміряно 20.11 по живому фіду (одиниця — унікальний g:id): із 3 340 товарів з фото
images.prom.ua 279 (8,4 %) мають меншу сторону < 500 px — Prom віддає максимум 500 px і не збільшує. Для них оригінал `https://toysi.ua/p/<pid>.jpg` існує (160 з 160) і в ≈85 %
(51 із 60) має обидві сторони ≥ 500 → ≈ 237 товарів. Решта (≈ 173) — фото постачальників, більшого немає (чекає фотобанк KIDDISVIT).

ЩО РОБИТЬ: для товарів, чий image_link — images.prom.ua, дивиться розмір цього фото; лише коли менша сторона < MIN_SIDE (500) І оригінал Toysi має БІЛЬШУ меншу сторону — віддає
оригінал Toysi. Розмір невідомий (нема в кеші / збій мережі / не картинка) → фото НЕ змінюємо (як було). Нічого не вгадуємо: розмір береться лише з заголовка самого файлу
(перші байти JPEG/PNG/WEBP, Range-запит).

ДЕ ЖИВЕ МЕРЕЖА: ТІЛЬКИ в `prefetch()` (його кличуть main() генераторів ПЕРЕД збіркою; бюджет часу; збій не валить фід). `build_feed_items` лише ЧИТАЄ кеш (`lookup`), без мережі —
тому тести й холодний кеш поводяться як раніше. Кеш `feed_photo_dims_cache.json` (gitignore; VPS: /opt/plutustoys) {url: {"d":[w,h]|null,"t":ts}}, пише атомарно (tmp + os.replace);
успіх діє 30 діб (URL Prom містить ідентифікатор файлу), збій — 1 добу. Перший прогін на холодному кеші заповнює його частково (бюджет), наступні докінчують.

БЕЗПЕКА: тягнемо лише https://images.prom.ua і https://toysi.ua (без SSRF на довільні хости); таймаут; читаємо ≤ 64 КБ. Вимкнути без релізу: FEED_SMALL_PHOTO_FIX=0 (у .env VPS).
"""
import json
import os
import struct
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

MIN_SIDE = 500                      # менша сторона, нижче якої Google вважає фото замалим для high-resolution (SEO 20.11: 410 товарів < 500)
OK_TTL_S = 30 * 24 * 3600
FAIL_TTL_S = 24 * 3600
ALLOWED_HOSTS = ("images.prom.ua", "toysi.ua")
READ_CAP = 65536
TIMEOUT_S = 15
DEFAULT_BUDGET_S = 300.0
WORKERS = 8


def fix_on() -> bool:
    return os.environ.get("FEED_SMALL_PHOTO_FIX", "1").strip() != "0"


def cache_path() -> Path:
    return Path(os.environ.get("FEED_PHOTO_DIMS_CACHE") or (Path(__file__).resolve().parent / "feed_photo_dims_cache.json"))


def parse_dims(data: bytes):
    """(ширина, висота) із перших байтів JPEG/PNG/WEBP; інакше None."""
    try:
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                m = data[i + 1]
                if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                    i += 2
                    continue
                if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return (w, h) if w and h else None
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
            return None
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", data[16:24])
            return (w, h) if w and h else None
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            if data[12:16] == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return (w & 0x3FFF, h & 0x3FFF)
            if data[12:16] == b"VP8X":
                return (1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little"))
            if data[12:16] == b"VP8L":
                b = struct.unpack("<I", data[21:25])[0]
                return ((b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1)
    except (struct.error, IndexError):
        return None
    return None


def host_allowed(url) -> bool:
    if not isinstance(url, str):
        return False
    p = urlparse(url)
    return p.scheme == "https" and p.hostname in ALLOWED_HOSTS and not p.username and not p.password


def fetch_dims(url):
    """Розмір фото за URL (Range-запит, лише дозволені хости). Збій/не картинка → None. Ніколи не кидає."""
    if not host_allowed(url):
        return None
    try:
        rq = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (PlutusToys feed photo check)", "Range": f"bytes=0-{READ_CAP - 1}"})
        with urllib.request.urlopen(rq, timeout=TIMEOUT_S) as r:
            if not host_allowed(r.geturl()):   # аудит #1861: редірект на інший хост/схему — не довіряємо
                return None
            return parse_dims(r.read(READ_CAP))
    except Exception:  # noqa: BLE001 — мережа/HTTP/таймаут: розмір невідомий → фото не міняємо
        return None


_CACHE = None


def _load() -> dict:
    global _CACHE
    if _CACHE is None:
        try:
            d = json.loads(cache_path().read_text(encoding="utf-8"))
            _CACHE = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            _CACHE = {}
    return _CACHE


def reset_cache():
    """Лише для тестів: скинути пам'ять (файл не чіпає)."""
    global _CACHE
    _CACHE = None


def _save(cache: dict):
    p = cache_path()
    tmp = p.with_name(f"{p.name}.tmp.{os.getpid()}")   # аудит #1861: два одночасні запуски не пишуть в один tmp
    tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, p)


def lookup(url):
    """(w, h) з кешу або None (нема/збій/прострочено не перевіряємо — це читач без мережі)."""
    e = _load().get(url)
    if isinstance(e, dict):
        d = e.get("d")
        if isinstance(d, list) and len(d) == 2 and all(isinstance(x, int) and x > 0 for x in d):
            return (d[0], d[1])
    return None


def _fresh(entry, now) -> bool:
    if not isinstance(entry, dict):
        return False
    ttl = OK_TTL_S if entry.get("d") else FAIL_TTL_S
    try:
        return now - float(entry.get("t", 0)) < ttl
    except (TypeError, ValueError):
        return False


def _probe(urls, budget_s, now, workers=WORKERS) -> dict:
    """Опитати url без свіжого запису кешу в межах бюджету. Повертає {url: (w,h)|None} лише для опитаних."""
    cache = _load()
    todo = [u for u in dict.fromkeys(urls) if host_allowed(u) and not _fresh(cache.get(u), now)]
    out = {}
    if not todo:
        return out
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        it = iter(todo)
        pending = {}
        done_all = False
        while pending or not done_all:
            while not done_all and len(pending) < workers * 2 and time.time() - t0 <= budget_s:
                try:
                    u = next(it)
                except StopIteration:
                    done_all = True
                    break
                pending[u] = ex.submit(fetch_dims, u)
            if time.time() - t0 > budget_s:
                done_all = True       # бюджет вичерпано: нових не беремо, дочікуємось запущених
            if not pending:
                break
            u, fut = next(iter(pending.items()))
            out[u] = fut.result()
            del pending[u]
    for u, d in out.items():
        old = cache.get(u)
        if d is None and isinstance(old, dict) and old.get("d"):
            cache[u] = {"d": old["d"], "t": now}   # аудит #1861: тимчасовий збій не затирає давній успіх (розмір файлу за тим URL не міняється)
        else:
            cache[u] = {"d": list(d) if d else None, "t": now}
    return out


def prefetch(pairs, budget_s: float = DEFAULT_BUDGET_S, now: float | None = None) -> dict:
    """pairs: [(prom_url, toysi_original_url|None)]. Крок 1: розмір Prom-фото. Крок 2: розмір оригіналу Toysi лише там, де Prom < MIN_SIDE.
    Пише кеш. Статистика {"prom": n опитано, "toysi": n опитано, "need": скільки Prom < MIN_SIDE (за кешем), "swap": скільки замін діятиме}. Ніколи не кидає."""
    st = {"prom": 0, "toysi": 0, "need": 0, "swap": 0}
    if not fix_on():
        return st
    try:
        now = time.time() if now is None else now
        t0 = time.time()
        st["prom"] = len(_probe([p for p, _ in pairs], budget_s, now))
        small = [(p, t) for p, t in pairs if t and (lookup(p) and min(lookup(p)) < MIN_SIDE)]
        st["need"] = len(small)
        left = max(0.0, budget_s - (time.time() - t0))
        st["toysi"] = len(_probe([t for _, t in small], left, now))
        st["swap"] = sum(1 for p, t in small if choose_image(p, t) != p)
        _save(_load())
    except Exception as e:  # noqa: BLE001 — найкращі зусилля, фід не валимо
        print(f"[feed_photo_size] prefetch збій ({type(e).__name__}: {str(e)[:120]}) — фото лишаються як були.", file=sys.stderr)
    return st


def choose_image(prom_url, toysi_url):
    """Фото для фіду: оригінал Toysi — лише коли Prom-фото відоме, менше MIN_SIDE, а оригінал відомий і більший за меншою стороною. Інакше — prom_url як було."""
    if not fix_on() or not toysi_url or toysi_url == prom_url:
        return prom_url
    p, t = lookup(prom_url), lookup(toysi_url)
    if p and t and min(p) < MIN_SIDE and min(t) > min(p):
        return toysi_url
    return prom_url
