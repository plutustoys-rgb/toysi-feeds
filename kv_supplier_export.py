# -*- coding: utf-8 -*-
"""kv_supplier_export.py — дані KIDDISVIT для сайту й фідів (рішення власника 17.11.2026: «додаєш їхні товари у всі фіди, подивимось як вони працюють із замовленнями»): картки `kv-<productID>` → ПРИВАТНИЙ <корінь>/supplier_kv.json (ПОЗА DocumentRoot: там закупівля "cost"), фото на НАШОМУ сервері site/img/kv/<id>-N.jpg.

Той самий контракт, що supplier_rt.json (Shopify-code: site/build_site.py за аналогією `_load_supplier_rt`): {"updated": ISO UTC, "catalog_n": N, "items": {"kv-<id>": {name, description, vendor, vendor_code, barcode, category (УКР. назва листка каталогу KIDDISVIT), category_path[…], images[https://plutustoys.com.ua/img/kv/…],
price (закрита формула сайту), cost (clientPrice із API: НАША закупівля), rrp (поточна РРЦ), stock, dims_cm [Д,Ш,В], weight_kg|null, index}}}.
Джерела (усе через kiddisvit_api.KiddisvitClient, ЛИШЕ ЧИТАННЯ): catalog/products (назва, опис, фото, бренд, штрихкод, габарити з properties), catalog/categories, catalog/brands + products/rests/prices по бренду (clientPrice, залишки).
Правила: залишок = лише склад «Dnepr» (склади NTV20/NTV30 — товар із пошкодженим пакуванням, умови не з'ясовано → НЕ публікуємо), «101» у API = «100+» → кап 100; відбір — політика (зброя/18+; марка UNO відсікається далі, у build_site через trademark_filter), `kv_selection.json` (необов'язковий: мін. залишок, бренди-не-іграшки, exclude_not_toys; нема файлу → типові значення), позиція без фото на диску, без габаритів, без ціни/назви — не публікується.
Ціна сайту — ЗАКРИТА формула (13.10.2026, `rt_supplier_export.site_price`), НЕ змінювати без слова власника. Порожній каталог/ціни → попередній файл НЕ перезаписується (build_site сам вимкне картки, коли «updated» старший за 3 доби — fail-closed), сигнал раз на добу.
Запуск: kv-supplier-export.timer щогодини (клієнт тримає ≤1,4 запиту/с: ліміт постачальника 25 запитів/10 с). stock_snapshot додає в site/stock.json ключі kv-<id>. Замовлення KIDDISVIT тут НЕ чіпаються."""
import json
import math
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import rt_supplier_export as rt
from kiddisvit_api import KiddisvitClient, KiddisvitError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
OUT_FILE = Path(os.environ.get("SUPPLIER_KV_FILE", str(HERE / "supplier_kv.json")))
IMG_DIR = Path(os.environ.get("SITE_DIR", str(HERE / "site"))) / "img" / "kv"
IMG_URL = "https://plutustoys.com.ua/img/kv/{name}"
SELECTION_FILE = HERE / "kv_selection.json"
INDEX_FILE = HERE / "kv_index_ids.json"
MAX_IMAGES = 3
STOCK_CAP = 100                     # у API «101» = «100+» (ДОВІДНИК §4)
SITE_WAREHOUSE = "Dnepr"            # NTV20/NTV30 — пошкоджене пакування, умови продажу не з'ясовано (Т. Сіденко 17.11 13:04: «такий товар продається») → не публікуємо, доки власник не вирішить
PREFETCH_WORKERS = 6
PREFETCH_BUDGET_S = 900
MIN_FREE_GB = 10
MIN_KEEP_RATIO = rt.MIN_KEEP_RATIO
_ID_RE = re.compile(r"kv-[0-9]+")
# exclude_not_toys=False: регекси `rt_supplier_export._NOT_TOY_*` писані під рос. категорії RoyalToys і хибно спрацьовують на укр. назвах KIDDISVIT («Каталка з ручкою», «Набір олівців», «Книга-гра»; аудит #1691) → не-іграшки відсікаємо лише брендами (kv_selection.json), а не цим регексом.
DEFAULT_SELECTION = {"min_stock": 3, "min_cost": None, "exclude_not_toys": False, "exclude_brands": ["Goo.N Plus", "Goo.N Premium Soft", "Goo.N Slim & Comfortable", "Lansinoh"]}   # підгузки/гігієна — не іграшки (асортимент, не політика); власник може зняти у kv_selection.json


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).strip().casefold()


def load_selection(path: Path = SELECTION_FILE) -> dict:
    """kv_selection.json: {"min_stock": 3, "min_cost": число|null, "exclude_brands": [назви]}. Нема/битий файл → DEFAULT_SELECTION (fail-safe)."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError
        return {**DEFAULT_SELECTION, **d}
    except (OSError, ValueError):
        return dict(DEFAULT_SELECTION)


def load_index_ids(path: Path = INDEX_FILE) -> set:
    """kv_index_ids.json {"ids": ["kv-…"]} — картки, відкриті для Google (хвилі індексації; веде SEO). Нема файлу → порожньо (усі noindex)."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return {str(i) for i in (d.get("ids") or []) if _ID_RE.fullmatch(str(i))} if isinstance(d, dict) else set()
    except (OSError, ValueError):
        return set()


def _num(raw) -> float | None:
    try:
        x = float(str(raw).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def dims_from_properties(props) -> list | None:
    """[довжина, ширина, висота] упаковки, см, з properties «Розмір іграшки в упаковці довжина/ширина/висота»; нема чи нереальні (>200 см) → None."""
    got = {}
    for p in props or []:
        if not isinstance(p, dict):
            continue
        n = _norm(p.get("name"))
        if "в упаковці" not in n or "без упаковки" in n:
            continue
        for key, word in (("l", "довжина"), ("w", "ширина"), ("h", "висота")):
            if n.endswith(word):
                got[key] = _num(p.get("value"))
    d = [got.get("l"), got.get("w"), got.get("h")]
    if not all(d) or any(x > 200 for x in d):
        return None
    return [round(x, 1) for x in d]


def category_path(cat_id: str, cats: dict) -> list:
    """Назви від кореня до листка за catalog/categories (цикли й глибина >8 відсікаються)."""
    out, seen, cur = [], set(), cat_id
    while cur and cur in cats and cur not in seen and len(out) < 8:
        seen.add(cur)
        out.append(str(cats[cur].get("name") or "").strip())
        cur = cats[cur].get("parentID")
    return list(reversed([x for x in out if x]))


def stock_of(rest, warehouse: str = SITE_WAREHOUSE) -> int:
    """Залишок складу Dnepr (кап STOCK_CAP). rest=[{WarehouseID, Rest}]; регістр складу не важливий, «7.0» теж число."""
    tot = 0
    for r in rest or []:
        if isinstance(r, dict) and _norm(r.get("WarehouseID")) == _norm(warehouse):
            n = _num(r.get("Rest"))
            if n is not None:
                tot += int(n)
    return min(tot, STOCK_CAP)


def _ua_ok(name: str) -> bool:
    return bool(name) and not (rt._RU_LETTERS_RE.search(name) or rt._RU_WORDS_RE.search(name) or (rt._RU_LANG_MARK_RE.search(name) and not rt._UA_LANG_MARK_RE.search(name)))


def build_item(pid: str, prod: dict, price_row: dict, cats: dict, images: list) -> dict | None:
    """Елемент supplier_kv.json або None. Чиста функція (без мережі)."""
    name = str(prod.get("name") or "").strip()
    if not _ua_ok(name) or not images:
        return None
    cost = _num((price_row or {}).get("clientPrice"))
    if cost is None:
        return None
    dims = dims_from_properties(prod.get("properties"))
    if dims is None:
        return None          # ТТН потребує розмірів (як для RT)
    desc = rt.plain_text(prod.get("description") or "")
    if rt._RU_LETTERS_RE.search(desc) or rt._RU_WORDS_RE.search(desc):
        desc = ""
    path = category_path(prod.get("category_id"), cats)
    rrp = _num((price_row or {}).get("retailPrice"))
    return {"name": name, "description": desc, "vendor": str(prod.get("brand_name") or "").strip(), "vendor_code": str(prod.get("sku") or "").strip(),
            "barcode": re.sub(r"\D", "", str(prod.get("barcode") or "")) or None, "category": path[-1] if path else "", "category_path": path,
            "images": images, "price": rt.site_price(cost), "cost": round(cost, 2), "rrp": round(rrp, 2) if rrp else None,
            "stock": stock_of((price_row or {}).get("rest")), "dims_cm": dims, "weight_kg": None}


def select_ids(products: dict, prices: dict, cats: dict, sel: dict) -> list:
    """Упорядкований список pid до публікації: політика, фільтри kv_selection.json, залишок Dnepr ≥ min_stock; за спаданням внеску (ціннішим фото качаємо першими)."""
    excl = {_norm(b) for b in (sel.get("exclude_brands") or [])}
    mc = sel.get("min_cost")
    try:
        ms = int(sel.get("min_stock") or 0)
    except (TypeError, ValueError):
        ms = DEFAULT_SELECTION["min_stock"]           # сміття в kv_selection.json → типове (fail-safe), а не падіння прогону
    extra = []
    for pid, p in products.items():
        sid = f"kv-{pid}"
        if not _ID_RE.fullmatch(sid) or _norm(p.get("brand_name")) in excl:
            continue
        row = prices.get(pid)
        cost = _num((row or {}).get("clientPrice"))
        if cost is None or (mc is not None and cost < float(mc)):
            continue
        if stock_of(row.get("rest")) < max(1, ms):
            continue
        path = category_path(p.get("category_id"), cats)
        reason = rt.policy_reason({"id": pid, "name": p.get("name"), "name_ua": p.get("name"), "category_name": path[-1] if path else ""})
        if reason == "policy" or (reason == "not_toy" and sel.get("exclude_not_toys", True)):
            continue
        sp = rt.site_price(cost)
        extra.append((sp - cost - sp * 0.028, sid))
    extra.sort(key=lambda x: (-x[0], x[1]))
    return [sid for _, sid in extra]


def collect(client: KiddisvitClient) -> tuple[dict, dict, dict]:
    """(products{pid:…}, prices{pid:…}, cats{id:…}) з API. Збій → KiddisvitError (виклик не пише файл)."""
    cl = client.clients_list()
    c0 = (cl.get("clients") or [None])[0]
    if not c0 or not c0.get("ClientID") or not c0.get("ContractID"):
        raise KiddisvitError("clients/list: нема ClientID/ContractID")
    products = {str(p.get("productID")): p for p in client.products() if p.get("productID")}
    cats = {c["id"]: c for c in client.categories() if isinstance(c, dict) and c.get("id")}
    brand_guid = {str(b.get("brand_id")): b.get("ID") for b in client.brands() if b.get("brand_id") and b.get("ID")}
    need = {p.get("brand_id") for p in products.values() if p.get("brand_id")}
    prices, unknown = {}, 0
    for code in sorted(need, key=str):
        guid = brand_guid.get(str(code))
        if not guid:
            unknown += 1
            continue
        for row in client.prices_by_brand(c0["ClientID"], c0["ContractID"], guid):
            if isinstance(row, dict) and row.get("ProductID") is not None:
                prices[str(row["ProductID"])] = row
    if unknown:
        print(f"[kv_export] бренди без відповідника в catalog/brands: {unknown} (їхні товари без ціни → не публікуються)", file=sys.stderr)
    return products, prices, cats


MIN_PRICE_COVERAGE = 0.8       # частка товарів каталогу, для яких API віддав ціну клієнта: на живому прогоні 17.11 було ≈0,98; нижче → збій частини запитів цін (бренд без відповіді/Cloudflare), файл не перезаписуємо


def out_guard_ok(out: Path, catalog_n: int, priced_n: int = 0) -> bool:
    """Гарди усадки: (а) покриття цінами <MIN_PRICE_COVERAGE каталогу; (б) розмір каталогу <50 % попереднього запису; (в) цін клієнта <50 % попереднього priced_n (ціни йдуть окремими ~140 запитами, яких catalog_n не міряє). Законне звуження вибірки (min_stock, exclude_brands) гард не зачіпає. Свідоме звуження каталогу — видалити supplier_kv.json на VPS (гард покриття цінами (а) так НЕ обходиться: поріг MIN_PRICE_COVERAGE змінюється кодом — якщо договір колись відкриє товари без цін, покриття впаде нижче порога)."""
    if catalog_n and priced_n < catalog_n * MIN_PRICE_COVERAGE:
        rt._alert("kv_export_coverage", f"⚠️ kv_supplier_export: ціни клієнта є лише для {priced_n} із {catalog_n} товарів каталогу (<{int(MIN_PRICE_COVERAGE * 100)}%) — частина запитів products/rests/prices не відповіла? supplier_kv.json НЕ перезаписано.")
        return False
    try:
        prev_d = json.loads(Path(out).read_text(encoding="utf-8"))
        prev, prev_p = int(prev_d.get("catalog_n") or 0), int(prev_d.get("priced_n") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return True
    if prev and catalog_n < prev * MIN_KEEP_RATIO:
        rt._alert("kv_export_shrink", f"⚠️ kv_supplier_export: у каталозі KIDDISVIT {catalog_n} позицій проти {prev} під час попереднього запису (менше {int(MIN_KEEP_RATIO * 100)}%) — відповідь усічена? supplier_kv.json НЕ перезаписано. Свідоме звуження — видалити supplier_kv.json на VPS.")
        return False
    if prev_p and priced_n < prev_p * MIN_KEEP_RATIO:
        rt._alert("kv_export_coverage", f"⚠️ kv_supplier_export: цін клієнта {priced_n} проти {prev_p} під час попереднього запису (менше {int(MIN_KEEP_RATIO * 100)}%) — supplier_kv.json НЕ перезаписано.")
        return False
    return True


def _write_json(out: Path, items: dict, now, catalog_n: int, priced_n: int) -> None:
    """Як rt_supplier_export._write_json, плюс priced_n (покриття цінами для гарду)."""
    payload = {"updated": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "catalog_n": catalog_n, "priced_n": priced_n, "items": items}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, out)


def run_once(client: KiddisvitClient | None = None, collected=None, out: Path | None = None, fetch=rt.fetch_image, img_dir: Path = IMG_DIR, now: datetime | None = None,
             selection: dict | None = None, workers: int = 1, budget_s: float | None = None, check_disk: bool = False, index_ids: set | None = None) -> bool:
    """True — файл записано. Порожнє/збій → попередній файл лишається, False."""
    out = Path(out or OUT_FILE)
    try:
        if collected is None:
            client = client or KiddisvitClient()
            if not client.has_key():
                print("[kv_export] KIDDISVIT_API_KEY не задано — нічого не робимо", file=sys.stderr)
                return False
            collected = collect(client)
        products, prices, cats = collected
        if not products or not prices:
            rt._alert("kv_export_empty", "⚠️ kv_supplier_export: API KIDDISVIT повернув порожні товари/ціни (ключ, ліміт, Cloudflare?) — supplier_kv.json НЕ оновлено; через 3 доби картки kv- зникнуть із сайту (fail-closed).")
            return False
        priced_n = sum(1 for pid in products if _num((prices.get(pid) or {}).get("clientPrice")) is not None)
        if not out_guard_ok(out, len(products), priced_n):
            return False
        sel = selection if selection is not None else load_selection()
        idx = load_index_ids() if index_ids is None else index_ids
        ids = select_ids(products, prices, cats, sel)
        cand, tasks, nophoto = [], [], 0
        for sid in ids:
            pid = sid[3:]
            p = products[pid]
            if build_item(pid, p, prices[pid], cats, ["-"]) is None:
                nophoto += 1                                   # назва/ціна/габарити невалідні — фото даремно не качаємо
                continue
            cand.append((sid, pid))
            for n, url in enumerate([u for u in (p.get("images") or []) if isinstance(u, str)][:MAX_IMAGES], 1):
                tasks.append((url, img_dir / f"{sid}-{n}.jpg"))

        def build_items(got):
            res, miss = {}, 0
            for sid, pid in cand:
                p = products[pid]
                n_img = len([u for u in (p.get("images") or []) if isinstance(u, str)][:MAX_IMAGES])
                imgs = [IMG_URL.format(name=f"{sid}-{n}.jpg") for n in range(1, n_img + 1) if got.get(img_dir / f"{sid}-{n}.jpg")]
                it = build_item(pid, p, prices[pid], cats, imgs)
                if it is None:
                    miss += 1
                    continue
                it["index"] = sid in idx
                res[sid] = it
            return res, miss

        disk = {dest: True for _u, dest in tasks if rt._on_disk(dest)}
        disk_items = build_items(disk)[0] if disk else {}
        if disk_items:                                          # проміжний запис: залишки/картки не чекають на докачування фото
            _write_json(out, disk_items, now, len(products), priced_n)
        todo = [(u, d) for u, d in tasks if d not in disk]
        if check_disk and todo:
            import shutil
            free_gb = shutil.disk_usage(img_dir.parent if img_dir.parent.exists() else HERE).free / 1e9
            if free_gb < MIN_FREE_GB:
                rt._alert("kv_export_disk", f"⚠️ kv_supplier_export: вільно {free_gb:.1f} ГБ < {MIN_FREE_GB} ГБ — нових фото kv- не качаємо.")
                todo = []
        got = {**disk, **rt.prefetch(todo, fetch, workers, budget_s)}
        items, pending = build_items(got)
        if pending:
            print(f"[kv_export] без жодного фото цього разу (бюджет часу/збій): {pending} — будуть наступним запуском", file=sys.stderr)
        if not items:
            rt._alert("kv_export_none", f"⚠️ kv_supplier_export: жодної з {len(ids)} відібраних карток не зібрано (без фото/назви/ціни/габаритів: {nophoto}) — supplier_kv.json НЕ оновлено.")
            return False
        _write_json(out, items, now, len(products), priced_n)
        print(f"[kv_export] ✅ {len(items)} карток із {len(products)} у каталозі (відібрано {len(ids)}, без назви/ціни/габаритів {nophoto})")
        return True
    except KiddisvitError as e:
        print(f"[kv_export] збій API: {e}", file=sys.stderr)
        rt._alert("kv_export_api", f"⚠️ kv_supplier_export: збій API KIDDISVIT ({str(e)[:120]}) — supplier_kv.json НЕ оновлено.")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[kv_export] збій: {type(e).__name__}: {e}", file=sys.stderr)
        rt._alert("kv_export_crash", f"⚠️ kv_supplier_export: неочікуваний збій {type(e).__name__} — supplier_kv.json НЕ оновлено; дивись журнал kv-supplier-export.")
        return False


if __name__ == "__main__":
    if not KiddisvitClient().has_key():
        print("[kv_export] KIDDISVIT_API_KEY не задано — експорт не налаштований, пропускаємо (це не збій)")
        if OUT_FILE.exists():          # ключ зник/зіпсований після того, як файл уже був: залишки зникнуть через 3 год, картки через 3 доби — сигнал, не тиша
            rt._alert("kv_export_nokey", "⚠️ kv_supplier_export: KIDDISVIT_API_KEY зник із .env, а supplier_kv.json уже існує — залишки kv- зникнуть через 3 год, картки через 3 доби. Поверніть ключ.")
        sys.exit(0)
    sys.exit(0 if run_once(workers=PREFETCH_WORKERS, budget_s=PREFETCH_BUDGET_S, check_disk=True) else 1)
