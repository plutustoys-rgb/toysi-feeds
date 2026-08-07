"""
prom_review_requester.py — автоматичний ТОВАРНИЙ запит на відгук у Prom-чаті.

НАВІЩО (2026-08-07, завдання Cowork): Prom подзвонив — для росту в пошуку треба
відгуки, а їх 0% за рік при 17 реальних замовленнях. Причина: авто-нагадування
Prom іде лише на email, а наші покупці (накладений, Нова Пошта) лишають телефон.
Робочий канал (перевірено вручну власником): текст запиту → у Prom-чат покупцю.

ДОСЛІДЖЕНО ЖИВО 2026-08-07 (усе на API, БЕЗ SMS/браузера):
  - Кнопка «Запит на відгук про товар» — суто фронтенд, прихованого API нема, АЛЕ
    URL детермінований: `https://prom.ua/ua/product-opinions/create/{prom_product_id}`,
    а `prom_product_id` приходить прямо з Orders API (`GET /orders/{id}` → products[].id).
  - Відправка — Prom Chat API (`POST /chat/send_message`, Bearer PROM_API_KEY), той
    самий канал, що prom_chat_bot.py. Адресація по кімнаті: order.client_id → кімната
    з тим самим buyer_client_id (`GET /chat/rooms`).

ТРИГЕР (рішення власника): замовлення, чий `delivery_status='delivered'` («Отримано»)
СТАВ таким ≥24 год тому (за `prom_delivered_pushed_at`), товарний запит ще не слався
(`prom_review_request_sent_at IS NULL`). Критерій — СТАН замовлення, не «щойно нове»:
бэклог уже доставлених підхопиться при першому прогоні. ЛИШЕ товарний відгук —
компанійський НЕ чіпаємо (пряме рішення власника).

⚠️ ВІДКРИТЕ: у покупця, який нам не писав, кімнати чату може не бути — тоді відправка
пропускається (лог), позначка НЕ ставиться (спробує пізніше). Перша реальна відправка
під наглядом власника покаже, чи `send_message` створює кімнату сам.

БЕЗПЕКА: за замовчуванням DRY-RUN (лише друкує, що надіслав би). Реальна відправка —
лише з `--send`. `--mark-sent <order_id>` ставить позначку БЕЗ відправки (для
backdate замовлень, уже оброблених вручну, напр. №419488858 Вика Цвигун).

ЗАПУСК:
    python prom_review_requester.py                 # DRY-RUN: показати, що надіслав би
    python prom_review_requester.py --send          # реально надіслати
    python prom_review_requester.py --mark-sent 419488858   # backdate без відправки
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from orders_db import get_connection, init_db, _row_to_dict

load_dotenv()

PROM_API_KEY = os.environ.get("PROM_API_KEY", "")
PROM_API_URL = "https://my.prom.ua/api/v1"
PROJECT = "promua"
REQUEST_TIMEOUT = 30
MAX_MSG_LEN = 1000

MIN_HOURS_SINCE_DELIVERED = 24
DELIVERED_STATUS = "delivered"

REVIEW_URL_TEMPLATE = "https://prom.ua/ua/product-opinions/create/{pid}"
# Дослівно той текст, що Prom генерує в модалці «Запит на відгук про товар»
# (звірено живо 2026-08-07). Один запит = один товар.
MESSAGE_TEMPLATE = "Дякуємо за покупку! Будь ласка, залиште відгук про товар тут: {url}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {PROM_API_KEY}"}


# ---- Чисті, тестовані функції (без мережі) --------------------------------

def build_review_messages(order: dict) -> list:
    """Список повідомлень (по одному на товар із відомим Prom-id) для замовлення
    з Orders API. Товар без `id` пропускаємо — URL відгуку без нього не побудувати."""
    msgs = []
    for p in order.get("products", []) or []:
        pid = p.get("id")
        if not pid:
            continue
        url = REVIEW_URL_TEMPLATE.format(pid=pid)
        msgs.append(MESSAGE_TEMPLATE.format(url=url)[:MAX_MSG_LEN])
    return msgs


def resolve_room_ident(rooms: list, client_id) -> str | None:
    """Знаходить room_ident кімнати покупця за client_id (== buyer_client_id).
    None, якщо кімнати нема (покупець нам не писав)."""
    if client_id in (None, "", 0, "0"):
        return None
    try:
        target = int(client_id)
    except (TypeError, ValueError):
        return None
    for r in rooms or []:
        bcid = r.get("buyer_client_id")
        if bcid and int(bcid) == target and r.get("ident"):
            return r["ident"]
    return None


def select_eligible(conn, min_hours: int = MIN_HOURS_SINCE_DELIVERED, now: datetime = None) -> list:
    """Prom-замовлення, доставлені (`delivery_status='delivered'`) ≥ min_hours тому
    (за `prom_delivered_pushed_at`), яким товарний запит на відгук ще не слався."""
    now = now or datetime.now()
    cutoff = (now - timedelta(hours=min_hours)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT * FROM orders "
        "WHERE platform = 'prom' AND delivery_status = ? "
        "AND prom_delivered_pushed_at IS NOT NULL AND prom_delivered_pushed_at <= ? "
        "AND prom_review_request_sent_at IS NULL",
        (DELIVERED_STATUS, cutoff),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---- Мережеві виклики Prom API --------------------------------------------

def fetch_prom_order(order_id: str) -> dict:
    r = requests.get(f"{PROM_API_URL}/orders/{order_id}", headers=_headers(), timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("order", {}) or {}


def fetch_chat_rooms() -> list:
    r = requests.get(f"{PROM_API_URL}/chat/rooms", headers=_headers(),
                     params={"project": PROJECT}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("data", {}).get("rooms", []) or []


def send_chat_message(room_ident: str, body: str) -> int | None:
    r = requests.post(f"{PROM_API_URL}/chat/send_message", headers=_headers(),
                      json={"room_ident": room_ident, "body": body[:MAX_MSG_LEN], "project": PROJECT},
                      timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"Prom send_message повернув помилку: {data}")
    return data.get("message_id")


def mark_sent(conn, internal_order_id: str) -> None:
    conn.execute(
        "UPDATE orders SET prom_review_request_sent_at = ? WHERE internal_order_id = ?",
        (datetime.now().isoformat(timespec="seconds"), internal_order_id),
    )


# ---- Оркестрація -----------------------------------------------------------

def run(dry_run: bool = True, min_hours: int = MIN_HOURS_SINCE_DELIVERED) -> dict:
    if not PROM_API_KEY:
        print("[ReviewReq] PROM_API_KEY не задано в .env — не можу працювати.", file=sys.stderr)
        return {"eligible": 0, "sent": 0, "skipped_no_room": 0, "skipped_no_products": 0}

    init_db()
    stats = {"eligible": 0, "sent": 0, "skipped_no_room": 0, "skipped_no_products": 0, "errors": 0}
    with get_connection() as conn:
        eligible = select_eligible(conn, min_hours=min_hours)
        stats["eligible"] = len(eligible)
        if not eligible:
            print("[ReviewReq] Придатних замовлень нема (усе вже оброблено або нічого не доставлено ≥24г).")
            return stats

        rooms = fetch_chat_rooms()
        mode = "DRY-RUN (нічого не шлю)" if dry_run else "SEND (реальна відправка)"
        print(f"[ReviewReq] {mode}. Придатних замовлень: {len(eligible)}.\n")

        for row in eligible:
            oid = row["order_id"]
            try:
                order = fetch_prom_order(oid)
            except requests.RequestException as e:
                print(f"  ⚠️ №{oid}: не вдалось отримати замовлення ({e}) — пропускаю цей прогін.")
                stats["errors"] += 1
                continue

            messages = build_review_messages(order)
            if not messages:
                print(f"  ⏭️ №{oid}: у товарах нема Prom-id — запит не побудувати, пропускаю.")
                stats["skipped_no_products"] += 1
                continue

            client_id = order.get("client_id")
            room_ident = resolve_room_ident(rooms, client_id)
            buyer = f"{order.get('client_first_name','')} {order.get('client_last_name','')}".strip()

            if not room_ident:
                print(f"  ⏭️ №{oid} ({buyer}): нема кімнати чату (покупець не писав) — "
                      f"пропускаю, позначку НЕ ставлю (спробую пізніше).")
                stats["skipped_no_room"] += 1
                continue

            if dry_run:
                print(f"  ✉️ №{oid} ({buyer}) → кімната {room_ident}:")
                for m in messages:
                    print(f"       «{m}»")
                continue

            try:
                for m in messages:
                    send_chat_message(room_ident, m)
                mark_sent(conn, row["internal_order_id"])
                stats["sent"] += 1
                print(f"  ✅ №{oid} ({buyer}): надіслано {len(messages)} повідомл., позначено.")
            except (requests.RequestException, RuntimeError) as e:
                print(f"  ⚠️ №{oid} ({buyer}): помилка відправки ({e}) — позначку НЕ ставлю.")
                stats["errors"] += 1

    print(f"\n[ReviewReq] Підсумок: придатних {stats['eligible']}, надіслано {stats['sent']}, "
          f"без кімнати {stats['skipped_no_room']}, без Prom-id {stats['skipped_no_products']}, "
          f"помилок {stats['errors']}.")
    return stats


def cmd_mark_sent(order_id: str) -> None:
    """Ставить позначку prom_review_request_sent_at БЕЗ відправки — для замовлень,
    уже оброблених вручну (щоб автоматика не надіслала дубль)."""
    init_db()
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE orders SET prom_review_request_sent_at = ? "
            "WHERE order_id = ? AND platform = 'prom' AND prom_review_request_sent_at IS NULL",
            (datetime.now().isoformat(timespec="seconds"), str(order_id)),
        )
        n = cur.rowcount
    if n:
        print(f"[ReviewReq] №{order_id}: позначено як «запит надіслано» (backdate, без відправки).")
    else:
        print(f"[ReviewReq] №{order_id}: не знайдено Prom-замовлення без позначки (можливо, вже позначено).")


def main() -> None:
    ap = argparse.ArgumentParser(description="Товарний запит на відгук у Prom-чаті (за замовч. DRY-RUN).")
    ap.add_argument("--send", action="store_true", help="Реально надіслати (без цього — лише dry-run).")
    ap.add_argument("--mark-sent", metavar="ORDER_ID", help="Позначити замовлення обробленим БЕЗ відправки (backdate).")
    ap.add_argument("--hours", type=int, default=MIN_HOURS_SINCE_DELIVERED,
                    help=f"Мін. годин від доставки (деф. {MIN_HOURS_SINCE_DELIVERED}).")
    args = ap.parse_args()

    if args.mark_sent:
        cmd_mark_sent(args.mark_sent)
    else:
        run(dry_run=not args.send, min_hours=args.hours)


if __name__ == "__main__":
    main()
