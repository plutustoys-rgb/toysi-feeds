import re
import sys

from orders_db import get_connection, get_orders_ready_to_forward, mark_forwarded_to_toysi, update_delivery_status
from toysi_order_submit import submit_order
from nova_poshta import resolve_shipping, NovaPoshtaAPIError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

"""
Крок 5 плану: маршрутизація замовлень з orders.db до Toysi.
Накладені (cod) — одразу. Передоплачені (prepaid) — лише коли
payment_confirmed=1 (виставляє bank_check.py). Обирає кандидатів через
orders_db.get_orders_ready_to_forward() — воно вже враховує обидва правила
і виключає замовлення з попередньою помилкою (status='toysi_error').
"""

_WAREHOUSE_RE = re.compile(r"(?:відділенн\w*|відд\.?|№)\s*№?\s*(\d+)", re.IGNORECASE)
_CITY_PREFIX_RE = re.compile(r"^(м\.|с\.|смт\.?)\s*", re.IGNORECASE)


def parse_np_branch(np_branch: str) -> tuple:
    """
    Витягує (місто, запит_відділення) з вільнотекстового np_branch
    (напр. "Київ, відділення №15" з мок-даних, або довільний
    delivery_address з реального Prom — формат там ще не перевірено
    на живому прикладі, бо замовлень поки 0).
    """
    if not np_branch:
        return "", ""

    warehouse_match = _WAREHOUSE_RE.search(np_branch)
    warehouse_query = warehouse_match.group(1) if warehouse_match else ""

    city_part = np_branch.split(",")[0]
    city = _CITY_PREFIX_RE.sub("", city_part).strip()
    return city, warehouse_query


def _split_name(customer_name: str) -> tuple:
    """Toysi вимагає ім'я/прізвище окремо; customer_name у нас — один рядок."""
    parts = (customer_name or "").split(maxsplit=1)
    first_name = parts[0] if parts else "Клієнт"
    last_name = parts[1] if len(parts) > 1 else "Невідомо"
    return first_name, last_name


def build_toysi_order(order: dict) -> dict:
    """Перетворює запис orders_db на структуру для toysi_order_submit.submit_order()."""
    city, warehouse_query = parse_np_branch(order.get("np_branch", ""))

    shipping_fields = {}
    if city:
        try:
            shipping = resolve_shipping(city, warehouse_query)
        except NovaPoshtaAPIError as e:
            print(
                f"[order_router] Проблема з API Нової Пошти для {order['internal_order_id']}: {e}",
                file=sys.stderr,
            )
            shipping = None
        if shipping:
            shipping_fields = {
                "shipping_city_id": shipping["shipping_city_id"],
                "shipping_warehouse_id": shipping["shipping_warehouse_id"],
            }

    first_name, last_name = _split_name(order.get("customer_name", ""))

    moneyback = 0.0
    if order["payment_method"] == "cod":
        moneyback = sum(item.get("price", 0) * item.get("qty", 1) for item in order["items"])

    return {
        "internal_order_id": order["internal_order_id"][:25],
        "items": order["items"],
        "first_name": first_name,
        "last_name": last_name,
        "phone": order.get("phone", ""),
        "shipping_city_name": city or "Київ",  # Toysi вимагає непорожнє місто
        # Без NP-резолву адреса лишається вільним текстом np_branch — бажано,
        # ніж порожній рядок (response_code 20 "порожня адреса доставки").
        "shipping_address": order.get("np_branch", "") if not shipping_fields else "",
        "moneyback": moneyback,
        "comment": f"Автоматично: {order['platform']} #{order['order_id']}",
        **shipping_fields,
    }


def route_order(conn, order: dict, test_mode: bool = True) -> None:
    toysi_order = build_toysi_order(order)
    result = submit_order(toysi_order, test_mode=test_mode)

    if result["accepted"]:
        toysi_id = result.get("toysi_order_id")
        mark_forwarded_to_toysi(conn, order["internal_order_id"], str(toysi_id) if toysi_id is not None else "")
        dup_note = " (дублікат — вже існував у Toysi)" if result["is_duplicate"] else ""
        print(
            f"[order_router] Передано Toysi: {order['internal_order_id']} -> "
            f"toysi_order_id={toysi_id}{dup_note}"
        )
    elif result["should_retry"]:
        print(
            f"[order_router] Тимчасова помилка, спробуємо в наступному циклі: "
            f"{order['internal_order_id']} — {result['message']}",
            file=sys.stderr,
        )
    else:
        update_delivery_status(conn, order["internal_order_id"], status="toysi_error")
        print(
            f"[order_router] ПОМИЛКА даних замовлення {order['internal_order_id']}: "
            f"response_code={result['response_code']} — {result['message']}",
            file=sys.stderr,
        )


def route_pending_orders(test_mode: bool = True) -> None:
    with get_connection() as conn:
        candidates = get_orders_ready_to_forward(conn)
        if not candidates:
            print("[order_router] Немає замовлень, готових до передачі")
            return

        for order in candidates:
            route_order(conn, order, test_mode=test_mode)


if __name__ == "__main__":
    route_pending_orders(test_mode=True)
