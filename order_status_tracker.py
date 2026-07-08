import sys

from orders_db import get_connection, get_active_toysi_orders, update_delivery_status
from toysi_order_submit import (
    fetch_order_statuses,
    describe_order_status,
    TERMINAL_ORDER_STATUSES,
    ToysiAPIError,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

"""
Крок 6 плану: пакетно опитує order_status для замовлень, уже переданих
Toysi (order_router.py), поки їхній статус не термінальний. Мапить числові
коди Toysi на власний delivery_status, зберігає ТТН, як тільки з'являється.
"""

_STATUS_TO_DELIVERY_STATUS = {
    0:   "processing",
    10:  "cancelled",
    20:  "processing",
    30:  "processing",
    40:  "assembling",
    50:  "packed",
    60:  "shipped",
    70:  "delivered",
    80:  "returned",
    503: "expired",
}


def track_orders() -> None:
    with get_connection() as conn:
        active = get_active_toysi_orders(conn)
        if not active:
            print("[order_status_tracker] Немає активних замовлень для відстеження")
            return

        by_toysi_id = {str(o["toysi_order_id"]): o["internal_order_id"] for o in active}

        try:
            statuses = fetch_order_statuses(list(by_toysi_id.keys()))
        except (RuntimeError, ToysiAPIError) as e:
            print(f"[order_status_tracker] {e}", file=sys.stderr)
            return

        for toysi_id, internal_id in by_toysi_id.items():
            info = statuses.get(toysi_id)
            if info is None:
                print(
                    f"[order_status_tracker] {internal_id} (Toysi #{toysi_id}): "
                    f"не знайдено у відповіді (можливо, застаріло >40 днів)",
                    file=sys.stderr,
                )
                continue

            status_code = int(info.get("status", 0))
            ttn = info.get("TTN") or None
            delivery_status = _STATUS_TO_DELIVERY_STATUS.get(status_code, f"unknown_{status_code}")

            update_delivery_status(conn, internal_id, toysi_ttn=ttn, delivery_status=delivery_status)

            ttn_note = f", ТТН: {ttn}" if ttn else ""
            terminal_note = " [термінальний, більше не опитуємо]" if status_code in TERMINAL_ORDER_STATUSES else ""
            print(
                f"[order_status_tracker] {internal_id} (Toysi #{toysi_id}): "
                f"{describe_order_status(status_code)}{ttn_note}{terminal_note}"
            )


if __name__ == "__main__":
    track_orders()
