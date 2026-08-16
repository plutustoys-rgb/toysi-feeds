"""rozetka_competitor_repricer.py — Фаза 2 Rozetka-репрайсера (задача власника 2026-08-16,
«дієм як на Prom»). Читає конкурентні дані з кабінету (rozetka_price_monitor.py → Фаза 1) і
рахує ЦІНОВІ OVERRIDE-и для Rozetka-фіду:

  • Є рекомендована ціна Rozetka (конкурент) → підбиваємось під неї, але НЕ нижче флору
    ROZETKA_COMPETITOR_MARGIN (5%, рішення власника) з ТОЧНОЮ комісією per-SKU з кабінету.
  • Немає конкурента → формульна ціна з MIN_PROFIT (25%), АЛЕ теж із точною комісією
    (краще за наближення «24% на все» у фіді).

КЛЮЧ: усе — за НАШИМ offer id (== item_article комісії; рекомендації вже ремапнуті Фазою 1).

БЕЗПЕКА цін: ціна НІКОЛИ не нижча за флор (ceil до копійки, не round — щоб не впасти під межу);
битий cost/комісія → SKU пропускаємо (лишаємо формулу фіду). Комісія Rozetka ступінчаста за
ціною — беремо ставку за ПОТОЧНОЮ ціною з кабінету (точна для більшості; при значному підбитті
може трохи змінитись тир — прийнятне наближення Фази 2, флор захищає від збитку).

ВИХІД: rozetka_price_overrides.json {our_offer_id: retail} — вантажить generate_rozetka_feed.
"""
import json
import math
import os
import sys
from pathlib import Path

from competitor_pricing import real_toysi_cost, MIN_PROFIT  # MIN_PROFIT = 0.25

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / ".local_secrets" / "rozetka_competitor_state.json"
OVERRIDES_FILE = BASE_DIR / "rozetka_price_overrides.json"
# Нижня маржа в конкурентному режимі (рішення власника 2026-08-16: 5%).
ROZETKA_COMPETITOR_MARGIN = float(os.environ.get("ROZETKA_COMPETITOR_MARGIN", "0.05"))


def _floor(cost: float, commission: float, margin: float) -> float:
    """Мінімальна ціна для заданої маржі ПІСЛЯ комісії: (cost + cost*margin)/(1-commission).
    ceil до копійки (1e-6 епсилон), щоб через округлення НЕ опинитись під флором."""
    raw = (cost * (1.0 + margin)) / (1.0 - commission)
    return math.ceil(raw * 100 - 1e-6) / 100.0


def build_overrides(catalog: dict, state: dict) -> tuple[dict, dict]:
    """catalog: {our_id: toysi_item}; state: rozetka_competitor_state.json.
    Повертає (overrides {our_id: retail}, stats)."""
    comm_map = state.get("commission", {}) or {}
    rec_map = state.get("recommended", {}) or {}
    overrides = {}
    stats = {"competitor": 0, "solo": 0, "clamped_to_floor": 0,
             "skipped_no_item": 0, "skipped_bad_cost": 0, "skipped_bad_commission": 0}

    for sku, cinfo in comm_map.items():
        item = catalog.get(sku)
        if item is None:
            stats["skipped_no_item"] += 1
            continue
        try:
            cost = float(real_toysi_cost(item) or 0)
        except (TypeError, ValueError):
            cost = 0
        if cost <= 0:
            stats["skipped_bad_cost"] += 1
            continue
        pct = cinfo.get("commission_pct")
        commission = (pct / 100.0) if isinstance(pct, (int, float)) else None
        if commission is None or not (0.0 < commission < 1.0):
            stats["skipped_bad_commission"] += 1
            continue

        rec = ((rec_map.get(sku) or {}).get("internal") or {}).get("recommended")
        floor_comp = _floor(cost, commission, ROZETKA_COMPETITOR_MARGIN)
        if isinstance(rec, (int, float)) and rec > 0:
            # підбиваємось під рекомендовану, але не нижче 5%-флору
            price = max(float(rec), floor_comp)
            if price > floor_comp:
                pass
            else:
                stats["clamped_to_floor"] += 1   # ринок нижчий за наш флор → стоїмо на флорі
            price = math.ceil(price * 100 - 1e-6) / 100.0
            stats["competitor"] += 1
        else:
            # без конкурента — 25% маржі, але з ТОЧНОЮ комісією
            price = _floor(cost, commission, MIN_PROFIT)
            stats["solo"] += 1
        overrides[sku] = round(price, 2)
    return overrides, stats


def main() -> None:
    if not STATE_FILE.exists():
        print(f"[RzReprice] нема {STATE_FILE.name} — спершу `rozetka_price_monitor.py`.", file=sys.stderr)
        sys.exit(1)
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    print("[RzReprice] Завантажую каталог Toysi...")
    from parser import fetch_toysi_catalog
    catalog = fetch_toysi_catalog()
    if not catalog:
        print("[RzReprice] каталог порожній — вихід.", file=sys.stderr)
        sys.exit(1)
    catalog = {str(k): v for k, v in catalog.items()}

    overrides, stats = build_overrides(catalog, state)
    OVERRIDES_FILE.write_text(json.dumps(overrides, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[RzReprice] Override-ів: {len(overrides)} "
          f"(конкурент {stats['competitor']}, без конкурента {stats['solo']}, "
          f"на флорі {stats['clamped_to_floor']}). "
          f"Пропущено: нема-товару {stats['skipped_no_item']}, cost {stats['skipped_bad_cost']}, "
          f"комісія {stats['skipped_bad_commission']}. Маржа-конкурент {ROZETKA_COMPETITOR_MARGIN:.0%}.")
    print(f"[RzReprice] → {OVERRIDES_FILE.name}")


if __name__ == "__main__":
    main()
