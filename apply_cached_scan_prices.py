"""apply_cached_scan_prices.py — разовий, дешевий feed-only перерахунок
цін для SKU ПОЗА топ-970 з уже наявних даних full_catalog_scan_state.json
(нічний скан full_catalog_competitor_scan.py), БЕЗ живого пошуку
конкурента і БЕЗ прямого apply_price() у Prom.

НАВІЩО (пряме прохання власниці, 2026-07-26): "я не хочу зміювати все...
я хочу підлаштувати цю вітрину разово не чекаючи репрайсера, треба в
фіді виставити ціни конкурент - 3 грн" — вітрина зараз системно дешевша
за конкурентів (і подекуди збиткова, PR #168 щойно виправив формулу
собівартості — Toysi "Збірка" 15₴/замовлення не рахувалась). Звичайний
prom_competitor_pricer.py покриває SKU поза топ-970 лише невеликими
щоденними партіями (ROTATED_OUT_BATCH_LIMIT=1000) — на весь обсяг
(~17000 SKU) пішло б кілька тижнів. Дані конкурента для НИХ уже є в кеші
(full_catalog_scan_state.json, оновлюється щоночі ротаційно, BATCH_SIZE
3000/день) — лишається лише перерахувати decide_price_for_platform() з
ПОТОЧНОЮ формулою (той самий шлях, що й _decide_from_scan_entry() у
prom_competitor_pricer.py) і записати результат у price_state, БЕЗ
живого повторного пошуку конкурента і БЕЗ живого патчу ціни в Prom API —
власниця explicitly попросила "лише фід": ціна на вітрині зміниться
на наступному імпорті Prom, без миттєвого сплеску ~17000 живих API-
запитів за один прогін.

БЕЗПЕКА: НІКОЛИ не робить delist — той самий принцип, що й
_decide_from_scan_entry() (дані нічного скану не мають достатньо деталей
конкурента для живої verify_competitor_really_available() перед
видаленням). cost рахується заново з ЖИВОГО каталогу Toysi (не зі
збереженого в скані значення) — єдина мережева витрата тут, решта
(конкурент/score/alive) береться з кешу як є.

Запуск:
    python apply_cached_scan_prices.py
"""
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from parser import fetch_toysi_catalog, assert_catalog_size_sane, CatalogSizeError
from competitor_pricing import load_prom_price_state, save_prom_price_state, real_toysi_cost
from generate_prom_feed_top import select_top_items, load_scan_state
from prom_competitor_pricer import (
    _rotated_out_scan_candidates,
    _decide_from_scan_entry,
    _category_commission_is_default,
    _load_prom_category_cache,
)

SAVE_EVERY = 500


def main() -> None:
    price_state = load_prom_price_state()

    print("[ScanSweep] Завантажую каталог Toysi...")
    toysi_catalog = fetch_toysi_catalog()
    try:
        assert_catalog_size_sane(toysi_catalog)
    except CatalogSizeError as e:
        print(f"[ScanSweep] {e}", file=sys.stderr)
        sys.exit(1)

    top_catalog = select_top_items(toysi_catalog)
    scan_state = load_scan_state()
    prom_category_cache = _load_prom_category_cache()

    # Best-effort, той самий принцип, що й у prom_competitor_pricer.py:
    # без кешу живих товарів Prom просто не фільтруємо (жодного нового
    # ризику — apply_price() тут все одно ніколи не викликається).
    live_prom_ids = None
    try:
        from generate_google_feed import load_prom_products_cache
        _live = load_prom_products_cache()
        live_prom_ids = set(_live.keys()) if _live is not None else None
    except Exception:
        live_prom_ids = None

    rotated_out = _rotated_out_scan_candidates(top_catalog, toysi_catalog, scan_state, live_prom_ids)
    print(f"[ScanSweep] SKU поза топ-970 з даними скану: {len(rotated_out)} — "
          "обробляю ВСІ за один прохід (без щоденного ліміту, лише price_state).")

    updated = 0
    default_commission_count = 0
    now_iso = datetime.now().isoformat()
    for pid, item in rotated_out.items():
        try:
            cost = real_toysi_cost(item)  # свіжа собівартість з ЖИВОГО каталогу, не зі збереженої в скані
        except (TypeError, ValueError):
            continue
        if cost <= 0:
            continue

        category_name = item.get("category_name")
        prom_category_id = (prom_category_cache.get(pid) or {}).get("category_id")
        scan_entry = scan_state.get(pid, {})
        decision = _decide_from_scan_entry(cost, category_name, prom_category_id, scan_entry, item.get("pictures"))

        if _category_commission_is_default(category_name, prom_category_id):
            default_commission_count += 1

        price_state[pid] = {
            "price": decision["price"], "timestamp": now_iso, "competitor_key": None,
            "category": decision["category"], "competitor_price": decision["competitor_price"],
            "cost": cost, "margin_pct": decision["margin_pct"],
        }
        updated += 1
        if updated % SAVE_EVERY == 0:
            save_prom_price_state(price_state)
            print(f"[ScanSweep] {updated}/{len(rotated_out)}...")

    save_prom_price_state(price_state)
    print(f"[ScanSweep] Готово. Записано у price_state (лише фід, БЕЗ apply_price): {updated} "
          f"(з них на непідтвердженій комісії, все одно у фіді — {default_commission_count}). "
          "Ціна на вітрині зміниться на наступному імпорті Prom, не миттєво.")


if __name__ == "__main__":
    main()
