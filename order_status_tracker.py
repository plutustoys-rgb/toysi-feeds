import sys

from checkbox_client import register_ettn, CheckboxAPIError
from orders_db import (
    get_connection, get_active_toysi_orders, mark_checkbox_ettn_registered,
    mark_rozetka_ttn_pushed, update_delivery_status,
)
import rozetka_client
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

Автофіскалізація Checkbox (2026-07-09): щойно з'являється реальний ТТН для
carrier=nova_poshta + payment_method=cod — реєструє ЕТТН у Checkbox
(_maybe_register_ettn). ЛИШЕ для НП + накладеного платежу: "Контроль
оплати"/ЕТТН стосується саме накладеного платежу на ТТН; передоплачені
замовлення фіскалізуються окремо, в момент оплати (не тут), а Укрпошта не
має автоматичного ТТН від Toysi взагалі (дивись orders_db.
get_orders_awaiting_manual_ttn_entry() — ручний шлях, окремий від цього).

🔴 ТТН для carrier=nova_poshta створює TOYSI під СВОЇМ акаунтом НП, не
нашим — дивись докстрінг checkbox_client.py, розділ "СЕРЙОЗНИЙ
АРХІТЕКТУРНИЙ РИЗИК": незалежні джерела стверджують, що Checkbox не
підв'язує ТТН, створений ЧУЖИМ токеном НП. _maybe_register_ettn() нижче
МОЖЕ мовчки й постійно повертати CheckboxAPIError для КОЖНОГО замовлення
з цієї причини — це НЕ підтверджено без живого тесту (sandbox немає).

Прикріплення ТТН НАЗАД у Rozetka (2026-07-15, _maybe_push_ttn_to_rozetka):
щойно з'являється toysi_ttn для platform=rozetka — викликає
rozetka_client.update_order_status(order_id, status=2, ttn=ttn). За
документацією Rozetka Seller API, ttn разом зі status=2 автоматично
переводить замовлення в статус 61, і Rozetka сама починає відстежувати
трекінг доставки — подальших ручних переходів статусу не потрібно.
На відміну від Prom: у цьому репозиторії НЕМАЄ аналогічного механізму
"написати ТТН назад у Prom" — Prom Orders API такого ендпоінту в поточній
інтеграції не використовує (сам Prom, судячи з усього, підхоплює трекінг
іншим шляхом, не через явний push від продавця).
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


def _receipt_items_from_order(order: dict) -> list:
    """order["items"] (toysi_code/name/qty/price) -> receipt_items для
    checkbox_client.register_ettn() — best-effort форма, звір з офіційною
    документацією Checkbox перед першим реальним викликом."""
    return [
        {"name": item.get("name", ""), "price": item.get("price", 0), "quantity": item.get("qty", 1)}
        for item in order["items"]
    ]


def _maybe_register_ettn(conn, order: dict, ttn: str) -> None:
    """Реєструє ЕТТН у Checkbox для carrier=nova_poshta + payment_method=cod,
    коли є ТТН і ще НЕ зареєстровано (checkbox_ettn_registered_at IS NULL).

    Навмисно НЕ прив'язано до "ТТН щойно з'явився" — перевіряється щоразу,
    коли є toysi_ttn і прапорець ще не виставлено, щоб мережева/тимчасова
    помилка Checkbox природно повторювалась на наступному циклі опитування
    (той самий підхід, що й should_retry для Toysi), а не губилась назавжди
    після одного невдалого виклику.

    Помилка тут (включно з НЕ-Checkbox винятком, напр. malformed order
    ["items"]) НЕ має зупиняти track_orders() для ІНШИХ замовлень у тому ж
    циклі опитування — тому ловимо широко (Exception), а не лише
    CheckboxAPIError."""
    if not ttn:
        return
    if order.get("carrier", "nova_poshta") != "nova_poshta":
        return
    if order["payment_method"] != "cod":
        return
    if order.get("checkbox_ettn_registered_at"):
        return

    try:
        payment_control_amount = sum(item.get("price", 0) * item.get("qty", 1) for item in order["items"])
        result = register_ettn(
            carrier="nova_poshta",
            ttn=ttn,
            receipt_items=_receipt_items_from_order(order),
            payment_control_amount=payment_control_amount,
        )
    except CheckboxAPIError as e:
        print(
            f"[order_status_tracker] Не вдалось зареєструвати ЕТТН у Checkbox для "
            f"{order['internal_order_id']} (ТТН {ttn}): {e}",
            file=sys.stderr,
        )
        return
    except Exception as e:
        print(
            f"[order_status_tracker] Неочікувана помилка при реєстрації ЕТТН для "
            f"{order['internal_order_id']} (ТТН {ttn}): {e}",
            file=sys.stderr,
        )
        return

    receipt_id = result.get("id") or result.get("receipt_id") if isinstance(result, dict) else None
    mark_checkbox_ettn_registered(conn, order["internal_order_id"], receipt_id)
    print(f"[order_status_tracker] ЕТТН зареєстровано в Checkbox: {order['internal_order_id']} (ТТН {ttn})")


def _maybe_push_ttn_to_rozetka(conn, order: dict, ttn: str) -> None:
    """Прикріплює ТТН до замовлення на СТОРОНІ Rozetka (PUT /orders/{id},
    status=2 + ttn) — щойно з'явився toysi_ttn і ще не передавали
    (rozetka_ttn_pushed_at IS NULL). Ідемпотентність — той самий підхід,
    що й _maybe_register_ettn() вище: перевіряємо прапорець щоразу, а не
    лише "у момент появи ttn", щоб тимчасова мережева помилка Rozetka
    природно повторилась на наступному циклі опитування, а не загубилась.

    order["order_id"] тут — це ID замовлення В САМІЙ Rozetka (не
    toysi_order_id) — саме те, що приймає rozetka_client.update_order_status().
    Помилка (включно з НЕ-RozetkaAPIError винятком) НЕ має зупиняти
    track_orders() для інших замовлень у тому самому циклі."""
    if not ttn:
        return
    if order.get("platform") != "rozetka":
        return
    if order.get("rozetka_ttn_pushed_at"):
        return

    try:
        rozetka_client.update_order_status(
            order["order_id"], status=rozetka_client.ORDER_STATUS_PROCESSING, ttn=ttn,
        )
    except rozetka_client.RozetkaAPIError as e:
        print(
            f"[order_status_tracker] Не вдалось передати ТТН у Rozetka для "
            f"{order['internal_order_id']} (ТТН {ttn}): {e}",
            file=sys.stderr,
        )
        return
    except Exception as e:
        print(
            f"[order_status_tracker] Неочікувана помилка при передачі ТТН у Rozetka для "
            f"{order['internal_order_id']} (ТТН {ttn}): {e}",
            file=sys.stderr,
        )
        return

    mark_rozetka_ttn_pushed(conn, order["internal_order_id"])
    print(f"[order_status_tracker] ТТН передано в Rozetka: {order['internal_order_id']} (ТТН {ttn})")


def track_orders() -> None:
    with get_connection() as conn:
        active = get_active_toysi_orders(conn)
        if not active:
            print("[order_status_tracker] Немає активних замовлень для відстеження")
            return

        by_toysi_id = {str(o["toysi_order_id"]): o["internal_order_id"] for o in active}
        orders_by_internal_id = {o["internal_order_id"]: o for o in active}

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

            order = orders_by_internal_id[internal_id]
            order["toysi_ttn"] = ttn
            _maybe_register_ettn(conn, order, ttn)
            _maybe_push_ttn_to_rozetka(conn, order, ttn)

            ttn_note = f", ТТН: {ttn}" if ttn else ""
            terminal_note = " [термінальний, більше не опитуємо]" if status_code in TERMINAL_ORDER_STATUSES else ""
            print(
                f"[order_status_tracker] {internal_id} (Toysi #{toysi_id}): "
                f"{describe_order_status(status_code)}{ttn_note}{terminal_note}"
            )


if __name__ == "__main__":
    track_orders()
