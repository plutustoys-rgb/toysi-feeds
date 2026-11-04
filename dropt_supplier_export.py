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
REFRESH_MIN_AGE_S = 40 * 60   # картку «в наявності» (і будь-яку спробу читання) повторюємо, коли попередня спроба старша за 40 хв (годинний таймер → щоразу)
OOS_REFRESH_S = 4 * 3600      # обрані товари НЕ в наявності перечитуємо раз на 4 год (аудит #1099 п.D: вдвічі менше навантаження на Dropt, наявність важить для тих, що «є»)
ITEM_MAX_AGE_S = 3 * 3600     # у файл — лише картки, ціну/наявність яких читали не давніше (довше — вважаємо невідомою; у кожної картки є checked_at)
OTHER_TTL_S = 7 * 86400       # сторінки розділів (не-товари) перечитуємо раз на 7 діб
FOREIGN_TTL_S = 30 * 86400    # товари поза 7 розділами — раз на 30 діб (розділ товару змінюється рідко; їх ≈5 300)
GONE_TTL_S = 24 * 3600         # товар, що раніше був у каталозі, а тепер віддав 404/410, перевіряємо за добу (перехідний 404 не лишає картку без перевірки на 7 діб)
PRUNE_AFTER_S = 3 * 86400     # скільки адреса може бракувати в sitemap, перш ніж її запис видаляється з кешу
STALE_RELEASE_S = 12 * 3600   # гард усадки не тримає файл довше: якщо попередній запис старший — приймаємо нове (легітимне звуження, напр. Новий Рік після свят)
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
_POLICY_RE = re.compile(r"(?<!\w)(зброя\w*|зброї|зброю|пістолет\w*|револьвер\w*|рушниц\w*|гвинтів\w*|кулемет\w*|дробовик\w*|арбалет\w*|шабл\w*|катан[аиуі]?|тесак\w*|нунчак\w*|кастет\w*|\w*шокер\w*|травмат\w*|ракетниц\w*|газов\w+ балончик\w*|меч|мечі|мечик\w*|шпаг\w*|рогатк\w*|бластер\w*|вейп\w*|сигар\w*|тютюн\w*|петард\w*|піротехн\w*|еротик\w*|казино|для дорослих)(?!\w)", re.I)
_AVTOMAT_RE = re.compile(r"(?<![\w-])автомат(?![\w-])", re.I)        # «Іграшковий автомат», «Автомат іграшковий»; не «автоматична/автоматичний», не «Намет-автомат»
_AVTOMAT_OK_RE = re.compile(r"намет|палатк|тент|компресор|напувалк|годівниц|повідець|гойдалк|люльк|кавомашин|торгов", re.I)      # «автомат» у значенні «з автоматичним механізмом» (аудит #1099 п.6/E)
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
    if _AVTOMAT_RE.search(short) and not _AVTOMAT_OK_RE.search(short + " " + (category or "")):
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
    try:
        os.chmod(tmp, 0o600)          # у записах кешу — ціна «Дроп» (= cost): як у supplier_dropt.json (аудит #1099 п.C)
    except OSError:
        pass
    os.replace(tmp, p)


def is_selected(e):
    return isinstance(e, dict) and e.get("kind") == "product" and e.get("top") in dp.TOP_NAMES


def refresh(urls, cache, fetch, now_ts, time_budget=DETAIL_TIME_BUDGET_S, clock=time.monotonic, save=None):
    """Читає сторінки в порядку: (1) адреси, яких у кеші нема; (2) обрані товари В НАЯВНОСТІ з давньою спробою (найстаріші першими); (3) обрані НЕ в наявності (раз на OOS_REFRESH_S);
    (4) чужі товари (раз на FOREIGN_TTL_S) і сторінки розділів (раз на OTHER_TTL_S). Зупиняється за часовим бюджетом або після MAX_CONSEC_ERRORS мережевих збоїв поспіль.
    Правила кешу (аудит #1099 п.A/B): розбір None на раніше відомому ТОВАРІ не перезаписує запис (збій розбору ≠ «не товар»; повтор за REFRESH_MIN_AGE_S);
    404/410 → запис gone/other із датою (не повторюється щоразу, не рахується мережевим збоєм); решта помилок оновлює лише `try_at` — мертва адреса не стоїть першою в черзі вічно.
    Повертає {"read": n, "errors": n, "parse_failures": n, "aborted": bool}."""
    new, q_in, q_oos, q_other, q_err = [], [], [], [], []
    for u in urls:
        e = cache.get(u)
        if not isinstance(e, dict):
            new.append(u)
            continue
        age = now_ts - float(e.get("fetched_at") or 0)
        try_at = float(e.get("try_at") or e.get("fetched_at") or 0)
        since_try = now_ts - try_at
        if age < 0 or since_try < 0:                              # годинник пішов назад/запис «з майбутнього» → вважаємо застарілим і перечитуємо (аудит #1099)
            age, since_try = 1e12, 1e12
        key = try_at if since_try < 1e11 else 0.0                  # черга — за давністю ОСТАННЬОЇ СПРОБИ: збійна адреса після спроби йде в кінець (аудит #1099, 3-й раунд п.1)
        if is_selected(e):
            if e.get("in_stock"):
                if age > REFRESH_MIN_AGE_S and since_try > REFRESH_MIN_AGE_S:
                    q_in.append((key, u))
            elif age > OOS_REFRESH_S and since_try > REFRESH_MIN_AGE_S:
                q_oos.append((key, u))
        elif e.get("kind") == "product":
            ttl = FOREIGN_TTL_S if e.get("top") else OOS_REFRESH_S      # без розділу (нема BreadcrumbList) — не «чужий», коротко
            if age > ttl and since_try > REFRESH_MIN_AGE_S:
                q_other.append((key, u))
        elif e.get("kind") == "gone":
            if age > GONE_TTL_S and since_try > REFRESH_MIN_AGE_S:
                q_other.append((key, u))
        elif e.get("kind") == "other":
            if age > OTHER_TTL_S and since_try > REFRESH_MIN_AGE_S:
                q_other.append((key, u))
        elif since_try > REFRESH_MIN_AGE_S:                        # kind=error: адреса, яку ще жодного разу не вдалося прочитати
            q_err.append((key, u))
    for q in (q_in, q_oos, q_other, q_err):
        q.sort()                                                   # найстарша остання спроба — першою
    queue = new + [u for _a, u in q_in] + [u for _a, u in q_oos] + [u for _a, u in q_other] + [u for _a, u in q_err]
    t0, st, consec = clock(), {"read": 0, "errors": 0, "parse_failures": 0, "aborted": False}, 0
    for u in queue:
        if clock() - t0 > time_budget:
            break
        prev = cache.get(u) if isinstance(cache.get(u), dict) else None
        try:
            d = dp.parse_detail(fetch(u))
            consec = 0
        except Exception as ex:  # noqa: BLE001
            code = getattr(getattr(ex, "response", None), "status_code", None)
            if code in (404, 410):
                cache[u] = {"kind": "gone" if (prev and prev.get("kind") == "product") else "other", "fetched_at": now_ts}
                st["read"] += 1
                consec = 0
                continue
            st["errors"] += 1
            consec += 1
            print(f"[dropt_export] сторінка: {type(ex).__name__}{' ' + str(code) if code else ''}", file=sys.stderr)
            if prev is not None:
                prev["try_at"] = now_ts
            else:
                cache[u] = {"kind": "error", "try_at": now_ts}
            if consec >= MAX_CONSEC_ERRORS:
                st["aborted"] = True
                break
            continue
        if d is not None and not d.get("top") and prev and is_selected(prev):
            d = None                                         # сторінка без BreadcrumbList на раніше ОБРАНОМУ товарі = збій розбору, а не «чужий розділ» (аудит #1099, 3-й раунд п.2)
        if d is None and prev and prev.get("kind") == "product":
            st["parse_failures"] += 1                        # сторінка раніше була товаром, а тепер без JSON-LD (заглушка/зміна верстки): старий запис лишаємо
            prev["try_at"] = now_ts
            continue
        if d is None:
            cache[u] = {"kind": "other", "fetched_at": now_ts}
        elif d.get("top") in dp.TOP_NAMES:
            cache[u] = {"kind": "product", "fetched_at": now_ts, **d}
            if prev and prev.get("img_sig"):
                cache[u]["img_sig"] = prev["img_sig"]          # підпис фото, з якими вже качали файли: переживає перечитування (інакше зміну фото не помітити)
        else:
            cache[u] = {"kind": "product", "fetched_at": now_ts, "sku": d["sku"], "top": d.get("top") or "", "in_stock": False}      # чужий розділ: мінімальний запис (без опису/фото/ціни — кеш не роздувається)
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


def write_file(out, items, now, catalog_n):
    """Як rt_supplier_export._write_json + `min_checked_at` (найстаріша перевірка серед карток; `updated` показує момент запису файлу, а не свіжість карток — аудит #1099 п.D)."""
    payload = {"updated": now.isoformat(timespec="seconds"), "min_checked_at": min((v.get("checked_at") or 0) for v in items.values()) if items else 0, "catalog_n": catalog_n, "items": items}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, out)


def _top_counts(items):
    out = {}
    for v in items.values():
        t = str(v.get("category") or "").split(" / ")[0]
        out[t] = out.get(t, 0) + 1
    return out


def out_guard_ok(out, items, now_ts=0):
    """Усадка файлу (всього або одного розділу) >50 % проти попереднього запису → False + сигнал. Свідоме звуження — видалити supplier_dropt.json на VPS."""
    try:
        doc = json.loads(Path(out).read_text(encoding="utf-8"))
        prev = doc.get("items") or {}
        age = now_ts - datetime.fromisoformat(str(doc.get("updated")).replace("Z", "+00:00")).timestamp() if now_ts else 0
    except (OSError, ValueError, TypeError, AttributeError):
        return True
    if not isinstance(prev, dict) or not prev:
        return True
    if age > STALE_RELEASE_S:
        return True                        # попередній запис давній (≥12 год): легітимну зміну (сезон, зняття розділу) приймаємо — гард не тримає файл вічно
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


def fetch_image(url, dest):
    """Як rt_supplier_export.fetch_image, але з нашим User-Agent (аудит #1099: фото ходили зі стандартним python-requests)."""
    if rt._on_disk(dest):
        return True
    try:
        import io
        import requests
        from PIL import Image
        if not str(url).startswith(("http://", "https://")):
            return False
        r = requests.get(url, timeout=rt.IMG_TIMEOUT, stream=True, headers={"User-Agent": dp.USER_AGENT})
        r.raise_for_status()
        body = r.raw.read(rt.IMG_MAX_BYTES + 1, decode_content=True)
        if len(body) > rt.IMG_MAX_BYTES:
            raise ValueError("фото завелике")
        im = Image.open(io.BytesIO(body))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            im = bg
        im = im.convert("RGB")
        im.thumbnail((rt.IMG_MAX_SIDE, rt.IMG_MAX_SIDE))
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        im.save(tmp, "JPEG", quality=85, optimize=True)
        os.replace(tmp, dest)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[dropt_export] фото не отримано {str(url)[:80]}: {type(e).__name__}", file=sys.stderr)
        return False


def run_once(urls=None, cache=None, fetch=None, out=None, cache_path=None, img_dir=None, fetch_img=None, now=None, workers=1, budget_s=None, time_budget=DETAIL_TIME_BUDGET_S, clock=time.monotonic, check_disk=False):
    """True — файл записано. Збій мережі/sitemap, усадка, порожній результат → попередній файл лишається, False."""
    out, img_dir = Path(out or OUT_FILE), Path(img_dir or IMG_DIR)
    now = now or datetime.now(timezone.utc)
    now_ts = now.timestamp()
    try:
        fetch = fetch or dp.make_fetch()
        fetch_img = fetch_img or fetch_image
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
        for u in [u for u in cache if u not in live and now_ts - float((cache[u] or {}).get("fetched_at") or (cache[u] or {}).get("try_at") or 0) > PRUNE_AFTER_S]:
            del cache[u]                      # адреси, яких нема в sitemap УЖЕ понад 3 доби (усічений sitemap на годину не стирає кеш на 7 770 сторінок)
        save_cache(cache, cache_path)
        if st["aborted"]:
            _alert("dropt_export_down", f"⚠️ dropt_supplier_export: {MAX_CONSEC_ERRORS} збоїв читання поспіль — Dropt недоступний або частина адрес віддає помилки? Читання зупинено (прочитано {st['read']}); файл збираємо з того, що вже в кеші й свіже (ITEM_MAX_AGE_S).")
        cand = []
        for u, e in cache.items():
            if u not in live:
                continue                          # адреси, яких уже нема в sitemap, не публікуємо (їх запис ще 3 доби лежить у кеші лише на випадок усіченого sitemap)
            if is_selected(e) and e.get("in_stock") and 0 <= now_ts - float(e.get("fetched_at") or 0) <= ITEM_MAX_AGE_S and e.get("images"):
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
                it["checked_at"] = int(e.get("fetched_at") or 0)        # коли саме прочитано ціну/наявність цієї картки (споживач сам вирішує, що вважати застарілим)
                items[sid] = it
            return items, skipped

        disk = {p: True for _u, p in tasks if rt._on_disk(p)}
        if disk and not todo:
            got = disk
        else:
            # проміжний запис із фото, що вже на диску: картки з готовими фото не чекають докачування (прогін, убитий таймаутом юніта, не лишає файл старим)
            first, _s = build_all(disk)
            if first and out_guard_ok(out, first, now_ts):
                write_file(out, first, now, len(urls))
            got = {**disk, **rt.prefetch(todo, fetch_img, workers, budget_s)}
        items, skipped = build_all(got)
        if not items:
            _alert("dropt_export_none", f"⚠️ dropt_supplier_export: жодної картки не зібрано (кандидатів {len(cand)}, прочитано сторінок {st['read']}) — supplier_dropt.json НЕ оновлено.")
            return False
        if not out_guard_ok(out, items, now_ts):
            return False
        write_file(out, items, now, len(urls))
        _prune_images(img_dir, {f"dropt-{e['sku']}" for e in cache.values() if is_selected(e) and e.get("in_stock")}, now_ts)
        unread = sum(1 for u in urls if (cache.get(u) or {}).get("kind") in (None, "error"))
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
