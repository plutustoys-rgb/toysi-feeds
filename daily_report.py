import json
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv

from orders_db import get_connection, get_orders_awaiting_payment, init_db
from parser import fetch_toysi_catalog
from telegram_notify import send_telegram_message

# Консоль Windows (cp1251) не показує emoji — не заважає systemd/journald на VPS,
# але без цього локальний тестовий запуск падає на print().
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

PROM_API_KEY    = os.environ.get("PROM_API_KEY", "")
ROZETKA_API_KEY = os.environ.get("ROZETKA_API_KEY", "")

LOOKBACK_HOURS = 24

"""
Крок 8 плану. Не вистачає лише балансів Rozetka/Prom (Крок 7 — окрема
інтеграція з балансовими API маркетплейсів, ще не написана, не пов'язана
з order_router.py/order_status_tracker.py) — усе інше з оригінального
переліку Кроку 8 тепер доступне: нові замовлення по платформах, виручка,
скільки передано Toysi, скільки чекає оплати, повернення/відмови,
замовлення з помилками Toysi.
"""


def _source_label(platform: str) -> str:
    """Реальні дані для платформи є, лише якщо задано відповідний API-ключ —
    інакше orders_watcher.py досі повертає мок-замовлення (Крок 3 плану)."""
    key = PROM_API_KEY if platform == "prom" else ROZETKA_API_KEY
    return "реальні дані" if key else "мок-дані (ключа ще немає)"


def _order_total(order_items: list) -> float:
    return sum(item.get("price", 0) * item.get("qty", 1) for item in order_items)


def _toysi_wholesale_cost(item: dict, catalog: dict) -> Optional[float]:
    """Оптова ціна Toysi (собівартість) для позиції замовлення — на відміну
    від item["price"], яка в orders_db це РОЗДРІБНА ціна клієнту. Кожна
    позиція замовлення сьогодні завжди від Toysi (item["toysi_code"]) —
    маршрутизація RoyalToys ще не існує (Фаза 2), тому іншого постачальника
    тут поки й не буває."""
    cat_item = catalog.get(str(item.get("toysi_code") or ""))
    if not cat_item:
        return None
    try:
        return float(cat_item.get("price") or 0)
    except (TypeError, ValueError):
        return None


def _cogs_for_forwarded_orders(conn, since: str, catalog: dict) -> tuple:
    """Графа 6 КОДВ: собівартість реалізованих і оплачених постачальнику
    товарів. `orders_db` не має окремого поля "оплата постачальнику
    підтверджена" (Toysi API не дає такого статусу) — тому як проксі
    використовуємо forwarded_to_toysi_at, як і решта звіту. За дропшип-
    моделлю (лист "Інструкція" КОДВ) розрив між передачею замовлення й
    оплатою постачальнику мінімальний, тож це ОЦІНКА для звірки з
    накладною Toysi, а не остаточна цифра графи 6."""
    rows = conn.execute(
        "SELECT items FROM orders WHERE forwarded_to_toysi_at >= ?", (since,)
    ).fetchall()

    total_cost = 0.0
    items_priced = 0
    items_missing = 0
    for row in rows:
        for item in json.loads(row["items"]):
            cost = _toysi_wholesale_cost(item, catalog)
            if cost is None:
                items_missing += 1
                continue
            total_cost += cost * item.get("qty", 1)
            items_priced += 1

    return total_cost, items_priced, items_missing


def build_report() -> str:
    init_db()
    since = (datetime.now() - timedelta(hours=LOOKBACK_HOURS)).isoformat(timespec="seconds")

    with get_connection() as conn:
        new_rows = conn.execute(
            "SELECT platform, items FROM orders WHERE created_at >= ?", (since,)
        ).fetchall()

        forwarded_today = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE forwarded_to_toysi_at >= ?", (since,)
        ).fetchone()[0]

        awaiting_bank_check = len(get_orders_awaiting_payment(conn))

        awaiting_manual = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status = 'awaiting_manual_confirmation'"
        ).fetchone()[0]

        toysi_errors = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status = 'toysi_error'"
        ).fetchone()[0]

        returns_cancellations = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE delivery_status IN ('returned', 'cancelled')"
        ).fetchone()[0]

        cogs = items_priced = items_missing = 0
        cogs_catalog_unavailable = False
        if forwarded_today:
            toysi_catalog = fetch_toysi_catalog()
            if toysi_catalog:
                cogs, items_priced, items_missing = _cogs_for_forwarded_orders(conn, since, toysi_catalog)
            else:
                # fetch_toysi_catalog() повертає {} і при відсутньому ключі, і при
                # timeout/HTTP/XML-помилці — 0.00 грн тут виглядав би як "витрат
                # немає", хоча насправді просто не вдалось порахувати.
                cogs_catalog_unavailable = True

    counts_by_platform = {}
    revenue_by_platform = {}
    for row in new_rows:
        items = json.loads(row["items"])
        counts_by_platform[row["platform"]] = counts_by_platform.get(row["platform"], 0) + 1
        revenue_by_platform[row["platform"]] = revenue_by_platform.get(row["platform"], 0) + _order_total(items)

    total_revenue = sum(revenue_by_platform.values())

    lines = [f"📋 Щоденний звіт PlutusToys — {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"]
    lines.append(f"\nНових замовлень за останні {LOOKBACK_HOURS} год: {len(new_rows)}")

    for platform in ("prom", "rozetka"):
        count = counts_by_platform.get(platform, 0)
        revenue = revenue_by_platform.get(platform, 0)
        lines.append(f"\n  {platform.capitalize()}: {count} на {revenue:.2f} грн ({_source_label(platform)})")

    lines.append(f"\n\nВиручка нових замовлень за {LOOKBACK_HOURS} год: {total_revenue:.2f} грн")
    lines.append(
        "\n  (сума замовлень за собівартістю Toysi + націнка, як у полі \"price\" замовлення — "
        "не фактично отримані гроші, лише вартість оформлених замовлень)"
    )

    lines.append(f"\n\nПередано Toysi за {LOOKBACK_HOURS} год: {forwarded_today}")
    lines.append(f"\nОчікують перевірки оплати (prepaid, ще не підтверджено): {awaiting_bank_check}")
    lines.append(f"\nПозначено \"очікує ручного підтвердження\": {awaiting_manual}")

    if toysi_errors:
        lines.append(f"\n\n🔴 Замовлення з помилками Toysi (потребують уваги): {toysi_errors}")
    if returns_cancellations:
        lines.append(f"\n⚠️ Повернень/скасувань (поточний стан, не лише за добу): {returns_cancellations}")

    lines.append(f"\n\n📒 Дані для КОДВ за {LOOKBACK_HOURS} год (для граф 6/8/9):")

    if cogs_catalog_unavailable:
        lines.append(
            "\n\nГрафа 6 (собівартість реалізованих і оплачених товарів): "
            "⚠️ НЕ ПОРАХОВАНО — каталог Toysi не завантажився (ключ/timeout/помилка "
            f"XML), хоча за {LOOKBACK_HOURS} год передано {forwarded_today} замовлень. "
            "Це НЕ означає нульові витрати — порахуй вручну за накладною."
        )
    else:
        lines.append(f"\n\nГрафа 6 (собівартість реалізованих і оплачених товарів): {cogs:.2f} грн")
        if forwarded_today:
            lines.append(
                f"\n  ({items_priced} позицій оцінено за поточним прайсом Toysi з "
                f"{forwarded_today} переданих замовлень — \"передано постачальнику\" тут "
                "проксі для \"оплачено постачальнику\" (дропшип, розрив мінімальний), "
                "звір із фактичною накладною Toysi/RoyalToys)"
            )
            if items_missing:
                lines.append(
                    f"\n  ⚠️ {items_missing} позицій не знайдено в поточному каталозі Toysi — "
                    "не враховано в сумі, перевір вручну"
                )
        else:
            lines.append("\n  (сьогодні не було переданих Toysi замовлень)")

    lines.append(
        "\n\nГрафа 9 (комісія Prom/Rozetka за продаж): дані з API недоступні — "
        "балансове/статистичне API маркетплейсів ще не підключено (Крок 7 плану). "
        "Внести вручну з виписки/акту маркетплейсу."
    )
    lines.append(
        "\n\nГрафа 9 (інші сервісні платежі — Checkbox, хостинг VPS, Anthropic API тощо): "
        "автоматичного джерела даних немає, вносити вручну за вхідними документами."
    )
    lines.append(
        "\n\nГрафа 8 (ЄСВ, податки) — навмисно НЕ рахую, вноситься вручну з платіжок."
    )

    return "".join(lines)


def send_daily_report() -> None:
    message = build_report()
    print(message)
    sent = send_telegram_message(message)
    if not sent:
        print("[daily_report] Не вдалося надіслати в Telegram (див. повідомлення вище)", file=sys.stderr)


if __name__ == "__main__":
    send_daily_report()
