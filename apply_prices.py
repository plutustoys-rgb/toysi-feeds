"""
apply_prices.py — застосовує коригування цін та перегенеровує rozetka_feed.xml.

Два режими:
  1) --csv price_corrections.csv   (старий режим, разом з repricer.py)
     Просто підставляє нову ціну замість calc_price(), нічого не виключає.

  2) --pricing-results pricing_results.csv   (режим competitor_pricing.py)
     Бере ціну з категорій A/B/D, а товари категорії C (продаж у збиток
     за поточними цінами конкурентів) ВИКЛЮЧАЄ з фіду повністю.
     Товари, яких ще немає в pricing_results.csv (не перевірені), лишаються
     у фіді зі стандартною calc_price() — вважаємо їх "поки не проаналізовані",
     а не автоматично збитковими.

Запуск:
    python apply_prices.py --csv price_corrections.csv
    python apply_prices.py --pricing-results pricing_results.csv
"""

import argparse
import csv
import sys
from pathlib import Path

from generate_rozetka_feed import generate_feed, OUTPUT_FILE

DEFAULT_CSV = "price_corrections.csv"


def load_corrections(path: str) -> dict[str, float]:
    """Читає CSV (id, new_price) і повертає {product_id: new_price}."""
    corrections: dict[str, float] = {}
    p = Path(path)
    if not p.exists():
        print(f"[ApplyPrices] Файл не знайдено: {path}")
        sys.exit(1)

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid       = (row.get("id") or "").strip()
            raw_price = (row.get("new_price") or "").strip()
            if pid and raw_price:
                try:
                    corrections[pid] = float(raw_price)
                except ValueError:
                    print(f"[ApplyPrices] Пропускаємо рядок з невалідною ціною: {row}")

    return corrections


def load_pricing_results(path: str) -> tuple[dict[str, float], set[str]]:
    """Читає pricing_results.csv (формат competitor_pricing.py).

    Повертає (price_overrides, exclude_ids):
      - price_overrides: {id: price} для категорій A/B/D
      - exclude_ids: {id, ...} для категорії C (збиткові — виключити з фіду)
    """
    p = Path(path)
    if not p.exists():
        print(f"[ApplyPrices] Файл не знайдено: {path}")
        sys.exit(1)

    overrides: dict[str, float] = {}
    exclude_ids: set[str] = set()
    counts = {"A": 0, "B": 0, "C": 0, "D": 0}

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid      = (row.get("id") or "").strip()
            category = (row.get("category") or "").strip()
            raw_price = (row.get("price") or "").strip()
            if not pid or category not in counts:
                continue
            counts[category] += 1
            if category == "C":
                exclude_ids.add(pid)
            elif raw_price:
                overrides[pid] = float(raw_price)

    print(f"[ApplyPrices] pricing_results.csv: A={counts['A']} B={counts['B']} "
          f"C={counts['C']} (виключено) D={counts['D']}")
    return overrides, exclude_ids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None,
                    help=f"Файл коригувань у старому форматі id,new_price (default: {DEFAULT_CSV})")
    ap.add_argument("--pricing-results", default=None,
                    help="Файл результатів competitor_pricing.py (id,...,price,category)")
    ap.add_argument("--out", default=OUTPUT_FILE,
                    help=f"Вихідний XML файл (default: {OUTPUT_FILE})")
    args = ap.parse_args()

    if args.pricing_results:
        overrides, exclude_ids = load_pricing_results(args.pricing_results)
        generate_feed(output_file=args.out, price_overrides=overrides, exclude_ids=exclude_ids)
    else:
        csv_path = args.csv or DEFAULT_CSV
        corrections = load_corrections(csv_path)
        print(f"[ApplyPrices] Завантажено {len(corrections)} коригувань цін з {csv_path}")
        generate_feed(output_file=args.out, price_overrides=corrections)


if __name__ == "__main__":
    main()
