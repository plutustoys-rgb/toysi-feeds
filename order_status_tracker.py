import os
import sys

from checkbox_client import create_receipt, CheckboxAPIError
from orders_db import (
    get_connection, get_active_toysi_orders, mark_checkbox_ettn_registered,
    mark_rozetka_ttn_pushed, mark_rozetka_processing_pushed, mark_prom_delivered_pushed,
    mark_prom_ttn_pushed, mark_eva_ttn_pushed, update_delivery_status,
    mark_rozetka_cancel_ticket_sent,
)
import nova_poshta
from orders_watcher import update_prom_order_status, attach_prom_declaration_id, PromAPIError
import rozetka_client
import eva_orders_client
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
rozetka_client.update_order_status(order_id, status=61, ttn=ttn) —
status=61 «Заплановано передачу перевізникові» (звірено 2026-08-19:
apidoc status_order.61 + live status_available 26→61 напряму; раніше
помилково слали status=2 «Комплектується», куди ТТН не належить). Після
61 Rozetka сама веде трекінг доставки — ручних переходів не потрібно.

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


def _maybe_issue_receipt(conn, order: dict, ttn: str, delivery_status: str = None) -> None:
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
        # COD-чек CASH: гроші отримані у МОМЕНТ видачі. Сигнал видачі — РІЗНИЙ за перевізником:
        carrier = order.get("carrier", "nova_poshta")
        if carrier == "nova_poshta":
            if not ttn:
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
        elif carrier == "rozetka_delivery":
            # RZ Delivery COD: сигнал «покупець забрав+оплатив» = order-статус Rozetka **6 «Виконано»**
            # (rozetka_client.is_order_done). ЗВІРЕНО АВТОРИТЕТНО (apidoc api_data.js + живо 2026-08-23):
            #  • delivery_status (Toysi-коди) застрягає на 'shipped' — ніколи не 'delivered' (PR #352 хибний);
            #  • is_order_paid теж хибний для COD — apidoc: payment_type='cash' має payment_status=null,
            #    Rozetka не трекає готівку на пункті платіжним статусом (PR #382 хибний);
            #  • правильний enum: 4 Доставляється → 5/76 чекає на пункті → **6 Виконано** (Rozetka сама
            #    ставить після видачі+оплати); живо: 903654095 (COD-отримано)=6, Кравчук 903992205=80 (у дорозі).
            # is_order_done=False на помилці/невідомо → чек не поспішає (безпечний дефалт, наступний цикл
            # опитування повторить). Ідемпотентність (checkbox_ettn_registered_at, вгорі функції) гарантує
            # один чек: prepaid/НП-гілки виставляють свій чек раніше й ставлять прапорець, тож дубля на
            # статусі 6 не буде — ця гілка кличеться лише для cod+rozetka_delivery.
            if not rozetka_client.is_order_done(order["order_id"]):
                return
        else:
            return  # інші перевізники (напр. укрпошта) — COD-чек поки не покрито
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
            customer_phone=order.get("phone"),  # авто-надсилання чека покупцю (Viber/SMS), КОДВ §7
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


# delivery_status Toysi, на яких доречно показати клієнту «Комплектується» (2) на Rozetka.
_ROZETKA_PROCESSING_DELIVERY_STATUSES = {"assembling", "packed"}


def _maybe_advance_rozetka_processing(conn, order: dict, delivery_status: str, ttn: str) -> None:
    """Гладкість статусу для клієнта: коли Toysi у стані assembling/packed і ТТН ще нема —
    виставляє на Rozetka 2 «Комплектується. Дані підтверджені» (перехід 26→2, звірено живо
    2026-08-19: status_available 26 містить 2, а 2 містить 61). Щоб клієнт бачив плавну
    прогресію 26→2→61, а не стрибок. Ідемпотентно (rozetka_processing_pushed_at); best-effort —
    помилка НЕ валить track_orders() для інших замовлень. Пропускаємо, якщо ТТН уже є (тоді
    одразу 61 через _maybe_push_ttn_to_rozetka) або крок 2 уже робили / ТТН уже слали."""
    if order.get("platform") != "rozetka":
        return
    if ttn:
        return
    if order.get("rozetka_processing_pushed_at") or order.get("rozetka_ttn_pushed_at"):
        return
    if delivery_status not in _ROZETKA_PROCESSING_DELIVERY_STATUSES:
        return

    try:
        rozetka_client.update_order_status(
            order["order_id"], status=rozetka_client.ORDER_STATUS_PROCESSING,
        )
    except rozetka_client.RozetkaAPIError as e:
        # code=1005 «Наступний статус недоступний» — замовлення вже НЕ на 26 (уже
        # комплектується/далі за потоком). Це НЕ транзиторно: ретраї не допоможуть,
        # тож ставимо прапорець, щоб не спамити цим виклик щоцикл. Інші (мережеві)
        # помилки — лишаємо без прапорця, щоб природно повторились наступного разу.
        if "code=1005" in str(e):
            mark_rozetka_processing_pushed(conn, order["internal_order_id"])
            return
        print(
            f"[order_status_tracker] Не вдалось виставити 'Комплектується' у Rozetka для "
            f"{order['internal_order_id']}: {e}",
            file=sys.stderr,
        )
        return
    except Exception as e:
        print(
            f"[order_status_tracker] Неочікувана помилка при виставленні 'Комплектується' у Rozetka для "
            f"{order['internal_order_id']}: {e}",
            file=sys.stderr,
        )
        return

    mark_rozetka_processing_pushed(conn, order["internal_order_id"])
    print(f"[order_status_tracker] Rozetka 'Комплектується' (2) виставлено: {order['internal_order_id']}")


def _maybe_push_ttn_to_rozetka(conn, order: dict, ttn: str) -> None:
    """Прикріплює ТТН до замовлення на СТОРОНІ Rozetka (PUT /orders/{id},
    status=61 «Заплановано передачу перевізникові» + ttn) — щойно з'явився toysi_ttn і ще не передавали
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
            order["order_id"], status=rozetka_client.ORDER_STATUS_SCHEDULED_HANDOVER, ttn=ttn,
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


def _maybe_push_ttn_to_eva(conn, order: dict, ttn: str) -> None:
    """Прикріплює ТТН до замовлення на СТОРОНІ EVA (PATCH /orders/{id}/status,
    status=12 «Підтверджене покупцем» + tracking_number) — щойно з'явився
    toysi_ttn і ще не передавали (eva_ttn_pushed_at IS NULL). За потоком EVA
    (див. eva_orders_client): 1 -> 11 (accept, order_router) -> 12 (з ТТН, тут)
    -> EVA САМА виставляє 5 (Відправлено) з трекінгу перевізника.

    order["order_id"] — ID замовлення В САМІЙ EVA (не toysi_order_id), саме те,
    що приймає eva_orders_client.update_order_status(). Ідемпотентність і
    широкий except — той самий підхід, що _maybe_push_ttn_to_rozetka(): помилка
    (включно з НЕ-EvaAPIError) НЕ має зупиняти track_orders() для інших
    замовлень; прапорець перевіряється щоцикл, щоб тимчасова помилка природно
    повторилась наступного разу, а не загубилась."""
    if not ttn:
        return
    if order.get("platform") != "eva":
        return
    if order.get("eva_ttn_pushed_at"):
        return

    try:
        eva_orders_client.update_order_status(
            order["order_id"],
            status=eva_orders_client.EVA_STATUS_CONFIRMED_BY_BUYER,
            tracking_number=ttn,
        )
    except eva_orders_client.EvaAPIError as e:
        print(
            f"[order_status_tracker] Не вдалось передати ТТН у EVA для "
            f"{order['internal_order_id']} (ТТН {ttn}): {e}",
            file=sys.stderr,
        )
        return
    except Exception as e:
        print(
            f"[order_status_tracker] Неочікувана помилка при передачі ТТН у EVA для "
            f"{order['internal_order_id']} (ТТН {ttn}): {e}",
            file=sys.stderr,
        )
        return

    mark_eva_ttn_pushed(conn, order["internal_order_id"])
    print(f"[order_status_tracker] ТТН передано в EVA: {order['internal_order_id']} (ТТН {ttn})")


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


def _maybe_ticket_rozetka_cancelled(conn, order: dict) -> None:
    """Пост-форвард захист від «купили й відправили скасоване». Замовлення вже
    передане в Toysi (форвард відбувся), АЛЕ покупець скасував його на Rozetka.
    Toysi API НЕ має методу скасування (лише order_create + order_status), тож
    автоматично зняти замовлення через API ми не можемо — натомість шлемо запит
    на скасування ПРЯМО В ЧАТ МЕНЕДЖЕРА Toysi тим самим каналом, що й RZ-Delivery
    маркування: telegram_userbot_client.send_marking(). Гейт MARKING_TEST_MODE
    (той самий, що для маркування): '1' (ДЕФОЛТ) → на номер власника (перевірка
    формату), '0' → реальний чат Toysi. Текст — звернення до Toysi, готове як є.

    Живий rozetka_client.get_order_status(); якщо скасувальний код
    (ROZETKA_CANCELLED_STATUSES) і тікет ще не слався — send_marking у Toysi +
    короткий FYI власнику + позначка rozetka_cancel_ticket_sent_at (ідемпотентно).
    Мітка ставиться ЛИШЕ на УСПІШНІЙ відправці (send_marking кидає UserbotError
    при збої сесії/цілі/мережі) — інакше наступний цикл повторить спробу.
    Best-effort: RozetkaAPIError/None → тихо виходимо, не валимо основне
    відстеження Toysi-статусів інших замовлень. Лише platform='rozetka'."""
    if order.get("platform") != "rozetka":
        return
    if order.get("rozetka_cancel_ticket_sent_at"):
        return

    try:
        live_status = rozetka_client.get_order_status(order["order_id"])
    except rozetka_client.RozetkaAPIError as e:
        print(
            f"[order_status_tracker] Не вдалось перевірити скасування Rozetka #{order['order_id']} "
            f"(тікет): {e}",
            file=sys.stderr,
        )
        return

    if live_status is None or live_status not in rozetka_client.ROZETKA_CANCELLED_STATUSES:
        return

    toysi_id = order.get("toysi_order_id")
    # Тон повідомлення — звернення ДО Toysi (готове до пересилання менеджеру
    # постачальника): перша строка одразу проситься переслати, без внутрішньої
    # лексики «тікет/вручну». Другий рядок — компактна прив'язка до Rozetka-
    # замовлення для орієнтації власника.
    message = (
        f"Доброго дня! Просимо скасувати замовлення #{toysi_id}, поки воно ще не "
        f"відвантажене — покупець скасував його з нашого боку. Дякуємо!\n"
        f"Rozetka #{order['order_id']} · {order.get('customer_name') or '?'}"
    )
    print(f"[order_status_tracker] {message}", file=sys.stderr)

    # Канал до Toysi — той самий юзербот, що шле RZ-Delivery маркування, з тим
    # самим гейтом MARKING_TEST_MODE ('1' дефолт → номер власника; '0' → Toysi).
    test_mode = os.environ.get("MARKING_TEST_MODE", "1").strip() != "0"
    dest = "мій номер (тест)" if test_mode else "чат Toysi"
    try:
        import telegram_userbot_client
        sent = bool(telegram_userbot_client.send_marking(message, to_toysi=not test_mode))
    except Exception as e:  # noqa: BLE001 — send_marking кидає UserbotError; збій → ретрай наступним циклом
        print(
            f"[order_status_tracker] Запит на скасування Rozetka #{order['order_id']} "
            f"(Toysi #{toysi_id}) → {dest}: НЕ надіслано ({e}). Ретрай наступним циклом.",
            file=sys.stderr,
        )
        return

    if not sent:
        return

    mark_rozetka_cancel_ticket_sent(conn, order["internal_order_id"])
    # FYI власнику в алерти (send_marking у тест-режимі йде на його ж номер, але
    # у бойовому — в чат Toysi, тож окремий запис у канал алертів для видимості).
    send_telegram_message(
        f"↪️ Запит на скасування Rozetka #{order['order_id']} (Toysi #{toysi_id}, "
        f"{order.get('customer_name') or '?'}) надіслано → {dest}."
    )


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
            _maybe_issue_receipt(conn, order, ttn, delivery_status)
            _maybe_advance_rozetka_processing(conn, order, delivery_status, ttn)
            _maybe_push_ttn_to_rozetka(conn, order, ttn)
            _maybe_push_ttn_to_prom(conn, order, ttn)
            _maybe_push_ttn_to_eva(conn, order, ttn)
            _maybe_push_delivered_to_prom(conn, order, ttn)
            # Пост-форвард: покупець міг скасувати на Rozetka вже ПІСЛЯ передачі
            # в Toysi (Toysi цього не знає й веде замовлення далі) — тікет адміну
            # на ручне скасування в Toysi, поки не відвантажене.
            _maybe_ticket_rozetka_cancelled(conn, order)

            ttn_note = f", ТТН: {ttn}" if ttn else ""
            terminal_note = " [термінальний, більше не опитуємо]" if status_code in TERMINAL_ORDER_STATUSES else ""
            print(
                f"[order_status_tracker] {internal_id} (Toysi #{toysi_id}): "
                f"{describe_order_status(status_code)}{ttn_note}{terminal_note}"
            )


if __name__ == "__main__":
    # Гарантувати наявність колонок (міграція ідемпотентна) навіть якщо tracker
    # запускається раніше за order_router після деплою — інакше mark_rozetka_
    # cancel_ticket_sent() впав би «no such column» на щойно доданій колонці.
    from orders_db import init_db
    init_db()
    track_orders()
