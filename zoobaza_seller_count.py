# -*- coding: utf-8 -*-
"""zoobaza_seller_count.py — read-only: скільки РІЗНИХ продавців на Prom пропонують той самий товар (розмір) ZooBaza.

Запит Консультанта 2026-10-04: перелік SKU, де лістинг ОДИН або ЖОДНОГО (кандидати в пілот — з ким не б'ємось).
Для кожного SKU білого списку: пошук Prom (топ-20, SearchListingQuery), далі ті самі відсіви, що в
`prom_competitor_pricer._rank_competitor_candidates` (не наш company_id, в наявності, без маркерів уцінки/набору,
розумна ціна), + збіг розміру без одиниці (`dims_of`), і лічильники по score:
  n_sellers_060 — різних продавців з score ≥ 0.6 (рівень «довірений» для ціни),
  n_sellers_080 — з score ≥ 0.8 (імовірно та сама модель; розмір сам модель не гарантує).
Тому «ЖОДНОГО/ОДИН» за score ≥ 0.8 — оцінка знизу кількості конкурентів, за ≥ 0.6 — зверху. Видача обмежена топ-20.

Чекпоінт .jsonl, повторний запуск продовжує. Вихід: reports/zoobaza_sellers_<дата>.jsonl і .md.
Запуск: python zoobaza_seller_count.py
"""
import json
import sys
import time
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import prom_competitor_pricer as pc
import zoobaza_parser as z
from zoobaza_competitor_report import dims_of

OUT_DIR = Path(__file__).parent / "reports"
CK = OUT_DIR / f"zoobaza_sellers_{date.today().isoformat()}.jsonl"


def analyse(it: dict) -> dict:
    name, cost = it["name"], it["cost"]
    ours = dims_of(name)
    raw = pc.search_prom_products(name)
    rows = []
    for p in raw:
        if p.get("company_id") == pc.PROM_OWN_COMPANY_ID:
            continue
        if not (p.get("presence") or {}).get("isAvailable"):
            continue
        nm = (p.get("name") or "")
        low = nm.lower()
        if any(m in low for m in pc._BAD_NAME_MARKERS) or pc.is_bundle_listing(nm):
            continue
        try:
            price = float(p.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or not (cost * pc.PRICE_SANITY_MIN_RATIO <= price <= cost * pc.PRICE_SANITY_MAX_RATIO):
            continue
        if ours and not (ours & dims_of(nm)):
            continue
        score = pc._similarity(name, nm)
        if score >= pc.MATCH_MIN_SCORE_FOR_PRICING:
            rows.append({"company_id": p.get("company_id"), "price": price, "score": round(score, 3),
                         "id": p.get("id"), "name": nm})
    return {
        "id": it["id"], "vendor_code": it["vendor_code"], "name": name, "category": it["category_name"],
        "feed_price": it["price"], "has_dims": bool(ours), "search_results": len(raw),
        "n_listings_060": len(rows), "n_sellers_060": len({r["company_id"] for r in rows}),
        "n_listings_080": sum(1 for r in rows if r["score"] >= 0.8),
        "n_sellers_080": len({r["company_id"] for r in rows if r["score"] >= 0.8}),
        "sellers_080": sorted({str(r["company_id"]) for r in rows if r["score"] >= 0.8}),
        "min_price_080": min((r["price"] for r in rows if r["score"] >= 0.8), default=None),
    }


def main() -> None:
    items = list(z.filter_catalog(z.fetch_zoobaza_catalog(), include_clothing=False).values())
    OUT_DIR.mkdir(exist_ok=True)
    done = set()
    if CK.exists():
        done = {json.loads(l)["id"] for l in CK.read_text(encoding="utf-8").splitlines() if l.strip()}
        print(f"[ZB-sellers] продовжую: {len(done)} вже є")
    for i, it in enumerate(items, 1):
        if it["id"] in done:
            continue
        try:
            row = analyse(it)
        except Exception as e:  # noqa: BLE001
            row = {"id": it["id"], "name": it["name"], "error": str(e)}
        with CK.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + chr(10))
        if i % 25 == 0:
            print(f"[ZB-sellers] {i}/{len(items)}", flush=True)
        time.sleep(pc.SEARCH_DELAY)
    print("[ZB-sellers] готово")


if __name__ == "__main__":
    main()
