"""competitor_match_review.py — зріз для перевірки ЯКОСТІ матчингу конкурентів (заявка SEO).

Перед видаленням неконкурентних позицій (floor, >20% над ринком) SEO має звірити ОЧИМА, що
матч конкурента правильний — бо в даних є хибні співставлення («наша 210 грн проти конкурента
6 грн» = інший товар, не наш програш). Цей зріз дає топ-N за перевищенням ринку + прапорець
ймовірного мисматчу, щоб SEO переглянув вибірку, а не чистив наосліп.

ФІНАНСИ: cost/margin у зріз НЕ виводимо (правило витоку). Лише наша роздрібна ціна, ціна
конкурента, % перевищення, competitor_key (де є).

  python competitor_match_review.py            # топ-150 у stdout + CSV reports/competitor_match_review.csv
  python competitor_match_review.py --top 300
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "prom_competitor_price_state.json"
# Джерело назв (best-effort — щоб SEO бачив, ЩО за товар). Ключі можуть не збігтись (pid vs code);
# якщо назви нема — лишаємо порожньою, SEO дивиться по SKU.
_NAME_SOURCES = [BASE_DIR / ".local_secrets" / "toysi_catalog_cache.json",
                 BASE_DIR / "toysi_catalog_cache.json"]
MISMATCH_RATIO = 0.30   # конкурент < 30% нашої ціни → майже напевно ІНШИЙ товар (запобіжник SEO)
OVER_MARKET_MIN = 20.0  # % перевищення ринку (поріг заміни, обраний власником: 519 позицій >20%)


def _load_names() -> dict:
    for p in _NAME_SOURCES:
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = d.get("items") if isinstance(d, dict) and "items" in d else d
        out = {}
        src = items.values() if isinstance(items, dict) else (items or [])
        for it in src:
            if isinstance(it, dict):
                key = str(it.get("id") or it.get("sku") or it.get("code") or "")
                if key and it.get("name"):
                    out[key] = it["name"]
        if out:
            return out
    return {}


def build_slice(top: int = 150) -> list:
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    names = _load_names()
    rows = []
    for pid, e in state.items():
        if not (pid.isdigit() and isinstance(e, dict)):
            continue
        our = e.get("price")
        comp = e.get("competitor_price")
        if not our or not comp or comp <= 0:
            continue
        over = (our / comp - 1) * 100
        if over <= OVER_MARKET_MIN:
            continue
        rows.append({
            "sku": pid,
            "name": names.get(pid, ""),
            "our_price": round(our, 2),
            "competitor_price": round(comp, 2),
            "over_market_pct": round(over, 1),
            "likely_mismatch": "ТАК" if comp < MISMATCH_RATIO * our else "",
            "competitor_key": e.get("competitor_key") or "",
        })
    rows.sort(key=lambda r: -r["over_market_pct"])
    return rows[:top]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=150, help="скільки позицій у зрізі (за спаданням перевищення)")
    ap.add_argument("--out", default=str(BASE_DIR / "reports" / "competitor_match_review.csv"))
    a = ap.parse_args()
    rows = build_slice(a.top)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["sku", "name", "our_price", "competitor_price", "over_market_pct", "likely_mismatch", "competitor_key"]
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    mism = sum(1 for r in rows if r["likely_mismatch"])
    with_key = sum(1 for r in rows if r["competitor_key"])
    print(f"[MatchReview] Зріз топ-{len(rows)} за перевищенням ринку (>{OVER_MARKET_MIN:.0f}%) → {out}")
    print(f"[MatchReview] ⚠️ ймовірних мисматчів (конкурент <{MISMATCH_RATIO*100:.0f}% нашої): {mism}")
    print(f"[MatchReview] з competitor_key (URL/ключ для звірки): {with_key}/{len(rows)}")
    print("\nТоп-15 за перевищенням (SEO — звір очима, особливо ⚠️):")
    for r in rows[:15]:
        flag = " ⚠️МИСМАТЧ?" if r["likely_mismatch"] else ""
        print(f"  SKU {r['sku']}: {r['our_price']} vs {r['competitor_price']} (+{r['over_market_pct']:.0f}%){flag}  {r['name'][:35]}")


if __name__ == "__main__":
    main()
