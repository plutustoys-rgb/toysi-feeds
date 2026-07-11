import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from competitor_pricing import get_platform_commission
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

# Другий канал, крім Telegram (стандарт репо) — персистентний .md-файл на VPS.
BASE_DIR   = Path(__file__).parent
REPORT_DIR = BASE_DIR / "reports"


def write_local_report(message: str) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().date().isoformat()
    out_path = REPORT_DIR / f"daily_report_{today}.md"
    out_path.write_text(message, encoding="utf-8")

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
    тут поки й не буває.

    item["toysi_code"] походить із product["sku"]/["external_id"] у відповіді
    Prom Orders API (orders_watcher.py) — тобто це те, що Prom повертає як
    ідентифікатор товару в замовленні, а не напряму offer/@id з нашого фіда.
    Перевірено емпірично на живому фіді Toysi (28 987 товарів, 2026-07-07):
    <vendorCode> завжди дорівнює offer/@id (0 розбіжностей) — і саме
    vendorCode ми публікуємо як <vendorCode> в prom_feed.xml (generate_prom_
    feed.py). Тобто внутрішнє зіставлення справне; єдине, що неможливо
    перевірити без живого замовлення — чи Prom дійсно повертає це саме
    значення як sku/external_id. Перше реальне передане замовлення варто
    звірити вручну."""
    cat_item = catalog.get(str(item.get("toysi_code") or ""))
    if not cat_item:
        return None
    try:
        cost = float(cat_item.get("price") or 0)
    except (TypeError, ValueError):
        return None
    return cost if cost > 0 else None


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


def _estimated_commission(rows: list, catalog: dict) -> tuple:
    """Графа 9: ОЦІНКА комісії маркетплейсу за продаж. Prom Orders API не
    віддає фактичну суму комісії по замовленню (лише сама платформа знає
    остаточну ставку в момент виплати) — тому рахуємо per-позицію: виручка
    позиції × ставка категорії з competitor_pricing.py
    (PROM_CATEGORY_COMMISSION/PROM_COMMISSION_DEFAULT для Prom,
    ROZETKA_COMMISSION_DEFAULT для Rozetka) — та сама таблиця ставок, що й
    для ціноутворення (get_platform_commission). Категорія товару береться
    через toysi_code -> каталог Toysi (як і собівартість у
    _toysi_wholesale_cost); якщо товару немає в поточному каталозі,
    застосовується дефолтна ставка платформи без категорійного уточнення.
    Це РОЗРАХУНКОВА оцінка для щомісячної звірки з випискою маркетплейсу,
    не остаточна цифра."""
    commission_by_platform = {"prom": 0.0, "rozetka": 0.0}
    items_priced = 0
    items_no_category = 0

    for row in rows:
        platform = row["platform"]
        if platform not in commission_by_platform:
            continue
        for item in json.loads(row["items"]):
            item_revenue = item.get("price", 0) * item.get("qty", 1)
            cat_item = catalog.get(str(item.get("toysi_code") or ""))
            category_name = cat_item.get("category_name") if cat_item else None
            if not category_name:
                items_no_category += 1
            rate = get_platform_commission(platform, category_name)
            commission_by_platform[platform] += item_revenue * rate
            items_priced += 1

    return commission_by_platform, items_priced, items_no_category


# Ключові слова з РЕАЛЬНИХ назв способів оплати цього кабінету
# (my.prom.ua/cms/settings/payment, перевірено 2026-07-11): "Пром-оплата" і
# "Оплатити частинами 0% для покупця" — рівно ці 2 з 4 винятків комісії 10₴
# (pt24), які взагалі можна визначити з payment_option_name. Два інші винятки
# (дублікат замовлення протягом 24 год, тестове замовлення власника/менеджера)
# Prom Orders API ніяк не позначає — тому вони НЕ враховані тут, оцінка нижче
# схильна злегка ЗАВИЩувати комісію 10₴ (рідкісні випадки), не занижувати.
_SITE_FEE_EXEMPT_KEYWORDS = ("пром-оплата", "оплатити частинами")
SITE_ORDER_FEE = 10.0


def _site_order_fee_estimate(rows: list) -> tuple:
    """Графа 9, компонент "комісія 10₴ за замовлення з кошика власного сайту"
    (pt24) — ОКРЕМИЙ механізм від категорійної комісії ProSale/еквайрингу, що
    рахує _estimated_commission() вище, і додається ПОВЕРХ неї, не замість.
    Застосовується лише до platform="prom" AND source="company_site"
    (Rozetka не має цього механізму; каталожні замовлення Prom, source="portal",
    комісії 10₴ не підлягають). source=None (старі замовлення до 2026-07-11,
    коли поле ще не збиралось) свідомо НЕ рахується як сайт — не можемо
    довести те, чого не знаємо."""
    eligible_count = 0
    for row in rows:
        if row["platform"] != "prom" or row["source"] != "company_site":
            continue
        payment_name = (row["payment_option_name"] or "").lower()
        if any(kw in payment_name for kw in _SITE_FEE_EXEMPT_KEYWORDS):
            continue
        eligible_count += 1
    return eligible_count, eligible_count * SITE_ORDER_FEE


def build_report() -> str:
    init_db()
    since = (datetime.now() - timedelta(hours=LOOKBACK_HOURS)).isoformat(timespec="seconds")

    with get_connection() as conn:
        new_rows = conn.execute(
            "SELECT platform, items, source, payment_option_name FROM orders WHERE created_at >= ?", (since,)
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
        commission_by_platform = {"prom": 0.0, "rozetka": 0.0}
        commission_items_priced = commission_items_no_category = 0
        commission_catalog_unavailable = False

        # Один запит каталогу на обидві графи (6 і 9), якщо хоч одній він потрібен —
        # немає сенсу тягнути фід Toysi двічі за один прогін звіту.
        toysi_catalog = fetch_toysi_catalog() if (forwarded_today or new_rows) else None

        if forwarded_today:
            if toysi_catalog:
                cogs, items_priced, items_missing = _cogs_for_forwarded_orders(conn, since, toysi_catalog)
            else:
                # fetch_toysi_catalog() повертає {} і при відсутньому ключі, і при
                # timeout/HTTP/XML-помилці — 0.00 грн тут виглядав би як "витрат
                # немає", хоча насправді просто не вдалось порахувати.
                cogs_catalog_unavailable = True

        if new_rows:
            if toysi_catalog:
                commission_by_platform, commission_items_priced, commission_items_no_category = \
                    _estimated_commission(new_rows, toysi_catalog)
            else:
                commission_catalog_unavailable = True

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

    if commission_catalog_unavailable:
        lines.append(
            "\n\nГрафа 9 (комісія Prom/Rozetka за продаж): ⚠️ НЕ ПОРАХОВАНО — "
            "каталог Toysi не завантажився (ключ/timeout/помилка XML), хоча за "
            f"{LOOKBACK_HOURS} год є {len(new_rows)} нових замовлень. "
            "Внести вручну з виписки/акту маркетплейсу."
        )
    elif new_rows:
        commission_prom = commission_by_platform.get("prom", 0.0)
        commission_rozetka = commission_by_platform.get("rozetka", 0.0)
        lines.append(
            f"\n\nГрафа 9 (комісія Prom/Rozetka за продаж, РОЗРАХУНКОВО — звірити з випискою): "
            f"Prom {commission_prom:.2f} грн, Rozetka {commission_rozetka:.2f} грн "
            f"(разом {commission_prom + commission_rozetka:.2f} грн)"
        )
        lines.append(
            f"\n  ({commission_items_priced} позицій оцінено за ставкою категорії з "
            "competitor_pricing.py — PROM_CATEGORY_COMMISSION/ROZETKA_COMMISSION_DEFAULT; "
            "Prom API не віддає фактичну комісію по замовленню, це ОЦІНКА для щомісячної "
            "звірки з випискою/актом маркетплейсу, не остаточна цифра)"
        )
        if commission_items_no_category:
            lines.append(
                f"\n  ⚠️ {commission_items_no_category} позицій без категорії в поточному каталозі "
                "Toysi — використано дефолтну ставку платформи замість категорійної"
            )

        site_fee_count, site_fee_total = _site_order_fee_estimate(new_rows)
        if site_fee_count:
            lines.append(
                f"\n\nГрафа 9, окремо (комісія 10₴ за замовлення з кошика власного сайту, pt24): "
                f"{site_fee_count} замовлень x 10₴ = {site_fee_total:.2f} грн — ДОДАЄТЬСЯ поверх "
                "категорійної комісії вище, не замість неї"
            )
            lines.append(
                "\n  (винятки Пром-оплата/Оплатити частинами враховано за назвою способу оплати; "
                "винятки \"дублікат замовлення\"/\"тестове замовлення власника\" Prom API не "
                "позначає — тут НЕ враховані, тому оцінка може бути трохи ЗАВИЩеною, не заниженою)"
            )
    else:
        lines.append(
            f"\n\nГрафа 9 (комісія Prom/Rozetka за продаж): нових замовлень за {LOOKBACK_HOURS} год немає."
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
    write_local_report(message)
    sent = send_telegram_message(message)
    if not sent:
        print("[daily_report] Не вдалося надіслати в Telegram (див. повідомлення вище)", file=sys.stderr)


if __name__ == "__main__":
    send_daily_report()
