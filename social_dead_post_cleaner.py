"""social_dead_post_cleaner.py — щоденно прибирає FB-пости, чий товар ВИДАЛЕНО з магазина
(сторінка віддає 404). OOS (сторінка 200, «немає в наявності») НЕ чіпає — там вітрина чемна.
Instagram видалити через API не можна (обмеження Meta Content Publishing) — там тег/картка самі
показують недоступність, коли товар випадає з каталогу.

НАДІЙНІСТЬ — пост видаляється ЛИШЕ коли ДВА незалежні сигнали згодні:
  1. URL товару (збережений у ledger на момент посту) дає РІВНО 404;
  2. sku ВІДСУТНІЙ у own_product_links_cache (ре-валідатор #270 прибирає лише реально ЗНИКЛІ;
     переслаглені живі товари ЛИШАЄ з новим slug).
Отже переслаглений ЖИВИЙ товар (старий URL 404, але sku в кеші) — пост НЕ видаляємо. Мережева
помилка / 5xx / таймаут → не чіпаємо (транзієнт). Кеш недоступний → cross-check неможливий →
нічого не видаляємо (обережність). ЗАПОБІЖНИК: >25% «404» → аборт (масовий 404 = збій сайту).

Lock (спільний із постером) → не гнати ledger одночасно. Атомарний запис. `--dry-run`.
Запуск: щодня через systemd, ПІСЛЯ link-cache-validator (щоб cross-check ішов по свіжому кешу).
"""
import argparse
import json
import sys

import requests

from social_auto_poster import (FB_PAGE_ACCESS_TOKEN, GRAPH_VERSION, LEDGER,
                                _load_ledger, _acquire_lock)
from generate_google_feed import OWN_PRODUCT_LINKS_CACHE_FILE

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REQUEST_TIMEOUT = 20
SAFE_404_RATIO = 0.25


def _status(url: str):
    try:
        return requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True).status_code
    except requests.RequestException:
        return None   # мережа/таймаут → не 404 → не чіпаємо


def _live_skus():
    """set живих sku з own_product_links_cache, або None якщо кеш недоступний (тоді cross-check
    неможливий → нічого не видаляємо)."""
    try:
        d = json.loads(OWN_PRODUCT_LINKS_CACHE_FILE.read_text(encoding="utf-8"))
        return set(d) if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


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

    dead404 = [(sku, e) for sku, e in checkable.items() if _status(e["url"]) == 404]
    print(f"[dead-post] перевірено FB-постів: {len(checkable)}, з них 404: {len(dead404)}.")

    if len(dead404) / len(checkable) > SAFE_404_RATIO:
        print(f"[dead-post] АБОРТ: {len(dead404)}/{len(checkable)} "
              f"(>{int(SAFE_404_RATIO*100)}%) 404 — схоже на транзієнтний збій, нічого не видаляю.",
              file=sys.stderr)
        return {"checked": len(checkable), "dead404": len(dead404), "aborted": True}

    if live is None and dead404:
        print("[dead-post] own_product_links_cache недоступний → cross-check неможливий, "
              "жодного поста не чіпаю (обережність).", file=sys.stderr)
        return {"checked": len(checkable), "dead404": len(dead404), "deleted": 0}

    deleted = reslug = 0
    for sku, e in dead404:
        if sku in live:                 # 404, але sku ЖИВИЙ у кеші → переслаглено → лишаємо
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
