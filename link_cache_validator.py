"""link_cache_validator.py — ре-валідація own_product_links_cache.json, щоб фіди
(Google Merchant / Bing / Meta) НЕ несли 404-лінки (втрата видимості; GMC ще й відхиляє
товар — реальний інцидент 2026-07-27, CLAUDE.md).

ПРОБЛЕМА, ЯКУ РОЗВ'ЯЗУЄМО: кеш {pid: {prom_id, url_text}} тримає slug (`url_text`). Коли
ЖИВИЙ товар переслаглюється на Prom (зміна назви), старий slug у фіді 404-лить, а кеш ще
старий. Треба замінити його на свіжий канонічний slug.

⚠️ РЕДИЗАЙН 2026-08-15 (інцидент rate-limit, Stage 2). РАНІШЕ цей валідатор для перевірки
робив 5354 паралельних GET-и (16 воркерів) на НАШУ вітрину `plutustoys.com.ua` одним сплеском
→ Cloudflare rate-limit-ив наш IP → живі сторінки віддавали 429/timeout → виглядали «мертвими»,
а заразом троттл бив і по соц-постеру (той теж GET-ив вітрину). Тепер валідатор:

  • НЕ ЧІПАЄ НАШУ ВІТРИНУ ВЗАГАЛІ. Канонічний slug береться з prom.ua детермінованим запитом
    `_resolve_url_text(prom_id)` (allow_redirects=False, по СТАБІЛЬНОМУ числовому prom_id →
    301 Location із реальним slug). Це те саме джерело істини, яким slug спочатку й будувався.
  • РОТАЦІЙНО: за прогін перевіряє лише BATCH_SIZE записів (курсор у стані), обходячи весь
    каталог за ~кілька прогонів. Переслаглення — рідкість, тож щоденний повний обхід не потрібен.
  • ПОСЛІДОВНО з джитером (SEARCH_JITTER_RANGE, ~0.4-0.6с) — не сплеск, а рівний рівчак.
  • BACKOFF: серія MAX_TRANSIENT_FAILS підряд «невідомих» (None: мережа/блок/капча на prom.ua)
    → АБОРТ прогону (prom.ua нас глушить) — не пишемо, курсор не рушимо (наступний прогін
    повторить те саме вікно).

ЩО РОБИТЬ ІЗ КОЖНИМ ЗАПИСОМ ПАРТІЇ:
  • новий slug є і ВІДРІЗНЯЄТЬСЯ → ОНОВЛЮЄ url_text (переслаглений живий товар полагоджено);
  • новий slug є і той самий → запис коректний, нічого;
  • None (мережа/блок/зникле оголошення) → лишаємо як є (не тримаємо storefront-сигналу, щоб
    впевнено видаляти; делістнуті товари фід І ТАК омітить — нема цінового override, тож
    застарілий запис у кеші для делістнутого товару НЕШКІДЛИВИЙ, бо у фід не потрапляє).

ЗАПОБІЖНИК ФОРМАТУ: якщо серед РЕЗУЛЬТАТИВНИХ (не-None) записів партії частка «змінених»
перевищує SAFE_CHANGE_RATIO — АБОРТ без запису (масова «зміна» = радше Prom змінив формат URL,
а не всі товари раптом переслаглись). Атомарний запис (temp+rename). `--dry-run` — лише звіт.

Запуск: раз/добу через systemd link-cache-validator.timer (наразі ВИМКНЕНО власником до
розкатки цього редизайну — див. CODE_LOG 2026-08-15).
"""
import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

from generate_google_feed import (_resolve_url_text, OWN_PRODUCT_LINKS_CACHE_FILE,
                                   SEARCH_JITTER_RANGE)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Скільки записів перевіряємо за прогін (ротація курсором). 500×~0.5с ≈ 4 хв; весь каталог
# (~5.4k) за ~11 прогонів. Env-оверрайд — для тестів/тюнінгу.
BATCH_SIZE = int(os.environ.get("PLUTUS_LINKVAL_BATCH", "500"))
# Серія None підряд (мережа/блок prom.ua) → аборт, щоб не гатити далі й не робити хибних змін.
MAX_TRANSIENT_FAILS = int(os.environ.get("PLUTUS_LINKVAL_MAX_FAILS", "12"))
SAFE_CHANGE_RATIO = 0.25   # >25% «змінених» серед результативних → ймовірно зміна формату URL
CURSOR_FILE = Path(__file__).parent / "link_cache_validator_cursor.json"


def _load_cursor(total: int) -> int:
    """Зсув у СТАБІЛЬНО впорядкованому списку записів, з якого продовжуємо. Битий/відсутній
    стан → 0. Нормалізуємо за модулем на випадок, якщо каталог зменшився між прогонами."""
    try:
        off = int(json.loads(CURSOR_FILE.read_text(encoding="utf-8")).get("offset", 0))
    except (ValueError, OSError, AttributeError, TypeError):
        off = 0
    return off % total if total else 0


def _save_cursor(offset: int) -> None:
    tmp = CURSOR_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"offset": offset}), encoding="utf-8")
    tmp.replace(CURSOR_FILE)


def validate(dry_run: bool = False) -> dict:
    if not OWN_PRODUCT_LINKS_CACHE_FILE.exists():
        print("[link-val] кеша немає — нічого валідувати.", file=sys.stderr)
        return {"total": 0}
    try:
        cache = json.loads(OWN_PRODUCT_LINKS_CACHE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        print(f"[link-val] кеш нечитабельний ({e}) — не чіпаю.", file=sys.stderr)
        return {"total": 0}

    # Валідні записи (prom_id+url_text). Стабільний порядок за ключем pid — щоб курсор
    # означав те саме між прогонами (dict-порядок вставки міг би зсунутись при перезаписі кеша).
    checkable = sorted(
        ((pid, e) for pid, e in cache.items()
         if isinstance(e, dict) and e.get("prom_id") and e.get("url_text")),
        key=lambda kv: str(kv[0]),
    )
    total = len(checkable)
    if not total:
        print("[link-val] немає повних записів для перевірки.", file=sys.stderr)
        return {"total": 0}

    # Ротаційна партія від курсора (з обгортанням). Дедуп на випадок BATCH_SIZE >= total.
    offset = _load_cursor(total)
    take = min(BATCH_SIZE, total)
    seen_idx, batch = set(), []
    for k in range(take):
        i = (offset + k) % total
        if i in seen_idx:
            break
        seen_idx.add(i)
        batch.append(checkable[i])
    print(f"[link-val] всього {total}, партія {len(batch)} від зсуву {offset} "
          f"(prom.ua ре-резолв, вітрину НЕ чіпаємо).")

    fixed = unchanged = unknown = 0
    transient_streak = 0
    updates = {}   # pid -> новий slug (застосуємо разом після guard-перевірки)
    for pid, entry in batch:
        new_slug = _resolve_url_text(entry["prom_id"])
        time.sleep(random.uniform(*SEARCH_JITTER_RANGE))
        if new_slug is None:
            unknown += 1
            transient_streak += 1
            if transient_streak >= MAX_TRANSIENT_FAILS:
                print(f"[link-val] АБОРТ: {transient_streak} невідомих поспіль — prom.ua схоже "
                      f"нас глушить/блокує. Кеш і курсор НЕ чіпаю, повторю наступного прогону.",
                      file=sys.stderr)
                return {"total": total, "checked": fixed + unchanged + unknown,
                        "unknown": unknown, "aborted": "transient"}
            continue
        transient_streak = 0
        if new_slug != entry["url_text"]:
            updates[pid] = new_slug
            fixed += 1
        else:
            unchanged += 1

    decisive = fixed + unchanged
    # ЗАПОБІЖНИК ФОРМАТУ: забагато «змінених» серед результативних → ймовірно зміна формату URL
    # на Prom (regex тепер ловить інше), а не реальна масова переслаглення. Не пишемо.
    if decisive and fixed / decisive > SAFE_CHANGE_RATIO:
        print(f"[link-val] АБОРТ: {fixed}/{decisive} ({100*fixed/decisive:.0f}%) «змінених» "
              f"перевищує поріг {int(SAFE_CHANGE_RATIO*100)}% — схоже на зміну формату URL, "
              f"кеш НЕ переписую.", file=sys.stderr)
        return {"total": total, "checked": decisive + unknown, "fixed": fixed,
                "unknown": unknown, "aborted": "ratio"}

    print(f"[link-val] полагоджено (новий slug): {fixed} | без змін: {unchanged} | "
          f"невідомих (лишено): {unknown}.")
    stats = {"total": total, "checked": decisive + unknown, "fixed": fixed,
             "unchanged": unchanged, "unknown": unknown}

    if dry_run:
        print("[link-val] --dry-run: кеш і курсор НЕ переписано.")
        return stats

    # Застосовуємо оновлення slug і атомарно пишемо кеш (лише якщо були зміни).
    if updates:
        for pid, slug in updates.items():
            if pid in cache and isinstance(cache[pid], dict):
                cache[pid]["url_text"] = slug
        tmp = OWN_PRODUCT_LINKS_CACHE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(OWN_PRODUCT_LINKS_CACHE_FILE)
        print(f"[link-val] кеш оновлено: {len(updates)} slug'ів.")
    else:
        print("[link-val] змін немає — кеш не переписано.")

    # Курсор рушимо ЛИШЕ після успішного (не-абортованого) прогону.
    _save_cursor((offset + len(batch)) % total)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Лише звіт, кеш/курсор не переписувати.")
    args = ap.parse_args()
    validate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
