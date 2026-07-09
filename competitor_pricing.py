"""
competitor_pricing.py — конвеєр порівняння цін з конкурентами на Rozetka.

ВАЖЛИВО ПРО АРХІТЕКТУРУ: Rozetka віддає порожню HTML-заглушку на запити через
requests/urllib (перевірено — сайт розрізняє реальний браузер і бот за
фінгерпринтом, __NEXT_DATA__/ціни в сирому HTML відсутні). Тому автоматичний
пошук конкурентів БЕЗ браузера (як намагався robots.py) не працює.

Цей скрипт НЕ вміє сам відкривати браузер — він лише:
  1) обирає чергу товарів для перевірки (--next-batch),
  2) записує знайдений вручну/через claude-in-chrome результат (--record),
  3) веде checkpoint денного ліміту та pricing_results.csv.

Реальний пошук на Rozetka виконує оператор (Claude Code сесія) через
claude-in-chrome, по одному товару за раз, і викликає --record з результатом.

Логіка рішення (2026-07-09, окремо для кожного майданчика — Prom і Rozetka):
    нижня_межа = (собівартість + собівартість*цільова_маржа) / (1 - комісія_майданчика - комісія_оплати)
    кандидат   = ціна_конкурента - PRICE_STEP (фіксований крок, 1 грн — НЕ відсоток)
    ціна       = max(нижня_межа, кандидат)
    якщо конкурента немає -> ціна = max(нижня_межа, собівартість*NO_COMPETITOR_MULT)

    Комісія майданчика:
    - Prom: категорійна (12-23%+, з кабінету Prom -> Налаштування -> Комісії
      за категоріями) — PROM_CATEGORY_COMMISSION, з fallback-дефолтом, поки
      не всі категорії уточнено.
    - Rozetka: перенесено як орієнтовний дефолт (22%) з попередньої версії
      цієї логіки — Rozetka ще не активна (магазин не зареєстрований), не
      блокує розробку, просто немає живих даних для звірки.

    Комісія оплати (еквайринг/Prom-оплата через RozetkaPay тощо) — ОКРЕМА
    від комісії майданчика, віднімається так само з виручки. Точний % не
    підтверджено — PAYMENT_COMMISSION, за замовчуванням 0.0 (TODO власника).

    Кожен продукт тепер отримує ДВІ незалежні ціни — price_prom, price_rozetka
    (різні нижні межі через різні комісії) — не одну спільну ціну.

Запуск:
    python competitor_pricing.py --seed-test           # записує вже знайдені 20 тестових товарів
    python competitor_pricing.py --status               # прогрес
    python competitor_pricing.py --next-batch 200        # наступні кандидати для перевірки
    python competitor_pricing.py --record 11070 200      # записати знайдену ціну конкурента
    python competitor_pricing.py --record 11760 none     # конкурента не знайдено
"""

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

import time

from parser import fetch_toysi_catalog

BASE_DIR         = Path(__file__).parent
RESULTS_FILE     = BASE_DIR / "pricing_results.csv"
CHECKPOINT_FILE  = BASE_DIR / "competitor_pricing_checkpoint.json"
CATALOG_CACHE_FILE = BASE_DIR / "toysi_catalog_cache.json"
CATALOG_CACHE_TTL  = 3600  # секунд; --record викликається ~200 разів на день поспіль,
                           # кеш рятує від 200 зайвих запитів до фіду Toysi за один день

MIN_PROFIT       = 0.25   # цільовий мінімум прибутку від собівартості (як частка cost)
PRICE_STEP       = 1.0    # фіксований крок нижче конкурента, У ГРН — не відсоток
NO_COMPETITOR_MULT = 1.75
DAILY_LIMIT      = 200
MIN_SUPPLIER_PRICE = 20

PLATFORMS = ("prom", "rozetka")

# ---------------------------------------------------------------------------
# Комісії за майданчиком — окремо від комісії оплати (обидві віднімаються
# з виручки, тому floor враховує суму двох).
# ---------------------------------------------------------------------------

# Prom.ua: комісія КАТЕГОРІЙНА (12-23%+) — дивитись у кабінеті Prom ->
# Налаштування -> Комісії за категоріями. Ключ — назва категорії В НИЖНЬОМУ
# РЕГІСТРІ (get_platform_commission сам нормалізує вхідну category_name через
# .strip().lower(), тож сира назва з фіда Toysi, у будь-якому регістрі, все
# одно знайде відповідний ключ тут).
# Реально спостережено на замовленні №414634349 (2026-07-08, Prom,
# категорія "Пазли Dankotoys"): cpa_commission.amount=9.08 грн на sum=39 грн
# => ~23.28%. Це підтверджує верхню межу заявленого діапазону (12-23%+),
# але навмисно НЕ підставлено як загальний дефолт нижче — одна категорія не
# доказ для решти. TODO (власник): заповнити реальні % по категоріях.
PROM_CATEGORY_COMMISSION: dict[str, float] = {
    # "пазли": 0.2328,  # приклад — розкоментуй і додай решту зі свого кабінету
}
PROM_COMMISSION_DEFAULT = 0.20  # орієнтовний fallback, ПОКИ категорія не уточнена в кабінеті

# Rozetka: перенесено як орієнтовний дефолт з попередньої версії цієї логіки
# (комісія 22%, категорія "Дитячі іграшки") — Rozetka ще не активна (магазин
# не зареєстрований), тому немає живих даних для звірки. Не блокує розробку.
ROZETKA_COMMISSION_DEFAULT = 0.22

# Комісія оплати (еквайринг/Prom-оплата через RozetkaPay, банківський
# еквайринг Rozetka тощо) — ОКРЕМА статті від комісії майданчика вище.
# Точний % не підтверджено в жодному з наявних документів проєкту.
# TODO (власник): уточнити в кабінеті RozetkaPay / банку-еквайєра.
PAYMENT_COMMISSION: dict[str, float] = {
    "prom": 0.0,
    "rozetka": 0.0,
}

FIELDNAMES = [
    "id", "name", "cost", "min_competitor",
    "floor_prom", "price_prom", "margin_pct_prom", "category_prom",
    "floor_rozetka", "price_rozetka", "margin_pct_rozetka", "category_rozetka",
    "match_confidence",
]

# Товари, які завідомо погано підходять для пошуку конкурента (уцінка/брак)
_BAD_NAME_MARKERS = ("уцінка", "пошкодж", "не працює", "немає", "брак")


# ---------------------------------------------------------------------------
# Кеш каталогу Toysi (щоб --record, викликаний ~200x/день, не бив по мережі щоразу)
# ---------------------------------------------------------------------------

def get_catalog(force_refresh: bool = False) -> dict:
    if not force_refresh and CATALOG_CACHE_FILE.exists():
        age = time.time() - CATALOG_CACHE_FILE.stat().st_mtime
        if age < CATALOG_CACHE_TTL:
            return json.loads(CATALOG_CACHE_FILE.read_text(encoding="utf-8"))

    catalog = fetch_toysi_catalog()
    CATALOG_CACHE_FILE.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    return catalog


# ---------------------------------------------------------------------------
# Логіка ціноутворення
# ---------------------------------------------------------------------------

def get_platform_commission(platform: str, category_name: str | None = None) -> float:
    """Комісія майданчика (БЕЗ комісії оплати) для конкретної категорії.
    Для Prom — категорійний пошук у PROM_CATEGORY_COMMISSION з fallback на
    PROM_COMMISSION_DEFAULT; для Rozetka — один орієнтовний дефолт (категорія
    поки не впливає, немає живих даних).

    category_name нормалізується (strip + lower) перед пошуком — сира назва
    категорії з фіда Toysi (parser.py) зберігає оригінальний регістр, а ключі
    PROM_CATEGORY_COMMISSION заповнюються вручну з кабінету Prom (приклад у
    коментарі нижче — лише нижній регістр), тож без нормалізації збіг ніколи
    не спрацював би для реальних категорій."""
    if platform == "prom":
        key = category_name.strip().lower() if category_name else None
        if key and key in PROM_CATEGORY_COMMISSION:
            return PROM_CATEGORY_COMMISSION[key]
        return PROM_COMMISSION_DEFAULT
    if platform == "rozetka":
        return ROZETKA_COMMISSION_DEFAULT
    raise ValueError(f"Невідомий майданчик: {platform!r}, очікую один з {PLATFORMS}")


def decide_price_for_platform(
    cost: float, min_competitor: float | None, platform: str, category_name: str | None = None
) -> dict:
    """
    Рішення про ціну для ОДНОГО майданчика (Prom або Rozetka):
        нижня_межа = (cost + cost*MIN_PROFIT) / (1 - комісія_майданчика - комісія_оплати)
        кандидат   = min_competitor - PRICE_STEP (фіксований крок, грн — не %)
        ціна       = max(нижня_межа, кандидат)

    Завжди повертає ціну — товари з "нерентабельним" конкурентом більше НЕ
    пропускаються (як було в старій категорії C): якщо кандидат нижчий за
    нижню межу, ціна просто піднімається до межі (може вийти вищою за
    конкурента — так і задумано формулою, це не помилка).
    """
    platform_commission = get_platform_commission(platform, category_name)
    payment_commission = PAYMENT_COMMISSION.get(platform, 0.0)
    total_commission = platform_commission + payment_commission
    if total_commission >= 1:
        raise ValueError(
            f"[{platform}] сумарна комісія {total_commission:.0%} >= 100% — перевір "
            "PROM_CATEGORY_COMMISSION/ROZETKA_COMMISSION_DEFAULT/PAYMENT_COMMISSION"
        )

    floor = (cost + cost * MIN_PROFIT) / (1 - total_commission)

    if min_competitor is None:
        price = round(max(cost * NO_COMPETITOR_MULT, floor), 2)
        result_category = "no_competitor"
    else:
        candidate = min_competitor - PRICE_STEP
        price = round(max(floor, candidate), 2)
        result_category = "floor" if floor >= candidate else "undercut"

    net_revenue = price * (1 - total_commission)
    margin_pct = round((net_revenue - cost) / cost * 100, 1) if cost else 0.0

    return {
        "platform": platform,
        "floor": round(floor, 2),
        "price": price,
        "category": result_category,
        "margin_pct": margin_pct,
    }


def decide_price(cost: float, min_competitor: float | None, category_name: str | None = None) -> dict:
    """
    Рахує рішення ОКРЕМО для кожного майданчика (Prom, Rozetka) — комісії
    різні (категорійна для Prom, орієнтовний дефолт для Rozetka), тож нижня
    межа й підсумкова ціна теж різні. Повертає обидва результати під
    ключами "prom"/"rozetka".
    """
    return {
        "prom": decide_price_for_platform(cost, min_competitor, "prom", category_name),
        "rozetka": decide_price_for_platform(cost, min_competitor, "rozetka"),
    }


# ---------------------------------------------------------------------------
# Checkpoint (лише денний ліміт/лічильники; самі результати живуть у CSV)
# ---------------------------------------------------------------------------

def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
    return {"last_run_date": None, "processed_today": 0}


def save_checkpoint(cp: dict) -> None:
    CHECKPOINT_FILE.write_text(json.dumps(cp, ensure_ascii=False, indent=2), encoding="utf-8")


def _bump_checkpoint_for_today() -> dict:
    cp = load_checkpoint()
    today = date.today().isoformat()
    if cp.get("last_run_date") != today:
        cp["last_run_date"] = today
        cp["processed_today"] = 0
    return cp


# ---------------------------------------------------------------------------
# pricing_results.csv
# ---------------------------------------------------------------------------

def load_processed_ids() -> set:
    if not RESULTS_FILE.exists():
        return set()
    with open(RESULTS_FILE, newline="", encoding="utf-8-sig") as f:
        return {row["id"] for row in csv.DictReader(f)}


def append_result(row: dict) -> None:
    is_new = not RESULTS_FILE.exists()
    with open(RESULTS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Вибір черги
# ---------------------------------------------------------------------------

def select_batch(limit: int = DAILY_LIMIT) -> list:
    """Обирає наступні до `limit` товарів для перевірки на Rozetka.

    Пріоритет — дешевші товари: тест на 20 позиціях показав, що товари
    собівартістю <300 грн частіше дають "undercut" (перебиваємо конкурента
    й лишаємось прибутковими), тоді як товари >700 грн частіше впираються в
    "floor" (конкурент занадто дешевий, ціну доводиться піднімати до межі
    маржі). Уцінені/пошкоджені товари виключаються одразу (немає сенсу
    шукати конкурента на брак).
    """
    catalog = get_catalog()
    processed = load_processed_ids()

    candidates = []
    for item in catalog.values():
        pid = str(item["id"])
        if pid in processed:
            continue
        name = (item.get("name") or "").strip()
        if any(marker in name.lower() for marker in _BAD_NAME_MARKERS):
            continue
        try:
            cost = float(item.get("price") or 0)
        except (ValueError, TypeError):
            continue
        if cost < MIN_SUPPLIER_PRICE:
            continue
        candidates.append((cost, pid, name))

    candidates.sort(key=lambda c: c[0])  # дешевші — першими
    return candidates[:limit]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_status() -> None:
    processed = load_processed_ids()
    cp = load_checkpoint()
    counts = {"prom": {"undercut": 0, "floor": 0, "no_competitor": 0}, "rozetka": {"undercut": 0, "floor": 0, "no_competitor": 0}}
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                for platform in PLATFORMS:
                    cat = row.get(f"category_{platform}")
                    if cat in counts[platform]:
                        counts[platform][cat] += 1
    print(f"Оброблено всього: {len(processed)}")
    for platform in PLATFORMS:
        c = counts[platform]
        print(f"  {platform}: undercut={c['undercut']} floor={c['floor']} no_competitor={c['no_competitor']}")
    print(f"Останній запуск: {cp.get('last_run_date')}, оброблено сьогодні: {cp.get('processed_today', 0)}/{DAILY_LIMIT}")


def cmd_next_batch(limit: int) -> None:
    cp = _bump_checkpoint_for_today()
    remaining_today = max(0, DAILY_LIMIT - cp.get("processed_today", 0))
    limit = min(limit, remaining_today) if remaining_today else 0
    if limit == 0:
        print(f"Денний ліміт {DAILY_LIMIT} товарів вже вичерпано на сьогодні ({cp['last_run_date']}).")
        return
    batch = select_batch(limit)
    print(f"Наступні {len(batch)} товарів для перевірки на Rozetka (дешевші — першими):")
    for cost, pid, name in batch:
        print(f"{pid}\t{cost:.2f}\t{name}")


def cmd_record(pid: str, min_competitor_raw: str, match_confidence: str, category_name: str | None = None) -> None:
    catalog = get_catalog()
    item = catalog.get(pid)
    if item is None:
        print(f"Товар {pid} не знайдено в каталозі Toysi (можливо, зник з живого фіда постачальника).")
        sys.exit(1)

    cost = float(item.get("price") or 0)
    if cost <= 0:
        print(f"Товар {pid} має нульову/відсутню ціну постачальника Toysi (cost=0) — пропускаємо, ціну не рахуємо.")
        return
    min_competitor = None if min_competitor_raw.lower() == "none" else float(min_competitor_raw)

    decision = decide_price(cost, min_competitor, category_name)
    prom, rozetka = decision["prom"], decision["rozetka"]
    row = {
        "id": pid,
        "name": item.get("name", ""),
        "cost": f"{cost:.2f}",
        "min_competitor": f"{min_competitor:.2f}" if min_competitor is not None else "",
        "floor_prom": f"{prom['floor']:.2f}",
        "price_prom": f"{prom['price']:.2f}",
        "margin_pct_prom": f"{prom['margin_pct']:.1f}",
        "category_prom": prom["category"],
        "floor_rozetka": f"{rozetka['floor']:.2f}",
        "price_rozetka": f"{rozetka['price']:.2f}",
        "margin_pct_rozetka": f"{rozetka['margin_pct']:.1f}",
        "category_rozetka": rozetka["category"],
        "match_confidence": match_confidence,
    }
    append_result(row)

    cp = _bump_checkpoint_for_today()
    cp["processed_today"] = cp.get("processed_today", 0) + 1
    save_checkpoint(cp)

    print(
        f"{item.get('name','')[:50]} -> "
        f"Prom: [{prom['category']}] {prom['price']:.2f} грн ({prom['margin_pct']}%) | "
        f"Rozetka: [{rozetka['category']}] {rozetka['price']:.2f} грн ({rozetka['margin_pct']}%)"
    )


def cmd_seed_test() -> None:
    """Записує 20 товарів, вже вручну перевірених через claude-in-chrome (сесія аналізу конкурентів)."""
    seed_data = [
        # (id, min_competitor або None, впевненість збігу)
        ("11070", 200,  "приблизний (варіант «Маринка 2»)"),
        ("11127", 76,   "точний"),
        ("11128", 77,   "точний"),
        ("11130", 129,  "точний"),
        ("10038", 250,  "приблизний (загальна категорія)"),
        ("10159", 244,  "точний"),
        ("11107", 392,  "точний"),
        ("11108", 276,  "точний"),
        ("10991", 639,  "точний"),
        ("10992", 500,  "точний (той самий SKU)"),
        ("10993", 484,  "точний"),
        ("11088", 744,  "точний"),
        ("11940", 775,  "приблизний"),
        ("13776", 2010, "точний"),
        ("16727", 1008, "точний (той самий SKU)"),
        ("18204", 1357, "приблизний (варіант без картону)"),
        ("11760", None, "не знайдено відповідника"),
        ("18207", 2505, "точний (той самий SKU)"),
        ("34594", 3575, "приблизний"),
        ("37007", 5085, "точний (той самий SKU)"),
    ]
    already = load_processed_ids()
    added = 0
    for pid, comp, conf in seed_data:
        if pid in already:
            print(f"{pid} вже є в pricing_results.csv — пропущено")
            continue
        cmd_record(pid, "none" if comp is None else str(comp), conf)
        added += 1
    print(f"\nЗасіяно {added} товарів з ручного тесту.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("seed-test")

    p_batch = sub.add_parser("next-batch")
    p_batch.add_argument("limit", nargs="?", type=int, default=DAILY_LIMIT)

    p_record = sub.add_parser("record")
    p_record.add_argument("id")
    p_record.add_argument("min_competitor", help='ціна конкурента або "none"')
    p_record.add_argument("confidence", nargs="?", default="точний")
    p_record.add_argument(
        "--category", default=None,
        help="Категорія Prom для категорійної комісії (ключ у PROM_CATEGORY_COMMISSION); "
             "якщо не задано — використовується PROM_COMMISSION_DEFAULT",
    )

    args = ap.parse_args()

    if args.cmd == "status":
        cmd_status()
    elif args.cmd == "seed-test":
        cmd_seed_test()
    elif args.cmd == "next-batch":
        cmd_next_batch(args.limit)
    elif args.cmd == "record":
        cmd_record(args.id, args.min_competitor, args.confidence, args.category)


if __name__ == "__main__":
    main()
