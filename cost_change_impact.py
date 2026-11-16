#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cost_change_impact.py — «що зміниться» від двох рішень власника 16.11.2026 (ЛИШЕ ЧИТАННЯ, нічого не пише крім reports/).

  П.1  собівартість за тегом <isImport> (true -> 15 %, false -> 5 %)  — ворота TOYSI_COST_BY_IMPORT
  П.2  +10 ₴ до собівартості Prom (PROM_ORDER_FEE_UAH)

Рахує на ЖИВОМУ каталозі Toysi (parser.fetch_toysi_catalog; потрібен TOYSI_API_KEY у .env) усі товари В НАЯВНОСТІ (stock > 0,
валідна ціна). ОДИНИЦЯ — ТОВАР (унікальний id каталогу). «Стара» = ворота TOYSI_COST_BY_IMPORT=0 і PROM_ORDER_FEE_UAH=0
(рівно те, що було до змін), «нова» = ворота 1 і 10 ₴.

Спостережені знижки (toysi_observed_discount_state.json) — файл VPS-only; локально його нема. Параметр --observed-txt приймає
вивантаження observed_discount_out.txt (рядки «pid|pct|observed_at» після «== all entries») і підставляє їх як спостережені
(тоді товари з відомою знижкою НЕ змінюються — як на VPS). Без нього — «верхня межа» змін (усі SKU без спостереження).

Запуск:  python cost_change_impact.py [--env C:\\шлях\\до\\.env] [--observed-txt файл] [--catalog-json кеш.json] [--out reports]
"""
import argparse
import json
import math
import os
import statistics
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
os.environ.pop("SITE_MARKET_PRICE", None)   # без ринкового правила — порівнюємо чисту формулу

HERE = Path(__file__).parent


def _load_observed_txt(path: Path) -> dict:
    out = {}
    started = False
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("== all entries"):
            started = True
            continue
        if not started or "|" not in line:
            continue
        parts = line.strip().split("|")
        if len(parts) != 3:
            continue
        pid, pct, at = parts
        try:
            out[pid] = {"pct": float(pct), "observed_at": at, "source_order": "observed_txt"}
        except ValueError:
            continue
    return out


def _stats(vals: list) -> dict:
    if not vals:
        return {"n": 0, "mean": 0.0, "median": 0.0, "p95": 0.0}
    s = sorted(vals)
    p95 = s[min(len(s) - 1, int(math.ceil(0.95 * len(s))) - 1)]
    return {"n": len(vals), "mean": round(statistics.mean(vals), 2), "median": round(statistics.median(vals), 2), "p95": round(p95, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", help="шлях до .env з TOYSI_API_KEY (якщо не поруч зі скриптом)")
    ap.add_argument("--observed-txt")
    ap.add_argument("--catalog-json", help="замість живого каталогу — кеш-файл (для налагодження)")
    ap.add_argument("--out", default=str(HERE / "reports"))
    a = ap.parse_args()

    if a.env:
        from dotenv import load_dotenv
        load_dotenv(a.env)
    import competitor_pricing as cp

    if a.catalog_json:
        catalog = json.loads(Path(a.catalog_json).read_text(encoding="utf-8"))
        src = f"кеш {a.catalog_json}"
    else:
        from parser import fetch_toysi_catalog
        catalog = fetch_toysi_catalog()
        src = "ЖИВИЙ каталог Toysi (parser.fetch_toysi_catalog)"
    if not catalog:
        print("Каталог порожній (немає TOYSI_API_KEY/мережі) — нічого рахувати.", file=sys.stderr)
        return 2

    if a.observed_txt:
        cp._observed_discount_cache = _load_observed_txt(Path(a.observed_txt))
        obs_note = f"спостережені знижки: {len(cp._observed_discount_cache)} pid з {a.observed_txt}"
    else:
        cp._observed_discount_cache = {}
        obs_note = "спостережених знижок НЕМАЄ (локально) — оцінка змін = верхня межа"

    def mode(gate: bool, fee: float):
        os.environ["TOYSI_COST_BY_IMPORT"] = "1" if gate else "0"
        cp.PROM_ORDER_FEE_UAH = fee

    try:
        import generate_eva_feed as geva
        eva_price = geva._eva_price
    except Exception as e:  # noqa: BLE001
        eva_price = None
        print(f"[warn] generate_eva_feed не імпортується ({type(e).__name__}) — EVA-ціна не рахується", file=sys.stderr)

    rows = []
    for it in catalog.values():
        if not isinstance(it, dict):
            continue
        try:
            stock = float(it.get("stock") or 0)
            price = float(it.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if stock <= 0 or price <= 0:
            continue
        cat = it.get("category_name") or ""
        r = {"id": str(it.get("id")), "is_import": it.get("is_import"), "price": price}

        # --- собівартість: стара (ворота 0) / нова (ворота 1)
        mode(False, 0.0)
        r["cost_old"] = cp.real_toysi_cost(it)
        r["site_old"] = cp.site_retail_price(it)
        r["floor_site_old"] = math.ceil(r["cost_old"] * (1 + cp.SITE_MIN_MARGIN)) if r["cost_old"] > 0 else 0
        r["prom_old"] = cp.decide_price_for_platform(r["cost_old"], None, "prom", cat)["price"] if r["cost_old"] > 0 else None
        r["prom_old_floor"] = cp.decide_price_for_platform(r["cost_old"], None, "prom", cat)["floor"] if r["cost_old"] > 0 else None
        r["rz_old"] = cp.decide_price_for_platform(r["cost_old"], None, "rozetka")["floor"] if r["cost_old"] > 0 else None
        r["eva_old"] = eva_price(it) if eva_price else None
        mode(True, 0.0)
        r["cost_new"] = cp.real_toysi_cost(it)
        r["site_new"] = cp.site_retail_price(it)
        r["floor_site_new"] = math.ceil(r["cost_new"] * (1 + cp.SITE_MIN_MARGIN)) if r["cost_new"] > 0 else 0
        r["base_site"] = math.ceil(cp.toysi_discounted_price(it) * cp.SITE_PRICE_MULT)
        r["prom_cost_only"] = cp.decide_price_for_platform(r["cost_new"], None, "prom", cat)["price"] if r["cost_new"] > 0 else None
        r["rz_new"] = cp.decide_price_for_platform(r["cost_new"], None, "rozetka")["floor"] if r["cost_new"] > 0 else None
        r["eva_new"] = eva_price(it) if eva_price else None
        mode(True, 10.0)
        d = cp.decide_price_for_platform(r["cost_new"], None, "prom", cat) if r["cost_new"] > 0 else None
        r["prom_new"] = d["price"] if d else None
        r["prom_new_floor"] = d["floor"] if d else None
        mode(False, 10.0)
        r["prom_fee_only"] = cp.decide_price_for_platform(r["cost_old"], None, "prom", cat)["price"] if r["cost_old"] > 0 else None
        rows.append(r)
    mode(True, 10.0)
    os.environ.pop("TOYSI_COST_BY_IMPORT", None)

    out = []
    P = out.append
    P(f"cost_change_impact — {datetime.now():%Y-%m-%d %H:%M}")
    P(f"Джерело: {src}; {obs_note}")
    P("Одиниця: ТОВАР (унікальний id каталогу) В НАЯВНОСТІ (stock>0, ціна>0). Стара = TOYSI_COST_BY_IMPORT=0 і PROM_ORDER_FEE_UAH=0.")
    ids = {r["id"] for r in rows}
    P(f"Товарів у наявності з валідною ціною: {len(rows)} (унікальних id: {len(ids)})")
    n_t = sum(1 for r in rows if r["is_import"] is True)
    n_f = sum(1 for r in rows if r["is_import"] is False)
    n_n = len(rows) - n_t - n_f
    P(f"Тег isImport у каталозі: true={n_t}, false={n_f}, нема тега={n_n}")
    summary = {"date": date.today().isoformat(), "items": len(rows), "is_import": {"true": n_t, "false": n_f, "none": n_n}}

    # --- 1. real_toysi_cost
    P("\n=== 1. real_toysi_cost (собівартість, ₴) ===")
    P(f"{'isImport':10s}{'товарів':>9s}{'змін.':>9s}{'сер.%':>9s}{'медіана%':>10s}{'p95%':>9s}{'Σ Δ₴':>12s}")
    cost_summary = {}
    for label, flag in (("true", True), ("false", False), ("None", None), ("УСІ", "all")):
        sub = [r for r in rows if flag == "all" or r["is_import"] is flag]
        ch = [r for r in sub if abs(r["cost_new"] - r["cost_old"]) > 0.004]
        pct = [(r["cost_new"] / r["cost_old"] - 1) * 100 for r in ch if r["cost_old"] > 0]
        st = _stats(pct)
        dsum = round(sum(r["cost_new"] - r["cost_old"] for r in ch), 2)
        P(f"{label:10s}{len(sub):9d}{len(ch):9d}{st['mean']:9.2f}{st['median']:10.2f}{st['p95']:9.2f}{dsum:12.2f}")
        cost_summary[label] = {"items": len(sub), "changed": len(ch), **st, "sum_delta_uah": dsum}
    summary["cost"] = cost_summary

    # --- 2. Prom
    P("\n=== 2. Prom: decide_price_for_platform(cost, None, 'prom', category) — ціна «без конкурента» (Шлях 1) ===")
    pr = [r for r in rows if r["prom_old"] is not None]
    prom_summary = {}
    for label, key in (("ПОВНА зміна (isImport + 10 ₴)", "prom_new"), ("лише isImport", "prom_cost_only"), ("лише +10 ₴", "prom_fee_only")):
        up = [r for r in pr if r[key] > r["prom_old"] + 0.005]
        pct = [(r[key] / r["prom_old"] - 1) * 100 for r in up]
        st = _stats(pct)
        dsum = round(sum(r[key] - r["prom_old"] for r in up), 2)
        P(f"{label:30s} дорожчає: {len(up):6d} із {len(pr)}; ріст % сер./мед./p95 = {st['mean']}/{st['median']}/{st['p95']}; Σ різниці = {dsum} ₴ (по 1 од. кожного товару)")
        prom_summary[key] = {"up": len(up), "of": len(pr), **st, "sum_delta_uah": dsum}
    fl = [r for r in pr if r["prom_new_floor"] > r["prom_old_floor"] + 0.005]
    P(f"Prom нижня межа (floor, Шлях 1) змінилась у {len(fl)} із {len(pr)} товарів")
    prom_summary["floor_changed"] = len(fl)
    summary["prom"] = prom_summary

    # --- 3. сайт
    P("\n=== 3. Сайт: підлога cost×1,03 (site_retail_price) ===")
    fc = [r for r in rows if r["floor_site_new"] != r["floor_site_old"]]
    binding_now = [r for r in fc if r["floor_site_new"] >= r["base_site"]]
    changed_price = [r for r in rows if r["site_new"] != r["site_old"]]
    already_bound = [r for r in rows if r["floor_site_old"] >= r["base_site"]]
    P(f"Підлога cost×1,03 змінилась у {len(fc)} товарів")
    P(f"  з них підлога (нова) ≥ бази ×1,5, тобто ЗВ'ЯЗУЄ ціну: {len(binding_now)}")
    P(f"Ціна сайту site_retail_price реально змінилась: {len(changed_price)} товарів; Σ різниці = {sum(r['site_new'] - r['site_old'] for r in changed_price)} ₴ (по 1 од.)")
    if changed_price:
        pc = [(r["site_new"] / r["site_old"] - 1) * 100 for r in changed_price if r["site_old"] > 0]
        st = _stats(pc)
        P(f"  ріст ціни сайту % сер./мед./p95 = {st['mean']}/{st['median']}/{st['p95']}")
    P(f"(довідково: де підлога ВЖЕ зв'язувала ціну до змін: {len(already_bound)} товарів; база ціни сайту/EVA ×1,5 від isImport НЕ залежить)")
    summary["site"] = {"floor_changed": len(fc), "floor_binds_after": len(binding_now), "price_changed": len(changed_price),
                       "already_bound_before": len(already_bound)}

    # --- 4. Rozetka / EVA
    P("\n=== 4. Rozetka / EVA (лише ефект зміни собівартості; +10 ₴ їх не стосується) ===")
    rz = [r for r in rows if r["rz_old"] is not None and abs(r["rz_new"] - r["rz_old"]) > 0.004]
    P(f"Rozetka floor (Шлях 1) змінився у {len(rz)} товарів")
    summary["rozetka"] = {"floor_changed": len(rz)}
    if eva_price:
        ev = [r for r in rows if r["eva_old"] != r["eva_new"]]
        newly_invalid = [r for r in ev if r["eva_old"] is not None and r["eva_new"] is None]
        P(f"EVA ціна (_eva_price: база ×1,5 + покривна підлога збиткових) змінилась у {len(ev)} товарів; випали з EVA (cost < MIN_SUPPLIER_PRICE): {len(newly_invalid)}")
        summary["eva"] = {"price_changed": len(ev), "newly_invalid": len(newly_invalid)}

    text = "\n".join(out)
    print(text)
    od = Path(a.out)
    od.mkdir(parents=True, exist_ok=True)
    (od / f"cost_change_impact_{date.today().isoformat()}.txt").write_text(text + "\n", encoding="utf-8")
    (od / f"cost_change_impact_{date.today().isoformat()}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
