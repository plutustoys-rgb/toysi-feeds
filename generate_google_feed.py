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
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from parser import fetch_toysi_catalog
from generate_prom_feed import default_retail_price, normalize_vendor, fetch_russian_text
from generate_prom_feed_top import select_top_items
from prom_catalog_sync import fetch_prom_products
from prom_competitor_pricer import (
    search_prom_products, _similarity, _size_tokens_conflict, PROM_OWN_COMPANY_ID, SEARCH_DELAY,
)

OUTPUT_FILE = "feeds/google_merchant_feed.xml"

SHOP_NAME  = "PlutusToys"
SHOP_URL   = "https://plutustoys.com.ua"
FEED_LANG  = "uk"
FEED_CURRENCY = "UAH"

# Канонічний URL сторінки товару — {id}-{urlText}, обидва напряму з відповіді
# GraphQL-пошуку (див. find_own_product_id нижче). Без реконструйованого
# плейсхолдер-slug (був тут раніше, підтверджено гіршим — веде на той самий
# результат лише через редирект).
LINK_TEMPLATE = SHOP_URL + "/ua/p{prom_id}-{url_text}.html"

# Той самий джитер-інтервал, що вже усталений у prom_competitor_pricer.py
# для цього самого reverse-engineered GraphQL-ендпоінту (SEARCH_DELAY,
# 0.4с) — build_feed_items() робить ще один окремий запит на кожен товар
# топ-970 (до ~970 послідовних запитів), тож без паузи тут ми подвоюємо
# навантаження на ендпоінт понад те, що вже й так генерує
# prom_competitor_pricer.py в той самий день (аудит, pt34).
SEARCH_JITTER_RANGE = (SEARCH_DELAY, SEARCH_DELAY * 1.5)

GOOGLE_MIN_MATCH_SCORE = 0.55  # той самий клас порогу, що й MATCH_MIN_SCORE конкурентів —
                                # тут це збіг НАШОГО товару із САМИМ СОБОЮ в пошуку, тому
                                # нижчий за MATCH_MIN_SCORE_FOR_DELIST (0.85) цілком безпечний:
                                # найгірший наслідок помилки — пропущений link, не хибна дія.

GTIN_VALID_LENGTHS = {8, 12, 13, 14}


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
    (("пазл",), "3867"),                                    # Toys & Games > Puzzles
    (("дерев'ян", "пазл"), "6725"),                          # Wooden & Pegged Puzzles
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
# link — реальний URL сторінки товару на власному сайті. Ані Products API,
# ані наш власний каталог Toysi не містять цього поля напряму — єдиний
# перевірений робочий шлях: internal GraphQL-пошук (той самий ендпоінт і
# запит, що й prom_competitor_pricer.py), відфільтрований на company_id
# власного магазину замість виключення його з результатів.
# ---------------------------------------------------------------------------
def find_own_product_id(search_name: str) -> tuple[int, str] | None:
    """Шукає товар СЕРЕД ВЛАСНИХ, фільтруючи запит на company_id власного
    магазину напряму (не постфільтром по топ-20 загального пошуку —
    підтверджено емпірично, 2026-07-11: для популярних назв товарів
    конкуренти займають усі перші позиції, власний єдиний лістинг легко
    не потрапляє навіть у топ-20 без цього фільтра). Повертає (id, urlText)
    найкращого текстового збігу СЕРЕД ТИХ, ЩО НЕ конфліктують розмірним
    токеном із search_name, або None, якщо впевненого й безконфліктного
    збігу немає — у цьому разі link для товару НЕ будується (краще
    пропустити товар у фіді, ніж подати Google неправильне посилання).

    ВИПРАВЛЕНО 2026-07-11 (аудит, pt34): раніше брався єдиний найвищий за
    _similarity() кандидат без жодної перевірки, чи це справді ТОЙ САМИЙ
    товар, а не сусідній варіант (інший розмір/колір) з ВЛАСНОГО каталогу —
    SequenceMatcher систематично дає високий скор для товарів, що
    відрізняються лише коротким числовим токеном (той самий клас
    помилки, задокументований і вже виправлений для конкурентів у
    prom_competitor_pricer.py, pt14/pt16 — тут застосовано той самий
    _size_tokens_conflict() гейт). Найгірший наслідок БЕЗ цього гейту —
    не пропущений товар, а ВАЛІДНЕ, робоче посилання на сторінку ІНШОГО
    власного товару."""
    results = search_prom_products(search_name, limit=10, company_id=PROM_OWN_COMPANY_ID)
    candidates = []
    for p in results:
        score = _similarity(search_name, p.get("name", ""))
        if score >= GOOGLE_MIN_MATCH_SCORE:
            candidates.append((score, p))
    candidates.sort(key=lambda sp: sp[0], reverse=True)

    for score, p in candidates:
        if _size_tokens_conflict(search_name, p.get("name", "")):
            continue  # найкращий за текстом, але явно інший розмір/об'єм -- не наш товар, шукаємо далі
        prom_id = p.get("id")
        url_text = p.get("urlText")
        if prom_id is not None and url_text:
            return prom_id, url_text
    return None


def _clean_description(html_desc: str) -> str:
    """Google приймає HTML в description, але краще подати чистий текст —
    прибираємо теги, залишаємо переноси рядків як пробіли."""
    text = re.sub(r"<br\s*/?>", " ", html_desc or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_feed_items(catalog: dict, prom_products: dict, russian_text: dict) -> tuple[list, dict]:
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

        # Пошук Prom переважно російськомовний (та сама причина, що й у
        # prom_competitor_pricer.py) — шукаємо рос. назвою для кращого
        # текстового збігу, але показуємо в фіді (title/description)
        # ОРИГІНАЛЬНУ українську, за вимогою мови фіда.
        name_rus = (russian_text.get(pid, {}) or {}).get("name") or name
        match = find_own_product_id(name_rus)
        # Джитер між запитами до того самого reverse-engineered GraphQL-
        # ендпоінту, що й prom_competitor_pricer.py (SEARCH_DELAY) — без
        # цього повний прогін на топ-970 робить ~970 послідовних запитів
        # без жодної паузи (аудит, pt34).
        time.sleep(random.uniform(*SEARCH_JITTER_RANGE))
        if match is None:
            stats["no_link_skipped"] += 1
            continue
        prom_id, url_text = match

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

    print("[Google] Завантажуємо реальний список товарів Prom (для фото)...")
    prom_products = fetch_prom_products()
    # Індекс за external_id (vendorCode) — ключ, який реально використовує Prom API
    prom_by_external_id = {
        str(p.get("external_id")): p for p in prom_products.values()
        if p.get("external_id")
    } if isinstance(prom_products, dict) else {}

    russian_text = fetch_russian_text()

    print(f"[Google] Шукаємо реальні посилання на сторінки товарів (GraphQL, {len(top_catalog)} запитів)...")
    items, stats = build_feed_items(top_catalog, prom_by_external_id, russian_text)

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
