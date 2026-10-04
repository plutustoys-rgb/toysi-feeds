# -*- coding: utf-8 -*-
"""Зведення з reports/zoobaza_sellers_<дата>.jsonl (zoobaza_seller_count.py): SKU з 0/1 продавцем-конкурентом на Prom.
Маржа нетто = price*(1-TC)/cost-1, TC=0.117 (8% + 3.7%); RRC = ОПТ*1.5, cost = ОПТ (feed/1.4)."""
import csv, json, sys, collections
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TC, RRC_K, STEP = 0.117, 1.5, 3
def net(p, cost): return p * (1 - TC) / cost - 1
def main(path):
    rows = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if "error" not in r]
    out = []
    for r in rows:
        cost = round(r["feed_price"] / 1.4, 2); rrc = round(cost * RRC_K)
        mp = r["min_price_080"]
        r.update(cost=cost, rrc=rrc, net_rrc=net(rrc, cost),
                 net_vs=(net(mp - STEP, cost) if mp else None),
                 rrc_vs_comp=((rrc - mp) / mp if mp else None))
    sel = [r for r in rows if r["n_sellers_080"] <= 1]
    sel.sort(key=lambda r: (r["n_sellers_080"], r["category"], r["vendor_code"]))
    base = Path(path).with_suffix("")
    with open(f"{base}_pilot_0_1.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["id", "vendor_code", "name", "category", "feed_price", "cost_opt", "rrc_opt_x1.5",
                    "net_at_rrc_11.7", "sellers_080", "sellers_060", "listings_080", "seller_ids_080",
                    "min_comp_price", "rrc_vs_min_comp_%", "net_at_comp_minus3"])
        for r in sel:
            w.writerow([r["id"], r["vendor_code"], r["name"], r["category"], r["feed_price"], r["cost"], r["rrc"],
                        f"{r['net_rrc']:.1%}", r["n_sellers_080"], r["n_sellers_060"], r["n_listings_080"],
                        ",".join(r["sellers_080"]), r["min_price_080"] or "",
                        f"{r['rrc_vs_comp']:.0%}" if r["rrc_vs_comp"] is not None else "",
                        f"{r['net_vs']:.1%}" if r["net_vs"] is not None else ""])
    return rows, sel
if __name__ == "__main__":
    rows, sel = main(sys.argv[1])
    print(len(rows), len(sel))
