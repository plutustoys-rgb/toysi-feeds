"""social_dead_post_cleaner.py — щоденно прибирає FB-пости, чий товар ВИДАЛЕНО з магазина
(сторінка віддає 404). OOS (сторінка 200, «немає в наявності») НЕ чіпає — там вітрина чемна.
Instagram видалити через API не можна (обмеження Meta Content Publishing) — там тег/картка самі
показують недоступність, коли товар випадає з каталогу.

НАДІЙНІСТЬ — пост видаляється ЛИШЕ коли ДВА незалежні сигнали згодні:
  1. URL товару (збережений у ledger на момент посту) дає РІВНО 404;
  2. sku ВІДСУТНІЙ серед <g:id> поточного meta_feed.xml (видалений товар випадає з фіду;
     переслаглений живий — лишається з новим лінком; OOS теж у фіді). Той самий ключ-простір,
     що й ledger (обидва — g:id фіду), тож без розсинхрону ключів.
Отже переслаглений ЖИВИЙ товар (старий URL 404, але sku ще у фіді) — пост НЕ видаляємо. Мережева
помилка / 5xx / таймаут → не чіпаємо (транзієнт). Фід недоступний → cross-check неможливий →
нічого не видаляємо (обережність). ЗАПОБІЖНИК: >25% «404» → аборт (масовий 404 = збій сайту).

Lock (спільний із постером) → не гнати ledger одночасно. Атомарний запис. `--dry-run`.
Запуск: щодня через systemd, ПІСЛЯ link-cache-validator (щоб cross-check ішов по свіжому кешу).
"""
import argparse
import json
import random
import sys
import time
import xml.etree.ElementTree as ET

import requests

from social_auto_poster import (FB_PAGE_ACCESS_TOKEN, GRAPH_VERSION, LEDGER,
                                META_FEED, NS, _load_ledger, _acquire_lock)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REQUEST_TIMEOUT = 20
SAFE_404_RATIO = 0.25
# ⚠️ Stage 2 (інцидент rate-limit 2026-08-15): не гатити вітрину. Перевіряємо ПОСЛІДОВНО з
# джитером; серія «недоступних» (429/5xx/timeout/мережа) → вітрина глушить нас → АБОРТ (не
# видаляємо нічого цього прогону). Тільки РІВНО 404 = кандидат на видалення; троттл ≠ 404.
CHECK_JITTER = (0.3, 0.6)
MAX_TRANSIENT_FAILS = 8


def _status(url: str):
    try:
        return requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True).status_code
    except requests.RequestException:
        return None   # мережа/таймаут → не 404 → не чіпаємо


def _live_skus():
    """set sku ЖИВИХ товарів = усі <g:id> з meta_feed.xml (будь-яка наявність, включно OOS —
    фід несе й out-of-stock). НАВМИСНО meta_feed, а не own_product_links_cache: ledger ключується
    тим самим <g:id> фіду, тож ключ-простір збігається (кеш ключується pid → міг би розсинхрон).
    Товар, ВИДАЛЕНИЙ з магазина, у фіді відсутній; переслаглений живий — присутній (з новим
    лінком). Фід відсутній/битий/порожній → None (cross-check неможливий → нічого не видаляємо)."""
    if not META_FEED.exists():
        return None
    try:
        root = ET.parse(META_FEED).getroot()
    except ET.ParseError:
        return None
    ids = {(it.findtext("g:id", namespaces=NS) or it.findtext("id") or "").strip()
           for it in root.iter("item")}
    ids.discard("")
    return ids or None


def _delete_fb_post(post_id: str) -> bool:
    try:
        r = requests.delete(f"https://graph.facebook.com/{GRAPH_VERSION}/{post_id}",
                            params={"access_token": FB_PAGE_ACCESS_TOKEN}, timeout=REQUEST_TIMEOUT)
        j = r.json() if r.content else {}
        return bool(r.ok and j.get("success", True))
    except (requests.RequestException, ValueError):
        return False


def _save_ledger_atomic(led: dict) -> None:
    tmp = LEDGER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(led, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LEDGER)


def clean(dry_run: bool = False) -> dict:
    if not FB_PAGE_ACCESS_TOKEN:
        print("[dead-post] нема FB_PAGE_ACCESS_TOKEN — no-op.", file=sys.stderr)
        return {"deleted": 0}
    ledger = _load_ledger()
    fb = ledger.get("fb", {})   # посилання на під-словник ledger → pop модифікує ledger
    live = _live_skus()          # set або None
    checkable = {sku: e for sku, e in fb.items()
                 if isinstance(e, dict) and e.get("post_id") and e.get("url")}
    if not checkable:
        print("[dead-post] нема FB-постів з url для перевірки.")
        return {"deleted": 0}

    # Послідовна перевірка з джитером + backoff: серія недоступних (429/5xx/timeout/мережа)
    # = вітрина нас глушить → аборт (не робимо хибних рішень і не довбаємо далі).
    dead404, transient_streak = [], 0
    items = list(checkable.items())
    for idx, (sku, e) in enumerate(items):
        code = _status(e["url"])
        if idx + 1 < len(items):
            time.sleep(random.uniform(*CHECK_JITTER))
        if code == 404:
            dead404.append((sku, e))
            transient_streak = 0
        elif code == 200:
            transient_streak = 0
        else:                      # None / 429 / 5xx / інше — недоступно, НЕ 404
            transient_streak += 1
            if transient_streak >= MAX_TRANSIENT_FAILS:
                print(f"[dead-post] АБОРТ: {transient_streak} недоступних поспіль "
                      f"(rate-limit/збій вітрини) — нічого не видаляю цього прогону.",
                      file=sys.stderr)
                return {"checked": idx + 1, "dead404": len(dead404), "aborted": True}
    print(f"[dead-post] перевірено FB-постів: {len(checkable)}, з них 404: {len(dead404)}.")

    if len(dead404) / len(checkable) > SAFE_404_RATIO:
        print(f"[dead-post] АБОРТ: {len(dead404)}/{len(checkable)} "
              f"(>{int(SAFE_404_RATIO*100)}%) 404 — схоже на транзієнтний збій, нічого не видаляю.",
              file=sys.stderr)
        return {"checked": len(checkable), "dead404": len(dead404), "aborted": True}

    if live is None and dead404:
        print("[dead-post] meta_feed недоступний → cross-check неможливий, "
              "жодного поста не чіпаю (обережність).", file=sys.stderr)
        return {"checked": len(checkable), "dead404": len(dead404), "deleted": 0}

    deleted = reslug = 0
    for sku, e in dead404:
        if sku in live:                 # 404, але sku ЩЕ У ФІДІ → переслаглено (живий) → лишаємо
            reslug += 1
            continue
        if dry_run:                     # 404 + відсутній у кеші → видалено
            deleted += 1
            print(f"[dead-post] (dry) видалив би FB-пост {sku} ({e['post_id']})")
            continue
        if _delete_fb_post(e["post_id"]):
            fb.pop(sku, None)
            deleted += 1
            print(f"[dead-post] видалено FB-пост видаленого товару {sku} → {e['post_id']}")
        else:
            print(f"[dead-post] не вдалось видалити пост {sku} ({e['post_id']}) — лишаю ledger.",
                  file=sys.stderr)

    print(f"[dead-post] видалено: {deleted} | переслаглені (лишено): {reslug}.")
    if deleted and not dry_run:
        _save_ledger_atomic(ledger)
    return {"checked": len(checkable), "dead404": len(dead404), "deleted": deleted, "reslug": reslug}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Лише звіт, нічого не видаляти.")
    args = ap.parse_args()
    lock = _acquire_lock()   # спільний із постером → не гнати ledger одночасно
    if not lock:
        print("[dead-post] соц-постер/чистильник уже працює — пропускаю.", file=sys.stderr)
        return
    clean(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
