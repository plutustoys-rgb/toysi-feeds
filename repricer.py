"""
repricer.py — знижує ціни товарів що не продаються, орієнтуючись на Rozetka.

Вхід:  CSV файл з товарами без продажів (id, name, our_price)
Вихід: price_corrections.csv (id, name, toysi_price, competitor_price,
       new_price_prom, new_price_rozetka) — ціна рахується ОКРЕМО для
       кожного майданчика (різні комісії), формула — competitor_pricing.py
       (decide_price_for_platform): ціна = max(нижня_межа_маржі, конкурент - 1 грн).

Запуск:
    python repricer.py unsold.csv
або з явним файлом виводу:
    python repricer.py unsold.csv --out price_corrections.csv
"""

import csv
import io
import json
import random
import re
import sys
import time
import urllib.parse
from pathlib import Path

# Форсуємо UTF-8 для виводу в консоль Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import requests
from bs4 import BeautifulSoup

from competitor_pricing import decide_price, PLATFORMS

# ---------------------------------------------------------------------------
# Налаштування
# ---------------------------------------------------------------------------
MAX_PRODUCTS  = 200
PAUSE_MIN     = 3.0    # секунди між запитами
PAUSE_MAX     = 5.0

OUTPUT_FILE   = "price_corrections.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ---------------------------------------------------------------------------
# Пошук мінімальної ціни конкурента на Rozetka
# ---------------------------------------------------------------------------

def _parse_prices_from_next_data(html_text: str) -> list[float]:
    """Витягує ціни зі скрипта __NEXT_DATA__ (Next.js SSR)."""
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html_text, re.DOTALL
    )
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    prices = []
    # Рекурсивно шукаємо числові поля з назвою 'price' / 'old_price'
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("price", "sell_status_price") and isinstance(v, (int, float)):
                    prices.append(float(v))
                else:
                    walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return prices


def _parse_prices_from_html(html_text: str) -> list[float]:
    """Запасний варіант: BeautifulSoup по CSS-селекторах Rozetka."""
    soup = BeautifulSoup(html_text, "html.parser")
    prices = []

    selectors = [
        "span.goods-price__big",
        "p.goods-price__big",
        ".goods-tile__price-value",
        "[data-goods-price]",
    ]
    for sel in selectors:
        for el in soup.select(sel):
            raw = el.get_text(" ", strip=True)
            nums = re.findall(r"[\d\s]+", raw)
            for n in nums:
                clean = n.replace(" ", "").replace(" ", "")
                if clean.isdigit():
                    prices.append(float(clean))

    # Останній варіант: регулярка по всьому тексту сторінки
    if not prices:
        for m in re.finditer(r'"price"\s*:\s*(\d+(?:\.\d+)?)', html_text):
            prices.append(float(m.group(1)))

    return prices


def search_rozetka_min_price(name: str, session: requests.Session) -> float | None:
    """
    Шукає мінімальну ціну на Rozetka за назвою товару.
    Повертає float або None якщо знайти не вдалось.
    """
    query = urllib.parse.quote(name[:100])
    url   = f"https://rozetka.com.ua/ua/search/?text={query}"

    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"    [!] Помилка запиту: {e}")
        return None

    html_text = resp.text

    prices = _parse_prices_from_next_data(html_text)
    if not prices:
        prices = _parse_prices_from_html(html_text)

    # Фільтруємо нереалістичні значення (< 1 грн або > 500 000 грн)
    prices = [p for p in prices if 1 <= p <= 500_000]
    return min(prices) if prices else None


# ---------------------------------------------------------------------------
# Основна логіка
# ---------------------------------------------------------------------------

def load_unsold(path: str) -> list[dict]:
    """
    Читає вхідний CSV.
    Обов'язкові колонки: id, name, our_price
    Опціональна колонка: competitor_price  — якщо задана, скрапінг не запускається.
    """
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_comp = (row.get("competitor_price") or "").strip()
            rows.append({
                "id":               row.get("id", "").strip(),
                "name":             row.get("name", "").strip(),
                "our_price":        float(row.get("our_price", 0) or 0),
                "competitor_price": float(raw_comp) if raw_comp else None,
            })
    return rows


def load_toysi_prices() -> dict[str, float]:
    """Завантажує ціни постачальника з Toysi для розрахунку мінімальної маржі."""
    print("[Repricer] Завантажуємо каталог Toysi для цін постачальника...")
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        from parser import fetch_toysi_catalog
        catalog = fetch_toysi_catalog()
        return {k: float(v.get("price") or 0) for k, v in catalog.items() if v.get("price")}
    except Exception as e:
        print(f"[Repricer] Не вдалось завантажити каталог Toysi: {e}")
        return {}


def run(unsold_path: str, output_path: str = OUTPUT_FILE) -> None:
    unsold = load_unsold(unsold_path)
    if not unsold:
        print("[Repricer] Вхідний файл порожній.")
        return

    if len(unsold) > MAX_PRODUCTS:
        print(f"[Repricer] Обрізаємо до {MAX_PRODUCTS} товарів (з {len(unsold)})")
        unsold = unsold[:MAX_PRODUCTS]

    toysi_prices = load_toysi_prices()

    session     = requests.Session()
    corrections = []
    changed     = 0
    not_found   = 0

    for i, item in enumerate(unsold, 1):
        pid       = item["id"]
        name      = item["name"]
        our_price = item["our_price"]
        cost      = toysi_prices.get(pid, 0)

        print(f"[{i}/{len(unsold)}] {name[:60]}")

        # Якщо competitor_price задана вручну в CSV — використовуємо її
        if item["competitor_price"] is not None:
            comp_price = item["competitor_price"]
            print(f"    (ціна конкурента з CSV: {comp_price:.0f} грн)")
        else:
            comp_price = search_rozetka_min_price(name, session)
            if comp_price is None:
                print(f"    -> ціна конкурента не знайдена, пропускаємо")
                not_found += 1
                time.sleep(random.uniform(PAUSE_MIN, PAUSE_MAX))
                continue

        decision = decide_price(cost, comp_price) if cost > 0 else None
        if decision is None:
            print(f"    -> немає ціни постачальника Toysi для {pid}, пропускаємо")
            time.sleep(random.uniform(PAUSE_MIN, PAUSE_MAX))
            continue

        print(f"    наша: {our_price:.0f} грн | конкурент: {comp_price:.0f} грн")

        row = {"id": pid, "name": name, "toysi_price": f"{cost:.2f}", "competitor_price": f"{comp_price:.2f}"}
        changed_here = False
        for platform in PLATFORMS:
            result = decision[platform]
            new_price = result["price"]
            if new_price < our_price:
                row[f"new_price_{platform}"] = f"{new_price:.2f}"
                changed_here = True
                print(f"    -> {platform}: нова ціна {new_price:.0f} грн [{result['category']}]")
            else:
                row[f"new_price_{platform}"] = ""
                print(f"    -> {platform}: {new_price:.0f} грн не нижча за поточну ({our_price:.0f}), без змін")

        if not changed_here:
            time.sleep(random.uniform(PAUSE_MIN, PAUSE_MAX))
            continue

        corrections.append(row)
        changed += 1

        time.sleep(random.uniform(PAUSE_MIN, PAUSE_MAX))

    # Зберігаємо результат
    if corrections:
        fieldnames = ["id", "name", "toysi_price", "competitor_price"] + [f"new_price_{p}" for p in PLATFORMS]
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(corrections)
        print(f"\n[Repricer] Збережено {len(corrections)} коригувань -> {output_path}")
    else:
        print(f"\n[Repricer] Коригувань немає — файл не створено.")

    print(f"[Repricer] Оброблено: {len(unsold)} | змінено: {changed} | не знайдено: {not_found}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("unsold_csv", help="CSV з товарами без продажів (id, name, our_price)")
    ap.add_argument("--out", default=OUTPUT_FILE, help=f"Файл результату (default: {OUTPUT_FILE})")
    args = ap.parse_args()
    run(args.unsold_csv, args.out)
