"""promo_margin_guard.py — щоденна звірка маржі АКТИВНИХ промо-SKU із ЖИВОЮ собівартістю Toysi.

НАВІЩО (2026-08-26, наказ власника): у `promo_freeze` заморожено базові ціни на час акцій Prom
(«Сезонні знижки», -5%). Заморозка тримає ціну СТАБІЛЬНОЮ, але собівартість Toysi ДРЕЙФУЄ
(персональна знижка, «Збірка», зміни каталогу) весь час вікна. Floor-гард у фіді перевіряє
БАЗОВУ ціну, а Prom продає за БАЗА×0.95 — тобто теоретично товар може стати збитковим на
−5%, а floor цього не побачить (він дивиться базу). Цей гвард щодня рахує РЕАЛЬНУ маржу на
ціні продажу (база×0.95) з ЖИВОЮ собівартістю й комісією — ідентично фіду
(`real_toysi_cost` + `compute_total_commission`, ті самі функції, не своя формула) — і піднімає
збиткові/тонкі SKU у критичний календар (пише min-маржу в balance_history.jsonl → подія
`promo_margin` у critical_events.json). Самодіагностика: звіт називає КОЖЕН проблемний SKU з
числами (правило self-diagnosing-alerts).

ВАЖЛИВО: гвард нічого не МІНЯЄ — лише читає й сигналить. Реальний захист від збитку — floor-гард
у фіді (не публікує нижче собівартості); гвард дає ВИДИМІСТЬ наперед: хто став тонким/вилетить.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import promo_freeze
import competitor_pricing as cp
from competitor_pricing import real_toysi_cost, compute_total_commission
from parser import fetch_toysi_catalog

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
OUT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (BASE_DIR / "reports"))
HISTORY_FILE = OUT_DIR / "balance_history.jsonl"           # той самий, що читає critical_watch
STATUS_FILE = OUT_DIR / "promo_margin_guard_status.json"
CACHE_FILE = BASE_DIR / "prom_category_cache.json"

PROM_SEASONAL_DISCOUNT = 0.05   # Prom «Сезонні знижки» застосовує -5% до базової ціни
THIN_PCT = 5.0                  # маржа на собівартість нижче цього = «тонка» (попередження)


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def evaluate(today: date = None, catalog: dict = None) -> dict:
    """Рахує маржу кожного активного замороженого SKU на ціні продажу (база×0.95) із
    живою собівартістю. Повертає {active, rows, at_risk, loss, thin, gone, oos,
    min_net_pct, min_sku}. catalog можна впорснути (тест); інакше — живий fetch."""
    today = today or date.today()
    active = promo_freeze.load_active_freeze(today)  # {sku: заморожена БАЗОВА ціна}
    result = {"today": today.isoformat(), "active": len(active), "rows": [],
              "at_risk": [], "loss": [], "thin": [], "gone": [], "oos": [],
              "min_net_pct": None, "min_sku": None}
    if not active:
        return result
    if catalog is None:
        catalog = fetch_toysi_catalog()
    cache = _load_cache()

    for sku, base in sorted(active.items()):
        item = catalog.get(str(sku))
        if not item:
            result["gone"].append(str(sku))  # зник з каталогу Toysi (вилетить з акції)
            continue
        try:
            stock = int(float(item.get("stock") or 0))
        except (TypeError, ValueError):
            stock = 0
        cost = real_toysi_cost(item)
        prom_cat_raw = (cache.get(str(sku)) or {}).get("category_id")
        try:
            prom_cat = int(prom_cat_raw) if prom_cat_raw else None
        except (TypeError, ValueError):
            prom_cat = None
        cat_name = item.get("category_name") or ""
        # звідки комісія: реальна Prom-категорія (id) / збіг за назвою / неточний дефолт 20%.
        # Дефолт = ми НЕ знаємо реальної категорії SKU → маржа рахована консервативно,
        # алерт має це сказати (self-diagnosing), інакше «збиток» може бути хибним.
        if prom_cat is not None and prom_cat in cp.PROM_CATEGORY_ID_COMMISSION:
            comm_source = "id"
        elif cat_name.strip().lower() in cp.PROM_CATEGORY_COMMISSION:
            comm_source = "назва"
        else:
            comm_source = "ДЕФОЛТ20%"
        disc_price = round(float(base) * (1 - PROM_SEASONAL_DISCOUNT), 2)
        comm = compute_total_commission("prom", cat_name, disc_price, prom_cat)
        net = round(disc_price * (1 - comm) - cost, 2)
        net_pct = round(net / cost * 100, 1) if cost > 0 else None
        row = {"sku": str(sku), "base": round(float(base), 2), "disc": disc_price,
               "cost": round(cost, 2), "comm_pct": round(comm * 100, 2),
               "comm_source": comm_source, "net": net, "net_pct": net_pct, "stock": stock,
               "name": (item.get("name") or "")[:48]}
        result["rows"].append(row)
        if stock <= 0:
            result["oos"].append(row)
        if net < 0:
            result["loss"].append(row)
        elif net_pct is not None and net_pct < THIN_PCT:
            result["thin"].append(row)

    # at_risk = усе, що варте уваги (збиток / тонке / зник / OOS), найгірше першим
    seen = set()
    for bucket in ("loss", "thin"):
        for r in result[bucket]:
            if r["sku"] not in seen:
                seen.add(r["sku"]); result["at_risk"].append(r)
    result["at_risk"].sort(key=lambda r: (r["net_pct"] if r["net_pct"] is not None else -999))

    pcts = [r["net_pct"] for r in result["rows"] if r["net_pct"] is not None]
    if pcts:
        worst = min(result["rows"], key=lambda r: (r["net_pct"] if r["net_pct"] is not None else 999))
        result["min_net_pct"] = worst["net_pct"]
        result["min_sku"] = worst["sku"]
    return result


def _write_outputs(result: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")
    STATUS_FILE.write_text(json.dumps({**result, "ts": now}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    # min-маржа → balance_history.jsonl (critical_watch читає як balance_threshold).
    # Пишемо лише коли є що (active>0 і порахувалось) — щоб не забивати «немає даних».
    if result["min_net_pct"] is not None:
        row = {"ts": now, "platform": "promo_guard", "min_net_pct": result["min_net_pct"],
               "active": result["active"], "at_risk": len(result["at_risk"]),
               "worst_sku": result["min_sku"]}
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _print_report(result: dict) -> None:
    print(f"[promo_margin_guard] {result['today']}: активних промо-SKU {result['active']}")
    if not result["active"]:
        print("  заморозок нема — нічого перевіряти.")
        return
    if result["min_net_pct"] is not None:
        print(f"  найтонша маржа: {result['min_net_pct']}% (SKU {result['min_sku']}) "
              f"[поріг тонкої {THIN_PCT}%]")
    for label, key in (("🔴 ЗБИТКОВІ", "loss"), ("🟡 ТОНКІ", "thin"),
                       ("📦 OOS (stock 0)", "oos"), ("❌ ЗНИКЛИ з Toysi", "gone")):
        bucket = result[key]
        if not bucket:
            continue
        print(f"  {label}: {len(bucket)}")
        for r in bucket:
            if isinstance(r, dict):
                warn = " ⚠️комісія-ДЕФОЛТ, звірити реальну" if r.get("comm_source") == "ДЕФОЛТ20%" else ""
                print(f"    {r['sku']} '{r['name']}' база {r['base']} −5%→{r['disc']} "
                      f"соб {r['cost']} комісія {r['comm_pct']}%({r.get('comm_source')}) → net {r['net']} ₴ "
                      f"({r['net_pct']}%) stock {r['stock']}{warn}")
            else:
                print(f"    {r} — нема в каталозі Toysi")
    if not (result["loss"] or result["thin"] or result["oos"] or result["gone"]):
        print("  ✅ усі активні промо-SKU здорові (маржа вища за поріг, у наявності).")


def main() -> int:
    ap = argparse.ArgumentParser(description="Щоденний margin-guard активних промо-SKU.")
    ap.add_argument("--dry", action="store_true", help="не писати status/історію, лише звіт")
    args = ap.parse_args()
    result = evaluate()
    _print_report(result)
    if not args.dry:
        _write_outputs(result)
    # exit-код: 2 якщо є збиткові (для розкладу/алерту), 0 інакше
    return 2 if result["loss"] else 0


if __name__ == "__main__":
    sys.exit(main())
