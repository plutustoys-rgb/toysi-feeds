"""Експорт маржі/внеску по SKU з КАТЕГОРІЄЮ постачальника — інструмент для
відбору асортименту за внеском (запит Консультанта 2026-09-04, CONSULTANT_CHANNEL.md).

НАВІЩО: раніше per-SKU margin-зріз давався без `category_name`, і відбір
«дорогих» категорій робився на око по медіані. Цей скрипт джойнить каталог
Toysi (cost + category_name) з живою комісійною моделлю (`competitor_pricing`)
і рахує ВНЕСОК із замовлення по кожному наявному SKU, а також медіану внеску
по кожній категорії — щоб відбір ішов за числом, а не за оцінкою.

ОСНОВА РОЗРАХУНКУ (прозоро й параметрично):
  cost   = real_toysi_cost(item)  — фактична собівартість (знижка Toysi + Збірка),
           а НЕ сира каталожна price (та систематично завищена — див. докстрінг
           real_toysi_cost).
  retail = cost * markup           — ПРИПУЩЕНА роздрібна за середнім множником
           (дефолт 1.393 — блендед-факт серпня, яким Консультант рахував таблицю).
           Це НЕ жива ціна з конкурентного репрайсера (та потребує ціни конкурента
           по кожному SKU, якої тут нема offline) — це узгоджена з аналізом
           Консультанта база, щоб цифри мирилися. Множник змінюється --markup.
  commission = get_platform_commission('prom', category_name) — ЖИВА категорійна
           ставка, ВЖЕ з урахуванням PROSALE_TIER (econom ×0.5). Тобто самокати
           тут 5.6%, не 11.2% — бо саме так рахує бойовий флор.
  payment = PAYMENT_COMMISSION['prom'] (0.037).
  contribution (внесок) = retail - cost - retail*commission - retail*payment.

БЕЗПЕКА ФІНДАНИХ: вихідний CSV містить cost/маржу — лягає ЛОКАЛЬНО у reports/,
у спільну/публічну гілку (feed-data) НЕ публікується (запобіжник публічного
витоку, як price_state_redact). Агрегати по категоріях (без сирого cost) —
відкриті між агентами, їх друкуємо в stdout для каналу.

ВИКОРИСТАННЯ:
    python export_margin_by_category.py                    # усі наявні SKU, markup 1.393
    python export_margin_by_category.py --markup 1.45      # інший множник
    python export_margin_by_category.py --min-contribution 100   # лише внесок >= 100 грн
    python export_margin_by_category.py --out path.csv     # свій шлях
    python export_margin_by_category.py --top-categories 25 # скільки категорій у зведенні
"""

from __future__ import annotations

import argparse
import csv
import statistics
from datetime import datetime
from pathlib import Path

import parser as toysi_parser
import competitor_pricing as cp

BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "reports"

DEFAULT_MARKUP = 1.393  # блендед-факт серпня (roздріб/закупівля), яким рахував Консультант

_COLUMNS = [
    "toysi_id", "name", "category_id", "category_name", "stock",
    "cost", "retail", "commission_pct", "payment_pct", "contribution",
]


def _stock_of(item: dict) -> int:
    """Наявність із каталогу Toysi (ключ 'stock'; толерантно до типів)."""
    raw = item.get("stock")
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def build_rows(catalog: dict, markup: float) -> list[dict]:
    """Один рядок на НАЯВНИЙ SKU з розрахованим внеском. OOS (stock<=0) пропускаємо
    (їх не продаємо, у відбір не йдуть). Нульова собівартість — теж пропуск."""
    rows: list[dict] = []
    for item in catalog.values():
        if _stock_of(item) <= 0:
            continue
        cost = cp.real_toysi_cost(item)
        if not cost or cost <= 0:
            continue
        category_name = item.get("category_name") or ""
        retail = round(cost * markup, 2)
        commission = cp.get_platform_commission("prom", category_name=category_name)
        payment = cp.PAYMENT_COMMISSION.get("prom", 0.0)
        contribution = round(retail - cost - retail * commission - retail * payment, 2)
        rows.append({
            "toysi_id": item.get("id"),
            "name": item.get("name"),
            "category_id": item.get("category_id"),
            "category_name": category_name,
            "stock": _stock_of(item),
            "cost": round(cost, 2),
            "retail": retail,
            "commission_pct": round(commission * 100, 2),
            "payment_pct": round(payment * 100, 2),
            "contribution": contribution,
        })
    return rows


def category_summary(rows: list[dict]) -> list[dict]:
    """Агрегат по category_name: к-сть наявних SKU + медіана внеску + медіана роздрібу.
    Сортування — за медіаною внеску спадно (найдорожчі за внеском зверху)."""
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["category_name"] or "(без категорії)", []).append(r)
    summary = []
    for cat, items in by_cat.items():
        contribs = [i["contribution"] for i in items]
        retails = [i["retail"] for i in items]
        summary.append({
            "category_name": cat,
            "sku_in_stock": len(items),
            "median_contribution": round(statistics.median(contribs), 2),
            "median_retail": round(statistics.median(retails), 2),
            "commission_pct": items[0]["commission_pct"],
        })
    summary.sort(key=lambda s: s["median_contribution"], reverse=True)
    return summary


def write_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows_sorted = sorted(rows, key=lambda r: r["contribution"], reverse=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows_sorted)


def main() -> None:
    ap = argparse.ArgumentParser(description="Експорт внеску по SKU з категорією постачальника")
    ap.add_argument("--markup", type=float, default=DEFAULT_MARKUP,
                    help=f"множник роздріб/закупівля (дефолт {DEFAULT_MARKUP})")
    ap.add_argument("--min-contribution", type=float, default=None,
                    help="лишити у CSV лише SKU з внеском >= X грн")
    ap.add_argument("--top-categories", type=int, default=30,
                    help="скільки категорій показати у зведенні stdout")
    ap.add_argument("--out", default=None, help="шлях виходу CSV")
    a = ap.parse_args()

    catalog = toysi_parser.fetch_toysi_catalog()
    rows = build_rows(catalog, a.markup)
    summary = category_summary(rows)  # зведення по ПОВНОМУ наявному набору (до відсічки)

    if a.min_contribution is not None:
        rows = [r for r in rows if r["contribution"] >= a.min_contribution]

    out_path = Path(a.out) if a.out else REPORTS_DIR / f"margin_by_category_{datetime.now():%Y-%m-%d}.csv"
    write_csv(rows, out_path)

    total_stock = sum(s["sku_in_stock"] for s in summary)
    print(f"Наявних SKU (stock>0, cost>0): {total_stock}; markup={a.markup}")
    print(f"CSV (ЛОКАЛЬНО, не публікується): {out_path}  — рядків: {len(rows)}")
    if a.min_contribution is not None:
        print(f"  (застосовано відсічку внесок >= {a.min_contribution} грн)")
    print()
    print("ТОП категорій за медіаною внеску (агрегати — відкриті між агентами):")
    print(f"{'категорія':<34}{'SKU':>5}{'ком.%':>7}{'мед.внесок':>12}{'мед.роздріб':>13}")
    for s in summary[:a.top_categories]:
        print(f"{s['category_name'][:33]:<34}{s['sku_in_stock']:>5}{s['commission_pct']:>7.1f}"
              f"{s['median_contribution']:>12.1f}{s['median_retail']:>13.1f}")


if __name__ == "__main__":
    main()
