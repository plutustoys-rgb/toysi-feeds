"""rozetka_rz_delivery_monitor.py — моніторинг прибутковості RZ-Delivery-замовлень.

НАВІЩО (2026-08-18, рішення власника «варіант 1 + дивимось»): ROZETKA Delivery (видача в
магазинах Rozetka) коштує НАМ 35 грн/відправлення (в одну сторону, навіть при відмові).
Ми лишили RZ Delivery увімкненою на всіх товарах, а не фільтруємо — і хочемо БАЧИТИ
реальний збиток, щоб ухвалити рішення на фактах.

Монітор (read-only, НЕ чіпає живий order-пайплайн): тягне Rozetka-замовлення за період,
для тих, що з RZ Delivery, рахує маржу−35 грн, рахує збиткові, шле зведення.

⚠️ САМОКАЛІБРУВАННЯ: RZ Delivery щойно ввімкнено — живого такого замовлення ще НЕ БУЛО,
а поля Rozetka-замовлення (спосіб доставки + ціни позицій) у коді best-effort, не
підтверджені (див. orders_watcher._convert_rozetka_purchase). Тому монітор:
  • детектить RZ Delivery евристикою по delivery-блоку;
  • ЛОГУЄ сирий delivery-блок кожного замовлення ЛОКАЛЬНО (reports/, не в Telegram — PII),
    щоб на ПЕРШОМУ реальному RZ-Delivery-замовленні підтвердити точне поле-детектор і поля
    маржі, тоді доробити точно. До того — числа орієнтовні, і монітор це чесно позначає.

Маржа позиції = ціна×к-сть − собівартість Toysi − комісія Rozetka. Собівартість/комісія —
ті самі функції, що в decide_price/daily_report (real_toysi_cost, get_platform_commission).

ЛОКАЛЬНО/VPS (потрібні ROZETKA_USERNAME/PASSWORD або токен). Запуск раз на день по таймеру.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
CALIB_DIR = BASE_DIR / "reports"
HISTORY_FILE = BASE_DIR / ".local_secrets" / "rz_delivery_monitor_history.json"

DELIVERY_FEE = 35.0
DEFAULT_DAYS = 7


def _notify(msg: str) -> None:
    try:
        sys.path.insert(0, str(BASE_DIR))
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[RZmon] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def is_rz_delivery(order: dict):
    """Best-effort: чи замовлення з доставкою в точки видачі ROZETKA. Повертає True/False,
    або None якщо delivery-блок порожній/невідомий (не можемо судити). Евристика по тексту
    delivery-блоку — калібрується на першому реальному замовленні (див. лог нижче)."""
    delivery = order.get("delivery")
    if not delivery:
        return None
    blob = json.dumps(delivery, ensure_ascii=False).lower()
    RZ_MARKERS = ("rozetka delivery", "rozetkadelivery", "rozetka_delivery", "delivery-rozetka",
                  "видача в магазин", "точк видач", "магазин rozetka", "самовивіз rozetka",
                  "партнерськ відділенн")
    if any(m in blob for m in RZ_MARKERS):
        return True
    return False


def _order_margin(order: dict, catalog: dict) -> float:
    """Маржа замовлення в грн: Σ(ціна×к-сть − собівартість Toysi − комісія Rozetka).
    Поля purchases — best-effort (як orders_watcher._convert_rozetka_purchase)."""
    from competitor_pricing import real_toysi_cost, get_platform_commission
    total = 0.0
    for p in (order.get("purchases") or []):
        code = str(p.get("item_id") or p.get("id") or "").strip()
        try:
            price = float(p.get("price") or p.get("item_price") or 0)
            qty = int(float(p.get("quantity") or p.get("amount") or 1))
        except (TypeError, ValueError):
            continue
        revenue = price * qty
        item = catalog.get(code) or {}
        try:
            cost = float(real_toysi_cost(item) or 0) * qty
        except (TypeError, ValueError):
            cost = 0.0
        commission = revenue * get_platform_commission("rozetka", item.get("category_name"))
        total += revenue - cost - commission
    return total


def _log_calibration(orders: list) -> None:
    """Локально зберігає delivery-блоки Rozetka-замовлень (для калібрування детектора на
    першому реальному RZ Delivery). ЛОКАЛЬНО (reports/), не в Telegram — може містити PII."""
    try:
        CALIB_DIR.mkdir(parents=True, exist_ok=True)
        rows = [{"id": o.get("id"), "delivery": o.get("delivery"),
                 "is_rz": is_rz_delivery(o)} for o in orders]
        (CALIB_DIR / "rz_delivery_calibration.json").write_text(
            json.dumps({"at": datetime.now().isoformat(timespec="minutes"), "orders": rows},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[RZmon] калібрувальний лог не збережено (не критично): {e}", file=sys.stderr)


def run(days: int = DEFAULT_DAYS) -> dict:
    import rozetka_client
    from parser import fetch_toysi_catalog

    now = datetime.now()
    created_from = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    created_to = now.strftime("%Y-%m-%d %H:%M:%S")
    try:
        orders = rozetka_client.fetch_orders_by_date_range(created_from, created_to)
    except Exception as e:  # noqa: BLE001
        msg = f"🚨 RZ-Delivery-монітор: не зміг отримати замовлення Rozetka: {e}"
        print(f"[RZmon] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)

    _log_calibration(orders)   # калібрування детектора на реальних замовленнях
    catalog = fetch_toysi_catalog()

    rz_orders = [o for o in orders if is_rz_delivery(o) is True]
    unknown = sum(1 for o in orders if is_rz_delivery(o) is None)

    loss = 0
    net_total = 0.0     # сумарна маржа RZ-замовлень ПІСЛЯ −35
    for o in rz_orders:
        net = _order_margin(o, catalog) - DELIVERY_FEE
        net_total += net
        if net < 0:
            loss += 1

    result = {"at": now.isoformat(timespec="minutes"), "days": days,
              "total_orders": len(orders), "rz_delivery": len(rz_orders),
              "loss_making": loss, "net_after_fee": round(net_total, 2), "unknown_method": unknown}

    # історія
    try:
        hist = json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.exists() else []
        if not isinstance(hist, list):
            hist = []
    except (OSError, ValueError):
        hist = []
    hist.append(result)
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(hist[-90:], ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    print(f"[RZmon] за {days} дн: усього замовлень {len(orders)}, RZ Delivery {len(rz_orders)}, "
          f"збиткових {loss}, чистий ефект після −35 грн: {net_total:.2f} грн, невідомий спосіб {unknown}")

    # Telegram — лише коли Є RZ-Delivery-замовлення (щоб не шуміти нулями до першого)
    if rz_orders:
        note = (f"📦 RZ Delivery за {days} дн: {len(rz_orders)} замовлень, "
                f"збиткових {loss}, чистий ефект після −35 грн: {net_total:.2f} грн.")
        if loss:
            note += f" ⚠️ {loss} збиткових — глянь, чи не час фільтрувати дешеві."
        _notify(note)
    else:
        print("[RZmon] RZ-Delivery-замовлень поки нема (сервіс щойно ввімкнено) — Telegram не шлю. "
              "Калібрувальний лог: reports/rz_delivery_calibration.json")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Моніторинг прибутковості RZ-Delivery-замовлень (маржа−35 грн).")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="за скільки днів рахувати")
    a = ap.parse_args()
    run(a.days)


if __name__ == "__main__":
    main()
