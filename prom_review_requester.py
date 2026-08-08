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
  - Відправка — Prom Chat API `POST /chat/send_order_context` (Bearer PROM_API_KEY,
    multipart `{order_id, body}`): шле повідомлення + картку замовлення покупцю й САМ
    створює кімнату чату. Тобто адресуємо по `order_id`, а НЕ по кімнаті — працює для
    будь-якого замовлення, навіть якщо покупець нам ще не писав (це те, що робить
    кнопка «Чат» на сторінці замовлення). `send_message` вимагав би room_ident
    (`{user_id}_{company_id}_buyer`, де user_id — chat-id, НЕ order.client_id), тож
    його не використовуємо.

ТРИГЕР (рішення власника): Prom-замовлення, ОТРИМАНЕ покупцем ≥24 год тому, товарний
запит ще не слався (`prom_review_request_sent_at IS NULL`). Сигнал «отримано» —
`prom_delivered_pushed_at` (ставиться, коли Нова Пошта підтвердила видачу; живо
звірено, що `delivery_status` до 'delivered' не доходить — лишається 'shipped').
Критерій — СТАН замовлення: бэклог уже отриманих підхопиться першим прогоном.
ЛИШЕ товарний відгук — компанійський НЕ чіпаємо (пряме рішення власника).

Товар без Prom-`id` (делістнутий з каталогу — id/sku/url = null) пропускаємо: сторінки
`product-opinions/create/{id}` для нього не існує. Якщо ВСІ товари замовлення такі —
запит не шлемо.

БЕЗПЕКА: за замовчуванням DRY-RUN (лише друкує, що надіслав би). Реальна відправка —
лише з `--send`. `--mark-sent <order_id>` ставить позначку БЕЗ відправки (для backdate
замовлень, уже оброблених вручну, щоб їм не пішов дубль).

ЗАПУСК:
    python prom_review_requester.py                 # DRY-RUN: показати, що надіслав би
    python prom_review_requester.py --send          # реально надіслати
    python prom_review_requester.py --mark-sent <order_id>  # backdate без відправки
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
REQUEST_TIMEOUT = 30
MAX_BODY_LEN = 1000  # ліміт body у /chat/send_order_context

MIN_HOURS_SINCE_DELIVERED = 24

REVIEW_URL_TEMPLATE = "https://prom.ua/ua/product-opinions/create/{pid}"
# Дослівно текст, що Prom генерує в модалці «Запит на відгук про товар» (звірено
# живо 2026-08-07). Для кількох товарів — той самий заклик + список посилань.
MESSAGE_ONE = "Дякуємо за покупку! Будь ласка, залиште відгук про товар тут: {url}"
MESSAGE_MANY = "Дякуємо за покупку! Будь ласка, залиште відгук про товари:\n{urls}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {PROM_API_KEY}"}


# ---- Чисті, тестовані функції (без мережі) --------------------------------

def build_review_body(order: dict) -> str | None:
    """Одне повідомлення на ЗАМОВЛЕННЯ з посиланнями на відгук по кожному товару,
    що має Prom-`id`. Товар без `id` (делістнутий) пропускаємо. None, якщо жоден
    товар не має id (запит слати нема куди)."""
    urls = []
    for p in order.get("products", []) or []:
        pid = p.get("id")
        if pid:
            urls.append(REVIEW_URL_TEMPLATE.format(pid=pid))
    if not urls:
        return None
    if len(urls) == 1:
        body = MESSAGE_ONE.format(url=urls[0])
    else:
        body = MESSAGE_MANY.format(urls="\n".join(urls))
    return body[:MAX_BODY_LEN]


def select_eligible(conn, min_hours: int = MIN_HOURS_SINCE_DELIVERED, now: datetime = None) -> list:
    """Prom-замовлення, ОТРИМАНІ покупцем ≥ min_hours тому, яким товарний запит на
    відгук ще не слався.

    Сигнал «отримано» — `prom_delivered_pushed_at IS NOT NULL` (а НЕ рядок
    `delivery_status`): ця мітка ставиться саме коли Нова Пошта підтвердила
    ФАКТИЧНУ видачу посилки (order_status_tracker._maybe_push_delivered_to_prom,
    `tracking["delivered"]`). Живо звірено 2026-08-07: реальні orders.db не мають
    значення `delivery_status='delivered'` (доходить лише до 'shipped'=НП-код 60),
    а видачу відображає саме `prom_delivered_pushed_at` (=НП «вручено»). Час мітки
    ≈ момент видачі, тож `<= cutoff` дає «≥N год після отримання»."""
    now = now or datetime.now()
    cutoff = (now - timedelta(hours=min_hours)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT * FROM orders "
        "WHERE platform = 'prom' "
        "AND prom_delivered_pushed_at IS NOT NULL AND prom_delivered_pushed_at <= ? "
        "AND prom_review_request_sent_at IS NULL "
        "AND (delivery_status IS NULL OR delivery_status NOT IN ('returned', 'cancelled'))",
        (cutoff,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---- Мережеві виклики Prom API --------------------------------------------

def fetch_prom_order(order_id: str) -> dict:
    r = requests.get(f"{PROM_API_URL}/orders/{order_id}", headers=_headers(), timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("order", {}) or {}


def send_order_context(order_id: str, body: str) -> int | None:
    """POST /chat/send_order_context — шле повідомлення + картку замовлення покупцю,
    сам створює кімнату чату. Адресація по order_id (не по кімнаті). multipart/form-data
    зі схеми API. Кидає RuntimeError, якщо Prom повернув status != ok."""
    r = requests.post(
        f"{PROM_API_URL}/chat/send_order_context",
        headers=_headers(),
        files={"order_id": (None, str(int(order_id))), "body": (None, body[:MAX_BODY_LEN])},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"Prom send_order_context повернув помилку: {data}")
    return (data.get("data") or {}).get("message_id")


def mark_sent(conn, internal_order_id: str) -> None:
    conn.execute(
        "UPDATE orders SET prom_review_request_sent_at = ? WHERE internal_order_id = ?",
        (datetime.now().isoformat(timespec="seconds"), internal_order_id),
    )


# ---- Оркестрація -----------------------------------------------------------

def run(dry_run: bool = True, min_hours: int = MIN_HOURS_SINCE_DELIVERED) -> dict:
    if not PROM_API_KEY:
        print("[ReviewReq] PROM_API_KEY не задано в .env — не можу працювати.", file=sys.stderr)
        return {"eligible": 0, "sent": 0, "skipped_no_products": 0, "errors": 0}

    init_db()
    stats = {"eligible": 0, "sent": 0, "skipped_no_products": 0, "errors": 0}
    with get_connection() as conn:
        eligible = select_eligible(conn, min_hours=min_hours)
        stats["eligible"] = len(eligible)
        if not eligible:
            print("[ReviewReq] Придатних замовлень нема (усе вже оброблено або нічого не отримано ≥24г).")
            return stats

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

            body = build_review_body(order)
            buyer = f"{order.get('client_first_name', '')} {order.get('client_last_name', '')}".strip()
            if not body:
                print(f"  ⏭️ №{oid} ({buyer}): усі товари без Prom-id (делістнуті) — запит не побудувати, пропускаю.")
                stats["skipped_no_products"] += 1
                continue

            if dry_run:
                preview = body.replace("\n", "\n         ")
                print(f"  ✉️ №{oid} ({buyer}):\n         «{preview}»")
                continue

            try:
                send_order_context(oid, body)
                mark_sent(conn, row["internal_order_id"])
                stats["sent"] += 1
                print(f"  ✅ №{oid} ({buyer}): надіслано, позначено.")
            except (requests.RequestException, RuntimeError, ValueError) as e:
                print(f"  ⚠️ №{oid} ({buyer}): помилка відправки ({e}) — позначку НЕ ставлю.")
                stats["errors"] += 1

    print(f"\n[ReviewReq] Підсумок: придатних {stats['eligible']}, надіслано {stats['sent']}, "
          f"без Prom-id {stats['skipped_no_products']}, помилок {stats['errors']}.")
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
                    help=f"Мін. годин від отримання (деф. {MIN_HOURS_SINCE_DELIVERED}).")
    args = ap.parse_args()

    if args.mark_sent:
        cmd_mark_sent(args.mark_sent)
    else:
        run(dry_run=not args.send, min_hours=args.hours)


if __name__ == "__main__":
    main()
