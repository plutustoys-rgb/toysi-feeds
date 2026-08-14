"""link_cache_validator.py — щоденна ре-валідація own_product_links_cache.json, щоб фіди
(Google Merchant / Bing / Meta) НЕ несли 404-лінки (втрата видимості товару, а GMC ще й
відхиляє товар — реальний інцидент 2026-07-27, CLAUDE.md).

ПРОБЛЕМА: кеш {pid: {prom_id, url_text}} тримає slug (`url_text`) товару. Коли живий товар
переслаглюється на Prom, старий slug 404-лить, а кеш ще старий → фід публікує мертвий лінк.
Делістнуті товари вже відсіюються фідом (нема цінового override), тож мертві — це ЖИВІ товари
з застарілим slug.

ЩО РОБИТЬ (пріоритет — НАДІЙНІСТЬ):
  1. Будує URL кожного запису (LINK_TEMPLATE) і паралельно перевіряє 200 (швидко).
  2. Для мертвих — послідовно (з джитером, щоб не гатити prom.ua) ре-резолвить свіжий slug
     через `_resolve_url_text(prom_id)`:
       • новий slug дає 200 → ОНОВЛЮЄ запис (товар лишається видимим із правильним лінком);
       • slug той самий і далі 404 / товар зник → ПРИБИРАЄ запис (фід його омітить, не 404).
  3. Атомарний запис кеша (temp+rename).

ЗАПОБІЖНИК (щоб транзієнтний збій prom.ua не витер кеш): якщо частка «до зміни» (оновлені+
прибрані) перевищує SAFE_CHANGE_RATIO — АБОРТ без запису (масові «мертві» = радше збій мережі,
а не реальні 404). Порожній/нечитабельний кеш → нічого не пишемо.

Запуск: раз/добу через systemd link-cache-validator.timer. `--dry-run` — лише звіт.
"""
import argparse
import concurrent.futures as cf
import json
import random
import sys
import time
from pathlib import Path

import requests

from generate_google_feed import (LINK_TEMPLATE, _resolve_url_text,
                                   OWN_PRODUCT_LINKS_CACHE_FILE, SEARCH_JITTER_RANGE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REQUEST_TIMEOUT = 20
CHECK_WORKERS = 16
SAFE_CHANGE_RATIO = 0.25   # >25% «мертвих» → ймовірно транзієнтний збій, НЕ чіпаємо кеш


def _url_for(entry: dict) -> str:
    return LINK_TEMPLATE.format(prom_id=entry["prom_id"], url_text=entry["url_text"])


def _is_200(url: str) -> bool:
    try:
        return requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True).status_code == 200
    except requests.RequestException:
        return False


def validate(dry_run: bool = False) -> dict:
    if not OWN_PRODUCT_LINKS_CACHE_FILE.exists():
        print("[link-val] кеша немає — нічого валідувати.", file=sys.stderr)
        return {"total": 0}
    try:
        cache = json.loads(OWN_PRODUCT_LINKS_CACHE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        print(f"[link-val] кеш нечитабельний ({e}) — не чіпаю.", file=sys.stderr)
        return {"total": 0}

    # Валідні записи (з prom_id+url_text). Решту лишаємо як є (не наша відповідальність).
    checkable = {pid: e for pid, e in cache.items()
                 if isinstance(e, dict) and e.get("prom_id") and e.get("url_text")}
    if not checkable:
        print("[link-val] немає повних записів для перевірки.", file=sys.stderr)
        return {"total": 0}

    # 1) Паралельна 200-перевірка.
    items = list(checkable.items())
    with cf.ThreadPoolExecutor(max_workers=CHECK_WORKERS) as ex:
        alive_flags = list(ex.map(lambda kv: _is_200(_url_for(kv[1])), items))
    dead = [items[i] for i, ok in enumerate(alive_flags) if not ok]
    print(f"[link-val] перевірено {len(items)}, живих {len(items) - len(dead)}, мертвих {len(dead)}.")

    # ЗАПОБІЖНИК: забагато «мертвих» → ймовірно збій мережі/сайту, НЕ переписуємо кеш.
    if dead and len(dead) / len(items) > SAFE_CHANGE_RATIO:
        print(f"[link-val] АБОРТ: {len(dead)}/{len(items)} ({100*len(dead)/len(items):.0f}%) мертвих "
              f"перевищує поріг {int(SAFE_CHANGE_RATIO*100)}% — схоже на транзієнтний збій, кеш НЕ чіпаю.",
              file=sys.stderr)
        return {"total": len(items), "dead": len(dead), "aborted": True}

    # 2) Ре-резолв мертвих (послідовно з джитером).
    fixed = dropped = 0
    for pid, entry in dead:
        new_slug = _resolve_url_text(entry["prom_id"])
        time.sleep(random.uniform(*SEARCH_JITTER_RANGE))
        if new_slug and new_slug != entry["url_text"] and \
                _is_200(LINK_TEMPLATE.format(prom_id=entry["prom_id"], url_text=new_slug)):
            cache[pid]["url_text"] = new_slug      # переслаглений живий товар → лагодимо лінк
            fixed += 1
        else:
            cache.pop(pid, None)                   # зник / slug не рятує → прибираємо (фід омітить)
            dropped += 1

    stats = {"total": len(items), "dead": len(dead), "fixed": fixed, "dropped": dropped}
    print(f"[link-val] полагоджено (новий slug): {fixed} | прибрано (зникли): {dropped}.")

    if dry_run:
        print("[link-val] --dry-run: кеш НЕ переписано.")
        return stats
    if fixed or dropped:
        tmp = OWN_PRODUCT_LINKS_CACHE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(OWN_PRODUCT_LINKS_CACHE_FILE)   # атомарний swap
        print(f"[link-val] кеш оновлено: {len(cache)} записів (було {len(cache)+dropped}).")
    else:
        print("[link-val] змін немає — кеш не переписано.")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Лише звіт, кеш не переписувати.")
    args = ap.parse_args()
    validate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
