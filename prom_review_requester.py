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

ТРИГЕР (рішення власника 2026-08-21) — ДВА ЕТАПИ за сигналом «отримано» (`prom_delivered_pushed_at`
— ставиться, коли Нова Пошта підтвердила видачу; живо звірено, що `delivery_status` до 'delivered'
не доходить — лишається 'shipped'):
  • КОМПАНІЙСЬКИЙ відгук — У МОМЕНТ отримання (0 год), поки не слався (`prom_company_review_sent_at
    IS NULL`). Лягає на весь магазин + тай-брейк ранжування — вартий кратно більше (оцінка SEO).
  • ТОВАРНИЙ відгук — НАСТУПНОГО ДНЯ (≥24 год), поки не слався (`prom_review_request_sent_at IS NULL`).
Окремі мітки/тайминги, тож етапи не заважають один одному. Бэклог уже отриманих (у вікні max_days)
підхопиться першим прогоном.

Товар без Prom-`id` (делістнутий — id/sku/url = null) не має сторінки `product-opinions/create/{id}`,
тож товарний етап його пропускає (компанійський уже пішов при отриманні — відгук не втрачаємо).
URL: товар `product-opinions/create/{pid}`, компанія `opinions/create/{company_id}?order_id=...`.

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
from telegram_notify import send_telegram_message

load_dotenv()

PROM_API_KEY = os.environ.get("PROM_API_KEY", "")
PROM_API_URL = "https://my.prom.ua/api/v1"
REQUEST_TIMEOUT = 30
MAX_BODY_LEN = 1000  # ліміт body у /chat/send_order_context

MIN_HOURS_SINCE_DELIVERED = 24
# Верхня межа (рішення власника 2026-08-08): не слати по замовленнях, отриманих
# давніше тижня. Дві причини: (1) свіжий запит доречніший; (2) старі замовлення поза
# вікном відгуку Prom (~30 днів від дати замовлення) → send_order_context повертає
# «Order not found», і без цієї межі автоматика билась би об них щопрогону.
MAX_DAYS_SINCE_DELIVERED = 7

REVIEW_URL_TEMPLATE = "https://prom.ua/ua/product-opinions/create/{pid}"
# Тепліший, людяніший текст (наказ власника 2026-08-25) — голос бренду PlutusToys.
# ⚠️ Формулювання — зона SMM (збір відгуків, брендовий голос); це розумний дефолт, SMM уточнює.
# Прямий лінк на форму лишається в тексті (одноклік = менше тертя = більше лишають).
MESSAGE_ONE = ("Дякуємо, що обрали PlutusToys! 🦊 Дуже сподіваємось, малечі сподобалось. "
               "Якщо є хвилинка — поділіться коротким відгуком, нам це дуже допомагає: {url}")
MESSAGE_MANY = ("Дякуємо, що обрали PlutusToys! 🦊 Якщо все сподобалось — будемо вдячні "
                "за короткий відгук про товари:\n{urls}")

# Фолбек на відгук про КОМПАНІЮ (дозвіл власника 2026-08-07: коли товар делістнутий і
# сторінки відгуку про товар не існує). URL звірено живо — кнопка «Скопіювати запит на
# відгук про компанію» дає короткий лінк, що редіректить на цей канонічний вигляд:
# https://prom.ua/ua/opinions/create/{company_id}?order_id={order_id}. company_id 4219597
# — наш (ID кабінету PlutusToys, статичний).
COMPANY_ID = "4219597"
COMPANY_REVIEW_URL = "https://prom.ua/ua/opinions/create/{company_id}?order_id={order_id}"
MESSAGE_COMPANY = ("Дякуємо за замовлення в PlutusToys! 🦊 Якщо вам було зручно з нами — "
                   "залиште, будь ласка, короткий відгук про магазин, це допомагає іншим батькам обрати: {url}")


def _headers() -> dict:
    return {"Authorization": f"Bearer {PROM_API_KEY}"}


# ---- Чисті, тестовані функції (без мережі) --------------------------------

def build_review_body(order: dict) -> str | None:
    """ТОВАРНИЙ відгук (наступного дня) — посилання по кожному товару з Prom-`id`.
    None, якщо ЖОДНОГО товару з id (усі делістнуті) — тоді товарний етап пропускаємо:
    компанійський запит уже пішов при отриманні (окремий етап), дублювати нема сенсу."""
    urls = [REVIEW_URL_TEMPLATE.format(pid=p["id"]) for p in (order.get("products") or []) if p.get("id")]
    if not urls:
        return None
    if len(urls) == 1:
        return MESSAGE_ONE.format(url=urls[0])
    # Складаємо стільки ПОВНИХ URL, скільки влазить у MAX_BODY_LEN — ніколи не
    # ріжемо посеред лінка (реально замовлення 1-3 товари, тож майже недосяжно).
    body = MESSAGE_MANY.format(urls="\n".join(urls))
    while len(body) > MAX_BODY_LEN and len(urls) > 1:
        urls.pop()
        body = MESSAGE_MANY.format(urls="\n".join(urls))
    return body


def build_company_body(order: dict) -> str | None:
    """КОМПАНІЙСЬКИЙ відгук — шлемо У МОМЕНТ отримання посилки (рішення власника 2026-08-21).
    Компанійський відгук лягає на весь магазин + тай-брейк ранжування (вартий кратно більше за
    товарний за нашого обсягу — оцінка SEO). None лише якщо нема order_id (нема куди слати)."""
    order_id = order.get("id")
    if not order_id:
        return None
    url = COMPANY_REVIEW_URL.format(company_id=COMPANY_ID, order_id=order_id)
    return MESSAGE_COMPANY.format(url=url)


def select_eligible(conn, min_hours: int = MIN_HOURS_SINCE_DELIVERED,
                    max_days: int = MAX_DAYS_SINCE_DELIVERED, now: datetime = None) -> list:
    """Prom-замовлення, ОТРИМАНІ покупцем у вікні [max_days тому … min_hours тому],
    яким товарний запит на відгук ще не слався.

    Сигнал «отримано» — `prom_delivered_pushed_at IS NOT NULL` (а НЕ рядок
    `delivery_status`): ця мітка ставиться саме коли Нова Пошта підтвердила
    ФАКТИЧНУ видачу посилки (order_status_tracker._maybe_push_delivered_to_prom,
    `tracking["delivered"]`). Живо звірено 2026-08-07: реальні orders.db не мають
    значення `delivery_status='delivered'` (доходить лише до 'shipped'=НП-код 60),
    а видачу відображає саме `prom_delivered_pushed_at` (=НП «вручено»). Час мітки
    ≈ момент видачі.

    Нижня межа `min_hours` (24г) — «наступного дня після отримання». Верхня
    `max_days` (7 днів) — не чіпати старі замовлення (свіжіший запит + поза
    ~30-денним вікном відгуку Prom send_order_context дає «Order not found»)."""
    now = now or datetime.now()
    upper = (now - timedelta(hours=min_hours)).isoformat(timespec="seconds")   # не свіжіше 24г
    lower = (now - timedelta(days=max_days)).isoformat(timespec="seconds")     # не старіше 7 днів
    rows = conn.execute(
        "SELECT * FROM orders "
        "WHERE platform = 'prom' "
        "AND prom_delivered_pushed_at IS NOT NULL "
        "AND prom_delivered_pushed_at <= ? AND prom_delivered_pushed_at >= ? "
        "AND prom_review_request_sent_at IS NULL "
        "AND (delivery_status IS NULL OR delivery_status NOT IN ('returned', 'cancelled'))",
        (upper, lower),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def select_company_eligible(conn, max_days: int = MAX_DAYS_SINCE_DELIVERED, now: datetime = None) -> list:
    """Prom-замовлення, ОТРИМАНІ покупцем, яким КОМПАНІЙСЬКИЙ запит ще не слався — на відміну
    від товарного, шлемо ОДРАЗУ при отриманні (БЕЗ 24-год нижньої межі). Верхня межа max_days —
    щоб на першому прогоні не завалити старий бек-лог і не вийти за ~30-денне вікно відгуку Prom."""
    now = now or datetime.now()
    lower = (now - timedelta(days=max_days)).isoformat(timespec="seconds")
    rows = conn.execute(
        "SELECT * FROM orders "
        "WHERE platform = 'prom' "
        "AND prom_delivered_pushed_at IS NOT NULL "
        "AND prom_delivered_pushed_at >= ? "
        "AND prom_company_review_sent_at IS NULL "
        "AND (delivery_status IS NULL OR delivery_status NOT IN ('returned', 'cancelled'))",
        (lower,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---- Мережеві виклики Prom API --------------------------------------------

def fetch_prom_order(order_id: str) -> dict:
    r = requests.get(f"{PROM_API_URL}/orders/{order_id}", headers=_headers(), timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("order", {}) or {}


def send_order_context(order_id: str, body: str) -> int | None:
    """POST /chat/send_order_context — шле повідомлення + картку замовлення покупцю,
    сам створює кімнату чату. Адресація по order_id (не по кімнаті). Кидає RuntimeError,
    якщо Prom повернув status != ok.

    ФОРМАТ — JSON (звірено ЖИВО 2026-08-08): офіційна спека каже multipart/form-data,
    але реально multipart дає 400 Bad Request, а JSON `{order_id:int, body}` доходить до
    логіки API (200). order_id має бути числом."""
    r = requests.post(
        f"{PROM_API_URL}/chat/send_order_context",
        headers=_headers(),
        json={"order_id": int(order_id), "body": body[:MAX_BODY_LEN]},
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


def mark_company_sent(conn, internal_order_id: str) -> None:
    conn.execute(
        "UPDATE orders SET prom_company_review_sent_at = ? WHERE internal_order_id = ?",
        (datetime.now().isoformat(timespec="seconds"), internal_order_id),
    )


# ---- Оркестрація -----------------------------------------------------------

def _process_stage(conn, orders: list, body_fn, mark_fn, label: str, dry_run: bool, stats: dict) -> None:
    """Один етап (компанія/товар): для кожного замовлення — тіло → відправка → мітка. Позначку
    робимо ДУРАБЕЛЬНОЮ одразу (commit), щоб краш на наступному замовленні не відкотив уже
    надіслане (→ дублі). body_fn/mark_fn відрізняють компанійський і товарний етапи."""
    for row in orders:
        oid = row["order_id"]
        try:
            # ValueError ловить і JSONDecodeError (битий JSON на HTTP-200).
            order = fetch_prom_order(oid)
        except (requests.RequestException, ValueError) as e:
            print(f"  ⚠️ №{oid} [{label}]: не вдалось отримати замовлення ({e}) — пропуск.")
            stats["errors"] += 1
            continue
        body = body_fn(order)
        buyer = f"{order.get('client_first_name', '')} {order.get('client_last_name', '')}".strip()
        if not body:
            print(f"  ⏭️ №{oid} [{label}] ({buyer}): нема тіла запиту — пропуск.")
            stats["skipped_no_products"] += 1
            continue
        if dry_run:
            preview = body.replace("\n", "\n         ")
            print(f"  ✉️ №{oid} [{label}] ({buyer}):\n         «{preview}»")
            continue
        try:
            send_order_context(oid, body)
            mark_fn(conn, row["internal_order_id"])
            conn.commit()
            stats["sent"] += 1
            print(f"  ✅ №{oid} [{label}] ({buyer}): надіслано, позначено.")
        except (requests.RequestException, RuntimeError, ValueError) as e:
            print(f"  ⚠️ №{oid} [{label}] ({buyer}): помилка відправки ({e}) — позначку НЕ ставлю.")
            stats["errors"] += 1


def run(dry_run: bool = True, min_hours: int = MIN_HOURS_SINCE_DELIVERED) -> dict:
    if not PROM_API_KEY:
        print("[ReviewReq] PROM_API_KEY не задано в .env — не можу працювати.", file=sys.stderr)
        return {"eligible": 0, "sent": 0, "skipped_no_products": 0, "errors": 0}

    init_db()
    stats = {"eligible": 0, "sent": 0, "skipped_no_products": 0, "errors": 0}
    with get_connection() as conn:
        # Два етапи (рішення власника 2026-08-21): КОМПАНІЙСЬКИЙ — у момент отримання (0 год);
        # ТОВАРНИЙ — наступного дня (≥24 год). Окремі мітки, тож не заважають один одному.
        company = select_company_eligible(conn)
        product = select_eligible(conn, min_hours=min_hours)
        stats["eligible"] = len(company) + len(product)
        if not company and not product:
            print("[ReviewReq] Придатних замовлень нема (усе оброблено / нічого не отримано).")
            return stats
        mode = "DRY-RUN (нічого не шлю)" if dry_run else "SEND (реальна відправка)"
        print(f"[ReviewReq] {mode}. Компанійських (при отриманні): {len(company)}, "
              f"товарних (наступного дня): {len(product)}.\n")
        _process_stage(conn, company, build_company_body, mark_company_sent, "компанія", dry_run, stats)
        _process_stage(conn, product, build_review_body, mark_sent, "товар", dry_run, stats)

    print(f"\n[ReviewReq] Підсумок: придатних {stats['eligible']}, надіслано {stats['sent']}, "
          f"пропущено {stats['skipped_no_products']}, помилок {stats['errors']}.")
    _notify_summary(dry_run, stats)
    return stats


def _notify_summary(dry_run: bool, stats: dict) -> None:
    """Telegram-сповіщення про результат РЕАЛЬНОГО прогону (щоб не тримати в голові
    й не лізти в journal). Тихі дні (0 надіслано, 0 помилок) НЕ турбують — шлемо лише
    коли щось реально сталось. dry-run не сповіщає ніколи. Помилка Telegram — не критична."""
    if dry_run or not (stats["sent"] or stats["errors"]):
        return
    msg = f"🌟 Prom-відгуки: надіслано {stats['sent']}"
    if stats["errors"]:
        msg += (f", ⚠️ помилок {stats['errors']} — глянь "
                f"`journalctl -u prom-review-requester.service --since today`")
    try:
        send_telegram_message(msg)
    except Exception as e:
        print(f"[ReviewReq] Telegram не надіслано (не критично): {e}", file=sys.stderr)


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
