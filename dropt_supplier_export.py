# -*- coding: utf-8 -*-
"""dropt_supplier_export.py — дані Dropt для сайту й фіду EVA (рішення власника 04.11.2026: 7 розділів, закупівля ЛИШЕ «Дроп», формула ціни сайту та сама, що для RoyalToys, розміщення — сайт + фід EVA).

Контракт (той самий, що supplier_rt.json): {"updated": ISO UTC, "catalog_n": N, "items": {"dropt-<артикул Dropt>": {name, description, vendor, vendor_code, category, images[https://…], price, cost, stock, dims_cm, weight_kg, index, source_url}}}.
Правила: cost = ціна «Дроп» зі списку розділу (НЕ «Опт»); price = закрита формула сайту max(cost×1,5; (cost+15)×1,03) вгору до гривні (rt_supplier_export.site_price — одне місце правди);
у файл — лише картки «в наявності» (Dropt показує лише є/нема; кількість невідома → stock = STOCK_IN), з українською назвою, ≥1 фото, поза політикою «не продаємо» (зброя/18+/…).
dims_cm і weight_kg = None: Dropt габаритів упаковки публічно не дає (потрібні лише конвеєру замовлень — окремий етап). index=False (noindex), поки SEO не додасть id у dropt_index_ids.json.
Файл читає site/build_site.py (Shopify-code) і rt_eva-аналог для EVA — ЦЕЙ модуль їх не чіпає. Гарди: збій розділу / каталог < 50 % попереднього / жодної картки → попередній файл НЕ перезаписується + сигнал.
Мережа — лише читання публічних сторінок dropt.in.ua (dropt_parser). Запуск: dropt-supplier-export.timer щогодини; докачування сторінок товарів — порціями (DETAIL_BUDGET за запуск)."""
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import dropt_parser as dp
import rt_supplier_export as rt

HERE = Path(__file__).resolve().parent
OUT_FILE = Path(os.environ.get("SUPPLIER_DROPT_FILE", str(HERE / "supplier_dropt.json")))
DETAIL_CACHE = Path(os.environ.get("DROPT_DETAIL_CACHE", str(HERE / "dropt_detail_cache.json")))
INDEX_FILE = HERE / "dropt_index_ids.json"
IMG_DIR = Path(os.environ.get("SITE_DIR", str(HERE / "site"))) / "img" / "dropt"
IMG_URL = "https://plutustoys.com.ua/img/dropt/{name}"
MAX_IMAGES = 3
STOCK_IN = 3                  # Dropt публічно показує лише «є / нема в наявності» — кількість невідома; 3 = обережний віртуальний залишок (кошик не дасть замовити більше)
DETAIL_TTL_S = 7 * 86400      # опис/фото/бренд змінюються рідко; ціна й наявність беруться зі списку щогодини
DETAIL_BUDGET = 400           # сторінок товару за один запуск (≈0,25 с на запит + мережа → ≈2–3 хв); решта — наступним запуском
MIN_KEEP_RATIO = 0.5          # (запозичено з rt_supplier_export, не виміряно окремо) каталог менший за половину попереднього → файл усічений, не перезаписуємо
PREFETCH_WORKERS = 4
PREFETCH_BUDGET_S = 900
MIN_FREE_GB = 10
_ID_RE = __import__("re").compile(r"dropt-\d+")      # як має бути в build_site (Shopify-code): окремий префікс, не плутається з rt-/Toysi


def _alert(key, text):
    rt._alert(key, text)


def load_index_ids(path=None):
    try:
        d = json.loads(Path(path or INDEX_FILE).read_text(encoding="utf-8"))
        return {str(i) for i in (d.get("ids") or []) if _ID_RE.fullmatch(str(i))} if isinstance(d, dict) else set()
    except (OSError, ValueError):
        return set()


def load_detail_cache(path=None):
    try:
        d = json.loads(Path(path or DETAIL_CACHE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_detail_cache(cache, path=None):
    p = Path(path or DETAIL_CACHE)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, p)


def refresh_details(catalog, cache, fetch, now_ts, budget=DETAIL_BUDGET, ttl=DETAIL_TTL_S):
    """Докачує сторінки товарів лише для карток «в наявності» без свіжого запису в кеші (нові й найстаріші — першими), не більше budget. Збій сторінки → лишається старий запис (або нічого). Повертає кількість завантажених."""
    need = []
    for sku, c in catalog.items():
        if not c.get("in_stock"):
            continue
        e = cache.get(sku)
        age = now_ts - float(e.get("fetched_at", 0)) if isinstance(e, dict) else None
        if age is None or age > ttl or e.get("url") != c.get("url"):
            need.append((-1 if age is None else age, sku))
    need.sort(key=lambda x: (x[0] != -1, -x[0]))          # нові (без запису) першими, далі від найстаріших
    done = 0
    for _age, sku in need[:budget]:
        try:
            d = dp.parse_detail(fetch(catalog[sku]["url"]))
        except Exception as e:  # noqa: BLE001
            print(f"[dropt_export] сторінка {sku}: {type(e).__name__}", file=sys.stderr)
            continue
        if d:
            d.update({"fetched_at": now_ts, "url": catalog[sku]["url"]})
            cache[sku] = d
            done += 1
    return done


def build_item(sid, card, detail, images):
    """Елемент supplier_dropt.json або None (не публікуємо). Чиста функція (без мережі)."""
    if not _ID_RE.fullmatch(sid) or not card.get("in_stock") or not images:
        return None
    full = (detail.get("name") or card.get("name") or "").strip()
    short = full.partition(" • ")[0]
    short = short.strip()
    if not short or rt._RU_LETTERS_RE.search(short) or rt._RU_WORDS_RE.search(short):
        return None                      # назва порожня або російська — картку не публікуємо
    try:
        cost = float(card.get("drop"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(cost) or cost <= 0:
        return None
    desc = rt.plain_text(detail.get("description") or "")
    if rt._RU_LETTERS_RE.search(desc) or rt._RU_WORDS_RE.search(desc):
        desc = ""                        # російський опис не показуємо (картка лишається без блоку «Опис»)
    else:
        lines = desc.split("\n")
        if lines and " • " in lines[0] and len(lines[0]) < 300:
            desc = "\n".join(lines[1:]).strip()          # перший рядок опису Dropt — дубль SEO-заголовка «Назва • підзаголовок»: у картці він зайвий
    if rt.policy_reason({"id": sid[6:], "name_ua": short, "name": card.get("name"), "category_name": card.get("category") or ""}) == "policy":
        return None
    return {"name": short, "description": desc, "vendor": (detail.get("brand") or "").strip(), "vendor_code": sid[6:], "category": (card.get("category") or "").strip(),
            "images": images, "price": rt.site_price(cost), "cost": round(cost, 2), "stock": STOCK_IN, "dims_cm": None, "weight_kg": None, "source_url": card.get("url") or ""}


def out_guard_ok(out, catalog_n):
    try:
        prev = int(json.loads(Path(out).read_text(encoding="utf-8")).get("catalog_n") or 0)
    except (OSError, ValueError, TypeError):
        return True
    if prev and catalog_n < prev * MIN_KEEP_RATIO:
        _alert("dropt_export_shrink", f"⚠️ dropt_supplier_export: у каталозі Dropt {catalog_n} карток проти {prev} під час попереднього запису (менше {int(MIN_KEEP_RATIO * 100)}%) — читання усічене? supplier_dropt.json НЕ перезаписано.")
        return False
    return True


def run_once(catalog=None, failed=None, fetch=None, out=None, cache_path=None, img_dir=None, fetch_img=rt.fetch_image, now=None, workers=1, budget_s=None, detail_budget=DETAIL_BUDGET, check_disk=False):
    """True — файл записано. Збій розділу / порожній результат / усадка каталогу → попередній файл лишається, False."""
    out, img_dir = Path(out or OUT_FILE), Path(img_dir or IMG_DIR)
    now = now or datetime.now(timezone.utc)
    try:
        fetch = fetch or dp.make_fetch()
        if catalog is None:
            catalog, failed = dp.fetch_catalog(fetch)
        if failed:
            _alert("dropt_export_partial", f"⚠️ dropt_supplier_export: не прочитано розділи {failed} — supplier_dropt.json НЕ перезаписано (щоб картки цих розділів не зникли).")
            return False
        if not catalog or not out_guard_ok(out, len(catalog)):
            if not catalog:
                _alert("dropt_export_empty", "⚠️ dropt_supplier_export: каталог Dropt порожній/недоступний — supplier_dropt.json НЕ оновлено.")
            return False
        cache = load_detail_cache(cache_path)
        refresh_details(catalog, cache, fetch, now.timestamp(), detail_budget)
        save_detail_cache(cache, cache_path)
        cand, tasks = [], []
        for sku, c in catalog.items():
            sid = "dropt-" + sku
            d = cache.get(sku)
            if not c.get("in_stock") or not isinstance(d, dict) or not d.get("images"):
                continue
            cand.append((sid, c, d))
            for n, url in enumerate(d["images"][:MAX_IMAGES], 1):
                tasks.append((url, img_dir / f"{sid}-{n}.jpg"))
        todo = [(u, p) for u, p in tasks if not rt._on_disk(p)]
        if check_disk and todo:
            import shutil
            free_gb = shutil.disk_usage(img_dir.parent if img_dir.parent.exists() else HERE).free / 1e9
            if free_gb < MIN_FREE_GB:
                _alert("dropt_export_disk", f"⚠️ dropt_supplier_export: вільно {free_gb:.1f} ГБ < {MIN_FREE_GB} ГБ — нових фото dropt- не качаємо.")
                todo = []
        got = {p: True for _u, p in tasks if rt._on_disk(p)}
        got.update(rt.prefetch(todo, fetch_img, workers, budget_s))
        index_set = load_index_ids()
        items, skipped = {}, 0
        for sid, c, d in cand:
            imgs = [IMG_URL.format(name=f"{sid}-{n}.jpg") for n, _u in enumerate(d["images"][:MAX_IMAGES], 1) if got.get(img_dir / f"{sid}-{n}.jpg")]
            it = build_item(sid, c, d, imgs)
            if it is None:
                skipped += 1
                continue
            it["index"] = sid in index_set
            items[sid] = it
        if not items:
            _alert("dropt_export_none", f"⚠️ dropt_supplier_export: жодної картки не зібрано (у наявності {sum(1 for c in catalog.values() if c.get('in_stock'))}) — supplier_dropt.json НЕ оновлено.")
            return False
        rt._write_json(out, items, now, len(catalog))
        waiting = sum(1 for c in catalog.values() if c.get("in_stock")) - len(items) - skipped
        print(f"[dropt_export] ✅ {len(items)} карток у файлі (у наявності {sum(1 for c in catalog.values() if c.get('in_stock'))} з {len(catalog)}; відсіяно правилами {skipped}; чекають сторінки/фото {max(0, waiting)})")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[dropt_export] збій: {type(e).__name__}: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    sys.exit(0 if run_once(workers=PREFETCH_WORKERS, budget_s=PREFETCH_BUDGET_S, check_disk=True) else 1)
