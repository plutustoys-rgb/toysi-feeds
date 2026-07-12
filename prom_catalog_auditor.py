"""
prom_catalog_auditor.py — комплексний щоденний аудит каталогу Prom, об'єднує
в один прогін усі перевірки, накопичені за 2026-07-10 (звіти pt5-pt9):

1. Наявність    — SKU з ostatok=0 у Toysi API, досі активні в Prom (власник
                   підтвердив: API ostatok — єдине джерело правди для нашої
                   автоматизації, розбіжність з сайтом toysi.ua — не привід
                   звірятись із сайтом на масштабі 970+ SKU).
2. Зображення    — товари без жодного фото довше 24 годин (не щойно
                   імпортовані — обробка фото на боці Prom триває якийсь час,
                   це нормально; проблема лише якщо стан не змінюється).
3. Заблоковані   — SKU з поточного топ-970, які довше 24 годин відсутні в
                   Prom взагалі (ні активні, ні видалені) — ознака блокування
                   при імпорті (як 301008/301028) чи іншої стійкої проблеми.
                   ОБМЕЖЕННЯ: API не дає точної причини блокування — це лише
                   сигнал "перевір останній звіт імпорту в кабінеті".
4. Ціни          — SKU топ-970, чия розрахункова ціна впирається в нижню
                   межу маржі (MIN_PROFIT, той самий принцип, що
                   generate_prom_feed.py/competitor_pricing.py).
5. Характеристики — категорії топ-970 з масовим (>30%) браком країни
                   походження чи будь-якої змістовної характеристики (та сама
                   перевірка, що виявила проблему з Велосипедами).
6. Бренди        — варіанти написання одного бренду (case-insensitive
                   колізії постачальника), які ще не покриті normalize_vendor()
                   з generate_prom_feed.py (як була MIC/MiC).

Стан між запусками (для перевірок #2/#3, які залежать від "довше X годин")
зберігається в prom_catalog_audit_state.json поруч зі скриптом.

Звіт зберігається в REPORT_DIR/prom_catalog_audit_YYYY-MM-DD.md і додатково
надсилається власнику через Telegram (send_telegram_message) — бо цей
скрипт призначений для запуску на VPS за cron, а не інтерактивно на
Windows-машині, звідки code_report_*.md пишуться напряму в спільну
Windows-папку (C:\\Users\\smach\\Claude\\Projects\\PlutusToys_avtonomiya) —
з Linux-кронjob-у туди не дотягнутись, тож Telegram — еквівалентний канал
доставки власнику.

Опційно, разом з аудитом (--sync-apply): реальний повторний запуск
`prom_catalog_sync.py --apply` — бо одноразового прогону недостатньо
(з'ясовано 2026-07-10, pt8/pt9: товари стають "застарілими" (нульовий
ostatok чи зникають з Toysi) БЕЗПЕРЕРВНО, не одноразово, тож синхронізація
має бути періодичною).

Запуск:
    python prom_catalog_auditor.py                # лише звіт (dry-run для synс)
    python prom_catalog_auditor.py --sync-apply    # звіт + реальна деактивація застарілих
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from audit_prom_characteristics import audit as audit_characteristics
from competitor_pricing import decide_price_for_platform
from generate_prom_feed import _VENDOR_ALIASES, normalize_vendor
from generate_prom_feed_top import select_top_items
from parser import fetch_toysi_catalog
from prom_catalog_sync import (
    check_product_count_sane,
    deactivate,
    fetch_prom_products,
    find_stale_external_ids,
)
from telegram_notify import send_telegram_message

load_dotenv()

BASE_DIR    = Path(__file__).parent
STATE_FILE  = BASE_DIR / "prom_catalog_audit_state.json"
REPORT_DIR  = BASE_DIR / "reports"

STALE_AGE_THRESHOLD_HOURS = 24  # для перевірок #2 (фото) і #3 (заблоковані)
CHARACTERISTICS_THRESHOLD  = 0.30  # 30% SKU категорії без даних — "масовий брак"
CHARACTERISTICS_MIN_SAMPLE = 5     # категорії менше цього — відсоток на 1-2 SKU не показовий

TELEGRAM_MAX_LEN = 3800  # запас під ліміт Telegram (4096) для розбиття на частини


# ---------------------------------------------------------------------------
# Стан між запусками (першопоява проблеми — для порогу "довше 24 годин")
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"no_images_since": {}, "missing_since": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"no_images_since": {}, "missing_since": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _track_age(state_bucket: dict, current_ids: set, now_iso: str) -> dict:
    """Оновлює {external_id: перша_поява_ISO} — додає нові, прибирає ті, що
    зникли з current_ids (проблема вирішилась). Повертає ОНОВЛЕНИЙ bucket."""
    updated = {}
    for ext_id in current_ids:
        updated[ext_id] = state_bucket.get(ext_id, now_iso)
    return updated


def _older_than(state_bucket: dict, current_ids: set, now: datetime, hours: int) -> list:
    result = []
    for ext_id in current_ids:
        first_seen = state_bucket.get(ext_id)
        if not first_seen:
            continue
        age = now - datetime.fromisoformat(first_seen)
        if age >= timedelta(hours=hours):
            result.append((ext_id, age))
    return result


# ---------------------------------------------------------------------------
# Перевірка 1 — Наявність
# ---------------------------------------------------------------------------

def check_stock(prom_products: dict, desired_ids: set, toysi_ids: set) -> list:
    stale_ids = find_stale_external_ids(prom_products, desired_ids, toysi_ids)
    return [(ext_id, prom_products[ext_id].get("name", "")) for ext_id in stale_ids]


# ---------------------------------------------------------------------------
# Перевірка 2 — Зображення
# ---------------------------------------------------------------------------

def check_images(prom_products: dict, state: dict, now: datetime) -> tuple:
    now_iso = now.isoformat()
    no_images_ids = {
        ext_id for ext_id, p in prom_products.items()
        if p.get("status") != "deleted" and not p.get("images") and not p.get("main_image")
    }
    state["no_images_since"] = _track_age(state["no_images_since"], no_images_ids, now_iso)
    flagged = _older_than(state["no_images_since"], no_images_ids, now, STALE_AGE_THRESHOLD_HOURS)
    return [(ext_id, prom_products[ext_id].get("name", ""), age) for ext_id, age in flagged], state


# ---------------------------------------------------------------------------
# Перевірка 3 — Заблоковані товари
# ---------------------------------------------------------------------------

def check_blocked(desired_ids: set, prom_products: dict, top_catalog: dict, state: dict, now: datetime) -> tuple:
    now_iso = now.isoformat()
    missing_ids = desired_ids - set(prom_products.keys())
    state["missing_since"] = _track_age(state["missing_since"], missing_ids, now_iso)
    flagged = _older_than(state["missing_since"], missing_ids, now, STALE_AGE_THRESHOLD_HOURS)
    return [
        (ext_id, top_catalog.get(ext_id, {}).get("name", ""), age) for ext_id, age in flagged
    ], state


# ---------------------------------------------------------------------------
# Перевірка 4 — Ціни на межі маржі
# ---------------------------------------------------------------------------

def check_price_floor(top_catalog: dict) -> list:
    flagged = []
    for pid, item in top_catalog.items():
        try:
            cost = float(item.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if cost <= 0:
            continue
        decision = decide_price_for_platform(cost, None, "prom", item.get("category_name"))
        if decision["price"] <= decision["floor"] + 0.005:
            flagged.append((pid, item.get("name", ""), item.get("category_name", ""), decision["margin_pct"]))
    return flagged


# ---------------------------------------------------------------------------
# Перевірка 5 — Характеристики
# ---------------------------------------------------------------------------

def check_characteristics(top_catalog: dict) -> list:
    """Спрацьовує лише на "без країни" — "без характеристик" (тип/вікова
    група) відсутнє у ВСЬОГО каталогу Toysi завжди (задокументовано в
    audit_prom_characteristics.py: джерело просто не надає ці поля жодному
    SKU) — тобто ця метрика завжди ~100% і НЕ сигналізує про щось НОВЕ чи
    специфічне для категорії. "Без країни" натомість реально відрізняється
    по категоріях (0-100%) — саме це і виявило проблему з Велосипедами."""
    by_category = audit_characteristics(top_catalog)
    flagged = []
    for cat, row in by_category.items():
        total = row["total"]
        if total < CHARACTERISTICS_MIN_SAMPLE:
            continue
        country_pct = row["missing_country"] / total
        char_pct = row["no_characteristics"] / total
        if country_pct >= CHARACTERISTICS_THRESHOLD:
            flagged.append((cat, total, row["missing_country"], country_pct, row["no_characteristics"], char_pct))
    flagged.sort(key=lambda r: r[3], reverse=True)
    return flagged


# ---------------------------------------------------------------------------
# Перевірка 6 — Бренди (case-insensitive колізії, не покриті normalize_vendor)
# ---------------------------------------------------------------------------

def check_brands(toysi_catalog: dict) -> list:
    variants_by_lower: dict = {}
    for item in toysi_catalog.values():
        vendor = (item.get("vendor") or "").strip()
        if not vendor:
            continue
        variants_by_lower.setdefault(vendor.lower(), set()).add(vendor)

    flagged = []
    for lower, variants in variants_by_lower.items():
        if len(variants) < 2:
            continue
        normalized = {normalize_vendor(v) for v in variants}
        if len(normalized) > 1:  # normalize_vendor() ще не звела їх до одного написання
            flagged.append((lower, sorted(variants)))
    flagged.sort(key=lambda r: r[0])
    return flagged


# ---------------------------------------------------------------------------
# Звіт
# ---------------------------------------------------------------------------

def build_report(today: str, results: dict, prom_total: int, top_count: int, count_warning: str | None) -> str:
    lines = [f"# Аудит каталогу Prom — {today}", ""]
    lines.append(f"Товарів у кабінеті Prom (активні): {prom_total}. Поточний топ-970: {top_count}.")
    if count_warning:
        lines.append("")
        lines.append(f"⚠️ {count_warning}")
    lines.append("")

    stock = results["stock"]
    lines.append(f"## 1. Наявність — {len(stock)} SKU з ostatok=0, досі активні в Prom")
    if stock:
        lines.append("")
        for ext_id, name in stock[:30]:
            lines.append(f"- {ext_id}: {name[:70]}")
        if len(stock) > 30:
            lines.append(f"- ... та ще {len(stock) - 30}")
        lines.append("")
        lines.append(
            "Заберe наступний запуск `prom_catalog_sync.py --apply` "
            "(автоматично за розкладом, якщо увімкнено)."
        )
    else:
        lines.append("Немає — каталог відповідає поточному топ-970 по наявності.")
    lines.append("")

    images = results["images"]
    lines.append(f"## 2. Зображення — {len(images)} SKU без фото довше {STALE_AGE_THRESHOLD_HOURS} год")
    if images:
        lines.append("")
        for ext_id, name, age in images[:30]:
            lines.append(f"- {ext_id}: {name[:60]} (без фото {age.days}д {age.seconds // 3600}год)")
        if len(images) > 30:
            lines.append(f"- ... та ще {len(images) - 30}")
    else:
        lines.append("Немає — усі товари з фото, або відсутність ще в межах нормального часу обробки.")
    lines.append("")

    blocked = results["blocked"]
    lines.append(f"## 3. Заблоковані/відсутні товари — {len(blocked)} SKU з топ-970 відсутні в Prom довше {STALE_AGE_THRESHOLD_HOURS} год")
    if blocked:
        lines.append("")
        for ext_id, name, age in blocked[:30]:
            lines.append(f"- {ext_id}: {name[:60]} (відсутній {age.days}д {age.seconds // 3600}год)")
        if len(blocked) > 30:
            lines.append(f"- ... та ще {len(blocked) - 30}")
        lines.append("")
        lines.append(
            "⚠️ Точну причину API не дає — перевір звіт останнього імпорту "
            "в кабінеті (Товари -> Імпорт -> «Товари, які не завантажені через помилки»)."
        )
    else:
        lines.append("Немає.")
    lines.append("")

    price = results["price"]
    pct = (len(price) / top_count * 100) if top_count else 0
    lines.append(f"## 4. Ціни на межі маржі — {len(price)} SKU ({pct:.1f}% топ-970) впираються в нижню межу")
    if price:
        lines.append("")
        for pid, name, cat, margin_pct in price[:20]:
            lines.append(f"- {pid}: {name[:55]} [{cat}] margin={margin_pct:.1f}%")
        if len(price) > 20:
            lines.append(f"- ... та ще {len(price) - 20}")
    else:
        lines.append("Немає SKU на нижній межі маржі.")
    lines.append("")

    chars = results["characteristics"]
    lines.append(f"## 5. Характеристики — {len(chars)} категорій з масовим (>={CHARACTERISTICS_THRESHOLD:.0%}) браком даних")
    if chars:
        lines.append("")
        for cat, total, miss_country, country_pct, no_char, char_pct in chars:
            lines.append(
                f"- {cat}: {total} SKU, без країни {miss_country} ({country_pct:.0%}), "
                f"без характеристик {no_char} ({char_pct:.0%})"
            )
        lines.append("")
        lines.append(
            "⚠️ Розглянь виключення категорії з топ-970 (EXCLUDED_CATEGORIES у "
            "generate_prom_feed_top.py), як раніше зроблено для «Велосипеди»."
        )
    else:
        lines.append("Немає нових проблемних категорій.")
    lines.append("")

    brands = results["brands"]
    lines.append(f"## 6. Бренди — {len(brands)} нових варіантів написання, що потребують рішення власника")
    if brands:
        lines.append("")
        for lower, variants in brands:
            lines.append(f"- {' / '.join(variants)}")
        lines.append("")
        lines.append(
            "Додай рішення в `_VENDOR_ALIASES` (generate_prom_feed.py), якщо це "
            "той самий бренд — той самий процес, що й для MIC/MiC."
        )
    else:
        lines.append("Немає нових колізій написання бренду.")
    lines.append("")

    return "\n".join(lines)


def build_telegram_summary(today: str, results: dict, sync_result: str | None, count_warning: str | None) -> str:
    counts = {k: len(v) for k, v in results.items()}
    total_issues = sum(counts.values())
    lines = [f"📋 Аудит каталогу Prom — {today}"]
    if total_issues == 0:
        lines.append("Усе чисто — жодних знахідок по 6 перевірках.")
    else:
        lines.append(
            f"Наявність: {counts['stock']} | Фото: {counts['images']} | "
            f"Заблоковані: {counts['blocked']} | Ціни на межі: {counts['price']} | "
            f"Характеристики: {counts['characteristics']} | Бренди: {counts['brands']}"
        )
    if count_warning:
        lines.append(f"⚠️ {count_warning}")
    if sync_result:
        lines.append(sync_result)
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sync-apply", action="store_true",
                     help="Після аудиту реально викликати prom_catalog_sync.py --apply "
                          "(деактивувати застарілі SKU, не лише повідомити про них).")
    args = ap.parse_args()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    print("[Auditor] Завантажуємо каталог Toysi...")
    toysi_catalog = fetch_toysi_catalog()
    if not toysi_catalog:
        print("[Auditor] Каталог Toysi порожній — перевірку не виконано.", file=sys.stderr)
        sys.exit(1)
    top_catalog = select_top_items(toysi_catalog)
    desired_ids = {str(pid) for pid in top_catalog}
    toysi_ids   = {str(pid) for pid in toysi_catalog}

    print("[Auditor] Тягну повний список товарів кабінету Prom...")
    prom_products = fetch_prom_products()
    print(f"[Auditor] У кабінеті Prom: {len(prom_products)} товарів. У топ-970: {len(desired_ids)}.")
    count_warning = check_product_count_sane(prom_products)

    state = load_state()

    print("[Auditor] Виконую 6 перевірок...")
    results = {}
    results["stock"] = check_stock(prom_products, desired_ids, toysi_ids)
    results["images"], state = check_images(prom_products, state, now)
    results["blocked"], state = check_blocked(desired_ids, prom_products, top_catalog, state, now)
    results["price"] = check_price_floor(top_catalog)
    results["characteristics"] = check_characteristics(top_catalog)
    results["brands"] = check_brands(toysi_catalog)

    save_state(state)

    sync_result = None
    if args.sync_apply and results["stock"]:
        stale_ids = [ext_id for ext_id, _ in results["stock"]]
        print(f"[Auditor] --sync-apply: деактивую {len(stale_ids)} застарілих SKU...")
        processed, errors = deactivate(stale_ids)
        sync_result = f"Синхронізація: деактивовано {len(processed)}, помилок {len(errors)}."
        print(f"[Auditor] {sync_result}")

    report = build_report(today, results, len(prom_products), len(top_catalog), count_warning)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"prom_catalog_audit_{today}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"[Auditor] Звіт збережено: {out_path}")

    summary = build_telegram_summary(today, results, sync_result, count_warning)
    if send_telegram_message(summary):
        print("[Auditor] Короткий підсумок надіслано в Telegram.")


if __name__ == "__main__":
    main()
