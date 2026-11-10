# -*- coding: utf-8 -*-
"""google_rt_dropt.py — товари RoyalToys (rt-) і Dropt (dropt-) у фіді Google Merchant (рішення власника 10.11.2026: «РТ та дропт додай у гугл»).

ЧОМУ ОКРЕМО: build_feed_items(generate_google_feed) обходить КАТАЛОГ TOYSI; RT/Dropt у ньому нема. Їхні картки живуть на сайті (site/index.json: id, публічний slug `u`, назва `n`, ЦІНА КАРТКИ `pr`, фото `p` на НАШОМУ домені, категорія `c`, наявність `s`),
а бренд/опис/закупівля/штрихкод — у supplier_rt.json / supplier_dropt.json (VPS). Тут ці два джерела зводяться до тих самих dict-ів, що віддає build_feed_items, і йдуть у _build_xml.
ПРАВИЛА (ті самі, що в Toysi-шляху build_feed_items): ціна фіда = ЦІНА КАРТКИ (index.json) — без «Price mismatch»; посилання = публічна адреса картки (`/product-<u>.html`); id у фіді = публічний slug (x…/y…), БЕЗ імені постачальника;
фільтри: forbidden_products, FEED_EXCLUDED_CATEGORIES, UNO, ліцензійні марки (гейт/чорний/білий списки), репліки зброї, 18+, внесок за економікою сайту (ціна − закупівля − комісія − логістика > FEED_MIN_SITE_CONTRIBUTION), фото, залишок.
ГЕЙТИ у .env VPS (власник); без них нічого не змінюється. Google, Meta і Bing — кожен за СВОЄЮ брамою (GOOGLE_/META_/BING_RT_DROPT_IN_FEED=1); російський Google-фід НЕ чіпаємо (російського тексту для RT/Dropt нема).
НЕ ВИГАДУЄМО даних: нема джерела для бренду → «PlutusToys» (як Toysi); штрихкод — лише валідний GTIN; опис — з постачальника (очищений), інакше назва."""
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUPPLIER_MAX_AGE_H = 72          # supplier_*.json старший за це — джерело брендів/закупівлі не свіже: фід RT/Dropt цього прогону НЕ формуємо (fail-safe; решта фіда не страждає)
PREFIXES = ("rt-", "dropt-")
PHOTO_PREFIX = "https://plutustoys.com.ua/img/p/"        # фото лише з нашого шляху /img/p/<slug>-K.jpg (public_photo, build_site.py) — інакше позицію не віддаємо
LEAK_TOKENS = ("dropt", "royaltoys", "royal toys", "toysi")      # ім'я постачальника не має потрапити ні в бренд, ні в назву, ні в опис, ні в посилання, ні в id, ні в фото (рішення власника 10.11). «royal» окремо НЕ ловимо: легальні назви товарів («Royal Pony»); «rt-» — лише як id (нижче)
_RT_ID_RX = re.compile(r"(?<![a-z0-9])rt-[0-9a-z]", re.I)
_URL_RX = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|ua|net|org|in\.ua|com\.ua)\b\S*", re.I)
_PHONE_RX = re.compile(r"\+?\d[\d\s().-]{8,}\d")
_BAD_SENTENCE_RX = re.compile(r"накладен|післяплат|наложен|наложк|при отриманні|при получении|оплат\w* (?:при|на пошті|на поште)|менеджер|дропшип|dropship|на нашому сайті|на нашем сайте|наш сайт|\bдропт|royal ?toys|dropt|toysi|телефон\w*|звертайтесь|замовляйте", re.I)


def scrub_description(text: str) -> str:
    """Опис постачальника → безпечний для публічного фіда: без посилань/телефонів, без речень про накладений платіж/менеджера/сайт постачальника (як на картці сайту: передоплата; ім'я постачальника ніде). Порожній результат → викликач бере назву."""
    t = _URL_RX.sub(" ", str(text or ""))
    t = _PHONE_RX.sub(" ", t)
    parts = re.split(r"(?<=[.!?…])\s+|\n+", t)
    keep = [x.strip() for x in parts if x.strip() and not _BAD_SENTENCE_RX.search(x)]
    out = re.sub(r"\s{2,}", " ", " ".join(keep)).strip()
    return out if not leaks(out) else ""


def leaks(*values) -> bool:
    return any(tok in str(v or "").lower() or _RT_ID_RX.search(str(v or "")) for v in values for tok in LEAK_TOKENS)


def only_indexable() -> bool:
    """За замовчуванням у фід лише картки, які SEO відкрила для індексації (ix=1; контракт у site/build_site.py поруч з index_entries). FEED_RT_DROPT_ONLY_IX=0 — віддавати й закриті (рішення власника/SEO)."""
    return os.environ.get("FEED_RT_DROPT_ONLY_IX", "1") != "0"


def gate_on(feed: str = "google") -> bool:
    """Брама на КОЖЕН фід окремо: GOOGLE_/META_/BING_RT_DROPT_IN_FEED=1 у .env VPS."""
    return os.environ.get(f"{feed.upper()}_RT_DROPT_IN_FEED") == "1"


def extend_items(items: list, gf, feed: str = "google") -> list:
    """Додає RT/Dropt до items фіда `feed` за його брамою. Збій/вимкнено → items без змін (основний фід не страждає)."""
    if not gate_on(feed):
        return items
    try:
        extra, st = build_items(gf)
        print(f"[{feed}] RT/Dropt: " + describe(st))
        return items + extra
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[{feed}] RT/Dropt пропущено ({type(e).__name__}: {str(e)[:120]}) — фід лише з Toysi.", file=sys.stderr)
        return items


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
        if e.get("dup"):
            no("shadow_duplicate_of_toysi")        # RT-«тінь» товару Toysi: на сайті прихована, canonical на Toysi (build_site.py) — у фід не віддаємо
            continue
        if only_indexable() and e.get("ix") != 1:
            no("not_opened_for_index")             # SEO ще не відкрила картку (noindex) — контракт «фіди лише з ix=1»
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
        if not image.startswith(PHOTO_PREFIX):
            no("no_image")
            continue
        vendor = (r.get("vendor") or "").strip()
        cat_site = (e.get("c") or "").strip()
        cat_sup = (r.get("category") or "").strip()
        if any(gf.is_forbidden({"id": pid, "name": name, "vendor": vendor, "category_name": c}) for c in {cat_sup, cat_site} if c) or gf.is_forbidden({"id": pid, "name": name, "vendor": vendor, "category_name": ""}):
            no("forbidden")
            continue
        if gf.is_clearance_item(name, cat_site, "") or gf.is_clearance_item(name, cat_sup, ""):
            no("clearance")
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
        if leaks(brand):
            brand = gf.SHOP_NAME
        bc = str(r.get("barcode") or r.get("gtin") or "").strip()
        gtin = bc if bc and gf.is_valid_gtin(bc) else None
        desc = scrub_description(gf._clean_description(r.get("description") or ""))[:5000] or name
        desc = gf.enrich_brand_only_description(feed_id, name, desc, cat_site or cat_sup, "uk")
        link = gf.SHOP_URL + "/product-" + slug + ".html"
        if leaks(feed_id, title, desc, link, image, brand):
            no("supplier_name_leak")               # остання перевірка: ім'я постачальника ніде у позиції — інакше позицію не віддаємо
            seen.discard(feed_id)
            continue
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


# ───────────────────────── російський Google-фід (власник 10.11.2026: «переклад робіть») ─────────────────────────
RU_TEXTS_FILE = HERE / "rt_dropt_ru_text.json"       # {"version":1,"items":{pid:{"src":<hash укр. джерела>,"name":…,"description":…}}} — переклад робить Код (LLM) партіями; змінилось укр. джерело → переклад НЕ використовується (src не збігається)
RU_DESC_MAX = 450
_UK_ONLY = set("іїєґІЇЄҐ")


def gate_ru_on() -> bool:
    return os.environ.get("GOOGLE_RU_RT_DROPT_IN_FEED") == "1"


def ru_source(item: dict) -> tuple:
    """(назва, короткий опис ≤RU_DESC_MAX по межі речення) — те, що перекладаємо; той самий текст рахується і під час експорту, і під час фіда."""
    title = (item.get("title") or "").strip()
    d = (item.get("description") or "").strip()
    if len(d) > RU_DESC_MAX:
        cut = max(d.rfind(". ", 0, RU_DESC_MAX), d.rfind("! ", 0, RU_DESC_MAX), d.rfind("? ", 0, RU_DESC_MAX))
        d = d[:cut + 1] if cut > 120 else d[:RU_DESC_MAX].rsplit(" ", 1)[0]
    return title, d


def src_hash(title: str, desc: str) -> str:
    import hashlib
    return hashlib.sha1((title + "|" + desc).encode("utf-8")).hexdigest()[:16]


def ru_text_ok(name: str, desc: str) -> bool:
    """Переклад придатний: є кирилиця, нема українських літер (і ї є ґ — ознака неперекладеного), нема імені постачальника."""
    for t in (name, desc):
        if not t or not re.search(r"[А-Яа-я]", t) or any(ch in _UK_ONLY for ch in t) or leaks(t):
            return False
    return len(name) >= 3


def export_for_translation(gf, entries=None, rt_sup=None, dropt_sup=None) -> list:
    """Для перекладу: [{pid, id, title, desc, src}] по ВСІХ позиціях, що зараз пройшли б у фід (build_items) і ще не мають актуального перекладу."""
    items, _st = build_items(gf, entries=entries, rt_sup=rt_sup, dropt_sup=dropt_sup)
    have = _load_ru_file()
    out = []
    for it in items:
        t, d = ru_source(it)
        h = src_hash(t, d)
        if (have.get(it["pid"]) or {}).get("src") == h:
            continue
        out.append({"pid": it["pid"], "id": it["id"], "title": t, "desc": d, "src": h})
    return out


def _load_ru_file(path=None) -> dict:
    try:
        d = json.loads(Path(path or RU_TEXTS_FILE).read_text(encoding="utf-8"))
        items = d.get("items")
        return items if isinstance(items, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def merge_ru_texts(russian_text: dict, items: list, path=None) -> tuple:
    """Додає до russian_text (формат build_ru_items: {pid: {name, description}}) переклади RT/Dropt для позицій із items. → (russian_text, {used, stale, bad}). Жодного перекладу не вигадуємо: нема/застарів/непридатний → позиції немає в ru-фіді."""
    have = _load_ru_file(path)
    out = dict(russian_text or {})
    st = {"used": 0, "stale": 0, "bad": 0, "missing": 0}
    for it in items:
        pid = it.get("pid") or ""
        if not pid.startswith(PREFIXES):
            continue
        e = have.get(pid)
        if not e:
            st["missing"] += 1
            continue
        t, d = ru_source(it)
        if e.get("src") != src_hash(t, d):
            st["stale"] += 1
            continue
        if not ru_text_ok(e.get("name") or "", e.get("description") or ""):
            st["bad"] += 1
            continue
        out[pid] = {"name": e["name"].strip(), "description": e["description"].strip()}
        st["used"] += 1
    return out, st


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
