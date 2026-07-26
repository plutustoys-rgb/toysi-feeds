"""apply_live_dumping_fix.py — ручний, разовий контролер: живе прибирання
демпінгу з вітрини Prom, ЧЕРЕЗ apply_price(), не чекаючи звичайного
репрайсера.

НАВІЩО (пряме прохання власниці, 2026-07-26, її словами — див.
spec_2026-07-26_manual_live_price_controller.md, Security audit:
rozetka_agent): "Треба з вітрини прибрати демпінгові ціни, треба
виставити наші 6000 товарів на рівень конкурент - 3 грн, не чекаючи
репрайсера, ця операція контролера буде використовуватися вручну."

ІСТОРІЯ: перша версія цього файлу (apply_cached_scan_prices.py, PR #171)
писала результат ЛИШЕ у price_state — аудит (code_report_2026-07-26_pt5.md)
знайшов, що для цільової популяції (SKU ПОЗА топ-N) це НІКОЛИ не потрапляє
на вітрину: Prom імпортує лише prom_feed_top.xml, який будується лише для
pid з top_catalog; price_overrides для pid поза ним просто не читається.
Правильно порахована ціна лежала мертвим вантажем. Цей файл — переписана
версія, що РЕАЛЬНО патчить ціну через apply_price() (prom_api_client.py).

ОХОПЛЕННЯ — ОБИДВА джерела, які реально ЖИВІ на Prom зараз:
1. top_catalog (select_top_items(), поточний топ-N) — "наші 6000 товарів"
   власниці. Використовує КЕШОВАНОГО конкурента з нічного скану (не живий
   пошук) — свідомий компроміс свіжості заради швидкості одного разового
   прогону по тисячах SKU (жива вартість пошуку — SEARCH_DELAY на кожен
   SKU, це зробило б разовий ручний прогін непридатним).
2. SKU ПОЗА топ-N, але живі на Prom (_rotated_out_scan_candidates() —
   той самий live_prom_ids-фільтр, що вже є в prom_competitor_pricer.py).

Формула — decide_price_for_platform() БЕЗ ЗМІН (канонічна формула
власниці, PR #168): де можна прибутково підрізати конкурента —
підрізає (candidate = конкурент - 3₴); де не можна — піднімає до
безпечної нижньої межі (floor), а не й далі демпінгує. НІКОЛИ не робить
delist (той самий принцип, що й _decide_from_scan_entry() — дані
нічного скану недостатньо надійні для живого delist без
verify_competitor_really_available()).

БЕЗПЕКА для потенційно тисяч живих apply_price()-викликів за один
прогін:
- Часовий бюджет (MAX_RUNTIME_SECONDS, той самий патерн, що й PR #169) —
  добровільна зупинка з збереженням прогресу, не сліпе сподівання
  "встигне".
- Резюмований прогрес (PROGRESS_FILE) — SKU, вже оброблені в ЦЬОМУ
  sweep'і (незалежно від успіху/помилки — щоб постійна помилка на
  одному SKU не блокувала прогрес назавжди при повторних запусках),
  зберігаються окремо від price_state, тож повторний ручний запуск
  ПРОДОВЖУЄ, а не починає каталог заново. Щойно весь цільовий обсяг
  покрито — прогрес автоматично скидається, наступний запуск починає
  новий sweep (напр. після наступної зміни формули).
- Троттлінг (APPLY_THROTTLE_SECONDS) між живими apply_price()-викликами —
  захист від rate-limit/429 (той самий клас проблеми, що вже підтверджено
  живо для fetch_prom_products() у prom_catalog_sync.py).
- SKU на непідтвердженій комісії (PROM_COMMISSION_DEFAULT) виключені з
  живого apply_price() (той самий гейт, що й у звичайному репрайсері) —
  ціна все одно йде у price_state для фіда, потребує ручного перегляду.

Запуск:
    python apply_live_dumping_fix.py
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

from parser import fetch_toysi_catalog, assert_catalog_size_sane, CatalogSizeError
from competitor_pricing import load_prom_price_state, save_prom_price_state, real_toysi_cost
from generate_prom_feed_top import select_top_items, load_scan_state
from prom_competitor_pricer import (
    _rotated_out_scan_candidates,
    _decide_from_scan_entry,
    _category_commission_is_default,
    _load_prom_category_cache,
)
from prom_api_client import PromEditError, apply_price
from telegram_notify import send_telegram_message

SAVE_EVERY = 200
APPLY_THROTTLE_SECONDS = 0.2  # консервативна пауза між живими apply_price()-викликами, немає задокументованого офіційного ліміту Prom API
MAX_RUNTIME_SECONDS = 5 * 3600

PROGRESS_FILE = Path(__file__).parent / "live_dumping_fix_progress.json"

_RUN_DEADLINE: float | None = None


def _time_budget_exceeded() -> bool:
    return _RUN_DEADLINE is not None and time.monotonic() >= _RUN_DEADLINE


def _load_progress() -> dict:
    if not PROGRESS_FILE.exists():
        return {}
    try:
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_progress(progress: dict) -> None:
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    global _RUN_DEADLINE
    _RUN_DEADLINE = time.monotonic() + MAX_RUNTIME_SECONDS

    price_state = load_prom_price_state()

    print("[LiveDumpingFix] Завантажую каталог Toysi...")
    toysi_catalog = fetch_toysi_catalog()
    try:
        assert_catalog_size_sane(toysi_catalog)
    except CatalogSizeError as e:
        print(f"[LiveDumpingFix] {e}", file=sys.stderr)
        sys.exit(1)

    top_catalog = select_top_items(toysi_catalog)
    scan_state = load_scan_state()
    prom_category_cache = _load_prom_category_cache()

    live_prom_ids = None
    try:
        from generate_google_feed import load_prom_products_cache
        _live = load_prom_products_cache()
        live_prom_ids = set(_live.keys()) if _live is not None else None
    except Exception:
        live_prom_ids = None

    # Джерело 1: топ-N ("наші 6000 товарів") — лише ті, для яких уже є
    # дані конкурента в кеші нічного скану (без них decide_price_for_
    # platform() отримав би None замість реального конкурента).
    top_with_scan_data = {pid: item for pid, item in top_catalog.items() if pid in scan_state}

    # Джерело 2: живі на Prom, поза топ-N (той самий шлях, що й
    # звичайний rotated_out у prom_competitor_pricer.py).
    rotated_out = _rotated_out_scan_candidates(top_catalog, toysi_catalog, scan_state, live_prom_ids)

    candidates = dict(top_with_scan_data)
    candidates.update(rotated_out)  # немає перетину: rotated_out навмисно виключає top_catalog
    print(f"[LiveDumpingFix] Кандидатів усього: {len(candidates)} "
          f"(топ-N з даними скану: {len(top_with_scan_data)}, поза топ-N живі: {len(rotated_out)}).")

    progress = _load_progress()
    done_pids = set(progress.get("done_pids") or [])
    remaining = {pid: item for pid, item in candidates.items() if pid not in done_pids}
    if not remaining:
        print("[LiveDumpingFix] Попередній sweep повністю покрив цю множину кандидатів — починаю новий.")
        progress = {"started_at": datetime.now().isoformat(), "done_pids": []}
        done_pids = set()
        remaining = dict(candidates)
    else:
        print(f"[LiveDumpingFix] Продовжую попередній sweep (початок {progress.get('started_at')}): "
              f"{len(done_pids)} вже оброблено, {len(remaining)} залишилось.")

    applied_count = 0
    default_commission_count = 0
    error_count = 0
    category_counts = {"undercut": 0, "floor": 0, "no_competitor": 0}
    time_budget_hit = False

    for pid, item in remaining.items():
        if _time_budget_exceeded():
            print(f"[LiveDumpingFix] Часовий бюджет ({MAX_RUNTIME_SECONDS/3600:.0f}г) вичерпано — "
                  f"зупиняю достроково ({applied_count} застосовано з {len(remaining)} цього прогону).")
            time_budget_hit = True
            break

        try:
            cost = real_toysi_cost(item)  # свіжа собівартість з ЖИВОГО каталогу, не зі збереженої в скані
        except (TypeError, ValueError):
            done_pids.add(pid)
            continue
        if cost <= 0:
            done_pids.add(pid)
            continue

        category_name = item.get("category_name")
        prom_category_id = (prom_category_cache.get(pid) or {}).get("category_id")
        scan_entry = scan_state.get(pid, {})
        decision = _decide_from_scan_entry(cost, category_name, prom_category_id, scan_entry, item.get("pictures"))
        now_iso = datetime.now().isoformat()

        if _category_commission_is_default(category_name, prom_category_id):
            # Той самий гейт, що й у звичайному репрайсері: комісія не
            # підтверджена реальною категорією Prom -> НЕ auto-apply,
            # ціна все одно йде у price_state для фіда/ручного перегляду.
            default_commission_count += 1
            price_state[pid] = {
                "price": decision["price"], "timestamp": now_iso, "competitor_key": None,
                "category": decision["category"], "competitor_price": decision["competitor_price"],
                "cost": cost, "margin_pct": decision["margin_pct"],
            }
            done_pids.add(pid)
            continue

        try:
            apply_price(pid, decision["price"])
            price_state[pid] = {
                "price": decision["price"], "timestamp": now_iso, "competitor_key": None,
                "category": decision["category"], "competitor_price": decision["competitor_price"],
                "cost": cost, "margin_pct": decision["margin_pct"],
            }
            applied_count += 1
            category_counts[decision["category"]] = category_counts.get(decision["category"], 0) + 1
            time.sleep(APPLY_THROTTLE_SECONDS)
        except (requests.exceptions.RequestException, PromEditError) as e:
            error_count += 1
            print(f"  - {pid}: помилка зміни ціни — {e}", file=sys.stderr)

        done_pids.add(pid)  # позначаємо оброблений НЕЗАЛЕЖНО від успіху -- постійна помилка на одному SKU не має блокувати прогрес

        if len(done_pids) % SAVE_EVERY == 0:
            save_prom_price_state(price_state)
            progress["done_pids"] = sorted(done_pids)
            _save_progress(progress)
            print(f"[LiveDumpingFix] {len(done_pids)}/{len(candidates)} оброблено "
                  f"(застосовано {applied_count}, помилок {error_count})...")

    progress["done_pids"] = sorted(done_pids)
    _save_progress(progress)
    save_prom_price_state(price_state)

    remaining_after = len(candidates) - len(done_pids)
    print(f"[LiveDumpingFix] Готово. Застосовано живо: {applied_count}, "
          f"на непідтвердженій комісії (лише фід) — {default_commission_count}, помилок — {error_count}. "
          f"Залишилось у sweep'і: {remaining_after}.")

    digest = (
        f"🏷 apply_live_dumping_fix.py: застосовано живо — {applied_count} "
        f"(підрізано конкурента — {category_counts.get('undercut', 0)}, "
        f"піднято до floor — {category_counts.get('floor', 0)}), "
        f"на непідтвердженій комісії (лише фід) — {default_commission_count}, помилок — {error_count}."
        + (f"\n\n⏱ Часовий бюджет вичерпано — залишилось {remaining_after} SKU, "
           "запусти скрипт ще раз для продовження цього sweep'у." if time_budget_hit else
           "\n\n✅ Sweep повністю завершено.")
    )
    send_telegram_message(digest)


if __name__ == "__main__":
    main()
