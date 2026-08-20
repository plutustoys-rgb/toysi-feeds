"""social_auto_poster.py — автопостинг товарів на Facebook-Сторінку (Graph API).

Задача Cowork (STATUS.md найновіше-21/22, 2026-08-13). Власник обрав: касуальний соцмережевий
тон (як пілот Cowork), СПЕРШУ лише Facebook-Сторінка (Instagram — окремим PR, коли Meta схвалить
`instagram_content_publish`; тут не чіпаємо).

ДЖЕРЕЛО ТОВАРІВ — `feeds/meta_feed.xml` (уже згенерований Facebook/Meta-каталог): 123 ЖИВИХ
товари з коректними `<g:link>` (справжній URL сторінки зі slug — не вгадуємо), `<g:image_link>`
на `images.prom.ua` (Cowork з'ясувала: БЕЗ хотлінк-блокування, на відміну від toysi.ua — можна
брати фото прямо), свіжою ціною й `availability=in stock`. Тобто ні застарілих цін, ні мертвих
товарів, ні вгадування посилань — усе вже відфільтроване тим самим пайплайном, що годує Meta.

ПУБЛІКАЦІЯ — Graph API `POST /{page-id}/photos` (фото + підпис одним викликом; дозвіл
`pages_manage_posts`). СЕКРЕТ: `FB_PAGE_ID` + `FB_PAGE_ACCESS_TOKEN` — ЛИШЕ з оточення
(.env на VPS / .local_secrets), НІКОЛИ з коду/чату/Cowork-папки (той самий принцип, що CAPI/
NovaPay). Без токена — тихий no-op (dry-run усе одно готує пости). Тобто цей файл можна змержити
й деплоїти зараз; жива публікація оживе МОМЕНТ, щойно власник створить Meta-застосунок і покладе
Page Access Token у `.env` — без правок коду.

ГРАМАТИЧНА БЕЗПЕКА (урок «якісну тварини»): назва вживається як факт (не відмінюється); користь
— заздалегідь написані повні речення в називному. БЕЗ вигаданих фактів (вік/безпека/склад) — щоб
не повторити помилку, яку Cowork зловила в ручному пілоті (хибний вік/кількість).

DRY-RUN за замовчуванням: складає готові пости у `social_posts/auto/{sku}/` (caption.txt + фото)
на перегляд власнику. `--publish` реально постить. Ledger не дає постити той самий товар двічі.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from dotenv import load_dotenv

from seo_content_generator import CATEGORY_BENEFIT, GENERIC_BENEFIT, _pick

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FB_PAGE_ID = os.environ.get("FB_PAGE_ID", "").strip()
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()
# Instagram Business Account ID (з /{page-id}?fields=instagram_business_account). Токен той
# самий FB_PAGE_ACCESS_TOKEN — щойно застосунок отримає instagram_basic+instagram_content_publish
# (у Dev-режимі адмін вмикає собі без App Review, як з pages_manage_posts). Без IG_USER_ID —
# IG-бекенд тихий no-op.
IG_USER_ID = os.environ.get("IG_USER_ID", "").strip()
# Каталог Meta/IG Shopping (той самий PlutusToys Catalog, що годує meta_feed.xml). Потрібен,
# щоб знайти product_id для product-мітки (тап на фото → картка → сторінка товару). НЕ секрет.
IG_CATALOG_ID = os.environ.get("IG_CATALOG_ID", "2300136924133733").strip()
GRAPH_VERSION = "v21.0"
REQUEST_TIMEOUT = 30
IG_MEDIA_POLL_TRIES = 5      # контейнер IG інколи ще обробляється — коротко чекаємо FINISHED
IG_MEDIA_POLL_SLEEP = 3
# Reels — це ВІДЕО: контейнер обробляється помітно довше за фото, тож поллінг щедріший
# (до ~2.5 хв: 25×6с). IG вимагає публічний video_url (файл не приймає, на відміну від FB).
IG_REEL_POLL_TRIES = 25
IG_REEL_POLL_SLEEP = 6
# Кап перевірок посилань за прогін — щоб НЕ гатити вітрину тисячами GET-ів (self-throttle →
# Cloudflare rate-limit → усе виглядає «мертвим» → пропуск постів; реальний інцидент 15.08).
# За норми живий товар знаходиться за 1-2 перевірки (битих ~3%).
MAX_LINK_CHECKS = 40
MAX_TRANSIENT_FAILS = 6      # стільки «unsure» (429/5xx/timeout) → вітрина троттлить → аборт
                            # прогону (спроба наступного разу), а не мовчазний пропуск.

BASE = Path(__file__).parent
META_FEED = BASE / "feeds" / "meta_feed.xml"
OUT_DIR = BASE / "social_posts" / "auto"
LEDGER = BASE / "social_posted_ledger.json"
NS = {"g": "http://base.google.com/ns/1.0"}

# Плутус-overlay: ~кожен PLUTUS_EVERY_N-й FB-пост стає відео з маскотом на картці товару
# (engagement-«пасхалка» — глядач вгадує, де виринув Плутус). Детерміновано за sku → стабільно
# ~1/3 каталогу. Fail-safe: будь-який збій рендеру → відкат на звичайне фото (постер не ламається).
# Потребує plutus_overlay.py + plutus_scenes/*_GREEN.mp4 (у репо) + imageio/scipy/imageio-ffmpeg у venv.
PLUTUS_SCENES_DIR = BASE / "plutus_scenes"
PLUTUS_EVERY_N = int(os.environ.get("PLUTUS_EVERY_N", "3"))
PLUTUS_RENDER_TIMEOUT = 180

# Касуальні хуки (без змінних → без відмінкових помилок), обираються за hash(sku).
HOOKS = [
    "Нова іграшка вже в наявності! 🎉",
    "Гарний вибір для дитячого дозвілля! 🧸",
    "Час для нових ігор! 🎮",
    "Улюблена іграшка чекає на свого малюка! ✨",
    "Чудова ідея для подарунка! 🎁",
    "Яскрава новинка для дітей! 🌟",
]
# Хештеги за ключем-підрядком назви (ті самі ключі, що CATEGORY_BENEFIT).
HASHTAGS_BY_KEY = {
    "пазл": "#пазли", "конструктор": "#конструктор", "лял": "#ляльки", "пупс": "#пупси",
    "машин": "#машинки", "антистрес": "#антистрес", "сквіш": "#сквіш", "творч": "#творчість",
    "малюванн": "#малювання", "ліпленн": "#ліплення", "тварин": "#фігуркитварин",
    "настільн": "#настільніігри", "м'як": "#мякііграшки", "мяк": "#мякііграшки",
    "ведмед": "#мякііграшки", "музичн": "#музичніграшки", "розвива": "#розвитокдитини",
    "навчальн": "#навчання", "кубик": "#кубики", "фігурк": "#фігурки", "пісок": "#кінетичнийпісок",
    "вод": "#іграшкидляводи", "спорт": "#спортивніігри", "самокат": "#самокат",
}
DEFAULT_HASHTAGS = ["#дитячііграшки", "#іграшкиукраїна", "#plutustoys"]


def _clean_name(name: str) -> str:
    """Прибирає внутрішній мотлох із назви Toysi для соцпосту: '#374' (внутр. артикул),
    '(10/100)'/'(12/600)' (кількість у коробі). Кольори/розміри в дужках (не цифра/цифра)
    лишаються. Схлопує пробіли. Назву як факт НЕ перекручуємо — лише службові токени."""
    n = re.sub(r"<[^>]+>", " ", name or "")   # захист: жодних HTML-тегів у підписі
    n = re.sub(r"#\d+", "", n)
    n = re.sub(r"\(\s*\d+\s*/\s*\d+\s*\)", "", n)
    return re.sub(r"\s+", " ", n).strip()


def _link_status(url: str, cache: dict) -> str:
    """Статус посилання товару: 'live' (200), 'dead' (РІВНО 404 — товар видалено/переслаглено),
    'unsure' (429/5xx/timeout/мережа — це радше rate-limit/транзієнт вітрини, НЕ «мертвий»).
    ВАЖЛИВО (урок 15.08): раніше будь-який не-200 рахувався «мертвим» → під rate-limit усе
    виглядало мертвим і постер мовчки пропускав пости. Тепер лише 404 = dead; троттл → unsure
    (не постимо, але й не таврируємо мертвим). Кеш у межах прогону."""
    if url in cache:
        return cache[url]
    try:
        code = requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True).status_code
        st = "live" if code == 200 else ("dead" if code == 404 else "unsure")
    except requests.RequestException:
        st = "unsure"
    cache[url] = st
    return st


def _match_key(name: str):
    n = (name or "").lower()
    for key in CATEGORY_BENEFIT:
        if key in n:
            return key
    return None


def _benefit(name: str, sku: str) -> str:
    key = _match_key(name)
    return _pick(CATEGORY_BENEFIT[key], sku, "b") if key else _pick(GENERIC_BENEFIT, sku, "b")


def _hashtags(name: str) -> str:
    key = _match_key(name)
    tags = ([HASHTAGS_BY_KEY[key]] if key and HASHTAGS_BY_KEY.get(key) else []) + DEFAULT_HASHTAGS
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return " ".join(out[:4])


# Захист від дрейфу токена розміру у meta_feed.xml: соцпост має брати МАКСИМУМ, що віддає
# майстер (images.prom.ua апскейлу понад джерело не робить — фактично 500×500 для більшості
# іграшок; підтверджено вимірами SMM/SEO 2026-08-19). На VPS фід уже _w1024_, тож це no-op у
# проді, АЛЕ страхує, якщо колись фід віддасть зменшену копію (_w200_ тощо) — тоді соцпост усе
# одно попросить майстер, а не 200×200 у стрічку. Той самий підхід, що _upscale_prom_image у
# generate_google_feed.py.
_PROM_IMG_SIZE_RE = re.compile(r"_w\d+_h\d+_")


def _max_prom_image(url: str) -> str:
    if not url or "images.prom.ua" not in url:
        return url
    return _PROM_IMG_SIZE_RE.sub("_w1024_h1024_", url, count=1)


def load_products() -> list:
    """Товари з meta_feed.xml (уже живі/in-stock, з коректним URL і фото). Порядок фіду
    зберігаємо — він відображає пріоритет пайплайна."""
    if not META_FEED.exists():
        print(f"[social] нема {META_FEED} — спершу згенеруй Meta-фід.", file=sys.stderr)
        return []
    try:
        root = ET.parse(META_FEED).getroot()
    except ET.ParseError as e:   # битий/обрізаний фід не має валити прогін (симетрично з ledger)
        print(f"[social] {META_FEED} биті XML ({e}) — пропускаю прогін.", file=sys.stderr)
        return []
    out = []
    for item in root.iter("item"):
        g = lambda t: item.findtext("g:" + t, namespaces=NS)
        sku = (g("id") or item.findtext("id") or "").strip()
        name = _clean_name(item.findtext("title") or g("title") or "")
        url = (g("link") or item.findtext("link") or "").strip()
        image = _max_prom_image((g("image_link") or "").strip())
        price = (g("price") or "").strip()
        avail = (g("availability") or "").strip().lower()
        if not (sku and name and url and image):
            continue
        if avail and avail != "in stock":
            continue
        out.append({"sku": sku, "name": name, "url": url, "image": image, "price": price})
    return out


def _price_grn(raw: str) -> str:
    """'518.02 UAH' -> '518 грн.'; порожнє/невідоме -> ''."""
    try:
        return f"{round(float((raw or '').split()[0]))} грн."
    except (ValueError, IndexError):
        return ""


def build_caption(p: dict, platform: str = "fb", tagged: bool = False) -> str:
    """Підпис під платформу. Спільне: хук + назва + користь + ціна + хештеги. Різниця: на FB
    URL товару клікабельний → лишаємо; на IG посилання в підписі НЕ клікабельні. Якщо на IG
    причеплено product-мітку (tagged=True) → CTA на позначку (тап → сторінка товару); інакше —
    на посилання в профілі. Формулювання tagged згадує ОБИДВА шляхи, тож лишається коректним,
    навіть якщо мітка при публікації не причепилась (fallback у _publish_ig)."""
    sku = p["sku"]
    lines = [_pick(HOOKS, sku, "h"), p["name"] + ".", _benefit(p["name"], sku)]
    price = _price_grn(p.get("price"))
    if price:
        lines.append(f"Ціна: {price}")
    if platform == "ig":
        lines.append("🛍️ Щоб замовити — тапни позначку товару на фото або посилання в профілі."
                     if tagged else "Замовлення — за посиланням у шапці профілю 🔗")
    else:
        lines.append(p["url"])
    lines.append(_hashtags(p["name"]))
    return "\n".join(lines)


def _load_ledger() -> dict:
    """Ledger платформо-залежний: {platform: {sku: {at, post_id}}}. Legacy-формат (пласкі
    {sku: {at, post_id}} з FB-ери до IG) мігруємо під ключ 'fb', щоб уже постнуті на FB SKU
    не постнулись повторно."""
    try:
        raw = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or not raw:
        return {}
    # legacy-детект: значення верхнього рівня схожі на запис поста (мають 'at'/'post_id'),
    # а не на під-словник платформи.
    if any(isinstance(v, dict) and ("at" in v or "post_id" in v) for v in raw.values()):
        return {"fb": raw}
    return raw


def _save_ledger(led: dict) -> None:
    LEDGER.write_text(json.dumps(led, ensure_ascii=False, indent=2), encoding="utf-8")


def _already_posted(led: dict, platform: str, sku: str) -> bool:
    return sku in led.get(platform, {})


def _record_post(led: dict, platform: str, sku: str, post_id: str, url: str = "") -> None:
    # url — сторінка товару на момент посту; social_dead_post_cleaner перевіряє її на 404
    # (видалений товар), щоб прибрати мертвий FB-пост. Старі записи без url — чистильник пропускає.
    led.setdefault(platform, {})[sku] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                         "post_id": post_id, "url": url}
    _save_ledger(led)


def _write_dryrun(p: dict, caption: str, platform: str = "fb") -> None:
    d = OUT_DIR / p["sku"]
    d.mkdir(parents=True, exist_ok=True)
    (d / f"caption_{platform}.txt").write_text(caption, encoding="utf-8")
    (d / "info.json").write_text(json.dumps({"sku": p["sku"], "url": p["url"], "image": p["image"]},
                                            ensure_ascii=False, indent=2), encoding="utf-8")
    try:  # images.prom.ua без хотлінк-блокування → можна одразу тягнути на прев'ю власнику
        r = requests.get(p["image"], timeout=REQUEST_TIMEOUT)
        if r.ok and r.content:
            (d / "photo.jpg").write_bytes(r.content)
    except requests.RequestException:
        pass


def _is_plutus_post(sku: str) -> bool:
    """Детерміновано ~1/PLUTUS_EVERY_N постів → Плутус-відео (стабільно за sku)."""
    if PLUTUS_EVERY_N <= 1:
        return PLUTUS_EVERY_N == 1
    return int(hashlib.md5(sku.encode()).hexdigest(), 16) % PLUTUS_EVERY_N == 0


# Сцени, ВИКЛЮЧЕНІ з автопостингу через поганий хромакей (сірий ореол навколо маскота,
# видно оком у стрічці — знахідка SMM 2026-08-19, джерело PixVerse). Файли НЕ видаляємо
# (реверсивно, лишаються для переоцінки/перегенерації) — просто постер їх не бере. Сцени
# з Pika (02/03/04) чистяться нормально. Прибрати з набору, коли будуть чисті заміни.
_PLUTUS_SCENE_EXCLUDE = {
    "scene05_sneeze_pixverse_GREEN.mp4",
    "scene06_grooming_pixverse_GREEN.mp4",
}


def _plutus_scene_for(sku: str):
    """Зелена сценка для цього sku (детерміновано — розмаїття без випадковості між прогонами).
    Виключаємо сцени з _PLUTUS_SCENE_EXCLUDE (поганий хромакей)."""
    scenes = [s for s in sorted(PLUTUS_SCENES_DIR.glob("*_GREEN.mp4"))
              if s.name not in _PLUTUS_SCENE_EXCLUDE] if PLUTUS_SCENES_DIR.exists() else []
    if not scenes:
        return None
    return scenes[int(hashlib.md5(("sc" + sku).encode()).hexdigest(), 16) % len(scenes)]


def _render_plutus(image_url: str, sku: str, out_path: Path) -> bool:
    """Рендерить overlay-відео Плутуса на фото товару (plutus_overlay.py, subprocess).
    FAIL-SAFE: будь-який збій (нема сценок/залежностей, мережа, таймаут, ненульовий код) →
    False, і постер відкочується на звичайне фото. Причина логується (самодіагностика)."""
    scene = _plutus_scene_for(sku)
    if scene is None:
        print("[social] Плутус: нема зелених сценок у plutus_scenes/ — відкат на фото.", file=sys.stderr)
        return False
    try:
        r = requests.get(image_url, timeout=REQUEST_TIMEOUT)
        if not (r.ok and r.content):
            return False
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_img = out_path.parent / "_plutus_product.jpg"
        tmp_img.write_bytes(r.content)
        seed = int(hashlib.md5(sku.encode()).hexdigest()[:8], 16) % 100000
        res = subprocess.run(
            [sys.executable, str(BASE / "plutus_overlay.py"),
             "--scene", str(scene), "--product", str(tmp_img),
             "--out", str(out_path), "--seed", str(seed)],
            cwd=str(BASE), capture_output=True, text=True, timeout=PLUTUS_RENDER_TIMEOUT)
        ok = res.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
        if not ok:
            tail = (res.stderr or res.stdout or f"код {res.returncode}")[-200:]
            print(f"[social] Плутус-рендер {sku} не вдався ({tail}) — відкат на фото.", file=sys.stderr)
        return ok
    except Exception as e:  # noqa: BLE001 — рендер НІКОЛИ не має валити постинг
        print(f"[social] Плутус-рендер {sku} виняток: {e} — відкат на фото.", file=sys.stderr)
        return False


def _publish_fb_video(video_path: Path, caption: str) -> dict:
    """POST /{page-id}/videos — відео файлом + опис. Повертає {ok, id|error}. Ніколи не кидає."""
    try:
        with open(video_path, "rb") as f:
            resp = requests.post(
                f"https://graph.facebook.com/{GRAPH_VERSION}/{FB_PAGE_ID}/videos",
                data={"description": caption, "access_token": FB_PAGE_ACCESS_TOKEN},
                files={"source": f},
                timeout=PLUTUS_RENDER_TIMEOUT,
            )
        j = resp.json() if resp.content else {}
        if resp.ok and j.get("id"):
            return {"ok": True, "id": j.get("id")}
        return {"ok": False, "error": j.get("error", {}).get("message", f"HTTP {resp.status_code}")}
    except (requests.RequestException, ValueError, OSError) as e:
        return {"ok": False, "error": str(e)}


def _publish_fb(p: dict, caption: str) -> dict:
    """POST /{page-id}/photos — фото на URL + підпис. Повертає {ok, id|error}. Ніколи не кидає."""
    try:
        resp = requests.post(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{FB_PAGE_ID}/photos",
            data={"url": p["image"], "caption": caption, "published": "true",
                  "access_token": FB_PAGE_ACCESS_TOKEN},
            timeout=REQUEST_TIMEOUT,
        )
        j = resp.json() if resp.content else {}
        if resp.ok and (j.get("post_id") or j.get("id")):
            return {"ok": True, "id": j.get("post_id") or j.get("id")}
        return {"ok": False, "error": j.get("error", {}).get("message", f"HTTP {resp.status_code}")}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "error": str(e)}


def _ig_product_id(sku: str, name: str, cache: dict):
    """catalog product_id (для product-мітки) за нашим SKU через ПРЯМИЙ запит каталогу
    `/{catalog_id}/products?filter=retailer_id`. Саме він працює з дозволом catalog_management
    (живо перевірено 2026-08-14: IG-scoped `catalog_product_search` дає (#10) no permission, а
    цей ендпоінт коректно повертає {id, retailer_id}). Повертає catalog `id` (=product_id для
    мітки) або None (постимо БЕЗ мітки — товару нема в каталозі / помилка / бракує доступу).
    Мітка — БОНУС, ніколи не блокер. Кеш у межах прогону."""
    if sku in cache:
        return cache[sku]
    pid = None
    if IG_CATALOG_ID:
        try:
            r = requests.get(
                f"https://graph.facebook.com/{GRAPH_VERSION}/{IG_CATALOG_ID}/products",
                params={"fields": "id,retailer_id",
                        "filter": json.dumps({"retailer_id": {"eq": str(sku)}}),
                        "access_token": FB_PAGE_ACCESS_TOKEN},
                timeout=REQUEST_TIMEOUT,
            )
            for prod in ((r.json() if r.content else {}).get("data") or []):
                if isinstance(prod, dict) and str(prod.get("retailer_id")) == str(sku):
                    pid = prod.get("id")
                    break
        except (requests.RequestException, ValueError, AttributeError, TypeError):
            pass   # несподівана форма відповіді / мережа → None (постимо без мітки)
    cache[sku] = pid
    return pid


def _ig_create_media(image: str, caption: str, product_id):
    """Крок 1 IG — media-контейнер. З product_id причіпляє product_tag (позиція в центрі)."""
    data = {"image_url": image, "caption": caption, "access_token": FB_PAGE_ACCESS_TOKEN}
    if product_id:
        data["product_tags"] = json.dumps([{"product_id": product_id, "x": 0.5, "y": 0.5}])
    return requests.post(f"https://graph.facebook.com/{GRAPH_VERSION}/{IG_USER_ID}/media",
                         data=data, timeout=REQUEST_TIMEOUT)


def _publish_ig(p: dict, caption: str, product_id=None) -> dict:
    """Instagram-публікація — ДВА кроки: (1) media-контейнер (з product-міткою, якщо є
    product_id), (2) media_publish за creation_id. Між ними чекаємо status_code=FINISHED.
    Якщо створення З МІТКОЮ впало (товар не approved / позиція) — ретрай БЕЗ мітки, щоб пост
    усе одно вийшов (мітка — бонус, не блокер). Токен — FB_PAGE_ACCESS_TOKEN. Не кидає."""
    base = f"https://graph.facebook.com/{GRAPH_VERSION}"
    used_tag = bool(product_id)
    try:
        c = _ig_create_media(p["image"], caption, product_id)
        cj = c.json() if c.content else {}
        if not (c.ok and cj.get("id")) and product_id:
            # мітка завалила створення → повтор без неї (пост важливіший за мітку)
            print(f"[social] IG {p['sku']}: product-мітка не прийнялась, постю без неї.", file=sys.stderr)
            used_tag = False
            c = _ig_create_media(p["image"], caption, None)
            cj = c.json() if c.content else {}
        creation_id = cj.get("id")
        if not (c.ok and creation_id):
            return {"ok": False, "error": cj.get("error", {}).get("message", f"media HTTP {c.status_code}")}
        # почекати FINISHED (best-effort; IN_PROGRESS → повтор, ERROR/EXPIRED → вихід)
        for _ in range(IG_MEDIA_POLL_TRIES):
            s = requests.get(f"{base}/{creation_id}", params={"fields": "status_code",
                             "access_token": FB_PAGE_ACCESS_TOKEN}, timeout=REQUEST_TIMEOUT)
            code = (s.json() if s.content else {}).get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                return {"ok": False, "error": f"media status {code}"}
            time.sleep(IG_MEDIA_POLL_SLEEP)
        pub = requests.post(f"{base}/{IG_USER_ID}/media_publish",
                            data={"creation_id": creation_id, "access_token": FB_PAGE_ACCESS_TOKEN},
                            timeout=REQUEST_TIMEOUT)
        pj = pub.json() if pub.content else {}
        if pub.ok and pj.get("id"):
            return {"ok": True, "id": pj["id"], "tagged": used_tag}   # tagged = чи РЕАЛЬНО причепилась
        return {"ok": False, "error": pj.get("error", {}).get("message", f"publish HTTP {pub.status_code}")}
    except (requests.RequestException, ValueError, AttributeError, TypeError) as e:
        return {"ok": False, "error": str(e)}


def _ig_create_reel(video_url: str, caption: str):
    """Крок 1 IG-Reels — media-контейнер типу REELS. IG приймає лише публічний video_url
    (файл, на відміну від FB /videos, НЕ приймає) — його IG сам стягує й обробляє."""
    return requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{IG_USER_ID}/media",
        data={"media_type": "REELS", "video_url": video_url, "caption": caption,
              "access_token": FB_PAGE_ACCESS_TOKEN},
        timeout=REQUEST_TIMEOUT,
    )


def _publish_ig_reel(video_url: str, caption: str) -> dict:
    """IG-Reels публікація — ДВА кроки (як фото-IG, але для відео): (1) media-контейнер
    media_type=REELS з публічним video_url, (2) media_publish за creation_id, МІЖ ними
    чекаємо status_code=FINISHED (відео обробляється довше → щедріший поллінг). Токен —
    FB_PAGE_ACCESS_TOKEN. Product-мітки для Reels не чіпляємо. Не кидає — повертає {ok,id|error}."""
    base = f"https://graph.facebook.com/{GRAPH_VERSION}"
    try:
        c = _ig_create_reel(video_url, caption)
        cj = c.json() if c.content else {}
        creation_id = cj.get("id")
        if not (c.ok and creation_id):
            return {"ok": False, "error": cj.get("error", {}).get("message", f"reel media HTTP {c.status_code}")}
        for _ in range(IG_REEL_POLL_TRIES):
            s = requests.get(f"{base}/{creation_id}", params={"fields": "status_code",
                             "access_token": FB_PAGE_ACCESS_TOKEN}, timeout=REQUEST_TIMEOUT)
            code = (s.json() if s.content else {}).get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                return {"ok": False, "error": f"reel media status {code}"}
            time.sleep(IG_REEL_POLL_SLEEP)
        else:
            return {"ok": False, "error": "reel media не дійшов до FINISHED (ще обробляється?)"}
        pub = requests.post(f"{base}/{IG_USER_ID}/media_publish",
                            data={"creation_id": creation_id, "access_token": FB_PAGE_ACCESS_TOKEN},
                            timeout=REQUEST_TIMEOUT)
        pj = pub.json() if pub.content else {}
        if pub.ok and pj.get("id"):
            return {"ok": True, "id": pj["id"]}
        return {"ok": False, "error": pj.get("error", {}).get("message", f"reel publish HTTP {pub.status_code}")}
    except (requests.RequestException, ValueError, AttributeError, TypeError) as e:
        return {"ok": False, "error": str(e)}


def post_single_reel(video_url: str, caption: str, publish: bool) -> dict:
    """Опублікувати ОДИН Reel за публічним video_url + підпис. Без --publish або без
    IG-креденшелів — dry-run (лише друкує, що зробив би). Розв'язує блокер SMM: механіка
    Reels уже є, лишалось подати `video_url` (заявка SMM 2026-08-19)."""
    if not publish or not _platform_ready("ig"):
        why = "без --publish" if not publish else "нема IG_USER_ID/FB_PAGE_ACCESS_TOKEN у .env"
        print(f"[social] DRY-RUN Reel ({why}): video_url={video_url} | caption={caption[:60]}...")
        return {"ok": False, "dryrun": True}
    res = _publish_ig_reel(video_url, caption)
    if res.get("ok"):
        print(f"[social] IG-Reel опубліковано → {res['id']}")
    else:
        print(f"[social] IG-Reel ПОМИЛКА: {res.get('error')}", file=sys.stderr)
    return res


def _platform_ready(platform: str) -> bool:
    if platform == "fb":
        return bool(FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN)
    if platform == "ig":
        return bool(IG_USER_ID and FB_PAGE_ACCESS_TOKEN)
    return False


def _notify(text: str) -> None:
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message(text)
    except Exception:
        pass


def run(limit: int, publish: bool, targets=("fb",)) -> dict:
    """targets — платформи цього прогону ('fb'/'ig'). `limit` рахує ТОВАРИ, не пости: один
    обраний товар постить на всі задані платформи, де його ще нема (ledger per-platform).
    Товар «покрито», лише коли він у ledger усіх заданих платформ."""
    products = load_products()
    if not products:
        return {"processed": 0}
    ledger = _load_ledger()
    live = [pl for pl in targets if _platform_ready(pl)] if publish else []
    if publish:
        for pl in targets:
            if pl not in live:
                miss = "FB_PAGE_ID/FB_PAGE_ACCESS_TOKEN" if pl == "fb" else "IG_USER_ID (+FB_PAGE_ACCESS_TOKEN)"
                print(f"[social] {pl.upper()}: нема {miss} у .env — тихий no-op (dry-run усе одно "
                      f"готую). Оживе, щойно креденшели будуть в оточенні.", file=sys.stderr)

    stats = {"processed": 0, "posted": 0, "dryrun": 0, "skipped": 0, "dead_links": 0,
             "unsure_links": 0, "errors": 0, "tagged": 0, "plutus": 0}
    url_cache, pid_cache = {}, {}
    checks = 0            # перевірок посилань цього прогону — кап MAX_LINK_CHECKS проти self-throttle
    for p in products:
        if stats["processed"] >= limit:
            break
        sku = p["sku"]
        needed = [pl for pl in targets if not _already_posted(ledger, pl, sku)]
        if not needed:
            stats["skipped"] += 1     # уже покрито на всіх заданих платформах
            continue
        st = _link_status(p["url"], url_cache)
        if st == "unsure":            # rate-limit/транзієнт — не постимо, але й не «мертвий»
            stats["unsure_links"] += 1
            if stats["unsure_links"] >= MAX_TRANSIENT_FAILS:
                print(f"[social] вітрина недоступна/rate-limit ({stats['unsure_links']} поспіль) — "
                      f"зупиняю прогін, спроба наступного разу.", file=sys.stderr)
                _notify("⚠️ Соц-постер: вітрина недоступна (rate-limit?) — прогін зупинено без постів, "
                        "спроба наступного разу.")
                _report(stats)
                return stats
            continue
        if st == "dead":              # рівно 404 — товар видалено/переслаглено
            stats["dead_links"] += 1
            checks += 1
            if checks >= MAX_LINK_CHECKS:
                print(f"[social] {checks} перевірок без живого товару — зупиняю (без self-DoS).",
                      file=sys.stderr)
                _notify(f"⚠️ Соц-постер: не знайшов живого товару за {checks} перевірок — прогін без "
                        "постів (багато 404 у фіді чи rate-limit?).")
                _report(stats)
                return stats
            continue
        checks += 1                   # st == "live"
        stats["processed"] += 1
        for pl in needed:
            # IG: спробувати знайти catalog product_id → product-мітка (тап → сторінка товару).
            product_id = _ig_product_id(sku, p["name"], pid_cache) if (pl == "ig" and pl in live) else None
            caption = build_caption(p, pl, tagged=bool(product_id))
            _write_dryrun(p, caption, pl)   # завжди артефакт (прев'ю + лог)
            # Плутус-overlay для ~кожного PLUTUS_EVERY_N-го FB-поста (fail-safe → звичайне фото).
            # Рендеримо і для dry-run (прев'ю відео власнику), і для живого постингу.
            plutus_video = None
            if pl == "fb" and _is_plutus_post(sku):
                vpath = OUT_DIR / sku / "plutus.mp4"
                if _render_plutus(p["image"], sku, vpath):
                    plutus_video = vpath
            if pl not in live:
                stats["dryrun"] += 1
                continue
            if pl == "fb":
                res = _publish_fb_video(plutus_video, caption) if plutus_video else _publish_fb(p, caption)
                if plutus_video and res.get("ok"):
                    stats["plutus"] += 1
            else:
                res = _publish_ig(p, caption, product_id)
            if res["ok"]:
                _record_post(ledger, pl, sku, res["id"], p["url"])
                stats["posted"] += 1
                really_tagged = bool(res.get("tagged"))   # чи мітка справді причепилась (не намір)
                if really_tagged:
                    stats["tagged"] += 1
                print(f"[social] {pl.upper()} опубліковано {sku} → {res['id']}"
                      + (" (з product-міткою)" if really_tagged else ""))
            else:
                stats["errors"] += 1
                print(f"[social] {pl.upper()} ПОМИЛКА {sku}: {res['error']}", file=sys.stderr)
                if stats["errors"] >= 3 and stats["posted"] == 0:
                    # 3 поспіль невдачі без жодного успіху → системна проблема (токен/дозвіл/бан).
                    # Зупиняємось, щоб не гатити десятки мертвих запитів у Meta.
                    print("[social] 3 невдачі поспіль, 0 успіхів — зупиняю прогін (токен/дозвіл?).",
                          file=sys.stderr)
                    _notify("⚠️ Соц-постер зупинено: 3 невдачі поспіль (токен/дозвіл?).")
                    _report(stats)
                    return stats

    if stats["posted"]:
        pl_note = f", з них Плутус-відео: {stats['plutus']}" if stats.get("plutus") else ""
        _notify(f"📣 Соцпостинг: {stats['posted']} пост(ів) на {'+'.join(live)}{pl_note}. Помилок: {stats['errors']}.")
    elif live and (stats["dead_links"] or stats["unsure_links"]):
        # прогін закінчився без постів, хоча були кандидати з битими/недоступними лінками —
        # не мовчимо (урок 15.08: постер тихо пропускав пости під rate-limit).
        _notify(f"⚠️ Соц-постер: 0 постів (мертвих {stats['dead_links']}, недоступних "
                f"{stats['unsure_links']}) — не знайшов живого товару.")
    _report(stats)
    return stats


def _report(stats: dict) -> None:
    print(f"[social] processed={stats['processed']} posted={stats['posted']} "
          f"(з них з IG-міткою={stats.get('tagged', 0)}) dryrun={stats['dryrun']} "
          f"skipped(покрито)={stats['skipped']} мертвих_лінків={stats['dead_links']} "
          f"недоступних={stats.get('unsure_links', 0)} errors={stats['errors']}. Артефакти: {OUT_DIR}")


def _acquire_lock():
    """Ексклюзивний міжпроцесний lock — щоб окремі таймери (FB щодня + IG 2×/день) НІКОЛИ не
    гнали ledger одночасно (навіть якщо Persistent-наздоганяння на буті спробує запустити кілька
    прогонів разом). ledger пишеться повним перезаписом файлу → паралельні прогони затерли б
    правки один одного. Linux: fcntl.flock (неблокуючий). Інша ОС (локальні тести): no-op (True).
    Повертає file-handle/True при успіху або None, якщо інший прогін уже тримає lock."""
    try:
        import fcntl
    except ImportError:
        return True   # не-Linux (локальні тести) — без локу
    fh = open(BASE / ".social_poster.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh     # тримаємо відкритим до кінця процесу — лок звільниться сам при виході
    except OSError:
        fh.close()
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=3, help="Скільки ТОВАРІВ за прогін (дефолт 3).")
    ap.add_argument("--publish", action="store_true",
                    help="Реально постити. Без креденшелів платформи — тихий no-op (dry-run).")
    ap.add_argument("--target", default="fb", choices=["fb", "ig", "both"],
                    help="Куди постити: fb (дефолт), ig, both. IG активний лише з IG_USER_ID у .env.")
    ap.add_argument("--reel", metavar="VIDEO_URL",
                    help="Опублікувати ОДИН IG-Reel за публічним video_url (замість каталог-прогону). "
                         "Потребує --caption. Без --publish — dry-run.")
    ap.add_argument("--caption", default="", help="Підпис для --reel.")
    args = ap.parse_args()
    if args.reel:
        # Reels — окремий одноразовий вхід, ledger/каталог не чіпає, лок не потрібен.
        post_single_reel(args.reel, args.caption, args.publish)
        return
    lock = _acquire_lock()
    if not lock:
        print("[social] інший прогін соц-постера вже триває — пропускаю (уникаю гонки ledger).",
              file=sys.stderr)
        return
    targets = ("fb", "ig") if args.target == "both" else (args.target,)
    run(limit=args.limit, publish=args.publish, targets=targets)


if __name__ == "__main__":
    main()
