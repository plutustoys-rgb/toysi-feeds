import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

import rozetka_client
from competitor_pricing import get_platform_commission
from orders_db import get_connection, get_orders_awaiting_payment, init_db
from parser import fetch_toysi_catalog
from telegram_notify import send_telegram_message

# Консоль Windows (cp1251) не показує emoji — не заважає systemd/journald на VPS,
# але без цього локальний тестовий запуск падає на print().
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

PROM_API_KEY     = os.environ.get("PROM_API_KEY", "")
ROZETKA_USERNAME = os.environ.get("ROZETKA_USERNAME", "")
ROZETKA_PASSWORD = os.environ.get("ROZETKA_PASSWORD", "")

LOOKBACK_HOURS = 24

# 2026-07-15: дефолт, ще НЕ узгоджений з власницею — власне рішення, скільки
# і коли поповнювати, лишається за нею (агент нічого не оплачує й не
# нагадує текстом "заплати", лише позначає "низький" у звіті). Скоригуй за
# власним відчуттям типового обороту на цьому балансі.
ROZETKA_LOW_BALANCE_THRESHOLD = 500.0

# P0-2 (2026-07-17): Prom "показник успішних замовлень" (support.prom.ua/hc/
# uk/articles/4405624956573) — офіційно: 60-денне вікно, що ЗАКІНЧУЄТЬСЯ за
# 10 днів до сьогодні (не сьогодні), рахується лише за 8+ замовлень у цьому
# вікні; нижче 60% -> каталог ховають від покупців. За словами власниці — 2
# такі порушення за 6 місяців ведуть до ПОСТІЙНОЇ деактивації.
#
# Офіційна методика Prom ВИКЛЮЧАЄ зі знаменника: спам-замовлення/накрутки
# конкурентів, скасування за скаргою продавця, скасування САМИМ покупцем,
# і незабрані на відділенні. orders_db.py не розрізняє ХТО/ЧОМУ скасував
# (лише delivery_status) — тому це РАХУЄ КОЖНЕ cancelled/returned як
# "неуспішне", що структурно ЗАНИЖУЄ показник відносно офіційного числа
# Prom (тобто ця перевірка попереджає РАНІШЕ/СУВОРІШЕ, ніж реальний ризик
# від Prom, ніколи пізніше/м'якше) — свідомий компроміс, бо точної розбивки
# по ініціатору скасування в даних просто немає.
PROM_SUCCESS_WINDOW_DAYS = 60
PROM_SUCCESS_WINDOW_LAG_DAYS = 10
PROM_SUCCESS_MIN_ORDERS = 8
PROM_SUCCESS_OFFICIAL_THRESHOLD = 0.60   # нижче цього Prom ховає каталог
PROM_SUCCESS_WARN_THRESHOLD = 0.75       # наш власний "жовтий" поріг, раніше офіційного
PROM_SUCCESS_VIOLATION_WINDOW_DAYS = 182  # ~6 місяців
PROM_SUCCESS_VIOLATIONS_BEFORE_PERMANENT = 2

PROM_SUCCESS_STATE_FILE = Path(__file__).parent / "prom_success_rate_state.json"

"""
Крок 8 плану. Баланс Rozetka тепер підключено (Крок 7, 2026-07-15,
rozetka_client.get_balance()). Баланс Prom — НЕ вдалось: офіційний
публічний API Prom (public-api.docs.prom.ua) взагалі не має ендпоінту
балансу/фінансів серед своїх дев'яти розділів (Orders/Messages/Clients/
Products/Groups/Payment/Delivery/OrderStatus/Chat) — не питання
відсутнього ключа чи прав доступу, а структурна відсутність такого
методу в самому API. Показуємо це чесно в звіті нижче, а не мовчки
нулем. Усе інше з оригінального переліку Кроку 8 тепер доступне: нові
замовлення по платформах, виручка, скільки передано Toysi, скільки чекає
оплати, повернення/відмови, замовлення з помилками Toysi.
"""


def _source_label(platform: str) -> str:
    """Реальні дані для платформи є, лише якщо задано відповідні облікові
    дані — інакше orders_watcher.py досі повертає мок-замовлення (Крок 3
    плану)."""
    has_creds = bool(PROM_API_KEY) if platform == "prom" else bool(ROZETKA_USERNAME and ROZETKA_PASSWORD)
    return "реальні дані" if has_creds else "мок-дані (облікових даних ще немає)"


def _rozetka_balance_line() -> str:
    """Графа балансу Rozetka для щоденного звіту (Крок 7 плану) —
    rozetka_client.get_balance() (GET /v1/balances/current)."""
    if not (ROZETKA_USERNAME and ROZETKA_PASSWORD):
        return "\n\nБаланс Rozetka: ⚠️ ROZETKA_USERNAME/ROZETKA_PASSWORD не задані в .env — недоступно"

    try:
        balance = rozetka_client.get_balance()
    except rozetka_client.RozetkaAPIError as e:
        return f"\n\nБаланс Rozetka: ⚠️ не вдалось отримати ({e})"

    try:
        current = float(balance.get("current_balance", 0))
    except (TypeError, ValueError):
        return f"\n\nБаланс Rozetka: ⚠️ неочікуваний формат відповіді ({balance})"

    if current < ROZETKA_LOW_BALANCE_THRESHOLD:
        return (
            f"\n\n🔴 БАЛАНС ROZETKA НИЗЬКИЙ: {current:.2f} грн "
            f"(поріг {ROZETKA_LOW_BALANCE_THRESHOLD:.0f} грн) — розглянь поповнення"
        )
    return f"\n\nБаланс Rozetka: {current:.2f} грн"


def _prom_balance_line() -> str:
    """Prom НЕ має балансового ендпоінту в публічному API — структурна
    відсутність методу, не проблема ключа/прав. Позначено чесно, не нулем."""
    return (
        "\n\nБаланс Prom: ⚠️ недоступно через API — публічний API Prom "
        "(public-api.docs.prom.ua) не має жодного балансового/фінансового "
        "ендпоінту серед своїх розділів; перевіряти в кабінеті вручну."
    )


def _rozetka_validation_status_line() -> str:
    """
    Задача власниці 2026-07-15, п.4: "API-еквівалент інструмента 'Перевірка
    XML'" — GET /goods/errors + GET /goods/not-valid (rozetka_client.py) є
    саме цим: живий, поточний стан валідації каталогу на боці Rozetka,
    підтягується щоразу, коли будується цей звіт, без ручної перевірки
    кабінету. Кожен елемент goods/errors має blocked_reason.title (людський
    текст причини) — це й буде живим джерелом для стоп-списків категорій/
    брендів (Крок 3 задачі), коли з'являться облікові дані.
    """
    if not (ROZETKA_USERNAME and ROZETKA_PASSWORD):
        return "\n\nСтатус валідації Rozetka (goods/errors): ⚠️ облікових даних немає — недоступно"

    try:
        errors = rozetka_client.fetch_goods_errors()
        not_valid = rozetka_client.fetch_goods_not_valid()
    except rozetka_client.RozetkaAPIError as e:
        return f"\n\nСтатус валідації Rozetka (goods/errors): ⚠️ не вдалось отримати ({e})"

    if not errors and not not_valid:
        return "\n\nСтатус валідації Rozetka (goods/errors): ✅ 0 товарів з помилками, 0 невалідних"

    line = (
        f"\n\n⚠️ Статус валідації Rozetka (goods/errors): {len(errors)} товарів з помилками, "
        f"{len(not_valid)} невалідних — перевір деталі в кабінеті ('Товари з помилками'/'Невалідні товари')"
    )
    return line


def _load_prom_success_state() -> dict:
    if not PROM_SUCCESS_STATE_FILE.exists():
        return {"violations": []}
    try:
        return json.loads(PROM_SUCCESS_STATE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"violations": []}


def _save_prom_success_state(state: dict) -> None:
    PROM_SUCCESS_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def _prom_success_rate_section(conn) -> str:
    """Графа для щоденного звіту + P0-2 alerting: див. коментар біля
    PROM_SUCCESS_* констант вище щодо методики й свідомих спрощень."""
    window_end = datetime.now() - timedelta(days=PROM_SUCCESS_WINDOW_LAG_DAYS)
    window_start = window_end - timedelta(days=PROM_SUCCESS_WINDOW_DAYS)

    rows = conn.execute(
        "SELECT delivery_status FROM orders WHERE platform = 'prom' AND created_at >= ? AND created_at < ?",
        (window_start.isoformat(timespec="seconds"), window_end.isoformat(timespec="seconds")),
    ).fetchall()

    total = len(rows)
    if total < PROM_SUCCESS_MIN_ORDERS:
        return (
            f"\n\nПоказник успішних замовлень Prom: недостатньо даних для розрахунку "
            f"({total} замовлень за 60-денне вікно, Prom рахує від {PROM_SUCCESS_MIN_ORDERS})"
        )

    unsuccessful = sum(1 for r in rows if r["delivery_status"] in ("cancelled", "returned"))
    success_rate = (total - unsuccessful) / total

    state = _load_prom_success_state()
    violations = [v for v in state.get("violations", [])
                  if (datetime.now() - datetime.fromisoformat(v)).days <= PROM_SUCCESS_VIOLATION_WINDOW_DAYS]

    alert_lines = []
    if success_rate < PROM_SUCCESS_OFFICIAL_THRESHOLD:
        today_iso = datetime.now().date().isoformat()
        if today_iso not in violations:
            violations.append(today_iso)
        violation_count = len(violations)
        alert_lines.append(
            f"\n\n🔴 ПОКАЗНИК УСПІШНИХ ЗАМОВЛЕНЬ PROM НИЖЧЕ ОФІЦІЙНОГО ПОРОГУ "
            f"({success_rate * 100:.0f}% < {PROM_SUCCESS_OFFICIAL_THRESHOLD * 100:.0f}%) — "
            f"каталог може бути прихований від покупців. Порушення за останні "
            f"{PROM_SUCCESS_VIOLATION_WINDOW_DAYS // 30} міс: {violation_count}."
        )
        if violation_count >= PROM_SUCCESS_VIOLATIONS_BEFORE_PERMANENT:
            alert_lines.append(
                f"\n🔴🔴 {violation_count}-ге порушення за півроку — за словами власниці, "
                "це поріг ПОСТІЙНОЇ деактивації каталогу Prom. Перевір негайно."
            )
    elif success_rate < PROM_SUCCESS_WARN_THRESHOLD:
        alert_lines.append(
            f"\n\n⚠️ Показник успішних замовлень Prom знижується: {success_rate * 100:.0f}% "
            f"(жовтий поріг {PROM_SUCCESS_WARN_THRESHOLD * 100:.0f}%, офіційний поріг блокування "
            f"{PROM_SUCCESS_OFFICIAL_THRESHOLD * 100:.0f}%)"
        )

    state["violations"] = violations
    _save_prom_success_state(state)

    base_line = (
        f"\n\nПоказник успішних замовлень Prom (60-денне вікно): {success_rate * 100:.0f}% "
        f"({total - unsuccessful}/{total}, {unsuccessful} скасовано/повернуто) — ОЦІНКА: рахує "
        "кожне скасування/повернення як неуспішне, тоді як офіційна методика Prom виключає "
        "скасування покупцем/незабрані/скарги продавця — реальний офіційний показник, "
        "найімовірніше, вищий за це число"
    )
    return base_line + "".join(alert_lines)


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

        prom_success_section = _prom_success_rate_section(conn)

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

    lines.append(_rozetka_balance_line())
    lines.append(_prom_balance_line())
    lines.append(_rozetka_validation_status_line())
    lines.append(prom_success_section)

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
    sent = send_telegram_message(message)
    if not sent:
        print("[daily_report] Не вдалося надіслати в Telegram (див. повідомлення вище)", file=sys.stderr)


if __name__ == "__main__":
    send_daily_report()
