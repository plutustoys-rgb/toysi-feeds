"""
apply_prices.py — застосовує коригування цін та перегенеровує фід (Prom або Rozetka).

Обидва вхідні CSV тепер зберігають ціну ОКРЕМО для кожного майданчика
(price_corrections.csv від repricer.py, pricing_results.csv від
competitor_pricing.py) — тому --platform обов'язковий: він визначає, які
колонки (*_prom / *_rozetka) читати і який генератор фіду викликати.

Два режими:
  1) --csv price_corrections.csv   (формат repricer.py)
     Підставляє нову ціну (new_price_<platform>) замість calc_price(),
     нічого не виключає.

  2) --pricing-results pricing_results.csv   (формат competitor_pricing.py)
     Бере price_<platform> для кожного товару. Категорії "виключити з
     фіду" більше немає (стара категорія C прибрана — дивись
     decide_price_for_platform у competitor_pricing.py: товари з
     нерентабельним конкурентом тепер піднімаються до нижньої межі маржі
     замість виключення). Товари, яких ще немає в pricing_results.csv (не
     перевірені), лишаються у фіді зі стандартною calc_price().

Запуск:
    python apply_prices.py --platform prom --csv price_corrections.csv
    python apply_prices.py --platform prom --pricing-results pricing_results.csv
    python apply_prices.py --platform rozetka --pricing-results pricing_results.csv
"""

import argparse
import csv
import sys
from pathlib import Path

DEFAULT_CSV = "price_corrections.csv"
PLATFORMS = ("prom", "rozetka")


def _check_required_columns(reader: csv.DictReader, required: set, path: str) -> None:
    """Надійність, п.1: тиха поломка схеми CSV (перейменована/видалена
    колонка) раніше просто давала 0 коригувань без жодного пояснення —
    csv.DictReader.get(col) на відсутню колонку повертає None так само
    мовчки, як на порожнє значення в наявній колонці, тож немає способу
    відрізнити "схема зламана" від "просто немає даних у цьому рядку"
    без явної перевірки заголовка. Перевіряємо ОДИН раз, до першого
    рядка — reader.fieldnames є одразу після створення DictReader, ще
    до ітерації."""
    header = set(reader.fieldnames or [])
    missing = required - header
    if missing:
        print(
            f"[ApplyPrices] ПОМИЛКА: {path} не містить очікувані колонки {sorted(missing)} "
            f"(наявні: {sorted(header)}) — схема файлу змінилась чи файл пошкоджений. Зупиняюсь, "
            "щоб не згенерувати фід із мовчки порожніми/невірними коригуваннями.",
            file=sys.stderr,
        )
        sys.exit(1)


def load_corrections(path: str, platform: str) -> dict[str, float]:
    """Читає price_corrections.csv (формат repricer.py) і повертає {id: new_price} для одного майданчика."""
    corrections: dict[str, float] = {}
    p = Path(path)
    if not p.exists():
        print(f"[ApplyPrices] Файл не знайдено: {path}")
        sys.exit(1)

    price_col = f"new_price_{platform}"
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        _check_required_columns(reader, {"id", price_col}, path)
        for row in reader:
            pid       = (row.get("id") or "").strip()
            raw_price = (row.get(price_col) or "").strip()
            if pid and raw_price:
                try:
                    corrections[pid] = float(raw_price)
                except ValueError:
                    print(f"[ApplyPrices] Пропускаємо рядок з невалідною ціною: {row}")

    return corrections


def load_pricing_results(path: str, platform: str) -> dict[str, float]:
    """Читає pricing_results.csv (формат competitor_pricing.py) для одного майданчика.

    Повертає {id: price}. Категорії тут — "undercut"/"floor"/"no_competitor",
    жодна з них не означає "виключити з фіду" (на відміну від старої
    категорії C) — рахуються лише для інформативного підсумку в консолі.
    """
    p = Path(path)
    if not p.exists():
        print(f"[ApplyPrices] Файл не знайдено: {path}")
        sys.exit(1)

    price_col    = f"price_{platform}"
    category_col = f"category_{platform}"
    overrides: dict[str, float] = {}
    counts = {"undercut": 0, "floor": 0, "no_competitor": 0}

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        _check_required_columns(reader, {"id", price_col}, path)
        for row in reader:
            pid       = (row.get("id") or "").strip()
            raw_price = (row.get(price_col) or "").strip()
            if not pid or not raw_price:
                continue
            category = (row.get(category_col) or "").strip()
            if category in counts:
                counts[category] += 1
            try:
                overrides[pid] = float(raw_price)
            except ValueError:
                print(f"[ApplyPrices] Пропускаємо рядок з невалідною ціною: {row}")

    print(f"[ApplyPrices] pricing_results.csv ({platform}): "
          f"undercut={counts['undercut']} floor={counts['floor']} no_competitor={counts['no_competitor']}")
    return overrides


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True, choices=PLATFORMS,
                    help="Який майданчик готуємо — визначає, які price_*/category_*/new_price_* "
                         "колонки читати і який генератор фіду викликати")
    ap.add_argument("--csv", default=None,
                    help=f"Файл коригувань у форматі repricer.py: id,new_price_prom,new_price_rozetka,... "
                         f"(default: {DEFAULT_CSV})")
    ap.add_argument("--pricing-results", default=None,
                    help="Файл результатів competitor_pricing.py (id,...,price_prom,price_rozetka,...)")
    ap.add_argument("--out", default=None,
                    help="Вихідний XML файл (default: OUTPUT_FILE відповідного генератора фіду)")
    args = ap.parse_args()

    if args.platform == "prom":
        from generate_prom_feed import generate_feed, OUTPUT_FILE
    else:
        from generate_rozetka_feed import generate_feed, OUTPUT_FILE
    out_file = args.out or OUTPUT_FILE

    if args.pricing_results:
        overrides = load_pricing_results(args.pricing_results, args.platform)
        generate_feed(output_file=out_file, price_overrides=overrides)
    else:
        csv_path = args.csv or DEFAULT_CSV
        corrections = load_corrections(csv_path, args.platform)
        print(f"[ApplyPrices] Завантажено {len(corrections)} коригувань цін ({args.platform}) з {csv_path}")
        generate_feed(output_file=out_file, price_overrides=corrections)


if __name__ == "__main__":
    main()
