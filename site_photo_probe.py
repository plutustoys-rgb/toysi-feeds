#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""site_photo_probe.py — детектор «тихого зникнення фото» каталогу plutustoys.com.ua (Консультант 10.10.2026).

ПРОБЛЕМА: усі ~21 тис. фото сайту — хотлінк на toysi.ua; одне правило hotlink-захисту в їхньому nginx — і каталог лишається без картинок, а наші
сторінки далі віддають 200 (жоден монітор нічого не бачить). Дзеркалити 21 тис. фото (~715 МБ) зараз не на часі — натомість збій робимо ВИДИМИМ.

ЩО РОБИТЬ: після кожної збірки сайту (site-rebuild.service, кожні 2 год, VPS) бере вибірку PROBE_N випадкових фото з index.json, тягне кожне з НАШИМ Referer
(як справжній відвідувач) і порушенням вважає будь-що, крім HTTP 200 + image/* + непорожнє тіло. Алерт самодіагностичний: що питали, який код, скільки з вибірки.
Часткове блокування теж ловимо (≥1 поганого з вибірки, а не «усі впали»). Best-effort: збій самого зонда НЕ валить збірку.
"""
import random
from concurrent.futures import ThreadPoolExecutor

PROBE_N = 20
PROBE_TIMEOUT = 15
REFERER = "https://plutustoys.com.ua/"
UA = "Mozilla/5.0 (compatible; PlutusToysPhotoProbe/1.0; +https://plutustoys.com.ua/)"
ALERT_KEY = "site_photo_probe"
ALERT_COOLDOWN_SEC = 6 * 60 * 60


def pick_sample(idx, n=PROBE_N, rng=None):
    """n випадкових унікальних фото (url) з index.json; порожні/нерядкові пропускає."""
    rng = rng or random
    urls = sorted({p.get("p") for p in (idx or []) if isinstance(p, dict) and isinstance(p.get("p"), str) and p["p"].startswith("http")})
    return rng.sample(urls, min(n, len(urls)))


def probe_one(url, get=None):
    """Один запит: {url, status, ctype, size, err}. get — підміна requests.get для тестів."""
    out = {"url": url, "status": None, "ctype": "", "size": 0, "err": ""}
    try:
        if get is None:
            import requests
            get = requests.get
        r = get(url, headers={"Referer": REFERER, "User-Agent": UA}, timeout=PROBE_TIMEOUT, stream=True)
        out["status"] = r.status_code
        out["ctype"] = (r.headers.get("Content-Type") or "").lower()
        for chunk in r.iter_content(8192):      # перший непорожній шматок достатньо: «нульовий розмір» = нема жодного байта
            out["size"] = len(chunk)
            break
        try:
            r.close()
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        out["err"] = f"{type(e).__name__}"
    return out


def is_bad(res) -> bool:
    return not (res["status"] == 200 and res["ctype"].startswith("image/") and res["size"] > 0)


def evaluate(results):
    """Список поганих результатів (порожній = норма)."""
    return [r for r in results if is_bad(r)]


def format_alert(results, bad):
    codes = {}
    for r in bad:
        k = r["err"] or f"HTTP {r['status']}" + ("" if r["ctype"].startswith("image/") or r["status"] != 200 else f" (не image: {r['ctype'] or '—'})") + ("" if r["size"] or r["status"] != 200 else " (порожнє тіло)")
        codes[k] = codes.get(k, 0) + 1
    ex = bad[0]["url"]
    return (f"🚨 Фото каталогу plutustoys.com.ua: {len(bad)} з {len(results)} випадкових фото не віддалися з нашим Referer ({REFERER}). "
            f"Причини: {', '.join(f'{k} ×{v}' for k, v in codes.items())}. Приклад: {ex}. "
            "Усі фото — хотлінк на toysi.ua: ймовірно hotlink-захист/збій у Toysi (наші сторінки при цьому віддають 200, тож тиша). "
            "Що робити: відкрити сторінку каталогу у браузері; якщо картинок нема — вирішувати дзеркалення фото (Консультант: ~715 МБ).")


def run(idx, notify=None, get=None, rng=None, n=PROBE_N):
    """Повертає (results, bad). notify(dedup_key, text, cooldown) — за замовчуванням telegram_notify.send_throttled_alert."""
    urls = pick_sample(idx, n, rng)
    if not urls:
        return [], []
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda u: probe_one(u, get), urls))
    bad = evaluate(results)
    print(f"[photo_probe] вибірка {len(results)}, поганих {len(bad)}")
    if bad:
        text = format_alert(results, bad)
        print("[photo_probe] " + text)
        if notify is None:
            try:
                from telegram_notify import send_throttled_alert
                notify = lambda k, t, c: send_throttled_alert(k, t, cooldown_sec=c)  # noqa: E731
            except Exception as e:  # noqa: BLE001
                print(f"[photo_probe] нема каналу алертів: {type(e).__name__}")
                notify = None
        if notify:
            try:
                notify(ALERT_KEY, text, ALERT_COOLDOWN_SEC)
            except Exception as e:  # noqa: BLE001
                print(f"[photo_probe] алерт не надіслано: {type(e).__name__}")
    return results, bad
