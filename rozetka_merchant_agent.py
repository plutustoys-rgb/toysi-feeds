"""rozetka_merchant_agent.py — агент-товарознавець для Rozetka (рішення власника 2026-08-16).

МЕТА: автоматично РОЗШИРЮВАТИ каталог Rozetka лише КОНКУРЕНТНИМИ товарами — вирішувати ДО
додавання (не delist після, бо Rozetka модерує). Повністю АВТО, без ручного огляду. Помилка на
кілька % безпечна — репрайсер (rozetka_competitor_repricer.py) її ловить: щойно товар на Rozetka,
її моніторинг дає ціну, і репрайсер або робить його конкурентним, або тримає на 5%-флорі (без збитку).

ФАЗА A (цей файл, самодостатня, БЕЗПЕЧНА — жодного скрейпінгу, лише читання наших даних):
кандидати = «нічна вибірка» Prom (prom_competitor_price_state.json: товари у наявності + де ми
конкурентні на Prom, category="undercut", з відомою ціною конкурента). Для кожного:
  1. ще НЕ на Rozetka (не в rozetka_static_selection.json membership);
  2. у наявності (stock>0) і картка проходить Rozetka-валідацію (_qualifies_for_feed);
  3. є чисте фото (images.prom.ua — без вотермарки);
  4. КОНКУРЕНТНІСТЬ: Rozetka-флор (cost + Rozetka-комісія + 5%) ≤ ціна конкурента (з Prom-скану —
     ринок здебільшого спільний). Флор ≤ конкурент → ДОДАТИ; дорожче → пропустити.
Результат — rozetka_merchant_candidates.json (список pid «додати» + звіт). НЕ пише в membership сам
(власник тестує спершу). Підключення до фіду — окремо, після перевірки.

ФАЗА B (окремо, коли дійде черга): Rozetka-специфічна перевірка через публічний пошук
(common-api.rozetka.com.ua/v1/api/catalog/search — дво-ступеневий: search→get-goods) + pHash
фото-звірка (imagehash) для точного матчингу «той самий товар», замість Prom-проксі. Ротаційно, з
капом (rate-limit). Тут поки Prom-конкурент як надійний проксі ринку (уже зматчений Prom-скумером).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from generate_rozetka_feed import (_qualifies_for_feed, _load_prom_products_cache,
                                   ROZETKA_STATIC_SELECTION_FILE)
from competitor_pricing import _resolve_rozetka_floor, PAYMENT_COMMISSION, real_toysi_cost

BASE_DIR = Path(__file__).parent
PRICE_STATE_FILE = BASE_DIR / "prom_competitor_price_state.json"
OUTPUT_FILE = BASE_DIR / "rozetka_merchant_candidates.json"
ROZETKA_COMPETITOR_MARGIN = 0.05   # рішення власника 2026-08-16
ROZETKA_PAYMENT_COMMISSION = PAYMENT_COMMISSION.get("rozetka", 0.0)


def _load_membership() -> set:
    """pid, уже подані на Rozetka (ключі rozetka_static_selection.json)."""
    try:
        data = json.loads(ROZETKA_STATIC_SELECTION_FILE.read_text(encoding="utf-8"))
        return set((data.get("items") or {}).keys())
    except (ValueError, OSError):
        return set()


def _has_clean_image(item: dict, prom_products: dict) -> bool:
    """Чисте фото images.prom.ua (без вотермарки) — з prom-товару за vendor_code."""
    prod = prom_products.get(str(item.get("vendor_code") or item.get("id"))) if prom_products else None
    if not prod:
        return False
    if (prod.get("main_image") or "").startswith("https://images.prom.ua"):
        return True
    return any((im.get("url") or "").startswith("https://images.prom.ua") for im in (prod.get("images") or []))


def select_candidates(catalog: dict, state: dict, membership: set, prom_products: dict) -> tuple[list, dict]:
    """catalog: {pid: item}; state: prom_competitor_price_state. Повертає (candidates, stats)."""
    st = {"prom_pool": 0, "no_competitor": 0, "already_on_rozetka": 0, "not_in_catalog": 0,
          "oos": 0, "invalid_card": 0, "no_clean_image": 0, "bad_cost": 0,
          "competitive": 0, "uncompetitive": 0}
    candidates = []
    for pid, rec in state.items():
        if pid.startswith("_") or not isinstance(rec, dict):
            continue
        comp = rec.get("competitor_price")
        if not comp or rec.get("category") != "undercut":
            st["no_competitor"] += 1        # не «конкурентні на Prom з відомим конкурентом»
            continue
        st["prom_pool"] += 1
        if pid in membership:
            st["already_on_rozetka"] += 1
            continue
        item = catalog.get(pid)
        if item is None:
            st["not_in_catalog"] += 1
            continue
        if int(item.get("stock", 0) or 0) <= 0:
            st["oos"] += 1
            continue
        if not _qualifies_for_feed(item, set()):
            st["invalid_card"] += 1
            continue
        if not _has_clean_image(item, prom_products):
            st["no_clean_image"] += 1
            continue
        try:
            cost = float(rec.get("cost") or real_toysi_cost(item) or 0)
        except (TypeError, ValueError):
            cost = 0
        if cost <= 0:
            st["bad_cost"] += 1
            continue
        floor, commission = _resolve_rozetka_floor(cost, ROZETKA_COMPETITOR_MARGIN, ROZETKA_PAYMENT_COMMISSION)
        try:
            comp = float(comp)
        except (TypeError, ValueError):
            continue
        if floor <= comp:
            st["competitive"] += 1
            candidates.append({
                "pid": pid,
                "name": (item.get("name") or "")[:80],
                "category": item.get("category_name"),
                "cost": round(cost, 2),
                "prom_competitor_price": round(comp, 2),
                "rozetka_floor_5pct": round(floor, 2),
                "rozetka_commission": round(commission, 4),
                "headroom": round(comp - floor, 2),   # запас: наскільки нижче ринку можемо стати
            })
        else:
            st["uncompetitive"] += 1
    candidates.sort(key=lambda c: c["headroom"], reverse=True)
    return candidates, st


def main() -> None:
    if not PRICE_STATE_FILE.exists():
        print(f"[Merchant] нема {PRICE_STATE_FILE.name} (нічний Prom-скан) — вихід.", file=sys.stderr)
        sys.exit(1)
    state = json.loads(PRICE_STATE_FILE.read_text(encoding="utf-8"))
    membership = _load_membership()
    prom_products = _load_prom_products_cache()
    print("[Merchant] Завантажую каталог Toysi...")
    from parser import fetch_toysi_catalog
    catalog = {str(k): v for k, v in (fetch_toysi_catalog() or {}).items()}
    if not catalog:
        print("[Merchant] каталог порожній — вихід.", file=sys.stderr)
        sys.exit(1)

    candidates, st = select_candidates(catalog, state, membership, prom_products)
    OUTPUT_FILE.write_text(json.dumps(
        {"at": datetime.now().isoformat(), "margin": ROZETKA_COMPETITOR_MARGIN,
         "count": len(candidates), "candidates": candidates}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    print(f"[Merchant] Prom-конкурентний пул: {st['prom_pool']} | вже на Rozetka: {st['already_on_rozetka']}")
    print(f"[Merchant] відсіяно: нема в каталозі {st['not_in_catalog']}, OOS {st['oos']}, "
          f"невалідна картка {st['invalid_card']}, без чистого фото {st['no_clean_image']}, cost {st['bad_cost']}")
    print(f"[Merchant] ✅ КОНКУРЕНТНІ (флор ≤ ринок) до додавання: {st['competitive']} | "
          f"дорожчі за ринок (пропущено): {st['uncompetitive']}")
    print(f"[Merchant] → {OUTPUT_FILE.name} ({len(candidates)} кандидатів)")


if __name__ == "__main__":
    main()
