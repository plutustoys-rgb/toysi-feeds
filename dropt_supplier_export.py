# -*- coding: utf-8 -*-
"""dropt_supplier_export.py — дані Dropt для сайту й фіду EVA (рішення власника 04.11.2026: 7 розділів, закупівля ЛИШЕ «Дроп», формула ціни сайту та сама, що для RoyalToys, розміщення — сайт + фід EVA).

Контракт (той самий, що supplier_rt.json): {"updated": ISO UTC, "catalog_n": N, "items": {"dropt-<артикул Dropt>": {name, description, vendor, vendor_code, category, images[https://…], price, cost, stock, dims_cm, weight_kg, index, source_url}}}.
Правила: cost = ціна «Дроп» (JSON-LD offers.price сторінки товару); price = закрита формула сайту max(cost×1,5; (cost+15)×1,03) вгору до гривні (rt_supplier_export.site_price — одне місце правди);
у файл — лише картки «в наявності» (JSON-LD availability == InStock; усе інше — нема; кількість Dropt не дає → stock = STOCK_IN), у 7 розділах, з українською назвою, ≥1 фото, поза політикою «не продаємо».
ДЖЕРЕЛО (аудит #1099): robots.txt Dropt забороняє списки ?limit/?page, тож береться sitemap.xml → сторінки товарів. Кеш `dropt_detail_cache.json` {url: запис}: нові адреси читаються першими (перший прогін ≈ 7 800 сторінок за кілька годин),
далі — ціна/наявність обраних товарів оновлюються найстарішими першими в межах часового бюджету; у файл потрапляє лише картка, прочитана не давніше ITEM_MAX_AGE_S (ціна/наявність — fail-closed).
dims_cm і weight_kg = None: Dropt габаритів упаковки публічно не дає (потрібні лише конвеєру замовлень — окремий етап). index=False (noindex), поки SEO не додасть id у dropt_index_ids.json.
Файл читає site/build_site.py (Shopify-code) і rt_eva-аналог для EVA — ЦЕЙ модуль їх не чіпає. Гарди: sitemap < 1000 адрес / збій мережі / усадка файлу чи розділу >50 % / жодної картки → попередній файл НЕ перезаписується + сигнал.
Лише читання публічних сторінок. Запуск: dropt-supplier-export.timer щогодини."""
import hashlib
import json
import math
import os
import re
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
SITEMAP_URL = dp.BASE + "/sitemap.xml"
MIN_SITEMAP_URLS = 1000       # виміряно 04.11: ≈7 800 адрес /ua/; менше — sitemap усічений/підмінений → нічого не чіпаємо
MAX_IMAGES = 3
STOCK_IN = 3                  # Dropt публічно показує лише «є / нема в наявності» — кількість невідома; 3 = обережний віртуальний залишок (кошик не дасть замовити більше)
DETAIL_TIME_BUDGET_S = 1200   # час на читання сторінок за запуск; решта — наступним (TimeoutStartSec юніта 2700 = 1200 + фото 600 + запас)
REFRESH_MIN_AGE_S = 40 * 60   # обрану картку перечитуємо, коли її запис старший за 40 хв (годинний таймер → щоразу)
ITEM_MAX_AGE_S = 12 * 3600    # у файл — лише картки, ціну/наявність яких читали не давніше (довше — вважаємо невідомою)
OTHER_TTL_S = 7 * 86400       # не-товари й товари поза 7 розділами перевіряємо раз на 7 діб (розділ товару змінюється рідко)
SAVE_EVERY = 100              # кеш на диск кожні N сторінок: прогін, убитий таймаутом, не губить прогрес
MAX_CONSEC_ERRORS = 25        # стільки збоїв поспіль = Dropt недоступний, зупиняємо читання
MIN_KEEP_RATIO = 0.5          # (запозичено з rt_supplier_export, не виміряно окремо) усадка файлу/розділу >50 % → файл усічений, не перезаписуємо
IMG_KEEP_DAYS = 30            # фото знятих із публікації карток видаляємо через 30 діб (стоки мерехтять)
PREFETCH_WORKERS = 4
PREFETCH_BUDGET_S = 600
MIN_FREE_GB = 10
_ID_RE = re.compile(r"dropt-\d+")      # окремий префікс (не плутається з rt-/Toysi)

_SEP_RE = re.compile(r"\s[•∙·]\s")      # Dropt ріже назву на «основну • підзаголовок» трьома різними крапками (аудит #1099)
_DESC_CUT_RE = re.compile(r"^(Як замовити|Залиште заявку|Заходьте|Вдалих покупок|Доставка|Оплата)\b", re.I)    # шаблонний хвіст Dropt про замовлення/доставку/оплату — для нашого сайту чужі умови
_DESC_DROP_RE = re.compile(r"на нашому сайті|Dropt|dropt\.in\.ua", re.I)
_RU_FUNC_RE = re.compile(r"(?<![А-Яа-яІіЇїЄєҐґ'’])(?:и|или|что|это|наушник\w*|щетк\w*|груминг\w*|кошек|собак(?=\s+и\b))(?![А-Яа-яІіЇїЄєҐґ])")   # «и/или/что/это» в українській не існують; решта — слова, що вже слизнули з Dropt (аудит #1099)
_POLICY_RE = re.compile(r"(?<!\w)(зброя\w*|зброї|зброю|пістолет\w*|рушниц\w*|арбалет\w*|шабл\w*|катан[аиуі]?|меч|мечі|мечик\w*|шпаг\w*|рогатк\w*|бластер\w*|вейп\w*|сигар\w*|тютюн\w*|петард\w*|піротехн\w*|еротик\w*|казино|для дорослих)(?!\w)", re.I)
_KNIFE_RE = re.compile(r"(?<!\w)(ніж|ножі|ножик\w*|мультиніж\w*|мачете|кинджал\w*)(?!\w)", re.I)
_KNIFE_OK_RE = re.compile(r"кухонн|для кавуна|для хліба|для сиру|для піци|для овоч|для фрукт|кухн", re.I)       # кухонні ножі — не зброя


def _alert(key, text):
    rt._alert(key, text)


def policy_blocked(short, full="", category=""):
    """True — не продаємо (зброя/18+/небезпечне). Перевіряється КОРОТКА назва (без підзаголовка: аудит #1099 — повна давала подвійні хибні спрацювання); вузькі регекси замість RT-шних (`автомат`, `феєрверк`, `катання` ловили невинне)."""
    names = [n for n in (short, full) if n]
    try:
        import generate_google_feed as gg
        if any(gg.is_weapon_replica("dropt-x", n) or gg.is_adult_product(n) for n in names[:1]):
            return True
    except ImportError:
        pass
    if _POLICY_RE.search(short):
        return True
    if _KNIFE_RE.search(short) and not _KNIFE_OK_RE.search(short + " " + (full or "")):
        return True
    return False


def is_russian(text):
    return bool(rt._RU_LETTERS_RE.search(text) or rt._RU_WORDS_RE.search(text) or _RU_FUNC_RE.search(text) or (rt._RU_LANG_MARK_RE.search(text) and not rt._UA_LANG_MARK_RE.search(text)))


def clean_description(desc, short=""):
    """Опис Dropt без SEO-дубля заголовка в першому рядку і без шаблонного хвоста про замовлення/доставку/оплату."""
    lines = rt.plain_text(desc or "").split("\n")
    if lines and _SEP_RE.search(" " + lines[0] + " ") and len(lines[0]) < 300:
        lines = lines[1:]
    for i, ln in enumerate(lines):
        if i >= 1 and _DESC_CUT_RE.match(ln.strip()):
            lines = lines[:i]
            break
    lines = [ln for ln in lines if not _DESC_DROP_RE.search(ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def load_index_ids(path=None):
    try:
        d = json.loads(Path(path or INDEX_FILE).read_text(encoding="utf-8"))
        return {str(i) for i in (d.get("ids") or []) if _ID_RE.fullmatch(str(i))} if isinstance(d, dict) else set()
    except (OSError, ValueError):
        return set()


def load_cache(path=None):
    try:
        d = json.loads(Path(path or DETAIL_CACHE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(cache, path=None):
    p = Path(path or DETAIL_CACHE)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, p)


def is_selected(e):
    return isinstance(e, dict) and e.get("kind") == "product" and e.get("top") in dp.TOP_NAMES


def refresh(urls, cache, fetch, now_ts, time_budget=DETAIL_TIME_BUDGET_S, clock=time.monotonic, save=None):
    """Читає сторінки в такому порядку: (1) адреси, яких у кеші нема; (2) обрані товари, чий запис старший за REFRESH_MIN_AGE_S (найстаріші першими); (3) решта, що старша за OTHER_TTL_S.
    Зупиняється за часовим бюджетом або після MAX_CONSEC_ERRORS збоїв поспіль. Повертає {"read": n, "errors": n, "aborted": bool}."""
    new, stale_sel, stale_other = [], [], []
    for u in urls:
        e = cache.get(u)
        if not isinstance(e, dict):
            new.append(u)
            continue
        age = now_ts - float(e.get("fetched_at") or 0)
        if is_selected(e):
            if age > REFRESH_MIN_AGE_S:
                stale_sel.append((age, u))
        elif age > OTHER_TTL_S:
            stale_other.append((age, u))
    stale_sel.sort(reverse=True)
    stale_other.sort(reverse=True)
    queue = new + [u for _a, u in stale_sel] + [u for _a, u in stale_other]
    t0, st, consec = clock(), {"read": 0, "errors": 0, "aborted": False}, 0
    for u in queue:
        if clock() - t0 > time_budget:
            break
        try:
            d = dp.parse_detail(fetch(u))
            consec = 0
        except Exception as ex:  # noqa: BLE001
            st["errors"] += 1
            consec += 1
            print(f"[dropt_export] сторінка: {type(ex).__name__}", file=sys.stderr)
            if consec >= MAX_CONSEC_ERRORS:
                st["aborted"] = True
                break
            continue
        prev_sig = (cache.get(u) or {}).get("img_sig")                 # підпис фото, з якими вже качали файли: переживає перечитування сторінки (інакше зміну фото не помітити)
        cache[u] = ({"kind": "product", "fetched_at": now_ts, **d} if d else {"kind": "other", "fetched_at": now_ts})
        if d and prev_sig:
            cache[u]["img_sig"] = prev_sig
        st["read"] += 1
        if save and st["read"] % SAVE_EVERY == 0:
            save(cache)
    return st


def build_item(sid, e, images):
    """Елемент supplier_dropt.json або None (не публікуємо). Чиста функція (без мережі). e — запис кешу (parse_detail + kind/fetched_at)."""
    if not _ID_RE.fullmatch(sid) or not e.get("in_stock") or not images:
        return None
    full = (e.get("name") or "").strip()
    short = _SEP_RE.split(" " + full + " ", 1)[0].strip()
    if len(short) < 3 or not re.search(r"\w", short) or is_russian(short):
        return None                      # назва порожня/службова або російська — картку не публікуємо
    try:
        cost = float(e.get("price"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(cost) or cost <= 0:
        return None
    if policy_blocked(short, full, e.get("category") or ""):
        return None
    desc = clean_description(e.get("description"), short)
    if is_russian(desc):
        desc = ""                        # російський опис не показуємо (картка лишається без блоку «Опис»)
    return {"name": short, "description": desc, "vendor": (e.get("brand") or "").strip(), "vendor_code": sid[6:], "category": (e.get("category") or "").strip(),
            "images": images, "price": rt.site_price(cost), "cost": round(cost, 2), "stock": STOCK_IN, "dims_cm": None, "weight_kg": None, "source_url": e.get("url") or ""}


def _top_counts(items):
    out = {}
    for v in items.values():
        t = str(v.get("category") or "").split(" / ")[0]
        out[t] = out.get(t, 0) + 1
    return out


def out_guard_ok(out, items):
    """Усадка файлу (всього або одного розділу) >50 % проти попереднього запису → False + сигнал. Свідоме звуження — видалити supplier_dropt.json на VPS."""
    try:
        prev = json.loads(Path(out).read_text(encoding="utf-8")).get("items") or {}
    except (OSError, ValueError, TypeError, AttributeError):
        return True
    if not isinstance(prev, dict) or not prev:
        return True
    why = None
    if len(prev) >= 50 and len(items) < len(prev) * MIN_KEEP_RATIO:
        why = f"усього {len(items)} проти {len(prev)}"
    else:
        now = _top_counts(items)
        for t, n in _top_counts(prev).items():
            if n >= 10 and now.get(t, 0) < n * MIN_KEEP_RATIO:
                why = f"розділ «{t}»: {now.get(t, 0)} проти {n}"
                break
    if why:
        _alert("dropt_export_shrink", f"⚠️ dropt_supplier_export: у файлі стало набагато менше карток ({why}; поріг {int(MIN_KEEP_RATIO * 100)}%) — читання усічене або Dropt змінив сайт? supplier_dropt.json НЕ перезаписано. Свідоме звуження — видалити supplier_dropt.json на VPS.")
        return False
    return True


def _sig(images):
    return hashlib.sha1("|".join(images[:MAX_IMAGES]).encode("utf-8")).hexdigest()[:12]


def run_once(urls=None, cache=None, fetch=None, out=None, cache_path=None, img_dir=None, fetch_img=rt.fetch_image, now=None, workers=1, budget_s=None, time_budget=DETAIL_TIME_BUDGET_S, clock=time.monotonic, check_disk=False):
    """True — файл записано. Збій мережі/sitemap, усадка, порожній результат → попередній файл лишається, False."""
    out, img_dir = Path(out or OUT_FILE), Path(img_dir or IMG_DIR)
    now = now or datetime.now(timezone.utc)
    now_ts = now.timestamp()
    try:
        fetch = fetch or dp.make_fetch()
        if urls is None:
            try:
                urls = dp.parse_sitemap(fetch(SITEMAP_URL, timeout=120) if _accepts_timeout(fetch) else fetch(SITEMAP_URL))
            except Exception as e:  # noqa: BLE001
                _alert("dropt_export_sitemap", f"⚠️ dropt_supplier_export: sitemap Dropt недоступний ({type(e).__name__}) — supplier_dropt.json НЕ оновлено.")
                return False
        if len(urls) < MIN_SITEMAP_URLS:
            _alert("dropt_export_sitemap", f"⚠️ dropt_supplier_export: у sitemap Dropt лише {len(urls)} адрес (очікується ≥{MIN_SITEMAP_URLS}) — файл усічений/змінений, supplier_dropt.json НЕ оновлено.")
            return False
        cache = load_cache(cache_path) if cache is None else cache
        st = refresh(urls, cache, fetch, now_ts, time_budget, clock, save=lambda c: save_cache(c, cache_path))
        live = set(urls)
        for u in [u for u in cache if u not in live]:
            del cache[u]                      # адреси, яких уже нема в sitemap
        save_cache(cache, cache_path)
        if st["aborted"]:
            _alert("dropt_export_down", f"⚠️ dropt_supplier_export: {MAX_CONSEC_ERRORS} збоїв читання поспіль — Dropt недоступний? Читання зупинено (прочитано {st['read']}), файл не перезаписується.")
            return False
        cand = []
        for u, e in cache.items():
            if is_selected(e) and e.get("in_stock") and now_ts - float(e.get("fetched_at") or 0) <= ITEM_MAX_AGE_S and e.get("images"):
                e = dict(e, url=u)
                cand.append(("dropt-" + e["sku"], e, cache[u]))
        tasks = []
        for sid, e, ce in cand:
            sig = _sig(e["images"])
            if ce.get("img_sig") not in (None, sig):          # Dropt змінив фото — старі файли (з іншими індексами) прибираємо
                for p in img_dir.glob(f"{sid}-*.jpg"):
                    p.unlink(missing_ok=True)
            ce["img_sig"] = sig
            for n, url in enumerate(e["images"][:MAX_IMAGES], 1):
                tasks.append((url, img_dir / f"{sid}-{n}.jpg"))
        save_cache(cache, cache_path)
        todo = [(u, p) for u, p in tasks if not rt._on_disk(p)]
        if check_disk and todo:
            import shutil
            free_gb = shutil.disk_usage(img_dir.parent if img_dir.parent.exists() else HERE).free / 1e9
            if free_gb < MIN_FREE_GB:
                _alert("dropt_export_disk", f"⚠️ dropt_supplier_export: вільно {free_gb:.1f} ГБ < {MIN_FREE_GB} ГБ — нових фото dropt- не качаємо.")
                todo = []
        index_set = load_index_ids()
        rejected = []

        def build_all(got):
            items, skipped = {}, 0
            for sid, e, _ce in cand:
                imgs = [IMG_URL.format(name=f"{sid}-{n}.jpg") for n, _u in enumerate(e["images"][:MAX_IMAGES], 1) if got.get(img_dir / f"{sid}-{n}.jpg")]
                it = build_item(sid, e, imgs)
                if it is None:
                    if imgs:
                        skipped += 1
                        if len(rejected) < 8:
                            rejected.append(str(e.get("name") or "")[:50])
                    continue
                it["index"] = sid in index_set
                items[sid] = it
            return items, skipped

        disk = {p: True for _u, p in tasks if rt._on_disk(p)}
        if disk and not todo:
            got = disk
        else:
            # проміжний запис із фото, що вже на диску: картки з готовими фото не чекають докачування (прогін, убитий таймаутом юніта, не лишає файл старим)
            first, _s = build_all(disk)
            if first and out_guard_ok(out, first):
                rt._write_json(out, first, now, len(urls))
            got = {**disk, **rt.prefetch(todo, fetch_img, workers, budget_s)}
        items, skipped = build_all(got)
        if not items:
            _alert("dropt_export_none", f"⚠️ dropt_supplier_export: жодної картки не зібрано (кандидатів {len(cand)}, прочитано сторінок {st['read']}) — supplier_dropt.json НЕ оновлено.")
            return False
        if not out_guard_ok(out, items):
            return False
        rt._write_json(out, items, now, len(urls))
        _prune_images(img_dir, {f"dropt-{e['sku']}" for e in cache.values() if is_selected(e) and e.get("in_stock")}, now_ts)
        unread = sum(1 for u in urls if u not in cache)
        print(f"[dropt_export] ✅ {len(items)} карток у файлі (кандидатів {len(cand)}, відсіяно правилами {skipped}{": " + "; ".join(rejected) if rejected else ""}); сторінок: прочитано цього разу {st['read']}, збоїв {st['errors']}, ще не читано {unread}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[dropt_export] збій: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def _accepts_timeout(fetch):
    import inspect
    try:
        return "timeout" in inspect.signature(fetch).parameters
    except (TypeError, ValueError):
        return False


def _prune_images(img_dir, keep_sids, now_ts):
    """Фото карток, що давно не в наявності/не в 7 розділах, видаляються через IMG_KEEP_DAYS (диск не росте безкінечно)."""
    try:
        for p in img_dir.glob("dropt-*.jpg"):
            sid = p.name.rsplit("-", 1)[0]
            if sid not in keep_sids and now_ts - p.stat().st_mtime > IMG_KEEP_DAYS * 86400:
                p.unlink(missing_ok=True)
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(0 if run_once(workers=PREFETCH_WORKERS, budget_s=PREFETCH_BUDGET_S, check_disk=True) else 1)
