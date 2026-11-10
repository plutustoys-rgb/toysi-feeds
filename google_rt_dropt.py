# -*- coding: utf-8 -*-
"""google_rt_dropt.py — товари RoyalToys (rt-) і Dropt (dropt-) у фіді Google Merchant (рішення власника 10.11.2026: «РТ та дропт додай у гугл»).

ЧОМУ ОКРЕМО: build_feed_items(generate_google_feed) обходить КАТАЛОГ TOYSI; RT/Dropt у ньому нема. Їхні картки живуть на сайті (site/index.json: id, публічний slug `u`, назва `n`, ЦІНА КАРТКИ `pr`, фото `p` на НАШОМУ домені, категорія `c`, наявність `s`),
а бренд/опис/закупівля/штрихкод — у supplier_rt.json / supplier_dropt.json (VPS). Тут ці два джерела зводяться до тих самих dict-ів, що віддає build_feed_items, і йдуть у _build_xml.
ПРАВИЛА (ті самі, що в Toysi-шляху build_feed_items): ціна фіда = ЦІНА КАРТКИ (index.json) — без «Price mismatch»; посилання = публічна адреса картки (`/product-<u>.html`); id у фіді = публічний slug (x…/y…), БЕЗ імені постачальника;
фільтри: forbidden_products, FEED_EXCLUDED_CATEGORIES, UNO, ліцензійні марки (гейт/чорний/білий списки), репліки зброї, 18+, внесок за економікою сайту (ціна − закупівля − комісія − логістика > FEED_MIN_SITE_CONTRIBUTION), фото, залишок.
ГЕЙТ: GOOGLE_RT_DROPT_IN_FEED=1 у .env VPS (власник); без нього нічого не змінюється. ЛИШЕ Google-фід (uk): Meta/Bing/російський фід не чіпаємо (російського тексту для RT/Dropt нема).
НЕ ВИГАДУЄМО даних: нема джерела для бренду → «PlutusToys» (як Toysi); штрихкод — лише валідний GTIN; опис — з постачальника (очищений), інакше назва."""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUPPLIER_MAX_AGE_H = 72          # supplier_*.json старший за це — джерело брендів/закупівлі не свіже: фід RT/Dropt цього прогону НЕ формуємо (fail-safe; решта фіда не страждає)
PREFIXES = ("rt-", "dropt-")


def gate_on() -> bool:
    return os.environ.get("GOOGLE_RT_DROPT_IN_FEED") == "1"


def _site_dir() -> Path:
    return Path(os.environ.get("SITE_DIR", str(HERE / "site")))


def load_site_entries() -> dict:
    """{id → запис index.json} лише для rt-/dropt- з наявністю на сайті. Порожній dict — індекс недоступний."""
    try:
        data = json.loads((_site_dir() / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for p in data if isinstance(data, list) else []:
        if isinstance(p, dict) and str(p.get("id", "")).startswith(PREFIXES):
            out[str(p["id"])] = p
    return out


def _load_supplier(path: Path) -> tuple:
    """→ (items dict, свіжий?). Свіжість — за mtime файла (VPS пише його щогодини)."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        items = d.get("items")
        if not isinstance(items, dict) or not items:
            return {}, False
        fresh = (time.time() - path.stat().st_mtime) / 3600.0 <= SUPPLIER_MAX_AGE_H
        return items, fresh
    except (OSError, ValueError, AttributeError):
        return {}, False


def _num(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def build_items(gf, entries: dict | None = None, rt_sup: dict | None = None, dropt_sup: dict | None = None, rt_fresh: bool = True, dropt_fresh: bool = True) -> tuple:
    """→ (items для generate_google_feed._build_xml, stats). gf — модуль generate_google_feed (функції/константи Toysi-шляху). Параметри entries/rt_sup/dropt_sup — для тестів; None → читаємо з диска."""
    stats = {"candidates": 0, "included": 0, "included_rt": 0, "included_dropt": 0, "noindex_pages_included": 0}
    skip = {}

    def no(why):
        skip[why] = skip.get(why, 0) + 1

    if entries is None:
        entries = load_site_entries()
    if rt_sup is None:
        rt_sup, rt_fresh = _load_supplier(HERE / "supplier_rt.json")
    if dropt_sup is None:
        dropt_sup, dropt_fresh = _load_supplier(HERE / "supplier_dropt.json")
    if not entries:
        stats["skip"] = {"index_unavailable": 1}
        return [], stats
    licensed_gate = gf.licensed_gate_on()
    licensed_bl = gf.load_licensed_vendor_blocklist() if gf.licensed_blocklist_on() else frozenset()
    licensed_rx = gf.load_licensed_marks_regex() if (licensed_gate or licensed_bl) else None
    licensed_wl = gf.load_licensed_vendor_whitelist() if (licensed_gate and gf.licensed_whitelist_on()) else frozenset()
    items, seen = [], set()
    for pid, e in entries.items():
        stats["candidates"] += 1
        is_rt = pid.startswith("rt-")
        sup, fresh = (rt_sup, rt_fresh) if is_rt else (dropt_sup, dropt_fresh)
        if not fresh:
            no("supplier_stale")
            continue
        r = sup.get(pid)
        if not isinstance(r, dict):
            no("no_supplier_record")
            continue
        if e.get("s") != 1:
            no("out_of_stock")
            continue
        name = (e.get("n") or "").strip()
        slug = str(e.get("u") or "").strip()
        price = _num(e.get("pr"))
        image = (e.get("p") or "").strip()
        if not name or not price or price <= 0:
            no("no_name_or_price")
            continue
        if not slug:
            no("no_public_slug")           # публічних адрес ще нема (ворота сайту вимкнені) — не віддаємо Google ні id «rt-/dropt-», ні стару адресу з іменем постачальника (рішення власника 10.11)
            continue
        if not image.startswith("https://"):
            no("no_image")
            continue
        vendor = (r.get("vendor") or "").strip()
        cat_site = (e.get("c") or "").strip()
        cat_sup = (r.get("category") or "").strip()
        if gf.is_forbidden({"name": name, "vendor": vendor, "category_name": cat_sup or cat_site}):
            no("forbidden")
            continue
        if cat_site.lower() in gf.FEED_EXCLUDED_CATEGORIES or cat_sup.lower() in gf.FEED_EXCLUDED_CATEGORIES:
            no("excluded_category")
            continue
        if gf.is_uno_trademark_blocked(name, vendor):
            no("trademark_uno")
            continue
        lic_wl = False
        if licensed_rx is not None and licensed_rx.search(name):
            if gf.vendor_key(vendor) in licensed_bl:
                no("licensed_vendor_blocked")
                continue
            if licensed_gate:
                if gf.vendor_key(vendor) in licensed_wl:
                    lic_wl = True
                else:
                    no("licensed_excluded")
                    continue
        cost = _num(r.get("cost"))
        if not cost or cost <= 0:
            no("no_cost")
            continue
        contribution = price - cost - price * gf.PAYMENT_COMMISSION.get("site", 0.0) - gf.SITE_FULFILL_PER_ORDER
        if contribution <= gf.FEED_MIN_SITE_CONTRIBUTION:
            no("site_unprofitable")
            continue
        title = gf.normalize_title(name)
        if gf.is_weapon_replica(slug, title):
            no("weapon_replica")
            continue
        if gf.is_adult_product(title):
            no("adult")
            continue
        feed_id = slug
        if feed_id in seen:
            no("duplicate_id")
            continue
        seen.add(feed_id)
        brand = gf.normalize_vendor(vendor) or gf.SHOP_NAME
        bc = str(r.get("barcode") or r.get("gtin") or "").strip()
        gtin = bc if bc and gf.is_valid_gtin(bc) else None
        desc = gf._clean_description(r.get("description") or "")[:5000] or name
        desc = gf.enrich_brand_only_description(feed_id, name, desc, cat_site or cat_sup, "uk")
        link = gf.SHOP_URL + "/product-" + slug + ".html"
        price_str = f"{price:.2f} {gf.FEED_CURRENCY}"
        items.append({
            "pid": pid,
            "id": feed_id,
            "title": title,
            "description": desc,
            "category_name": cat_site or cat_sup,
            "link": link,
            "image_link": image,
            "price": price_str,
            "price_band": gf.price_band(price),
            "sale_price": None, "sale_dates": None, "sale_dates_colon": None,
            "availability": "in_stock",
            "condition": "new",
            "brand": brand,
            "gtin": gtin,
            "google_product_category": gf.google_product_category(cat_site or cat_sup),
            "licensed_whitelisted": lic_wl,
        })
        stats["included"] += 1
        stats["included_rt" if is_rt else "included_dropt"] += 1
        if e.get("ix") != 1:
            stats["noindex_pages_included"] += 1
    stats["skip"] = skip
    return items, stats


def describe(stats: dict) -> str:
    sk = ", ".join(f"{k}={v}" for k, v in sorted((stats.get("skip") or {}).items(), key=lambda kv: -kv[1]))
    return (f"кандидатів {stats.get('candidates', 0)}; у фіді {stats.get('included', 0)} (RT {stats.get('included_rt', 0)}, Dropt {stats.get('included_dropt', 0)}); "
            f"з них сторінок noindex: {stats.get('noindex_pages_included', 0)}; відсів: {sk or '—'}")


if __name__ == "__main__":
    import generate_google_feed as _gf
    its, st = build_items(_gf)       # ПРОБНИЙ прогін (нічого не пише): лічильники + 6 прикладів — для рішення власника перед вмиканням GOOGLE_RT_DROPT_IN_FEED=1
    print("[google_rt_dropt]", describe(st))
    import random
    random.seed(20261110)
    for it in random.sample(its, min(6, len(its))):
        print("  •", it["id"], "|", it["price"], "|", it["brand"], "|", it["title"][:70], "|", it["link"])
    print("items:", len(its))
