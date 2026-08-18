"""rozetka_merchant_commit.py — ПІДКЛЮЧЕННЯ кандидатів товарознавця у Rozetka-фід (крок ЗАПИСУ).

Товарознавець (rozetka_merchant_agent.py) — READ-ONLY: лише знаходить конкурентних/унікальних
кандидатів у rozetka_merchant_candidates.json. ЦЕЙ скрипт — крок ЗАПИСУ: додає їх у MEMBERSHIP
(rozetka_static_selection.json) з правильною СТАРТОВОЮ ціною, ДО КАПУ CAP товарів у фіді.

КАП (рішення власника 2026-08-17): додаємо автоматично до CAP=6000 у фіді; ПІСЛЯ 6000 — НЕ додаємо,
шлемо Telegram-алерт «уточнити в менеджера Rozetka».

ЦІНА (та сама канонічна decide_price_for_platform, що й фід):
  • конкурентний (є ринок) → undercut найдешевшого, не нижче 5%-флору;
  • унікальний (ринку нема) → соло-ціна (25% маржі).
Ціни пишуться в rozetka_merchant_prices.json — фід бере їх як БАЗУ для нових товарів, доки
репрайсер (живий моніторинг кабінету) не перекриє їх своїми (див. _load_price_overrides у фіді).

ПРОПУСКАЄ товари з чорного списку rozetka_rejected_ids.json (Rozetka вже відхилила — не подаємо знову).
Перед додаванням ПЕРЕПЕРЕВІРЯЄ живо: товар ще в каталозі Toysi, stock ≥ MIN_STOCK, валідна картка, є фото.

READ: candidates, catalog Toysi (живі cost/stock), membership, blocklist, наявні merchant-ціни.
WRITE: rozetka_static_selection.json (membership) + rozetka_merchant_prices.json.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from generate_rozetka_feed import (_qualifies_for_feed, _within_rz_delivery_dims,
                                   _load_prom_products_cache,
                                   ROZETKA_STATIC_SELECTION_FILE, ROZETKA_MERCHANT_PRICES_FILE)
from competitor_pricing import decide_price_for_platform, real_toysi_cost
from rozetka_merchant_agent import MIN_STOCK, _our_image, OUTPUT_FILE as CANDIDATES_FILE

BASE_DIR = Path(__file__).parent
REJECTED_FILE = BASE_DIR / ".local_secrets" / "rozetka_rejected_ids.json"
CAP = int(os.environ.get("ROZETKA_FEED_CAP", "6000"))   # стеля товарів у фіді; понад — питати менеджера


def _notify(msg: str) -> None:
    if os.environ.get("AUDIT_NO_TELEGRAM") == "1":
        return
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:
        print(f"[Commit] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default


def _load_blocklist() -> set:
    """vendor_code'и, які Rozetka вже відхилила (rozetka_rejected_ids.json). Немає файлу → порожньо."""
    data = _load_json(REJECTED_FILE, {})
    ids = data.get("ids") if isinstance(data, dict) else None
    return set(map(str, ids.keys())) if isinstance(ids, dict) else set()


def main() -> None:
    membership = _load_json(ROZETKA_STATIC_SELECTION_FILE, None)
    if not isinstance(membership, dict) or "items" not in membership:
        print("[Commit] нема/битий rozetka_static_selection.json — НЕ будую з нуля (це була б хвиля "
              "модерації). Спершу відновіть membership із feed-data.", file=sys.stderr)
        sys.exit(1)
    items = membership["items"]
    prices = membership.setdefault("prices", {})

    cand_data = _load_json(CANDIDATES_FILE, {})
    candidates = cand_data.get("candidates", []) if isinstance(cand_data, dict) else []
    if not candidates:
        print("[Commit] нема кандидатів (rozetka_merchant_candidates.json порожній) — нічого додавати.")
        return

    blocklist = _load_blocklist()
    prom_products = _load_prom_products_cache()
    print("[Commit] Завантажую каталог Toysi...")
    from parser import fetch_toysi_catalog
    catalog = {str(k): v for k, v in (fetch_toysi_catalog() or {}).items()}

    merchant_prices = _load_json(ROZETKA_MERCHANT_PRICES_FILE, {})
    if not isinstance(merchant_prices, dict):
        merchant_prices = {}

    membership_ids = set(map(str, items.keys()))
    start_count = len(membership_ids)
    if start_count >= CAP:
        msg = f"⚠️ Rozetka-фід уже досяг {start_count}/{CAP} — НЕ додаю нові товари. Уточни ліміт у менеджера Rozetka."
        print(f"[Commit] {msg}")
        _notify(msg)
        return

    # конкурентні — пріоритет над унікальними (власник: «додаємо як знайдем конкурентного»)
    order = {"competitive": 0, "unique": 1}
    candidates = sorted(candidates, key=lambda c: order.get(c.get("decision"), 9))

    added, skipped = 0, {"already": 0, "blocked": 0, "gone": 0, "oos": 0, "invalid": 0,
                         "no_img": 0, "bad_cost": 0, "oversized": 0}
    reached_cap = False
    count = start_count
    for c in candidates:
        if count >= CAP:
            reached_cap = True
            break
        pid = str(c.get("pid"))
        if pid in membership_ids:
            skipped["already"] += 1;  continue
        if pid in blocklist:
            skipped["blocked"] += 1;  continue
        item = catalog.get(pid)
        if not item:
            skipped["gone"] += 1;  continue          # зник з каталогу Toysi
        if int(item.get("stock", 0) or 0) < MIN_STOCK:
            skipped["oos"] += 1;  continue            # уже не в наявності / <2
        if not _qualifies_for_feed(item, set()):
            skipped["invalid"] += 1;  continue
        if not _within_rz_delivery_dims(item):       # великогабаритні (>120см) — не додаємо на Rozetka
            skipped["oversized"] += 1;  continue
        if not _our_image(item, prom_products):
            skipped["no_img"] += 1;  continue
        try:
            cost = float(real_toysi_cost(item) or 0)
        except (TypeError, ValueError):
            cost = 0
        if cost <= 0:
            skipped["bad_cost"] += 1;  continue
        market = c.get("rozetka_market")   # None для унікальних
        price = decide_price_for_platform(cost, market, "rozetka",
                                          category_name=item.get("category_name"))["price"]
        # додаємо в membership (важать КЛЮЧІ; значення — трасування)
        items[pid] = {
            "id": pid, "vendor_code": pid, "name": (item.get("name") or "")[:200],
            "added_by": "merchant_agent", "added_at": datetime.now().isoformat(),
            "decision": c.get("decision"), "rozetka_market": market,
        }
        prices[pid] = round(price, 2)
        merchant_prices[pid] = round(price, 2)
        membership_ids.add(pid)
        added += 1
        count += 1

    membership["last_merchant_commit"] = datetime.now().isoformat()
    ROZETKA_STATIC_SELECTION_FILE.write_text(
        json.dumps(membership, ensure_ascii=False, indent=1), encoding="utf-8")
    ROZETKA_MERCHANT_PRICES_FILE.write_text(
        json.dumps(merchant_prices, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[Commit] додано {added} → membership тепер {count}/{CAP} "
          f"(було {start_count}). Пропущено: {skipped}")
    if reached_cap or count >= CAP:
        msg = (f"🛑 Rozetka-фід досяг ліміту {count}/{CAP} товарів. Автододавання ЗУПИНЕНО. "
               f"Уточни в менеджера Rozetka, чи можна більше.")
        print(f"[Commit] {msg}")
        _notify(msg)


if __name__ == "__main__":
    main()
