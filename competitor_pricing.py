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

Логіка рішення (комісія Rozetka 22%, ФОП, категорія "Дитячі іграшки"):
    беззбитковість = собівартість / (1 - 0.22)
    ціль (25% прибутку) = беззбитковість * 1.25

    - конкурента немає                          -> ціна = собівартість*1.75  [D]
    - конкурент є, конкурент*0.97 < беззбитковість -> ПРОПУСК (збиток)       [C]
    - конкурент є, конкурент*0.97 >= ціль          -> ціна = конкурент*0.97, >=25% прибутку [A]
    - конкурент є, беззбитковість <= конкурент*0.97 < ціль -> ціна = конкурент*0.97, <25% [B]

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

COMMISSION       = 0.22
MIN_PROFIT       = 0.25   # цільовий мінімум прибутку від собівартості
UNDERCUT_K       = 0.97   # на 3% нижче конкурента
NO_COMPETITOR_MULT = 1.75
DAILY_LIMIT      = 200
MIN_SUPPLIER_PRICE = 20

FIELDNAMES = ["id", "name", "cost", "min_competitor", "breakeven", "price", "margin_pct", "category", "match_confidence"]

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

def decide_price(cost: float, min_competitor: float | None) -> dict:
    """Повертає рішення про ціну для одного товару."""
    breakeven = cost / (1 - COMMISSION)
    target = breakeven * (1 + MIN_PROFIT)

    if min_competitor is None:
        price = round(cost * NO_COMPETITOR_MULT, 2)
        category = "D"
        margin_pct = round((price * (1 - COMMISSION) - cost) / cost * 100, 1)
        return {"breakeven": round(breakeven, 2), "price": price, "category": category, "margin_pct": margin_pct}

    candidate = round(min_competitor * UNDERCUT_K, 2)
    margin_pct = round((candidate * (1 - COMMISSION) - cost) / cost * 100, 1)

    if candidate < breakeven:
        return {"breakeven": round(breakeven, 2), "price": None, "category": "C", "margin_pct": margin_pct}
    elif candidate >= target:
        return {"breakeven": round(breakeven, 2), "price": candidate, "category": "A", "margin_pct": margin_pct}
    else:
        return {"breakeven": round(breakeven, 2), "price": candidate, "category": "B", "margin_pct": margin_pct}


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
    собівартістю <300 грн майже завжди потрапляють у категорії A/B (є шанс
    на прибуток), тоді як товари >700 грн частіше опиняються в категорії C.
    Уцінені/пошкоджені товари виключаються одразу (немає сенсу шукати
    конкурента на брак).
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
    counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                counts[row["category"]] = counts.get(row["category"], 0) + 1
    print(f"Оброблено всього: {len(processed)}")
    print(f"  A (undercut, >=25% прибутку): {counts['A']}")
    print(f"  B (undercut, <25% прибутку):  {counts['B']}")
    print(f"  C (пропуск, збиток):          {counts['C']}")
    print(f"  D (конкурента немає):         {counts['D']}")
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


def cmd_record(pid: str, min_competitor_raw: str, match_confidence: str) -> None:
    catalog = get_catalog()
    item = catalog.get(pid)
    if item is None:
        print(f"Товар {pid} не знайдено в каталозі Toysi (можливо, зник з живого фіда постачальника).")
        sys.exit(1)

    cost = float(item.get("price") or 0)
    min_competitor = None if min_competitor_raw.lower() == "none" else float(min_competitor_raw)

    decision = decide_price(cost, min_competitor)
    row = {
        "id": pid,
        "name": item.get("name", ""),
        "cost": f"{cost:.2f}",
        "min_competitor": f"{min_competitor:.2f}" if min_competitor is not None else "",
        "breakeven": f"{decision['breakeven']:.2f}",
        "price": f"{decision['price']:.2f}" if decision["price"] is not None else "",
        "margin_pct": f"{decision['margin_pct']:.1f}",
        "category": decision["category"],
        "match_confidence": match_confidence,
    }
    append_result(row)

    cp = _bump_checkpoint_for_today()
    cp["processed_today"] = cp.get("processed_today", 0) + 1
    save_checkpoint(cp)

    print(f"[{decision['category']}] {item.get('name','')[:50]} -> "
          f"ціна={row['price'] or 'ПРОПУСК'} (прибуток {decision['margin_pct']}%)")


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

    args = ap.parse_args()

    if args.cmd == "status":
        cmd_status()
    elif args.cmd == "seed-test":
        cmd_seed_test()
    elif args.cmd == "next-batch":
        cmd_next_batch(args.limit)
    elif args.cmd == "record":
        cmd_record(args.id, args.min_competitor, args.confidence)


if __name__ == "__main__":
    main()
