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

from generate_rozetka_feed import (_qualifies_for_feed, _load_prom_products_cache,
                                   ROZETKA_STATIC_SELECTION_FILE)
from competitor_pricing import _resolve_rozetka_floor, PAYMENT_COMMISSION, real_toysi_cost

BASE_DIR = Path(__file__).parent
OUTPUT_FILE = BASE_DIR / "rozetka_merchant_candidates.json"
CURSOR_FILE = BASE_DIR / ".local_secrets" / "rozetka_merchant_cursor.json"
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


def rozetka_market_price(page, session, name: str, our_hash):
    """Публічний пошук Rozetka за назвою (СПРАВЖНІЙ Chrome рендерить плитки) → pHash-звірка фото →
    min ціна серед підтверджених «той самий товар».
    Повертає: float (ринок), None (сторінка ок, підтвердженого конкурента нема → унікальний),
    BLOCKED (антибот/помилка — кандидата пропустити, НЕ трактувати як унікальний)."""
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
            return {price:p?p.textContent:'', img:img?(img.getAttribute('src')||img.getAttribute('data-src')||''):''};
        })""" % MAX_TILES_PER_SEARCH)
    except PlaywrightTimeoutError:
        return BLOCKED
    if our_hash is None:
        return None   # нема нашого фото для звірки → не можемо підтвердити → трактуємо як унікальний
    matched = []
    for t in tiles:
        price = _price_num(t.get("price"))
        img = (t.get("img") or "").strip()
        if not price or not img.startswith("http"):
            continue
        th = _phash(img, session)
        if th is not None and (our_hash - th) <= PHASH_MAX_DISTANCE:
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


def run(catalog: dict, membership: set, prom_products: dict, limit: int) -> tuple[list, dict]:
    """Готує ротаційну партію придатних кандидатів, перевіряє ринок на Rozetka, повертає (add, stats)."""
    import random
    st = {"eligible": 0, "checked": 0, "competitive": 0, "uncompetitive": 0, "unique": 0,
          "blocked": 0, "oos_or_lowstock": 0, "already": 0, "invalid_card": 0, "no_clean_image": 0, "bad_cost": 0}
    # 1) придатні (Toysi-напряму): stock≥2, не на Rozetka, валідна картка, чисте фото
    eligible = []
    for pid in sorted(catalog.keys()):
        item = catalog[pid]
        if pid in membership:
            st["already"] += 1;  continue
        if int(item.get("stock", 0) or 0) < MIN_STOCK:
            st["oos_or_lowstock"] += 1;  continue
        if not _qualifies_for_feed(item, set()):
            st["invalid_card"] += 1;  continue
        our_img = _our_image(item, prom_products)
        if not our_img:
            st["no_clean_image"] += 1;  continue
        eligible.append((pid, item, our_img))
    st["eligible"] = len(eligible)

    # 2) ротаційна партія
    total = len(eligible)
    off = _load_cursor() % total if total else 0
    batch = [eligible[(off + k) % total] for k in range(min(limit, total))]

    add = []
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        # СПРАВЖНІЙ Chrome (не bundled chromium) — інакше антибот Rozetka віддає 0 плиток.
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
            our_hash = _phash(our_img, session)
            market = rozetka_market_price(page, session, item.get("name", ""), our_hash)
            if market is BLOCKED:
                st["blocked"] += 1
                time.sleep(random.uniform(*SEARCH_DELAY))
                continue   # непевно (антибот/помилка) — НЕ додаємо
            st["checked"] += 1
            floor, commission = _resolve_rozetka_floor(cost, ROZETKA_COMPETITOR_MARGIN, ROZETKA_PAYMENT_COMMISSION)
            if market is None:
                decision = "unique"; st["unique"] += 1
            elif floor <= market:
                decision = "competitive"; st["competitive"] += 1
            else:
                decision = "uncompetitive"; st["uncompetitive"] += 1
            if decision in ("competitive", "unique"):
                add.append({"pid": pid, "name": (item.get("name") or "")[:80],
                            "category": item.get("category_name"), "cost": round(cost, 2),
                            "rozetka_floor_5pct": round(floor, 2),
                            "rozetka_market": (round(market, 2) if market else None),
                            "decision": decision, "stock": item.get("stock")})
            time.sleep(random.uniform(*SEARCH_DELAY))
        ctx.close()

    if total:
        _save_cursor((off + len(batch)) % total)
    return add, st


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=BATCH_SIZE, help="кандидатів за прогін (ротація)")
    a = ap.parse_args()
    membership = _load_membership()
    prom_products = _load_prom_products_cache()
    print("[Merchant] Завантажую каталог Toysi...")
    from parser import fetch_toysi_catalog
    catalog = {str(k): v for k, v in (fetch_toysi_catalog() or {}).items()}
    if not catalog:
        print("[Merchant] каталог порожній — вихід.", file=sys.stderr); sys.exit(1)

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
          f"низький склад/OOS {st['oos_or_lowstock']}, невалідна картка {st['invalid_card']}, без фото {st['no_clean_image']}")
    print(f"[Merchant] → {OUTPUT_FILE.name} (накопичено {len(merged)} кандидатів «додати»)")


if __name__ == "__main__":
    main()
