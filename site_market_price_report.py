#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
site_market_price_report.py — ЧИСТЕ ЧИТАННЯ: що змінить правило «ринкова ціна − 3 ₴» (рішення власника 16.11.2026) для сайту ДО того, як вмикати ворота SITE_MARKET_PRICE.
Для кожного товару каталогу Toysi, що є в знімку Google (поле bench, ≤7 діб): стара ціна (формула ×1,5 / підлога) → нова (max(підлога, контрольна−3)).
Виводить: скільки товарів дешевшає / дорожчає / без змін, скільки впирається в підлогу, зміну суми цін каталогу (по товарах у наявності), розподіл контрольна/стара,
і 10 найбільших підвищень та зниження (id, стара, нова, контрольна) — щоб побачити сумнівні збіги. Нічого не пише в каталог/сайт/.env; лише друкує й кладе текст у reports/gmc/.
Запуск (VPS або де є toysi_catalog_cache.json і знімок): venv/bin/python3 site_market_price_report.py
"""
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _stock_positive(it) -> bool:
    try:
        return float(it.get("stock") or 0) > 0
    except (TypeError, ValueError):
        return False


def compute(catalog: dict, benchmarks: dict, formula_fn=None, market_fn=None) -> dict:
    """Чиста функція (тестується): catalog {pid: item}, benchmarks {offerId: грн} → зведення. formula_fn(item)→стара ціна, market_fn(item, floor, bm)→нова або None."""
    import competitor_pricing as cp
    formula_fn = formula_fn or cp.site_retail_price
    market_fn = market_fn or cp.site_market_price
    rows = []
    for pid, it in catalog.items():
        if not isinstance(it, dict):
            continue
        it = dict(it)
        it.setdefault("id", str(pid))
        oid = cp.market_offer_id(it)
        b = benchmarks.get(oid)
        if not b:
            continue
        try:
            cost = cp.real_toysi_cost(it)
        except (ValueError, TypeError):
            continue
        if not cost or cost <= 0:
            continue
        floor_price = int(math.ceil(cost * (1 + cp.SITE_MIN_MARGIN)))
        old = int(formula_fn(it))
        new = market_fn(it, floor_price, benchmarks)
        if new is None or old <= 0:
            continue
        rows.append({"id": oid, "old": old, "new": int(new), "bench": float(b), "floor": floor_price, "instock": _stock_positive(it)})
    return summarize(rows)


def summarize(rows: list) -> dict:
    out = {"units": len(rows), "cheaper": 0, "pricier": 0, "same": 0, "at_floor": 0, "above_bench_minus_3": 0}
    ins = [r for r in rows if r["instock"]]
    out["units_instock"] = len(ins)
    out["sum_old_instock"] = sum(r["old"] for r in ins)
    out["sum_new_instock"] = sum(r["new"] for r in ins)
    for r in rows:
        if r["new"] < r["old"]:
            out["cheaper"] += 1
        elif r["new"] > r["old"]:
            out["pricier"] += 1
        else:
            out["same"] += 1
        if r["new"] == r["floor"] and r["floor"] > math.floor(r["bench"] - 3 + 1e-9):
            out["at_floor"] += 1
        if r["new"] > math.floor(r["bench"] - 3 + 1e-9):
            out["above_bench_minus_3"] += 1
    ratios = sorted(r["bench"] / r["old"] for r in rows if r["old"] > 0)

    def q(p):
        return round(ratios[int((len(ratios) - 1) * p)], 3) if ratios else None
    out["bench_over_old"] = {"p05": q(.05), "p25": q(.25), "p50": q(.5), "p75": q(.75), "p95": q(.95), "p99": q(.99), "max": round(ratios[-1], 3) if ratios else None}
    inc = sorted(rows, key=lambda r: r["new"] - r["old"], reverse=True)[:10]
    dec = sorted(rows, key=lambda r: r["new"] - r["old"])[:10]
    out["top_up"] = [(r["id"], r["old"], r["new"], r["bench"]) for r in inc]
    out["top_down"] = [(r["id"], r["old"], r["new"], r["bench"]) for r in dec]
    return out


def render(s: dict, snapshot_generated: str = "") -> str:
    d_sum = s["sum_new_instock"] - s["sum_old_instock"]
    pct = (d_sum / s["sum_old_instock"] * 100.0) if s["sum_old_instock"] else 0.0
    L = [f"Ринкова ціна −3 ₴ — що зміниться (читання, ворота SITE_MARKET_PRICE ще НЕ ввімкнені). Знімок Google: {snapshot_generated or '?'}; звіт {datetime.now():%Y-%m-%d %H:%M}.",
         f"ОДИНИЦЯ — товар каталогу Toysi з контрольною ціною: {s['units']} (з них у наявності {s['units_instock']}).",
         f"дешевшає: {s['cheaper']} · дорожчає: {s['pricier']} · без змін: {s['same']} · впирається в підлогу (собівартість×1,03): {s['at_floor']}",
         f"сума цін товарів у наявності з контрольною: було {s['sum_old_instock']} ₴ → стане {s['sum_new_instock']} ₴ (різниця {d_sum:+d} ₴, {pct:+.1f} %)",
         f"контрольна / стара ціна (розподіл): {s['bench_over_old']}",
         "найбільші підвищення (id, стара, нова, контрольна): " + "; ".join(f"{a} {b}→{c} (к. {d})" for a, b, c, d in s["top_up"]),
         "найбільші зниження (id, стара, нова, контрольна): " + "; ".join(f"{a} {b}→{c} (к. {d})" for a, b, c, d in s["top_down"])]
    return "\n".join(L)


def main() -> int:
    import competitor_pricing as cp
    import gmc_price_competitiveness as g
    bm = g.load_benchmarks()
    if not bm:
        print("НЕМАЄ свіжих контрольних цін у знімку (поле bench відсутнє або знімок старший за 7 діб): спершу оновити знімок (feed-pipeline / gmc_price_competitiveness.py).")
        return 1
    cache = Path(os.environ.get("TOYSI_CATALOG_CACHE", str(cp.CATALOG_CACHE_FILE)))
    catalog = json.loads(cache.read_text(encoding="utf-8"))
    os.environ["SITE_MARKET_PRICE"] = "1"       # лише в цьому процесі: рахуємо «нову» ціну
    cp._MARKET_BENCH_CACHE = bm

    def old_formula(it):
        os.environ["SITE_MARKET_PRICE"] = "0"
        try:
            return cp.site_retail_price(it)
        finally:
            os.environ["SITE_MARKET_PRICE"] = "1"
    s = compute(catalog, bm, formula_fn=old_formula)
    gen = ""
    try:
        gen = json.loads(Path(g.SNAPSHOT).read_text(encoding="utf-8")).get("generated", "")
    except Exception:  # noqa: BLE001
        pass
    text = render(s, gen)
    print(text)
    try:
        out = Path(g.OUT_DIR) / f"market_price_report_{datetime.now():%Y-%m-%d}.txt"
        out.write_text(text + "\n", encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[report] файл не записано: {type(e).__name__}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
