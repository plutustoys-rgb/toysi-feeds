"""rozetka_merchant_agent.py — агент-товарознавець для Rozetka (рішення власника 2026-08-16).

МЕТА: автоматично РОЗШИРЮВАТИ каталог Rozetka лише КОНКУРЕНТНИМИ товарами — рішення ДО додавання
(не delist після — Rozetka модерує). Повністю АВТО, без ручного огляду. Помилка на кілька %
безпечна — репрайсер (rozetka_competitor_repricer.py) ловить: щойно товар на Rozetka, її моніторинг
дає ціну, репрайсер або робить його конкурентним, або тримає на 5%-флорі (без збитку).

ПЕРЕРОБЛЕНО 2026-08-16 (пряме зауваження власника): кандидати БЕЗПОСЕРЕДНЬО з каталогу Toysi (НЕ з
Prom-скану — у Prom і Rozetka РІЗНІ конкуренти, ціна конкурента Prom до Rozetka не стосується).
Конкурентність перевіряємо на САМІЙ Rozetka.

ЛАНЦЮГ (для кожного кандидата, РОТАЦІЙНО з капом — rate-limit):
  1. з каталогу Toysi: наявність stock ≥ MIN_STOCK (2) — запас, щоб не продати останній;
  2. ще НЕ на Rozetka (не в rozetka_static_selection.json membership);
  3. картка проходить Rozetka-валідацію (_qualifies_for_feed) + є фото (джерело — сам Toysi,
     `toysi.ua/p/enl_*.jpg`, у переважній більшості чисте; images.prom.ua — лише дзеркало-фолбек);
  4. РИНКОВА перевірка на Rozetka: публічний пошук за назвою (Playwright — товари рендеряться в
     браузері, requests не бачить; rz-client-state зашифрований) → плитки {назва, ціна, фото};
     pHash ФОТО-ЗВІРКА нашого фото vs фото плитки → підтвердити «той самий товар» (пошук дає різні
     варіанти: 10/12/22 кольори тощо з різними цінами — фото відбирає САМЕ наш); min ціна серед
     підтверджених = ринок;
  5. РІШЕННЯ: Rozetka-флор (cost + Rozetka-комісія + 5%) ≤ ринок → ДОДАТИ; дорожче → пропустити;
     нема підтвердженого матчу (унікальний / нема конкурентів) → ДОДАТИ (єдиний продавець).
Результат — rozetka_merchant_candidates.json (список «додати» + звіт). НЕ пише в membership сам
(власник тестує спершу). Підключення до фіду — окремо.

✅ pHash-поріг PHASH_MAX_DISTANCE=12 КАЛІБРОВАНО наживо 2026-08-17 (наше фото Toysi vs фото плиток
Rozetka): той самий товар дає dist 0–12, інший ≥14 — чистий розрив.
✅ КРИТИЧНО: пошук ганяється через СПРАВЖНІЙ Chrome (channel="chrome"), бо bundled chromium-headless
Playwright ловить антибот Rozetka (403 «Трохи зачекайте…»/500) → 0 плиток → усе хибно «унікальне».
"""
import argparse
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
import imagehash
from PIL import Image
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from generate_rozetka_feed import (_qualifies_for_feed, _within_rz_delivery_dims,
                                   _load_prom_products_cache, ROZETKA_STATIC_SELECTION_FILE)
from competitor_pricing import _resolve_rozetka_floor, PAYMENT_COMMISSION, real_toysi_cost

BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "rozetka_merchant_candidates.json"
CURSOR_FILE = BASE_DIR / ".local_secrets" / "rozetka_merchant_cursor.json"
# Переаудит наявних членів (--audit-members): окремий курсор ротації + окремий файл-накопичувач
# кандидатів на ЗНЯТТЯ (dry-run, membership НЕ чіпаємо тут — знімає окремий крок після огляду).
AUDIT_CURSOR_FILE = BASE_DIR / ".local_secrets" / "rozetka_merchant_audit_cursor.json"
REMOVAL_OUTPUT_FILE = BASE_DIR / "rozetka_member_removal_candidates.json"
# Профіль СПРАВЖНЬОГО Chrome для публічного пошуку Rozetka. КРИТИЧНО: bundled
# chromium-headless-shell Playwright ловить антибот Rozetka (403 «Трохи зачекайте…» / 500) →
# 0 плиток → усе хибно трактується як «унікальний». Справжній Chrome (channel="chrome") з
# персистентним профілем проходить (перевірено 2026-08-17: 60 плиток, навіть headless).
CHROME_PROFILE = BASE_DIR / ".local_secrets" / "rozetka_chrome_profile"

MIN_STOCK = int(__import__("os").environ.get("ROZETKA_MERCHANT_MIN_STOCK", "2"))   # запас ≥2 шт
ROZETKA_COMPETITOR_MARGIN = 0.05
ROZETKA_PAYMENT_COMMISSION = PAYMENT_COMMISSION.get("rozetka", 0.0)
BATCH_SIZE = int(__import__("os").environ.get("ROZETKA_MERCHANT_BATCH", "60"))     # кандидатів/прогін
PHASH_MAX_DISTANCE = 12        # ≤ цього = «той самий товар». КАЛІБРОВАНО наживо 2026-08-17:
                               # той самий товар (вкл. інший колір) дає dist 0–12; інший товар ≥14.
                               # Чистий розрив 12↔14, жоден чужий не потрапив ≤12.
MAX_TILES_PER_SEARCH = 24      # скільки плиток звіряти фото за пошук (rate-limit)
SEARCH_DELAY = (1.5, 2.5)      # пауза між пошуками
NAV_TIMEOUT_MS = 25000

# Матчинг конкурента — ДВА сигнали. pHash сам пропускає конкурентів з ІНШИМ фото (саме так
# товарознавець давав хибне «унікальний» — перевірено вручну 2026-08-27: «Літак ТехноК» у нас
# 72₴ vs 40/59/70₴ у інших, а матчер казав «нема конкурента»). Тому додаємо:
#   1) pHash фото ≤ PHASH_MAX_DISTANCE (сильний, точний);
#   2) НАЗВА збігається (частка спільних значущих токенів нашої назви ≥ NAME_OVERLAP_MIN) І ціна
#      в РОЗУМНІЙ смузі (≥ NAME_MATCH_MIN_PRICE_RATIO нашого floor — правило SEO проти хибних
#      «6₴»-матчів іншого товару).
# Конкурент = будь-який сигнал. Пороги КАЛІБРУВАТИ на 20-валідації ПЕРЕД масштабуванням.
NAME_OVERLAP_MIN = float(__import__("os").environ.get("ROZETKA_NAME_OVERLAP_MIN", "0.6"))
NAME_MATCH_MIN_PRICE_RATIO = 0.30
NAME_MATCH_MIN_SHARED = 2   # мінімум СПІЛЬНИХ значущих токенів (проти одно-токенних / родових збігів)
# Стоп-лист: службові + РОДОВІ токени іграшкового каталогу (інакше «іграшка/гра/дитяча» роздувають
# збіг різних товарів — аудит 2026-08-28, ниті 1-2). Специфіка (бренд/модель/серія) лишається.
_NAME_STOP = {"для", "з", "у", "в", "та", "і", "the", "and", "см", "шт", "мл", "кг", "набір",
              "іграшка", "іграшки", "іграшкова", "гра", "дитяча", "дитячий", "дитяче", "дитячі",
              "комплект", "розвивальна", "розвиваюча", "інтерактивна"}


def _name_tokens(s: str) -> set:
    """Значущі токени назви: нижній регістр, без пунктуації/стоп-слів, довжина ≥3."""
    import re as _re
    toks = _re.findall(r"[a-zа-яіїєґ0-9]+", (s or "").lower())
    return {t for t in toks if len(t) >= 3 and t not in _NAME_STOP}


def _name_overlap(ours: set, tile: set) -> float:
    """Частка спільних токенів відносно КОРОТШОЇ назви (0..1). Мін-база — щоб зловити конкурента
    з коротшою АБО довшою назвою (напр. наша «Карткова гра Люкс City» vs плитка «Люкс City» =
    1.0, а не 0.5). Гвард проти одно-токенних збігів («Пазл») — у виклику (len(tile)>=2)."""
    if not ours or not tile:
        return 0.0
    return len(ours & tile) / min(len(ours), len(tile))

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept-Language": "uk-UA,uk"}


def _load_membership() -> set:
    try:
        data = json.loads(ROZETKA_STATIC_SELECTION_FILE.read_text(encoding="utf-8"))
        return set((data.get("items") or {}).keys())
    except (ValueError, OSError):
        return set()


def _our_image(item: dict, prom_products: dict) -> str | None:
    """Фото товару для фото-звірки (pHash) та фіду.

    ДЖЕРЕЛО ЧИСТИХ ФОТО — САМ Toysi (`toysi.ua/p/enl_*.jpg`): у переважній більшості
    товарів фото Toysi чисті, без вотермарки (перевірено наживо 2026-08-17). `images.prom.ua`
    — це НЕ окреме «чисте джерело», а лише дзеркало тих самих фото Toysi, яке Prom перезалив
    собі при заведенні товару. Тому беремо фото Toysi НАПРЯМУ (пул кандидатів = весь каталог,
    не лише ~500 товарів, що вже на Prom).

    Prom-копія лишається ЛИШЕ як фолбек (на випадок рідкісного товару, де фото Toysi недоступне).
    Рідкісні вотермарк-товари (~0.3%, які Rozetka рубає на модерації) обробляються окремо —
    ідентифікацією за фідбеком модерації, не тут."""
    for p in (item.get("pictures") or []):
        u = (p or "").strip()
        if u.startswith("https://"):
            return u
    # фолбек: дзеркало тих самих фото на Prom (images.prom.ua)
    prod = prom_products.get(str(item.get("vendor_code") or item.get("id"))) if prom_products else None
    if prod:
        mi = (prod.get("main_image") or "").strip()
        if mi.startswith("https://images.prom.ua"):
            return mi
        for im in (prod.get("images") or []):
            u = (im.get("url") or "").strip()
            if u.startswith("https://images.prom.ua"):
                return u
    return None


def _phash(url: str, session: requests.Session):
    try:
        r = session.get(url, headers=HEADERS, timeout=15)
        if not r.ok or not r.content:
            return None
        return imagehash.phash(Image.open(io.BytesIO(r.content)).convert("RGB"))
    except Exception:
        return None


def _price_num(txt: str):
    digits = "".join(ch for ch in (txt or "") if ch.isdigit())
    return float(digits) if digits else None


# Сентинел: сторінку пошуку заблоковано/помилка (антибот «Трохи зачекайте…» / 500) — НЕ те саме,
# що «нема товару». У цьому разі кандидата ПРОПУСКАЄМО (не можна плутати з «унікальний → додати»).
BLOCKED = object()


def _page_blocked(page) -> bool:
    """Сторінка — антибот-челендж / серверна помилка, а не чесний результат пошуку."""
    try:
        t = (page.title() or "").lower()
        if "зачекайте" in t or "трохи" in t or not t:
            return True
        body = (page.evaluate("() => document.body ? document.body.innerText.slice(0,400) : ''") or "").lower()
        return ("пішло не так" in body) or ("500" in body and "помил" in body)
    except Exception:
        return False


def rozetka_market_price(page, session, name: str, our_hash, our_price=None):
    """Публічний пошук Rozetka за назвою (СПРАВЖНІЙ Chrome) → підтвердження «той самий товар»
    ДВОМА сигналами (pHash фото АБО збіг назви+розумна ціна, див. коментар над NAME_OVERLAP_MIN) →
    min ціна серед підтверджених.
    Повертає: float (ринок), None (сторінка ок, конкурента нема → унікальний),
    BLOCKED (антибот/помилка — кандидата пропустити, НЕ трактувати як унікальний).
    our_price (наш floor) — для гварда назви: конкурент дешевший за 30% нього = інший товар, ігнор."""
    from urllib.parse import quote
    try:
        page.goto(f"https://rozetka.com.ua/ua/search/?text={quote(name[:120])}", timeout=NAV_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        return BLOCKED
    try:
        page.wait_for_selector("rz-catalog-tile", timeout=8000)
    except PlaywrightTimeoutError:
        # нема плиток: або чесно нема товару (унікальний), або блок/помилка (пропустити)
        return BLOCKED if _page_blocked(page) else None
    try:
        tiles = page.evaluate("""() => [...document.querySelectorAll('rz-catalog-tile')].slice(0,%d).map(t=>{
            const p=t.querySelector('[class*="price"]'); const img=t.querySelector('img');
            const ti=t.querySelector('[class*="title"]');
            return {price:p?p.textContent:'', img:img?(img.getAttribute('src')||img.getAttribute('data-src')||''):'', title:ti?ti.textContent:''};
        })""" % MAX_TILES_PER_SEARCH)
    except PlaywrightTimeoutError:
        return BLOCKED
    # (прибрано ранній `our_hash is None → None`: без нашого фото сигнал назви ще працює —
    # саме через той return ми хибно вважали товари унікальними.)
    matched = []
    our_toks = _name_tokens(name)
    for t in tiles:
        price = _price_num(t.get("price"))
        if not price:
            continue
        # сигнал 1: pHash фото (сильний, точний)
        img = (t.get("img") or "").strip()
        if our_hash is not None and img.startswith("http"):
            th = _phash(img, session)
            if th is not None and (our_hash - th) <= PHASH_MAX_DISTANCE:
                matched.append(price)
                continue
        # сигнал 2: збіг назви + розумна ціна (ловить конкурента з ІНШИМ фото)
        tile_toks = _name_tokens(t.get("title"))
        if our_price and len(our_toks & tile_toks) >= NAME_MATCH_MIN_SHARED \
                and _name_overlap(our_toks, tile_toks) >= NAME_OVERLAP_MIN \
                and price >= our_price * NAME_MATCH_MIN_PRICE_RATIO:
            matched.append(price)
    return min(matched) if matched else None


def _load_cursor() -> int:
    try:
        return int(json.loads(CURSOR_FILE.read_text(encoding="utf-8")).get("offset", 0))
    except (ValueError, OSError, TypeError, AttributeError):
        return 0


def _save_cursor(off: int) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps({"offset": off}), encoding="utf-8")


def _empty_stats() -> dict:
    return {"eligible": 0, "checked": 0, "competitive": 0, "uncompetitive": 0, "unique": 0,
            "blocked": 0, "oos_or_lowstock": 0, "already": 0, "invalid_card": 0,
            "no_clean_image": 0, "bad_cost": 0, "oversized": 0, "gone": 0}


def _check_market_batch(batch: list, st: dict) -> list:
    """СПІЛЬНЕ ЯДРО: відкриває СПРАВЖНІЙ Chrome (не bundled — інакше антибот Rozetka віддає 0
    плиток) і для кожного (pid, item, our_img) рахує floor + ринкову ціну (rozetka_market_price,
    2 сигнали) + рішення competitive/uncompetitive/unique. Повертає list результатів
    {pid,item,cost,floor,market,decision}; оновлює st. Використовують run() (додавання) і
    audit_members() (переаудит наявних) — щоб чек був ОДИН, не два розсинхронені."""
    import random
    results = []
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(CHROME_PROFILE), channel="chrome", headless=True,
            args=["--disable-blink-features=AutomationControlled"], locale="uk-UA",
            viewport={"width": 1366, "height": 900}, user_agent=HEADERS["User-Agent"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        session = requests.Session()
        for pid, item, our_img in batch:
            try:
                cost = float(real_toysi_cost(item) or 0)
            except (TypeError, ValueError):
                cost = 0
            if cost <= 0:
                st["bad_cost"] += 1;  continue
            floor, _commission = _resolve_rozetka_floor(cost, ROZETKA_COMPETITOR_MARGIN, ROZETKA_PAYMENT_COMMISSION)
            our_hash = _phash(our_img, session)
            market = rozetka_market_price(page, session, item.get("name", ""), our_hash, our_price=floor)
            if market is BLOCKED:
                st["blocked"] += 1
                time.sleep(random.uniform(*SEARCH_DELAY))
                continue   # непевно (антибот/помилка) — пропускаємо (НЕ додаємо / НЕ знімаємо)
            st["checked"] += 1
            if market is None:
                decision = "unique"; st["unique"] += 1
            elif floor <= market:
                decision = "competitive"; st["competitive"] += 1
            else:
                decision = "uncompetitive"; st["uncompetitive"] += 1
            results.append({"pid": pid, "item": item, "cost": round(cost, 2),
                            "floor": round(floor, 2),
                            "market": (round(market, 2) if market else None), "decision": decision})
            time.sleep(random.uniform(*SEARCH_DELAY))
        ctx.close()
    return results


def run(catalog: dict, membership: set, prom_products: dict, limit: int) -> tuple[list, dict]:
    """ДОДАВАННЯ: ротаційна партія придатних НЕ-членів → перевірка ринку → кандидати «додати»
    (competitive/unique). Стара поведінка, тепер через спільне ядро _check_market_batch."""
    st = _empty_stats()
    eligible = []
    for pid in sorted(catalog.keys()):
        item = catalog[pid]
        if pid in membership:
            st["already"] += 1;  continue
        if int(item.get("stock", 0) or 0) < MIN_STOCK:
            st["oos_or_lowstock"] += 1;  continue
        if not _qualifies_for_feed(item, set()):
            st["invalid_card"] += 1;  continue
        if not _within_rz_delivery_dims(item):     # великогабаритні (>120см) — не додаємо на Rozetka
            st["oversized"] += 1;  continue
        our_img = _our_image(item, prom_products)
        if not our_img:
            st["no_clean_image"] += 1;  continue
        eligible.append((pid, item, our_img))
    st["eligible"] = len(eligible)
    total = len(eligible)
    off = _load_cursor() % total if total else 0
    batch = [eligible[(off + k) % total] for k in range(min(limit, total))]
    results = _check_market_batch(batch, st)
    add = [{"pid": r["pid"], "name": (r["item"].get("name") or "")[:80],
            "category": r["item"].get("category_name"), "cost": r["cost"],
            "rozetka_floor_5pct": r["floor"], "rozetka_market": r["market"],
            "decision": r["decision"], "stock": r["item"].get("stock")}
           for r in results if r["decision"] in ("competitive", "unique")]
    if total:
        _save_cursor((off + len(batch)) % total)
    return add, st


def _load_audit_cursor() -> int:
    try:
        return int(json.loads(AUDIT_CURSOR_FILE.read_text(encoding="utf-8")).get("offset", 0))
    except (ValueError, OSError, TypeError, AttributeError):
        return 0


def _save_audit_cursor(off: int) -> None:
    AUDIT_CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_CURSOR_FILE.write_text(json.dumps({"offset": off}), encoding="utf-8")


def audit_members(catalog: dict, membership: set, prom_products: dict, limit: int) -> tuple[list, dict]:
    """ПЕРЕАУДИТ наявних членів Rozetka тим самим чеком (окремий курсор ротації). DRY-RUN:
    membership НЕ чіпає — лише повертає (remove_candidates, st). remove = члени, де floor > ринок
    (uncompetitive) → кандидати на ЗНЯТТЯ. competitive/unique лишаємо; blocked/без-фото/OOS/зниклі
    пропускаємо (на непевності НЕ знімаємо — краще лишити, ніж помилково зняти живий лістинг)."""
    st = _empty_stats()
    eligible = []
    for pid in sorted(str(p) for p in membership):
        item = catalog.get(pid)
        if not item:
            st["gone"] += 1;  continue      # зник з каталогу Toysi — окремий випадок, тут не знімаємо
        if int(item.get("stock", 0) or 0) < MIN_STOCK:
            st["oos_or_lowstock"] += 1;  continue
        our_img = _our_image(item, prom_products)
        if not our_img:
            st["no_clean_image"] += 1;  continue
        eligible.append((pid, item, our_img))
    st["eligible"] = len(eligible)
    total = len(eligible)
    off = _load_audit_cursor() % total if total else 0
    batch = [eligible[(off + k) % total] for k in range(min(limit, total))]
    results = _check_market_batch(batch, st)
    remove = [{"pid": r["pid"], "name": (r["item"].get("name") or "")[:80],
               "category": r["item"].get("category_name"), "cost": r["cost"],
               "rozetka_floor_5pct": r["floor"], "rozetka_market": r["market"],
               "reason": "uncompetitive (floor>ринок)"}
              for r in results if r["decision"] == "uncompetitive"]
    if total:
        _save_audit_cursor((off + len(batch)) % total)
    return remove, st


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=BATCH_SIZE, help="кандидатів за прогін (ротація)")
    ap.add_argument("--audit-members", action="store_true",
                    help="ПЕРЕАУДИТ наявних членів Rozetka (dry-run): кандидати на ЗНЯТТЯ "
                         "неконкурентних (floor>ринок); membership НЕ чіпає")
    a = ap.parse_args()
    membership = _load_membership()
    prom_products = _load_prom_products_cache()
    print("[Merchant] Завантажую каталог Toysi...")
    from parser import fetch_toysi_catalog
    catalog = {str(k): v for k, v in (fetch_toysi_catalog() or {}).items()}
    if not catalog:
        print("[Merchant] каталог порожній — вихід.", file=sys.stderr); sys.exit(1)

    if a.audit_members:
        member_ids = set(membership.keys()) if isinstance(membership, dict) else set(membership)
        remove, st = audit_members(catalog, member_ids, prom_products, a.limit)
        prev = []
        if REMOVAL_OUTPUT_FILE.exists():
            try:
                prev = json.loads(REMOVAL_OUTPUT_FILE.read_text(encoding="utf-8")).get("remove", [])
            except (ValueError, OSError):
                prev = []
        seen = {c["pid"] for c in remove}
        merged = remove + [c for c in prev if c["pid"] not in seen]
        REMOVAL_OUTPUT_FILE.write_text(json.dumps(
            {"at": datetime.now().isoformat(), "count": len(merged), "remove": merged},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[Merchant/audit] членів до перевірки: {st['eligible']} "
              f"(зникли з Toysi {st['gone']}, OOS/низький склад {st['oos_or_lowstock']}, без фото {st['no_clean_image']})")
        print(f"[Merchant/audit] перевірено ринок: {st['checked']} → у ринку/лишаємо "
              f"{st['competitive'] + st['unique']}, ДОРОЖЧІ (на зняття) {st['uncompetitive']} "
              f"| пропущено (антибот) {st['blocked']}")
        print(f"[Merchant/audit] DRY-RUN → {REMOVAL_OUTPUT_FILE.name} "
              f"(накопичено {len(merged)} на ЗНЯТТЯ; membership НЕ змінено)")
        return

    add, st = run(catalog, membership, prom_products, a.limit)
    # мерджимо у файл-накопичувач (ротація за кілька прогонів)
    prev = []
    if OUTPUT_FILE.exists():
        try:
            prev = json.loads(OUTPUT_FILE.read_text(encoding="utf-8")).get("candidates", [])
        except (ValueError, OSError):
            prev = []
    seen = {c["pid"] for c in add}
    merged = add + [c for c in prev if c["pid"] not in seen]
    OUTPUT_FILE.write_text(json.dumps(
        {"at": datetime.now().isoformat(), "count": len(merged), "candidates": merged},
        ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[Merchant] придатних (Toysi, stock≥{MIN_STOCK}, не на Rozetka, валідна картка, чисте фото): {st['eligible']}")
    print(f"[Merchant] перевірено ринок цього прогону: {st['checked']} → "
          f"конкурентні {st['competitive']}, унікальні {st['unique']}, дорожчі {st['uncompetitive']} "
          f"| пропущено (антибот/помилка) {st['blocked']}")
    print(f"[Merchant] відсіяно (у придатності): вже на Rozetka {st['already']}, "
          f"низький склад/OOS {st['oos_or_lowstock']}, невалідна картка {st['invalid_card']}, без фото {st['no_clean_image']}, "
          f"великогабаритні >120см {st['oversized']}")
    print(f"[Merchant] → {OUTPUT_FILE.name} (накопичено {len(merged)} кандидатів «додати»)")


if __name__ == "__main__":
    main()
