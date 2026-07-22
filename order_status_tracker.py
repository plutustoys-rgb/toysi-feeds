import sys

from checkbox_client import create_receipt, CheckboxAPIError
from orders_db import (
    get_connection, get_active_toysi_orders, mark_checkbox_ettn_registered,
    mark_rozetka_ttn_pushed, mark_prom_delivered_pushed, mark_prom_ttn_pushed,
    update_delivery_status,
)
import nova_poshta
from orders_watcher import update_prom_order_status, attach_prom_declaration_id, PromAPIError
import rozetka_client
from telegram_notify import send_telegram_message
from toysi_order_submit import (
    fetch_order_statuses,
    describe_order_status,
    TERMINAL_ORDER_STATUSES,
    ToysiAPIError,
)

# Множина delivery_status, що враховуються як "неуспішні" для показника
# Prom "успішних замовлень" (P0-2, daily_report.py) — той самий набір тут,
# щоб алерт на кожне ОКРЕМЕ скасування (нижче) і 60-денний агрегат рахували
# одне й те саме.
_UNSUCCESSFUL_DELIVERY_STATUSES = {"cancelled", "returned"}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

"""
Крок 6 плану: пакетно опитує order_status для замовлень, уже переданих
Toysi (order_router.py), поки їхній статус не термінальний. Мапить числові
коди Toysi на власний delivery_status, зберігає ТТН, як тільки з'являється.

Фіскалізація Checkbox (2026-07-09, ПЕРЕРОБЛЕНО 2026-07-22): попередній
шлях через ЕТТН-прив'язку до Нової Пошти (_maybe_register_ettn,
checkbox_client.register_ettn) виявився СТРУКТУРНО непрацездатним для цієї
дропшип-моделі — підтверджено (не лише запідозрено): `InternetDocument.
getDocumentList` (наш власний NP_API_KEY, весь липень 2026) повертає 0
документів, бо ТТН для carrier=nova_poshta створює TOYSI під СВОЇМ
акаунтом НП, а офіційна інструкція Checkbox вимагає ВЛАСНОГО активного
контракту з Nova Pay для будь-якої ЕТТН-прив'язки. 5 реальних замовлень
без чеків (415858222/415965259/416114712/416236076/416856359) — пряме
свідчення цього провалу.

Новий шлях (_maybe_issue_receipt, checkbox_client.create_receipt) —
прямий чек продажу (POST /receipts/sell), ЗОВСІМ БЕЗ залежності від
Нової Пошти чи ТОГО, чиїм токеном створено ТТН:
  - payment_method="prepaid": тригер — payment_confirmed (гроші вже
    отримані, чекати доставки не треба), payment_type="CASHLESS".
  - payment_method="cod": тригер — nova_poshta.get_tracking_status(ttn)
    ["delivered"], ТОЙ САМИЙ сигнал, що вже підтверджує факт видачі
    посилки для _maybe_push_delivered_to_prom() нижче (PR #88) — гроші
    накладеного платежу фактично отримані саме в цей момент, не раніше.
    payment_type="CASH". Лише carrier=nova_poshta (Укрпошта не має
    автоматичного ТТН від Toysi взагалі — дивись orders_db.
    get_orders_awaiting_manual_ttn_entry(), ручний шлях, окремий від цього).

Прикріплення ТТН НАЗАД у Rozetka (2026-07-15, _maybe_push_ttn_to_rozetka):
щойно з'являється toysi_ttn для platform=rozetka — викликає
rozetka_client.update_order_status(order_id, status=2, ttn=ttn). За
документацією Rozetka Seller API, ttn разом зі status=2 автоматично
переводить замовлення в статус 61, і Rozetka сама починає відстежувати
трекінг доставки — подальших ручних переходів статусу не потрібно.

Prom ЕН (2026-07-17, _maybe_push_ttn_to_prom): ВИПРАВЛЕНО — попередній
докстрінг стверджував, що Prom Orders API "НЕ має ендпоінту прикріпити
ТТН" (code_report_2026-07-15_pt23.md); це виявилось неповним висновком.
Ендпоінт є — POST /delivery/save_declaration_id (не задокументований у
тому ж місці, що /orders/set_status, звідси й пропуск раніше) — живо
перевірено 2026-07-17 на реальному замовленні №415965259:
declaration_number у Prom справді заповнюється. Це РІВНО той виклик, що
Prom вимагає для активації "Дешевої доставки" Новою Поштою (офіційна
довідка support.prom.ua: "додайте ЕН не пізніше дня відправлення").
Раніше цей виклик не робився НІКОЛИ — тобто умова не виконувалась для
ЖОДНОГО замовлення за всю історію, незалежно від підписки клієнта.

Prom "delivered" (Auto-3, 2026-07-17, _maybe_push_delivered_to_prom): на
відміну від Rozetka, Prom Orders API не відстежує доставку автоматично
сам (підтверджено, code_report_2026-07-15_pt23.md). Тут ми самі опитуємо
nova_poshta.get_tracking_status(ttn) (TrackingDocument.getStatusDocuments
— реальний фізичний статус посилки від перевізника, окреме джерело від
toysi_ttn/delivery_status, які відображають лише СТАТУС ЗАМОВЛЕННЯ на
боці Toysi) і, щойно НП підтверджує фактичну видачу, самі викликаємо
orders_watcher.update_prom_order_status(order_id, status="delivered").
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


def _receipt_goods_from_order(order: dict) -> list:
    """order["items"] (toysi_code/name/qty/price) -> goods для
    checkbox_client.create_receipt()."""
    return [
        {
            "code": item.get("toysi_code", ""),
            "name": item.get("name", ""),
            "price": item.get("price", 0),
            "qty": item.get("qty", 1),
        }
        for item in order["items"]
    ]


def _maybe_issue_receipt(conn, order: dict, ttn: str) -> None:
    """Видає фіскальний чек напряму (checkbox_client.create_receipt) —
    замінює колишню ЕТТН-прив'язку (_maybe_register_ettn, ЗАКРИТО
    2026-07-22, див. докстрінг файлу) — не залежить від того, чиїм
    токеном НП створено ТТН.

    Ідемпотентність — той самий прапорець checkbox_ettn_registered_at,
    що й раніше (перевикористано, не нова колонка): перевіряємо щоразу,
    коли умова тригера виконана, а прапорець ще не виставлено, щоб
    тимчасова мережева помилка природно повторилась на наступному циклі
    опитування, а не загубилась назавжди.

    Два незалежні тригери (перший, що спрацював — видає чек, більше не
    перевіряємо другий цього ж циклу):
    - payment_method="prepaid": payment_confirmed — гроші вже отримані.
    - payment_method="cod" + carrier=nova_poshta: get_tracking_status(ttn)
      ["delivered"] — гроші накладеного платежу отримані фактично зараз.

    Помилка тут (включно з НЕ-Checkbox винятком, напр. malformed order
    ["items"]) НЕ має зупиняти track_orders() для ІНШИХ замовлень у тому ж
    циклі опитування — тому ловимо широко (Exception), а не лише
    CheckboxAPIError."""
    if order.get("checkbox_ettn_registered_at"):
        return

    payment_method = order.get("payment_method")
    if payment_method == "prepaid":
        if not order.get("payment_confirmed"):
            return
        payment_type = "CASHLESS"
    elif payment_method == "cod":
        if not ttn or order.get("carrier", "nova_poshta") != "nova_poshta":
            return
        try:
            tracking = nova_poshta.get_tracking_status(ttn)
        except nova_poshta.NovaPoshtaAPIError as e:
            print(
                f"[order_status_tracker] Не вдалось перевірити трекінг НП для видачі чека "
                f"{order['internal_order_id']} (ТТН {ttn}): {e}",
                file=sys.stderr,
            )
            return
        if not tracking or not tracking["delivered"]:
            return
        payment_type = "CASH"
    else:
        return

    try:
        total_amount = sum(item.get("price", 0) * item.get("qty", 1) for item in order["items"])
        result = create_receipt(
            goods=_receipt_goods_from_order(order),
            payment_type=payment_type,
            total_amount=total_amount,
            order_id=order["internal_order_id"],
        )
    except CheckboxAPIError as e:
        print(
            f"[order_status_tracker] Не вдалось видати чек Checkbox для "
            f"{order['internal_order_id']} ({payment_type}): {e}",
            file=sys.stderr,
        )
        return
    except Exception as e:
        print(
            f"[order_status_tracker] Неочікувана помилка при видачі чека для "
            f"{order['internal_order_id']}: {e}",
            file=sys.stderr,
        )
        return

    receipt_id = result.get("id") if isinstance(result, dict) else None
    mark_checkbox_ettn_registered(conn, order["internal_order_id"], receipt_id)
    print(f"[order_status_tracker] Чек Checkbox видано ({payment_type}): "
          f"{order['internal_order_id']} (receipt_id={receipt_id})")


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


def _maybe_push_ttn_to_prom(conn, order: dict, ttn: str) -> None:
    """Прикріплює ЕН до замовлення на СТОРОНІ Prom (POST
    /delivery/save_declaration_id) — щойно з'явився toysi_ttn і ще не
    передавали (prom_ttn_pushed_at IS NULL). Викликається НЕЗАЛЕЖНО від
    підтвердження доставки (на відміну від _maybe_push_delivered_to_prom
    нижче) — саме швидкість тут важлива: офіційна вимога Prom для
    "Дешевої доставки" — ЕН має з'явитись НЕ ПІЗНІШЕ дня відправлення,
    тож чекати підтвердження видачі від НП (може бути через кілька днів)
    означало б систематично запізнюватись з кожним замовленням.

    Лише platform=prom + carrier=nova_poshta (Prom /delivery/save_declaration_id
    підтримує nova_poshta/ukrposhta/meest/rozetka_delivery — з наших двох
    carrier ми підтримуємо лише ці два, ukrposhta теж технічно підійде,
    якщо колись знадобиться).

    Ідемпотентність — той самий підхід, що й rozetka_ttn_pushed_at/
    prom_delivered_pushed_at: перевіряємо прапорець щоразу, а не лише "у
    момент появи ttn", щоб тимчасова мережева помилка природно
    повторилась на наступному циклі, а не загубилась назавжди. Prom сам
    повертає ідемпотентну відповідь при повторному ЕН
    (attach_prom_declaration_id() трактує це як success), тож подвійний
    виклик (напр. якщо прапорець з якоїсь причини не збігся з реальністю)
    не шкідливий.

    Помилка тут (включно з НЕ-PromAPIError винятком) НЕ має зупиняти
    track_orders() для інших замовлень у тому самому циклі."""
    if not ttn:
        return
    if order.get("platform") != "prom":
        return
    if order.get("carrier", "nova_poshta") not in ("nova_poshta", "ukrposhta"):
        return
    if order.get("prom_ttn_pushed_at"):
        return

    try:
        attach_prom_declaration_id(order["order_id"], ttn, delivery_type=order.get("carrier", "nova_poshta"))
    except PromAPIError as e:
        print(
            f"[order_status_tracker] Не вдалось передати ЕН у Prom для "
            f"{order['internal_order_id']} (ТТН {ttn}): {e}",
            file=sys.stderr,
        )
        return
    except Exception as e:
        print(
            f"[order_status_tracker] Неочікувана помилка при передачі ЕН у Prom для "
            f"{order['internal_order_id']} (ТТН {ttn}): {e}",
            file=sys.stderr,
        )
        return

    mark_prom_ttn_pushed(conn, order["internal_order_id"])
    print(f"[order_status_tracker] ЕН передано в Prom: {order['internal_order_id']} (ТТН {ttn})")


def _maybe_push_delivered_to_prom(conn, order: dict, ttn: str) -> None:
    """Auto-3 (2026-07-17): щойно Нова Пошта підтверджує ФАКТИЧНУ видачу
    посилки (nova_poshta.get_tracking_status(ttn)["delivered"]) — викликає
    orders_watcher.update_prom_order_status(order_id, status="delivered"),
    щоб клієнт бачив реальний стан у кабінеті Prom, а не застряглий
    "Прийнято" (Prom не робить цього сам, підтверджено — Vis-10). Лише
    platform=prom + carrier=nova_poshta (Укрпошта немає цього API взагалі;
    той самий carrier-гейт, що й _maybe_register_ettn вище).

    Ідемпотентність — prom_delivered_pushed_at, той самий підхід, що й
    rozetka_ttn_pushed_at: перевіряємо прапорець щоразу (не лише "у момент
    появи ttn"), щоб тимчасова мережева помилка природно повторилась на
    наступному циклі, а не загубилась назавжди.

    Не критично (на гроші не впливає — Prom-оплата гейтована підтвердженням
    покупця, не статусом замовлення, Vis-10) — тому будь-яка помилка тут
    (мережа НП, помилка Prom API, неочікуваний виняток) лише логується,
    ніколи не зупиняє track_orders() для інших замовлень у тому самому
    циклі."""
    if not ttn:
        return
    if order.get("platform") != "prom":
        return
    if order.get("carrier", "nova_poshta") != "nova_poshta":
        return
    if order.get("prom_delivered_pushed_at"):
        return

    try:
        tracking = nova_poshta.get_tracking_status(ttn)
    except nova_poshta.NovaPoshtaAPIError as e:
        print(
            f"[order_status_tracker] Не вдалось перевірити трекінг НП для "
            f"{order['internal_order_id']} (ТТН {ttn}): {e}",
            file=sys.stderr,
        )
        return
    except Exception as e:
        print(
            f"[order_status_tracker] Неочікувана помилка трекінгу НП для "
            f"{order['internal_order_id']} (ТТН {ttn}): {e}",
            file=sys.stderr,
        )
        return

    if not tracking or not tracking["delivered"]:
        return

    try:
        update_prom_order_status(order["order_id"], status="delivered")
    except PromAPIError as e:
        print(
            f"[order_status_tracker] Не вдалось передати статус \"delivered\" у Prom для "
            f"{order['internal_order_id']} (ТТН {ttn}): {e}",
            file=sys.stderr,
        )
        return
    except Exception as e:
        print(
            f"[order_status_tracker] Неочікувана помилка при передачі статусу \"delivered\" у Prom для "
            f"{order['internal_order_id']} (ТТН {ttn}): {e}",
            file=sys.stderr,
        )
        return

    mark_prom_delivered_pushed(conn, order["internal_order_id"])
    print(f"[order_status_tracker] Статус \"delivered\" передано в Prom: "
          f"{order['internal_order_id']} (ТТН {ttn}, НП: {tracking['status']})")


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

            order = orders_by_internal_id[internal_id]
            was_unsuccessful = order["delivery_status"] in _UNSUCCESSFUL_DELIVERY_STATUSES if order["delivery_status"] else False
            update_delivery_status(conn, internal_id, toysi_ttn=ttn, delivery_status=delivery_status)
            if delivery_status in _UNSUCCESSFUL_DELIVERY_STATUSES and not was_unsuccessful:
                # Одразу, а не лише в щоденному звіті (P0-2) — власниця
                # прямо просила алерт на КОЖНЕ скасування, не лише
                # агрегований 60-денний показник раз на добу.
                send_telegram_message(
                    f"⚠️ Замовлення {internal_id} (Toysi #{toysi_id}, {order.get('platform', '?')}): "
                    f"{'скасовано' if delivery_status == 'cancelled' else 'повернення'} "
                    f"({describe_order_status(status_code)})"
                )

            order["toysi_ttn"] = ttn
            _maybe_issue_receipt(conn, order, ttn)
            _maybe_push_ttn_to_rozetka(conn, order, ttn)
            _maybe_push_ttn_to_prom(conn, order, ttn)
            _maybe_push_delivered_to_prom(conn, order, ttn)

            ttn_note = f", ТТН: {ttn}" if ttn else ""
            terminal_note = " [термінальний, більше не опитуємо]" if status_code in TERMINAL_ORDER_STATUSES else ""
            print(
                f"[order_status_tracker] {internal_id} (Toysi #{toysi_id}): "
                f"{describe_order_status(status_code)}{ttn_note}{terminal_note}"
            )


if __name__ == "__main__":
    track_orders()
