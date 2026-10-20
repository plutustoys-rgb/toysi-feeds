# -*- coding: utf-8 -*-
"""rt_supplier_export.py — дані RoyalToys для сайту (рішення власника 19.10.2026: спершу пілот, потім — «увесь каталог РТ зі своїми артикулами, а перетин по назвах вирішимо потім»; режим у rt_selection.json, за замовчуванням лише пілот): картки `rt-<id>` → ПРИВАТНИЙ <корінь>/supplier_rt.json (ПОЗА DocumentRoot: там опт "cost"), фото на НАШОМУ сервері site/img/rt/<id>-N.jpg.

Контракт з Shopify-code (site/build_site.py `_load_supplier_rt`): {"updated": ISO UTC, "items": {"rt-<id>": {name, description, vendor, vendor_code, category (рос. назва RoyalToys), images[https://…], price, cost, stock}}}.
Правила: назва й опис ЛИШЕ українські (name_ua/description_ua; нема name_ua → картки нема); ціна сайту за ЗАКРИТОЮ формулою max(опт×1,5; (опт+15)×1,03), вгору до гривні; відбір — пілот (rt_pilot_ids.json: лише id, без грошей, завжди) + режим rt_selection.json;
позиція без фото на диску, з нульовою/нечисловою ціною — не публікується. stock = 0, якщо offer не available. YML RoyalToys порожній/недоступний → попередній файл НЕ перезаписується (build_site сам вимкне картки, коли "updated" старший за 3 доби — fail-closed), сигнал раз на добу.
Файл пишеться атомарно, права 0600. Запуск: rt-supplier-export.timer щогодини (залишки в YML RoyalToys оновлюються щогодини). stock_snapshot.py додає в site/stock.json ключі rt-<id> з цього файлу."""
import html
import io
from concurrent.futures import ThreadPoolExecutor
import time
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
OUT_FILE = Path(os.environ.get("SUPPLIER_RT_FILE", str(HERE / "supplier_rt.json")))
IDS_FILE = HERE / "rt_pilot_ids.json"
IMG_DIR = Path(os.environ.get("SITE_DIR", str(HERE / "site"))) / "img" / "rt"
IMG_URL = "https://plutustoys.com.ua/img/rt/{name}"
MAX_IMAGES = 3
SELECTION_FILE = HERE / "rt_selection.json"
DUP_FILE = HERE / "rt_dup_map.json"
INDEX_FILE = HERE / "rt_index_ids.json"
PREFETCH_WORKERS = 6
PREFETCH_BUDGET_S = 900       # ліміт часу на завантаження фото за один запуск (виміряно аудитом ≈10 фото/с на 6 потоках): решта — наступного разу; TimeoutStartSec юніта (2400) з запасом перекриває бюджет + таймаути запитів + запис
MIN_FREE_GB = 10              # запас диска (Shopify-code, замір VPS: 30 ГБ вільно): менше → нових фото не качаємо
MIN_KEEP_RATIO = 0.5          # (запозичено з гарду розміру каталогу Toysi, не виміряно окремо) нових карток менше половини попереднього файлу → не перезаписуємо, сигнал; річні коливання YML ≈0,1%
IMG_MAX_SIDE = 1200
IMG_TIMEOUT = 30
IMG_MAX_BYTES = 15 * 1024 * 1024
_ID_RE = re.compile(r"rt-[0-9A-Za-z_]+")      # як build_site._RT_ID_RX


def site_price(cost: float) -> int:
    """Закрита формула ціни сайту (власник 13.10.2026): max(опт×1,5; (опт+15)×1,03), вгору до гривні. НЕ змінювати без слова власника."""
    return int(math.ceil(max(cost * 1.5, (cost + 15) * 1.03)))


def _alert(key: str, text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=24 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[rt_export] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


_TAG_RE = re.compile(r"<[^>]+>")
_RU_WORD_STEMS = (r"набор|набором|наборы|игрушк\w*|игрушек|детск(?:ий|ая|ое|ие|ого|ую)|детей|ребенк\w*|цвет|цвета|цвету|цветов|размер\w*|мальчик\w*|девоч\w*|девочек|проведени\w+|раскоп\w*|расписани\w*|ламинирован\w*|деревянн(?:ая|ое|ый|ые|ую)|развивающ\w+|подарочн\w+")
_RU_WORDS_RE = re.compile(r"(?<![а-яіїєґ'’-])(?:" + _RU_WORD_STEMS + r")(?![а-яіїєґ])", re.I)       # однозначно російські слова (в укр. їх нема; «наборі/наборах» — укр. місцевий відмінок — НЕ збігаються)
# Мовний маркер у назві (рішення власника 20.10: «російськомовні прибери»; ті самі правила, що generate_prom_feed/rozetka_feed._RUSSIAN_LANG_RE + виняток для двомовних «рос. і укр. мовами»)
_CYR = "А-Яа-яІіЇїЄєҐґ"
_RU_LANG_MARK_RE = re.compile(rf"(?<![{_CYR}])(?:рос|рус)(?![{_CYR}])|російською|російськомов|рос\.\s*звуч", re.I)
_UA_LANG_MARK_RE = re.compile(r"укр", re.I)
_RU_LETTERS_RE = re.compile(r"[ыэъёЫЭЪЁ]")      # літери, яких нема в українській: поле name_ua/description_ua у постачальника інколи російське (виявлено 19.10: rt-46827) — опис не показуємо (картка піде без блоку «Опис» і з noindex у Shopify-code)


def plain_text(raw: str) -> str:
    """Опис RoyalToys інколи містить HTML-теги: лишаємо чистий текст (теги прибрано, сутності розкодовано, абзаци збережено)."""
    t = re.sub(r"(?i)<\s*(?:br\s*/?|/p|/li|/div)\s*>", "\n", raw or "")
    t = html.unescape(_TAG_RE.sub("", t))
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


# Політика «що не продаємо» для позицій RoyalToys (категорії RoyalToys — рос. назви; forbidden_products.is_forbidden ключується категоріями Toysi, тому тут ще й власні правила):
# ЗБРОЯ/18+/небезпечне — виключення за політикою (власник 07.10: «приберіть»); «НЕ ІГРАШКИ» — окрема група (книги, зошити, ручки, фітнес, електромобілі, уцінка, аксесуари) — керується rt_selection.json, бо це не політика, а асортимент.
_POLICY_RE = re.compile(r"оружи|пистолет|автомат|ружь|арбалет|лук\b|(?<!\w)нож(?:а|у|ом|е|и|ей|ам|ами|ах|ик\w*)?(?!\w)|меч\b|эроти|18\+|вейп|сигар|табак|алкогол|зажигал|петард|фейерверк", re.I)
_POLICY_UA_RE = re.compile(r"(?<!\w)(зброя\w*|зброє\w*|зброю|зброї|шпаг(?:а|и|ою|і|ам|ами)(?!\w)|шпаґ\w*|рогатк\w*|бластер\w*|покер\w*|казино|для дорослих|ніж|ножі|ножик\w*|меч|мечі|мечик\w*|шабл\w*|катан\w*|арбалет\w*|рушниц\w*|пістолет\w*|автомат\w*|зброя|зброї|еротик\w*|вейп\w*|сигар\w*|тютюн\w*|петард\w*|феєрверк\w*)(?!\w)", re.I)   # українські назви (name_ua)
_NOT_TOY_NAME_RE = re.compile(r"(?<!\w)(зошит\w*|блокнот\w*|олівц\w*|ручк[аи]\b|книг\w*|гантел\w*)(?!\w)", re.I)   # за назвою — лише явні слова (аудит: «ручк» давало хибні «не іграшки»: кошики з ручками)
_NOT_TOY_RE = re.compile(r"книг|тетрад|блокнот|фитнес|электромобил|уценен|аксессуары для|канцеляр|ручк|карандаш", re.I)


_POLICY_WARNED = False


def policy_reason(v: dict) -> str | None:
    """'policy' (не продаємо), 'not_toy' (не іграшка) або None. Чиста функція."""
    names = [x for x in (v.get("name_ua"), v.get("name")) if x]       # перевіряємо обидві назви (укр. і рос.)
    cat = v.get("category_name") or ""
    try:
        import generate_google_feed as gg
        from forbidden_products import is_forbidden
        if is_forbidden({"id": v.get("id"), "category_name": cat}) or any(gg.is_weapon_replica("rt-" + str(v.get("id")), n) or gg.is_adult_product(n) for n in names):
            return "policy"
    except ImportError as e:
        global _POLICY_WARNED
        if not _POLICY_WARNED:
            _POLICY_WARNED = True
            print(f"[rt_export] ⚠ спільні перевірки політики недоступні ({e}) — працюють лише локальні регекси", file=sys.stderr)
    if _POLICY_RE.search(cat) or any(_POLICY_RE.search(n) or _POLICY_UA_RE.search(n) for n in names):
        return "policy"
    if _NOT_TOY_RE.search(cat) or _NOT_TOY_NAME_RE.search(names[0] if names else ""):
        return "not_toy"
    return None


def load_selection(path: Path = SELECTION_FILE) -> dict:
    """rt_selection.json: {"mode": "pilot"|"full", "min_cost": число|null, "min_stock": число|null, "exclude_not_toys": bool}. Нема/битий файл → лише пілот (fail-safe: нічого не розширюємо мовчки)."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if d.get("mode") not in ("pilot", "full"):
            raise ValueError
        return d
    except (OSError, ValueError):
        return {"mode": "pilot"}


def load_index_ids(path: Path = INDEX_FILE) -> set:
    """rt_index_ids.json {"ids": ["rt-…"]} — картки, відкриті для Google (хвилі індексації; веде SEO). Нема файлу → порожньо."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return {str(i) for i in (d.get("ids") or []) if _ID_RE.fullmatch(str(i))} if isinstance(d, dict) else set()
    except (OSError, ValueError):
        return set()


def load_dup_map(path: Path = DUP_FILE) -> dict:
    """rt_dup_map.json {"rt-<id>": "<id товару Toysi>"} — майбутнє «дубль із товаром Toysi» (рішення про 301/canonical — пізніше, власник: спершу весь каталог, перетин потім). Нема файлу → {}."""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in d.items() if _ID_RE.fullmatch(str(k))} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def select_ids(catalog: dict, pilot_ids: list, sel: dict) -> list:
    """Упорядкований список id до публікації: спершу пілот (завжди), далі — режим full: решта каталогу за політикою й фільтрами з rt_selection.json, за спаданням внеску (ціннішим фото качаємо першими)."""
    out = list(dict.fromkeys(pilot_ids))
    if sel.get("mode") != "full":
        return out
    seen = set(out)
    extra = []
    for rid, v in catalog.items():
        sid = "rt-" + str(rid)
        if sid in seen or not _ID_RE.fullmatch(sid):
            continue
        reason = policy_reason(v)
        if reason == "policy" or (reason == "not_toy" and sel.get("exclude_not_toys", True)):
            continue
        try:
            cost = float(str(v.get("price") or "").replace(",", "."))
        except ValueError:
            continue
        if not math.isfinite(cost) or cost <= 0:
            continue
        mc, ms = sel.get("min_cost"), sel.get("min_stock")
        if mc is not None and cost < float(mc):
            continue
        try:
            stock = int(v.get("stock") or 0)
        except (TypeError, ValueError):
            continue
        if not v.get("available") or stock <= 0:
            continue                                 # немає в наявності — картку не публікуємо (у full)
        if ms is not None and stock < int(ms):
            continue
        sp = site_price(cost)
        extra.append((sp - cost - sp * 0.028, sid))
    extra.sort(key=lambda x: (-x[0], x[1]))
    return out + [sid for _, sid in extra]


def _on_disk(dest: Path) -> bool:
    try:
        return dest.exists() and dest.stat().st_size > 0
    except OSError:
        return False


def prefetch(tasks: list, fetch, workers: int = 1, budget_s: float | None = None) -> dict:
    """tasks=[(url, dest)] → {dest: bool}. Паралельно (workers), з бюджетом часу: що не встигли — False (досягнемо наступного запуску). Фото, що вже на диску, — True незалежно від бюджету; виняток у fetch → False (не валить прогін)."""
    res = {}
    t0 = time.monotonic()

    def one(t):
        url, dest = t
        if _on_disk(dest):
            return dest, True
        if budget_s is not None and time.monotonic() - t0 > budget_s:
            return dest, False
        try:
            return dest, bool(fetch(url, dest))
        except Exception as e:  # noqa: BLE001
            print(f"[rt_export] fetch збій {type(e).__name__}", file=sys.stderr)
            return dest, False

    if workers <= 1:
        for t in tasks:
            d, ok = one(t)
            res[d] = ok
        return res
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for d, ok in ex.map(one, tasks):
            res[d] = ok
    return res


def load_pilot_ids(path: Path = IDS_FILE) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = [str(i) for i in data.get("ids") or [] if _ID_RE.fullmatch(str(i))]
    if not ids:
        raise ValueError("rt_pilot_ids.json: порожній список")
    return ids


def build_item(rid: str, v: dict, images: list) -> dict | None:
    """Елемент supplier_rt.json або None (не публікуємо). Чиста функція (без мережі)."""
    name = (v.get("name_ua") or "").strip()
    if not name or not images or _RU_LETTERS_RE.search(name) or _RU_WORDS_RE.search(name) or (_RU_LANG_MARK_RE.search(name) and not _UA_LANG_MARK_RE.search(name)):
        return None          # укр. назва порожня або насправді російська (ы э ъ ё) — картку не публікуємо
    try:
        cost = float(str(v.get("price") or "").replace(",", "."))
    except ValueError:
        return None
    if cost <= 0 or not math.isfinite(cost):
        return None
    try:
        stock = max(0, int(v.get("stock") or 0)) if v.get("available") else 0
    except (TypeError, ValueError):
        stock = 0
    dims = v.get("dims_cm")
    if not dims or len(dims) != 3 or not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in dims):
        return None      # без габаритів картку не публікуємо (ТТН потребує розмірів; рішення Коду 19.10, чекаємо підтвердження RoyalToys)
    wt = v.get("weight_kg")
    return {"name": name, "description": ("" if (_RU_LETTERS_RE.search(v.get("description_ua") or "") or _RU_WORDS_RE.search(v.get("description_ua") or "")) else plain_text(v.get("description_ua"))), "vendor": (v.get("vendor") or "").strip(), "vendor_code": (v.get("vendor_code") or "").strip(),
            "category": (v.get("category_name") or "").strip(), "images": images, "price": site_price(cost), "cost": round(cost, 2), "stock": stock,
            "dims_cm": [round(float(x), 1) for x in dims], "weight_kg": round(float(wt), 3) if isinstance(wt, (int, float)) and math.isfinite(wt) and wt > 0 else None}


def fetch_image(url: str, dest: Path) -> bool:
    """Завантажує фото, стискає до JPEG ≤1200 px по довшій стороні. Є файл — не качаємо вдруге. Збій → False (картка піде без цього фото)."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    try:
        import requests
        from PIL import Image
        if not str(url).startswith(("http://", "https://")):
            return False
        r = requests.get(url, timeout=IMG_TIMEOUT, stream=True)
        r.raise_for_status()
        body = r.raw.read(IMG_MAX_BYTES + 1, decode_content=True)
        if len(body) > IMG_MAX_BYTES:
            raise ValueError("фото завелике")
        im = Image.open(io.BytesIO(body))
        if im.mode in ("RGBA", "LA", "P"):         # прозорі PNG — на білий фон (convert("RGB") дав би чорний)
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            im = bg
        im = im.convert("RGB")
        im.thumbnail((IMG_MAX_SIDE, IMG_MAX_SIDE))
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        im.save(tmp, "JPEG", quality=85, optimize=True)
        os.replace(tmp, dest)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[rt_export] фото не отримано {url[:80]}: {type(e).__name__}", file=sys.stderr)
        return False


def images_for(rid: str, pictures: list, fetch=fetch_image, img_dir: Path = IMG_DIR) -> list:
    out = []
    for n, url in enumerate((pictures or [])[:MAX_IMAGES], 1):
        name = f"{rid}-{n}.jpg"
        if fetch(url, img_dir / name):
            out.append(IMG_URL.format(name=name))
    return out


def out_guard_ok(out: Path, catalog_n: int) -> bool:
    """Гард усадки КАТАЛОГУ джерела: у YML RoyalToys позицій менше MIN_KEEP_RATIO від кількості під час попереднього запису (usічений файл постачальника) → не перезаписуємо, сигнал (картки не зникають за годину). Порівнюємо розмір каталогу (catalog_n у файлі), а не розмір виходу: звуження вибірки/посилення політики — законні. Нема попереднього catalog_n → ок."""
    try:
        prev = int(json.loads(Path(out).read_text(encoding="utf-8")).get("catalog_n") or 0)
    except (OSError, ValueError, TypeError):
        return True
    if prev and catalog_n < prev * MIN_KEEP_RATIO:
        _alert("rt_export_shrink", f"⚠️ rt_supplier_export: у YML RoyalToys {catalog_n} позицій проти {prev} під час попереднього запису (менше {int(MIN_KEEP_RATIO * 100)}%) — файл постачальника усічений? supplier_rt.json НЕ перезаписано. Свідоме звуження каталогу — видалити supplier_rt.json на VPS.")
        return False
    return True


def load_category_keys(path: Path | None = None) -> set | None:
    """Ключі site/rt_category_map.json (рос. назви категорій RT, які сайт уміє публікувати) або None, якщо файл недоступний (тоді не фільтруємо). Картка з невідомою категорією не публікується build_site — тож і фото для неї не качаємо."""
    try:
        d = json.loads(Path(path or (HERE / "site" / "rt_category_map.json")).read_text(encoding="utf-8-sig"))
        return {str(k).strip() for k in d} if isinstance(d, dict) and d else None
    except (OSError, ValueError):
        return None


def _write_json(out: Path, items: dict, now, catalog_n: int = 0) -> None:
    payload = {"updated": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "catalog_n": catalog_n, "items": items}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, out)


def run_once(catalog: dict | None = None, ids: list | None = None, out: Path | None = None, fetch=fetch_image, img_dir: Path = IMG_DIR, now: datetime | None = None,
             selection: dict | None = None, workers: int = 1, budget_s: float | None = None, dup_map: dict | None = None, category_keys: set | None = None, check_disk: bool = False) -> bool:
    """True — файл записано. Порожній каталог → попередній файл лишається, False. ids задано явно → лише ці id (тести/ручний запуск); інакше пілот + режим з rt_selection.json."""
    out = Path(out or OUT_FILE)
    try:
        if catalog is None:
            import royaltoys_parser
            catalog = royaltoys_parser.fetch_royaltoys_catalog()
        index_set = None            # None → усі картки index=true (явний список id: тести/ручний запуск)
        if not ids:
            pilot = load_pilot_ids()
            ids = select_ids(catalog or {}, pilot, selection if selection is not None else load_selection())
            index_set = set(pilot) | load_index_ids()          # пілот завжди відкритий; решта — noindex і поза sitemap, поки SEO не відкриє хвилю (rt_index_ids.json)
        dup_map = load_dup_map() if dup_map is None else dup_map
        if not catalog:
            _alert("rt_export_empty", "⚠️ rt_supplier_export: YML RoyalToys порожній/недоступний (ROYALTOYS_YML_URL у .env на VPS?) — supplier_rt.json НЕ оновлено; через 3 доби картки rt- зникнуть із сайту (fail-closed).")
            return False
        items, missing, nophoto, nocat = {}, [], [], []
        cand, tasks = [], []
        for sid in ids:
            rid = sid[3:]
            v = catalog.get(rid)
            if not v:
                missing.append(sid)
                continue
            if build_item(sid, v, ["-"]) is None:      # назва/ціна/габарити невалідні — фото даремно не качаємо
                nophoto.append(sid)
                continue
            if category_keys is not None and (v.get("category_name") or "").strip() not in category_keys:
                nocat.append(sid)                      # сайт не опублікує картку без мапи категорії — фото не качаємо
                continue
            cand.append((sid, v))
            for n, url in enumerate((v.get("pictures") or [])[:MAX_IMAGES], 1):
                tasks.append((url, img_dir / f"{sid}-{n}.jpg"))
        def build_items(got):
            res, miss = {}, 0
            for sid, v in cand:
                imgs = [IMG_URL.format(name=f"{sid}-{n}.jpg") for n, _u in enumerate((v.get("pictures") or [])[:MAX_IMAGES], 1) if got.get(img_dir / f"{sid}-{n}.jpg")]
                it = build_item(sid, v, imgs)
                if it is None:
                    miss += 1
                    continue
                it["index"] = True if index_set is None else (sid in index_set)       # Shopify-code: false → noindex,follow і поза sitemap-rt.xml
                if sid in dup_map:
                    it["dup_of"] = dup_map[sid]
                res[sid] = it
            return res, miss

        # 1) проміжний запис з фото, що вже на диску: опубліковані картки й залишки rt- не чекають на докачування (інакше юніт, убитий таймаутом, лишив би файл старим → через 3 год rt- «Немає»)
        disk = {dest: True for _u, dest in tasks if _on_disk(dest)}
        if not out_guard_ok(out, len(catalog)):
            return False
        disk_items = build_items(disk)[0] if disk else {}
        if disk_items:
            _write_json(out, disk_items, now, len(catalog))
        # 2) докачування (паралельно, з бюджетом часу й перевіркою вільного місця)
        todo = [(u, d) for u, d in tasks if d not in disk]
        if check_disk and todo:
            import shutil
            free_gb = shutil.disk_usage(img_dir.parent if img_dir.parent.exists() else HERE).free / 1e9
            if free_gb < MIN_FREE_GB:
                _alert("rt_export_disk", f"⚠️ rt_supplier_export: вільно {free_gb:.1f} ГБ < {MIN_FREE_GB} ГБ — нових фото rt- не качаємо.")
                todo = []
        got = {**disk, **prefetch(todo, fetch, workers, budget_s)}
        pending = 0
        items, pending = build_items(got)
        if nocat:
            print(f"[rt_export] категорії нема в site/rt_category_map.json: {len(nocat)} карток (фото не качаємо; Shopify-code розширює мапу)", file=sys.stderr)
        if pending:
            print(f"[rt_export] без жодного фото цього разу (бюджет часу/збій): {pending} — будуть наступним запуском", file=sys.stderr)
        if not items:
            _alert("rt_export_none", f"⚠️ rt_supplier_export: жодної з {len(ids)} відібраних карток не зібрано (нема в YML: {len(missing)}, без фото/назви/ціни/габаритів: {len(nophoto)}) — supplier_rt.json НЕ оновлено.")
            return False
        if missing or nophoto:
            print(f"[rt_export] пропущено: нема в YML {len(missing)} ({missing[:5]}), без фото/назви/ціни/габаритів {len(nophoto)} ({nophoto[:5]})", file=sys.stderr)
        _write_json(out, items, now, len(catalog))
        print(f"[rt_export] ✅ {len(items)} з {len(ids)} карток, пропущено {len(missing) + len(nophoto)}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[rt_export] збій: {type(e).__name__}: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    sys.exit(0 if run_once(workers=PREFETCH_WORKERS, budget_s=PREFETCH_BUDGET_S, category_keys=load_category_keys(), check_disk=True) else 1)
