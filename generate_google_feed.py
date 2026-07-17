"""
generate_google_feed.py — товарний фід для Google Merchant Center (Free
listings), за аналогією з generate_prom_feed_top.py, але ОКРЕМА логіка —
вимоги Google відрізняються від Prom (інший набір обов'язкових полів,
інша структура XML, інша ціна показу немає значення взагалі).

Мова/валюта: українська (uk) / UAH — відповідно до реального магазину
(cs4219597.prom.ua / plutustoys.com.ua), НЕ російська, як у Prom-фіді
(там name/description "російська" — вимога самого Prom, тут такої вимоги
немає).

ПОЛЯ GOOGLE MERCHANT — що вже є в даних Toysi, а що доводиться домислювати:
- id, title, description, image_link, availability — є напряму (name,
  description, pictures, stock).
- price — Я НЕ беру сировину Toysi (собівартість), а ту саму
  default_retail_price() з generate_prom_feed.py, що й реально йде на
  сайт — Google звіряє ціну фіда з ціною на сторінці товару, розбіжність
  = "Price mismatch" і немодерація.
- link — Toysi/Prom Products API НЕ віддає URL сторінки товару (перевірено
  напряму: GET /products/{id} і /products/list — жодного поля "url" немає
  в жодному з двох). Числовий id береться через internal GraphQL-пошук
  (той самий SearchListingQuery, що й prom_competitor_pricer.py), фільтруючи
  на company_id власного магазину замість виключення його.
  ВИПРАВЛЕНО 2026-07-11 (аудит, pt34): SearchListingQuery вже повертає
  поле `urlText` — реальний, транслітерований slug сторінки товару (напр.
  "konstruktor-magicheskij-mir"), а не вигаданий плейсхолдер. Перевірено
  напряму живим запитом: `/ua/p{id}-item.html` (старий плейсхолдер-підхід)
  дійсно повертає 200, але через 30x-редирект на канонічний
  `/ua/p{id}-{urlText}.html` — Google Merchant негативно ставиться саме
  до посилань з редиректом у полі link (рекомендує кінцеву, канонічну
  адресу). Тепер посилання будується одразу з `urlText`, без редиректу.
  Товар без `urlText` у відповіді (малоймовірно, але можливо) — той самий
  безпечний fallback, що й при невпевненому збігу: пропускаємо товар, а не
  вигадуємо slug.
- condition — Toysi цього поля не дає; увесь каталог дропшип, новий товар
  з коробки — жорстко "new" для всіх позицій.
- brand — vendor Toysi є ~для більшості, але непослідовно (пробіли з
  MIC/MiC вже виправлені normalize_vendor(), та частина позицій узагалі
  без vendor). Порожній vendor -> тут ставимо "PlutusToys" як
  fallback-бренд (магазин власного асортименту, не завод-виробник) —
  чесний компроміс: Google вимагає непорожній brand для частини категорій,
  а вигадувати виробника гірше, ніж підписати як бренд магазину.
- gtin/mpn — Toysi віддає <barcode>, але це РІЗНИЙ формат у різних SKU
  (іноді реальний EAN, іноді внутрішній номер постачальника) — перевіряємо
  на валідний GTIN-8/12/13/14 (лише цифри, правильна довжина) ПЕРЕД тим,
  як писати як gtin; інакше пропускаємо поле повністю (краще без gtin, ніж
  з хибним — Google блокує/ігнорує товар за завідомо невалідний код).
- google_product_category — Toysi НЕ дає жодного відповідника таксономії
  Google. Карта нижче — власний keyword-мапінг на РЕАЛЬНУ офіційну
  таксономію Google (завантажено напряму з
  google.com/basepages/producttype/taxonomy-with-ids.en-US.txt,
  2026-07-11 — не з пам'яті), фолбек — загальна "Toys & Games" (1239)
  для категорій, які жодне правило не впізнало.
"""
import json
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

import requests

from parser import fetch_toysi_catalog
from generate_prom_feed import default_retail_price, normalize_vendor
from generate_prom_feed_top import select_top_items
from prom_catalog_sync import fetch_prom_products
from prom_competitor_pricer import SEARCH_DELAY

OUTPUT_FILE = "feeds/google_merchant_feed.xml"

SHOP_NAME  = "PlutusToys"
SHOP_URL   = "https://plutustoys.com.ua"
FEED_LANG  = "uk"
FEED_CURRENCY = "UAH"

# Канонічний URL сторінки товару — {id}-{urlText}. id — гарантований, напряму
# з fetch_prom_products() (реальний Prom id за external_id, не пошук).
# urlText — одним детермінованим HTTP-запитом (див. resolve_own_product_links
# нижче), не з відповіді пошуку.
LINK_TEMPLATE = SHOP_URL + "/ua/p{prom_id}-{url_text}.html"

# Той самий джитер-інтервал, що вже усталений у prom_competitor_pricer.py
# для аналогічних послідовних запитів до prom.ua (SEARCH_DELAY, 0.4с) —
# build_feed_items()/resolve_own_product_links() роблять до ~970
# послідовних запитів на прогін, тож без паузи це непотрібне навантаження
# на prom.ua понад те, що вже генерує prom_competitor_pricer.py в той
# самий день.
SEARCH_JITTER_RANGE = (SEARCH_DELAY, SEARCH_DELAY * 1.5)

GTIN_VALID_LENGTHS = {8, 12, 13, 14}

# Формат Location-заголовка редиректу prom.ua: /ua/p{id}-{urlText}.html —
# захоплюємо лише urlText (частина після дефіса й до .html).
_URL_TEXT_RE = re.compile(r"/ua/p\d+-([^/]+)\.html")

REQUEST_TIMEOUT = 15

# Кеш self-match результатів (Toysi pid -> {prom_id, url_text}), спільний
# з generate_rozetka_feed.py: той файл своїх GraphQL-запитів НЕ робить,
# лише читає цей кеш (якщо він свіжий), щоб не подвоювати навантаження на
# reverse-engineered пошуковий ендпоінт Prom. Тут (Google-фід) кеш завжди
# рахується наново з живого пошуку — це "джерело правди"; Rozetka-фід —
# лише опційний, best-effort споживач.
OWN_PRODUCT_LINKS_CACHE_FILE = Path(__file__).parent / "own_product_links_cache.json"
OWN_PRODUCT_LINKS_CACHE_TTL_DAYS = 7  # slug/id товару на Prom стабільний,
                                       # тижневий кеш — розумний баланс
                                       # свіжості проти зайвих запитів

# 2026-07-17 (Autonomy-11/Vis-11): {external_id: {"category_id": int,
# "category_caption": str}} — РЕАЛЬНА Prom-категорія кожного товару, за
# тим самим патерном, що й OWN_PRODUCT_LINKS_CACHE_FILE вище. На відміну
# від self-match (окремий HTTP-запит на товар), тут ЖОДНОГО додаткового
# запиту не потрібно — prom_products_by_external_id вже містить поле
# `category: {id, caption}` для кожного товару, повернуте тим самим
# fetch_prom_products(), що й так викликається щодня для self-match/фото.
# Читає цей кеш prom_competitor_pricer.py/full_catalog_competitor_scan.py
# (через _load_prom_category_cache() у prom_competitor_pricer.py), щоб
# передати `prom_category_id` у get_platform_commission() з фолбеком на
# Toysi-based PROM_CATEGORY_COMMISSION, коли кеш відсутній/застарів.
PROM_CATEGORY_CACHE_FILE = Path(__file__).parent / "prom_category_cache.json"
PROM_CATEGORY_CACHE_TTL_DAYS = 7


def is_valid_gtin(barcode: str) -> bool:
    b = (barcode or "").strip()
    return b.isdigit() and len(b) in GTIN_VALID_LENGTHS


# ---------------------------------------------------------------------------
# google_product_category — keyword-мапінг на РЕАЛЬНУ таксономію Google
# (ID звірено напряму з офіційним файлом, 2026-07-11). Перевіряється по
# category_name (нижній регістр) через "перше правило, що збіглось" —
# порядок важливий: специфічніші правила мають йти РАНІШЕ загальних.
# ---------------------------------------------------------------------------
GOOGLE_CATEGORY_FALLBACK = "1239"  # Toys & Games (загальна, коли жодне правило не підійшло)

_CATEGORY_RULES = [
    # (ключові слова в category_name, ID Google)
    (("дерев'ян", "пазл"), "6725"),                          # Wooden & Pegged Puzzles
    (("пазл",), "3867"),                                    # Toys & Games > Puzzles
    (("конструктор",), "3805"),                              # Construction Set Toys
    (("кубик", "дерев"), "3617"),                            # Wooden Blocks
    (("лялькови", "будино"), "2499"),                        # Dollhouses
    (("лялька", "аксесуар"), "3584"),                        # Doll & Action Figure Accessories
    (("лялька",), "1257"),                                   # Dolls
    (("пупс",), "1257"),
    (("м'яка іграшка",), "1259"),                             # Stuffed Animals
    (("мяка іграшка",), "1259"),
    (("фігурк",), "6058"),                                    # Action & Toy Figures
    (("іграшков", "набір"), "3166"),                          # Toy Playsets
    (("ігровий набір",), "3166"),
    (("машинк",), "3551"),                                    # Toy Cars
    (("трактор",), "3296"),                                   # Toy Trucks & Construction Vehicles
    (("вантажівк",), "3296"),
    (("залізниц", "потяг"), "5152"),                          # Toy Trains & Train Sets
    (("потяг",), "5152"),
    (("самокат",), "2799"),                                   # Riding Toys
    (("велосипед",), "2799"),
    (("каталк",), "2799"),
    (("радіокеруванн",), "2546"),                             # Remote Control Toys
    (("робот",), "3625"),                                     # Robotic Toys
    (("кухн",), "3298"),                                      # Toy Kitchens & Play Food
    (("посуд",), "3298"),
    (("лікар", "набір"), "3129"),                             # Pretend Professions & Role Playing
    (("професі",), "3129"),
    (("ванн",), "3911"),                                      # Bath Toys
    (("пісочниц",), "2743"),                                   # Sandboxes
    (("пісок", "кінетичн"), "505818"),                        # Play Dough & Putty
    (("пластилін",), "505818"),
    (("слайм",), "505818"),
    (("басейн",), "6464"),                                    # Water Play Equipment
    (("надувн", "коло"), "6464"),
    (("водний пістолет",), "3627"),                           # Toy Weapons & Gadgets
    (("водяний пістолет",), "3627"),
    (("зброя",), "3627"),
    (("батут",), "1738"),                                     # Trampolines
    (("м'яч",), "1266"),                                       # Sports Toys
    (("мяч",), "1266"),
    (("бульбашк",), "3874"),                                   # Bubble Blowing Toys
    (("антистрес",), "4352"),                                  # Activity Toys
    (("спіннер",), "3466"),                                    # Spinning Tops
    (("дзиґ",), "3466"),
    (("розмальовк", "фарб"), "3731"),                          # Art & Drawing Toys
    (("картина за номерами",), "3731"),
    (("музичн",), "1264"),                                     # Musical Toys
    (("настільна гра",), "1246"),                              # Board Games
    (("карт", "гра"), "1247"),                                 # Card Games
    (("розвиваюч",), "1262"),                                  # Educational Toys
    (("мозаїк",), "3867"),
]


def google_product_category(category_name: str) -> str:
    text = (category_name or "").strip().lower()
    for keywords, cat_id in _CATEGORY_RULES:
        if all(kw in text for kw in keywords):
            return cat_id
    return GOOGLE_CATEGORY_FALLBACK


# ---------------------------------------------------------------------------
# link — реальний URL сторінки товару на власному сайті.
#
# ПЕРЕРОБЛЕНО 2026-07-17 (Autonomy-6 — попередній GraphQL текстовий пошук
# промахувався на ~71% топ-970, стеля покриття buyBox/Rozetka <url>/Google
# link). Замінено на ДЕТЕРМІНОВАНИЙ шлях без жодного пошуку чи текстової
# схожості:
#   1. fetch_prom_products() (вже викликається щодня, prom_catalog_sync.py)
#      дає ГАРАНТОВАНИЙ реальний Prom id за external_id напряму — це наш
#      ЖЕ товар за визначенням (пряме зіставлення по ключу, не "найсхожіший
#      серед знайдених").
#   2. urlText (потрібен лише для канонічного посилання) — одним
#      детермінованим запитом `GET /ua/p{id}-item.html` БЕЗ слідування
#      редиректу: Prom резолвить сторінку виключно за числовим id,
#      ігноруючи сам текст слага в запиті, і повертає 301 з
#      `Location: /ua/p{id}-{реальний-urlText}.html`. Підтверджено живим
#      запитом 2026-07-17 (SKU 300391 -> 301, Location із коректним
#      "antistres-igrashka-butter").
#
# Жодного порогу впевненості чи розмірного гейту тут більше не потрібно —
# попередній клас ризику ("хибний збіг з іншим власним варіантом
# розміру/кольору", pt34) структурно виключений: це прямий lookup за
# external_id, не текстовий пошук.
# ---------------------------------------------------------------------------
def _resolve_url_text(prom_id: int) -> str | None:
    """Один детермінований HTTP-запит на товар — без слідування редиректу,
    парсимо Location. Повертає None (не виняток) на будь-яку мережеву
    проблему чи неочікуваний формат відповіді — той самий безпечний
    дефолт, що й скрізь у цьому файлі: пропустити link для товару, а не
    вигадати його."""
    try:
        response = requests.get(
            f"https://prom.ua/ua/p{prom_id}-item.html",
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        )
    except requests.exceptions.RequestException as e:
        print(f"[Google] Не вдалось визначити urlText для prom_id={prom_id}: {e}", file=sys.stderr)
        return None

    location = response.headers.get("Location", "")
    match = _URL_TEXT_RE.match(location)
    if not match:
        print(
            f"[Google] Неочікувана відповідь для prom_id={prom_id} "
            f"(статус={response.status_code}, Location={location!r}) — можлива зміна формату URL на Prom",
            file=sys.stderr,
        )
        return None
    return match.group(1)


def _save_own_product_links_cache(links: dict) -> None:
    try:
        OWN_PRODUCT_LINKS_CACHE_FILE.write_text(
            json.dumps(links, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError as e:
        print(f"[Google] Не вдалось зберегти кеш посилань ({e}) — не критично, "
              f"наступний прогін просто порахує наново.", file=sys.stderr)


def resolve_own_product_links(catalog: dict, prom_products_by_external_id: dict) -> dict:
    """Для кожного товару catalog бере ГАРАНТОВАНИЙ Prom id за external_id
    з prom_products_by_external_id (fetch_prom_products(), не пошук), тоді
    визначає urlText одним детермінованим запитом (_resolve_url_text).
    Повертає {pid: {"prom_id": int, "url_text": str}} лише для товарів, що
    вже імпортовані в Prom (є в prom_products_by_external_id) І чий
    urlText вдалось визначити — решта відсутня в результаті (не вигадуємо
    посилання).

    Побічний ефект: результат зберігається в OWN_PRODUCT_LINKS_CACHE_FILE
    — generate_rozetka_feed.py читає цей файл (лише читає, не рахує сам),
    щоб додати <url> для товарів, які збігаються з цим самим топ-970, без
    повторного навантаження на prom.ua."""
    links = {}
    for pid, item in catalog.items():
        product = prom_products_by_external_id.get(pid)
        if not product:
            continue
        prom_id = product.get("id")
        if not prom_id:
            continue

        url_text = _resolve_url_text(prom_id)
        # Джитер між послідовними запитами до prom.ua (SEARCH_DELAY) — без
        # цього повний прогін на топ-970 робить до ~970 послідовних
        # запитів без жодної паузи.
        time.sleep(random.uniform(*SEARCH_JITTER_RANGE))
        if not url_text:
            continue
        links[pid] = {"prom_id": prom_id, "url_text": url_text}

    _save_own_product_links_cache(links)
    return links


def _save_prom_category_cache(cache: dict) -> None:
    try:
        PROM_CATEGORY_CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError as e:
        print(f"[Google] Не вдалось зберегти кеш категорій Prom ({e}) — не критично, "
              f"наступний прогін просто порахує наново.", file=sys.stderr)


def build_prom_category_cache(catalog: dict, prom_products_by_external_id: dict) -> dict:
    """{external_id: {"category_id": int, "category_caption": str}} — БЕЗ
    жодного додаткового HTTP-запиту: prom_products_by_external_id (уже
    отриманий fetch_prom_products() для self-match/фото вище) містить поле
    `category: {id, caption}` для кожного товару напряму з /products/list.

    Це РЕАЛЬНА Prom-категорія КОНКРЕТНОГО товару — на відміну від
    PROM_CATEGORY_COMMISSION (competitor_pricing.py), яка зіставляє
    комісію за НАЗВОЮ категорії Toysi і тому не розрізняє випадки, коли
    один Toysi category_name розпадається на кілька різних Prom-категорій
    з різними ставками (підтверджено на "рюкзаки": 3 різні Prom-категорії
    в реальному розподілі топ-970, див. PROM_CATEGORY_ID_COMMISSION).

    Товари, ще не імпортовані в Prom (немає в prom_products_by_external_id)
    чи без поля category — просто відсутні в результаті (не вигадуємо)."""
    cache = {}
    for pid in catalog:
        product = prom_products_by_external_id.get(pid)
        if not product:
            continue
        category = product.get("category") or {}
        category_id = category.get("id")
        if not category_id:
            continue
        cache[pid] = {"category_id": category_id, "category_caption": category.get("caption")}

    _save_prom_category_cache(cache)
    return cache


def _clean_description(html_desc: str) -> str:
    """Google приймає HTML в description, але краще подати чистий текст —
    прибираємо теги, залишаємо переноси рядків як пробіли."""
    text = re.sub(r"<br\s*/?>", " ", html_desc or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_feed_items(catalog: dict, prom_products: dict, links: dict) -> tuple[list, dict]:
    stats = {
        "total_considered": len(catalog),
        "no_price": 0,
        "no_link_skipped": 0,
        "no_image": 0,
        "no_gtin": 0,
        "no_brand_fallback": 0,
        "category_fallback": 0,
        "included": 0,
    }
    items = []

    for pid, item in catalog.items():
        try:
            cost = float(item.get("price") or 0)
        except (TypeError, ValueError):
            cost = 0
        if cost <= 0:
            stats["no_price"] += 1
            continue

        name = (item.get("name") or "").strip()
        if not name:
            continue

        link_info = links.get(pid)
        if link_info is None:
            stats["no_link_skipped"] += 1
            continue
        prom_id, url_text = link_info["prom_id"], link_info["url_text"]

        prom_product = prom_products.get(str(item.get("vendor_code") or pid)) or {}
        image = prom_product.get("main_image") or (item.get("pictures") or [None])[0]
        if not image:
            stats["no_image"] += 1
            continue

        retail_price = default_retail_price(cost, item.get("category_name"))
        stock = item.get("stock", 0)

        brand = normalize_vendor(item.get("vendor") or "")
        if not brand:
            brand = SHOP_NAME
            stats["no_brand_fallback"] += 1

        gtin = item.get("barcode") if is_valid_gtin(item.get("barcode")) else None
        if not gtin:
            stats["no_gtin"] += 1

        cat_id = google_product_category(item.get("category_name"))
        if cat_id == GOOGLE_CATEGORY_FALLBACK:
            stats["category_fallback"] += 1

        items.append({
            "id": str(item.get("vendor_code") or pid),
            "title": name[:150],
            "description": _clean_description(item.get("description", ""))[:5000] or name,
            "link": LINK_TEMPLATE.format(prom_id=prom_id, url_text=url_text),
            "image_link": image,
            "price": f"{retail_price:.2f} {FEED_CURRENCY}",
            "availability": "in_stock" if stock > 0 else "out_of_stock",
            "condition": "new",
            "brand": brand,
            "gtin": gtin,
            "google_product_category": cat_id,
        })
        stats["included"] += 1

    return items, stats


def _build_xml(items: list) -> ET.Element:
    NS = "http://base.google.com/ns/1.0"
    ET.register_namespace("g", NS)
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{SHOP_NAME} — товарний фід Google Merchant"
    ET.SubElement(channel, "link").text = SHOP_URL
    ET.SubElement(channel, "description").text = "Іграшки для дітей — PlutusToys"

    for it in items:
        entry = ET.SubElement(channel, "item")
        ET.SubElement(entry, f"{{{NS}}}id").text = it["id"]
        ET.SubElement(entry, "title").text = it["title"]
        ET.SubElement(entry, "description").text = it["description"]
        ET.SubElement(entry, "link").text = it["link"]
        ET.SubElement(entry, f"{{{NS}}}image_link").text = it["image_link"]
        ET.SubElement(entry, f"{{{NS}}}price").text = it["price"]
        ET.SubElement(entry, f"{{{NS}}}availability").text = it["availability"]
        ET.SubElement(entry, f"{{{NS}}}condition").text = it["condition"]
        ET.SubElement(entry, f"{{{NS}}}brand").text = it["brand"]
        if it.get("gtin"):
            ET.SubElement(entry, f"{{{NS}}}gtin").text = it["gtin"]
        ET.SubElement(entry, f"{{{NS}}}google_product_category").text = it["google_product_category"]

    return rss


def generate_google_feed(output_file: str = OUTPUT_FILE, limit: int = None) -> None:
    print("[Google] Завантажуємо каталог Toysi...")
    catalog = fetch_toysi_catalog()
    if not catalog:
        print("[Google] Каталог порожній — файл не створено.")
        return

    top_catalog = select_top_items(catalog)
    if limit:
        top_catalog = dict(list(top_catalog.items())[:limit])
    print(f"[Google] У топ-970: {len(top_catalog)} товарів для обробки.")

    # ЗМІНЕНО 2026-07-17 (Autonomy-6): fetch_prom_products() тепер
    # ВИКЛИКАЄТЬСЯ ПЕРШИМ і НАВМИСНО — на відміну від попередньої версії,
    # де self-match свідомо рахувався ДО нього (тоді self-match був
    # незалежним GraphQL-пошуком без потреби в PROM_API_KEY). Новий
    # resolve_own_product_links() сам залежить від fetch_prom_products()
    # (бере звідти гарантований id за external_id, замість пошуку) — тож
    # PROM_API_KEY тепер потрібен і для self-match теж. Це прийнятно:
    # PROM_API_KEY — уже обов'язковий секрет для цього ж workflow
    # (prom_competitor_pricer.py та інші кроки), не нова залежність на
    # практиці.
    print("[Google] Завантажуємо реальний список товарів Prom (для фото та self-match)...")
    prom_products = fetch_prom_products()
    # Індекс за external_id (vendorCode) — ключ, який реально використовує Prom API
    prom_by_external_id = {
        str(p.get("external_id")): p for p in prom_products.values()
        if p.get("external_id")
    } if isinstance(prom_products, dict) else {}

    print(f"[Google] Визначаємо реальні посилання на сторінки товарів ({len(top_catalog)} товарів)...")
    links = resolve_own_product_links(top_catalog, prom_by_external_id)

    # Autonomy-11/Vis-11: побічний ефект, без додаткових запитів (дані вже
    # в prom_by_external_id) — закриває категорії, що лишались на дефолтній
    # комісії через неоднозначність Toysi category_name (див. коментар над
    # build_prom_category_cache).
    category_cache = build_prom_category_cache(top_catalog, prom_by_external_id)
    print(f"[Google] Кеш реальних Prom-категорій: {len(category_cache)} товарів.")

    items, stats = build_feed_items(top_catalog, prom_by_external_id, links)

    root = _build_xml(items)
    ET.indent(root, space="  ")
    xml_str = ET.tostring(root, encoding="unicode")
    xml_str = f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_str}'

    import os
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(xml_str)

    print(f"[Google] Готово! Збережено: {output_file}")
    print(f"[Google] У фіді: {stats['included']} з {stats['total_considered']} розглянутих")
    print(f"[Google] Пропущено — без ціни: {stats['no_price']}")
    print(f"[Google] Пропущено — не знайдено впевненого посилання на сторінку: {stats['no_link_skipped']}")
    print(f"[Google] Пропущено — без фото: {stats['no_image']}")
    print(f"[Google] Без GTIN (пропущено поле, не весь товар): {stats['no_gtin']}")
    print(f"[Google] Бренд-фолбек на \"{SHOP_NAME}\" (vendor порожній у Toysi): {stats['no_brand_fallback']}")
    print(f"[Google] Категорія-фолбек на загальну \"Toys & Games\" (жодне keyword-правило не збіглось): {stats['category_fallback']}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    generate_google_feed(limit=limit)
