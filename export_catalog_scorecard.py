#!/usr/bin/env python3
"""export_catalog_scorecard.py — єдиний зріз якості каталогу для ролі «SEO + якість каталогу».

НАВІЩО (2026-08-20, спільне ТЗ SEO+Код за задачею власника «обʼєднати товарознавця з SEO,
наповнювати каталог правильно, формувати топ товарів»): склеює наявні локальні джерела в ОДИН
рядок на SKU, з якого SEO рахує ДВА зрізи:
  1) ЧЕРГА НАПОВНЕННЯ (fill_priority) — «бідна картка, на яку вже є попит» → що доводити першим,
     замість сліпого рівномірного наповнення 6000 карток;
  2) ТОП ТОВАРІВ (top_score) — попит × наявність × готовність картки → герої для SMM-постів,
     першочергових відгуків, сезонних добірок.

ДЖЕРЕЛА (усі локальні, без фінансів — файл лягає у спільну папку, cost/margin СЮДИ НЕ ЙДУТЬ):
  • Prom-знімок `.local_secrets/prom_full_catalog.json` (PR #337): product_attrs_count,
    category_attrs_count, quality_steps, position, orders_count, product_opinion_count, presence.
  • `reports/prom_seo_pilot_stats_*.csv` (prom_analytics): product_views (реальний попит на картку).
  • brand/GTIN (Google-готовність) — ФАЗА 2: merchant-фід генериться на VPS, локально його нема;
    поки колонка None і у completeness_gap НЕ штрафуємо за невідоме (не гадаємо).

«Топ за ПРИБУТКОМ» тут свідомо НЕ рахується — маржу/собівартість тримає Код/КОДВ; вони наклада-
ють свою маржу на цей попит-зріз окремо (щоб фінанси не текли у спільний канал).

ЗАПУСК:
    python export_catalog_scorecard.py                 # reports/catalog_scorecard_YYYY-MM-DD.csv + зведення
    python export_catalog_scorecard.py --top 20        # більше рядків у друкованому зведенні
    python export_catalog_scorecard.py --out path.csv   # свій шлях виходу
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
SNAPSHOT = Path(os.environ.get("PROM_CABINET_STATE_FILE",
                               str(BASE_DIR / ".local_secrets" / "prom_full_catalog.json")))
REPORTS_DIR = BASE_DIR / "reports"


MERCHANT_CANDIDATES = BASE_DIR / "rozetka_merchant_candidates.json"


def _latest_stats_csv() -> Path | None:
    files = sorted(REPORTS_DIR.glob("prom_seo_pilot_stats_*.csv"))
    return files[-1] if files else None


def load_merchant_signals(snapshot_skus: set) -> dict:
    """{sku: 'competitive'|'unique'} для товарів, які товарознавець (rozetka_merchant_agent) визнав
    вартими Rozetka І які присутні на Prom (перетин ~90/392). Легка КРОС-підказка: наповнювати
    першими Prom-картки товарів, уже доведено конкурентних на іншій площадці. БЕЗ фінполів — беремо
    ЛИШЕ decision (cost/ціни з файлу товарознавця свідомо НЕ читаємо; файл у спільну папку). Best-effort."""
    if not MERCHANT_CANDIDATES.exists():
        return {}
    try:
        data = json.loads(MERCHANT_CANDIDATES.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    cands = (data.get("candidates") if isinstance(data, dict) else None) or []
    out = {}
    for c in cands:
        pid = str(c.get("pid") or "").strip()
        if pid and pid in snapshot_skus:   # pid збігається з Prom sku для перетину
            out[pid] = c.get("decision") or ""
    return out


def load_product_views(stats_path: Path | None) -> dict:
    """{sku: product_views} — беремо МАКСИМУМ product_views по SKU (найкращий наявний період).
    Рядки-коментарі (#) пропускаємо. Немає файлу → порожньо (demand впаде на orders_count)."""
    views = {}
    if not stats_path or not stats_path.exists():
        return views
    with open(stats_path, encoding="utf-8") as f:
        rows = [ln for ln in f if not ln.lstrip().startswith("#")]
    for r in csv.DictReader(rows):
        sku = str(r.get("sku") or "").strip()
        if not sku:
            continue
        try:
            pv = int(float(r.get("product_views") or 0))
        except (ValueError, TypeError):
            pv = 0
        views[sku] = max(views.get(sku, 0), pv)
    return views


def _norm(values: dict) -> dict:
    """min-max нормалізація значень словника у [0,1]. Усі однакові → 0 (нема сигналу)."""
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi <= lo:
        return {k: 0.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def build_scorecard(snapshot: dict, product_views: dict, merchant_signals: dict = None) -> list:
    merchant_signals = merchant_signals or {}
    items = snapshot.get("items") or {}
    # сирі сигнали попиту для нормалізації по всьому avail-набору
    raw_pv, raw_orders = {}, {}
    base = {}
    for sku, p in items.items():
        presence = p.get("presence")
        avail = presence in ("avail", "available", True)
        cat_attrs = p.get("category_attrs_count") or 0
        prod_attrs = p.get("product_attrs_count") or 0
        completeness = (prod_attrs / cat_attrs) if cat_attrs else 1.0  # нема атрибутів у категорії → нема чого гапити
        completeness = min(completeness, 1.0)
        has_desc = bool((p.get("quality_steps") or {}).get("description"))
        pv = int(product_views.get(str(sku), 0))
        orders = int(p.get("orders_count") or 0)
        base[sku] = {
            "sku": sku,
            "category_id": p.get("category_id"),
            "presence": presence,
            "avail": avail,
            "product_attrs_count": prod_attrs,
            "category_attrs_count": cat_attrs,
            "completeness": round(completeness, 3),
            "has_description": has_desc,
            "position": p.get("position"),
            "product_views": pv,
            "orders_count": orders,
            "product_opinion_count": p.get("product_opinion_count") or 0,
            "rozetka_signal": merchant_signals.get(str(sku), ""),   # competitive/unique на Rozetka (крос-підказка)
        }
        if avail:
            raw_pv[sku] = pv
            raw_orders[sku] = orders
    npv, nord = _norm(raw_pv), _norm(raw_orders)
    out = []
    for sku, b in base.items():
        # demand рахуємо лише для avail (нормалізація по avail-набору); не-avail → demand 0
        demand = (npv.get(sku, 0.0) + nord.get(sku, 0.0)) if b["avail"] else 0.0
        # completeness_gap: дірка атрибутів + 0.5 якщо нема опису. brand/GTIN — фаза 2 (не штрафуємо невідоме).
        gap = (1.0 - b["completeness"]) + (0.5 if not b["has_description"] else 0.0)
        fill_priority = demand * gap
        card_readiness = b["completeness"]
        top_score = demand * (1.0 if b["avail"] else 0.0) * card_readiness
        b.update({
            "demand": round(demand, 4),
            "completeness_gap": round(gap, 4),
            "fill_priority": round(fill_priority, 5),
            "top_score": round(top_score, 5),
            "has_brand_gtin": "",  # фаза 2 (merchant-фід на VPS)
        })
        out.append(b)
    return out


_COLUMNS = ["sku", "category_id", "presence", "product_attrs_count", "category_attrs_count",
            "completeness", "has_description", "has_brand_gtin", "rozetka_signal", "position",
            "product_views", "orders_count", "product_opinion_count", "demand", "completeness_gap",
            "fill_priority", "top_score"]


def write_csv(rows: list, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # сортуємо за fill_priority спадання (черга наповнення — головний вихід); SEO пересортує за top_score
    rows_sorted = sorted(rows, key=lambda r: r["fill_priority"], reverse=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows_sorted)


def print_summary(rows: list, top: int) -> None:
    avail = [r for r in rows if r["avail"]]
    empty_desc = sum(1 for r in avail if not r["has_description"])
    no_attrs = sum(1 for r in avail if r["product_attrs_count"] == 0)
    with_demand = sum(1 for r in avail if r["demand"] > 0)
    print(f"\n=== SCORECARD: {len(rows)} SKU ({len(avail)} avail) ===")
    print(f"без опису: {empty_desc} | без жодної характеристики: {no_attrs} | з ненульовим попитом: {with_demand}")
    fill = sorted(avail, key=lambda r: r["fill_priority"], reverse=True)[:top]
    print(f"\n--- ЧЕРГА НАПОВНЕННЯ (топ-{top} fill_priority = попит×бідність) ---")
    for r in fill:
        print(f"  sku {r['sku']:<10} fill={r['fill_priority']:.4f} "
              f"attrs {r['product_attrs_count']}/{r['category_attrs_count']} "
              f"desc={'так' if r['has_description'] else 'НЕМА'} views={r['product_views']}")
    tops = sorted(avail, key=lambda r: r["top_score"], reverse=True)[:top]
    print(f"\n--- ТОП ТОВАРІВ (топ-{top} top_score = попит×наявність×готовність) ---")
    for r in tops:
        print(f"  sku {r['sku']:<10} top={r['top_score']:.4f} "
              f"views={r['product_views']} orders={r['orders_count']} готовність={r['completeness']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Зріз якості каталогу (черга наповнення + топ товарів) для SEO.")
    ap.add_argument("--snapshot", help=f"Prom-знімок (за замовч. {SNAPSHOT})")
    ap.add_argument("--stats", help="prom_seo_pilot_stats CSV (за замовч. найсвіжіший у reports/)")
    ap.add_argument("--out", help="шлях виходу CSV")
    ap.add_argument("--top", type=int, default=10, help="скільки рядків у друкованому зведенні")
    a = ap.parse_args()

    snap_path = Path(a.snapshot) if a.snapshot else SNAPSHOT
    if not snap_path.exists():
        raise SystemExit(f"[scorecard] нема знімка {snap_path} — спершу `prom_cabinet_catalog.py --summary`")
    snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    if "product_attrs_count" not in next(iter(snapshot.get("items", {}).values()), {}):
        raise SystemExit("[scorecard] у знімку нема полів повноти (#337) — онови знімок новим кодом")

    stats_path = Path(a.stats) if a.stats else _latest_stats_csv()
    product_views = load_product_views(stats_path)
    print(f"[scorecard] знімок: {snapshot.get('at')} ({snapshot.get('count')} SKU) | "
          f"product_views із: {stats_path.name if stats_path else 'НЕМА (demand лише orders)'} "
          f"({len(product_views)} SKU)")

    merchant_signals = load_merchant_signals(set(str(s) for s in snapshot.get("items", {})))
    print(f"[scorecard] крос-сигнал Rozetka (товарознавець ∩ Prom): {len(merchant_signals)} товарів")
    rows = build_scorecard(snapshot, product_views, merchant_signals)
    out_path = Path(a.out) if a.out else REPORTS_DIR / f"catalog_scorecard_{datetime.now().strftime('%Y-%m-%d')}.csv"
    write_csv(rows, out_path)
    print(f"[scorecard] записано: {out_path} ({len(rows)} рядків, сорт. за fill_priority)")
    print_summary(rows, a.top)


if __name__ == "__main__":
    main()
