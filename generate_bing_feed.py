"""
generate_bing_feed.py — товарний фід для Bing/Microsoft Merchant Center.
Задача #171 власниці: взяти google_merchant_feed.xml як основу, звірити
різницю в обов'язкових полях за специфікацією Bing.

Джерело звірки (перевірено напряму, 2026-07-20):
- developers.facebook.com-подібний підхід тут не застосовний — звірка
  йшла проти learn.microsoft.com/en-us/advertising/shopping-content/
  products-resource (Bing Content API, той самий набір полів, що й
  класичний Google Content API for Shopping — googleProductCategory
  навіть буквально так і називається "для сумісності з Google").
- Обов'язкові поля Bing (title, description, link, image_link, price,
  availability, brand, gtin/mpn/identifierExists) — той самий набір,
  що вже рахує build_feed_items() у generate_google_feed.py, БЕЗ ЗМІН.
  condition — не обов'язкове в Bing (дефолт "new", якщо не вказано), але
  наш фід і так завжди пише "new" — нешкідливо залишити.
- shipping/shippingWeight/shippingLabel — за документацією ОБОВ'ЯЗКОВІ
  ЛИШЕ для targetCountry=DE (Німеччина); ми продаємо в Україні —
  НЕ додаємо ці поля, вони тут не застосовні.
- mpn — офіційно "Yes" (обов'язкове), АЛЕ лише "якщо виробник призначив"
  (in an "if applicable" sense per спека) — Toysi не дає жодного поля
  з номером деталі виробника; свідомо НЕ вигадуємо MPN. gtin (валідний,
  is_valid_gtin()) уже покриває частину товарів, де він реально є —
  той самий компроміс, що й у Google-фіді.
- ⚠️ НЕЗАЛЕЖНЕ ПІДТВЕРДЖЕННЯ: значення availability за Bing Content API
  документацією ("in stock"/"out of stock", З ПРОБІЛОМ) відрізняється
  від Google XML-фіда ("in_stock", З ПІДКРЕСЛЕННЯМ) — АЛЕ це документація
  для JSON-based Content API (окремі виклики створення товару), НЕ для
  імпорту статичного XML-фіда за Google Shopping специфікацією, який
  власниця прямо попросила взяти за основу ("той самий формат, який
  Bing приймає майже без змін"). Через цю невизначеність (JSON API vs
  імпорт готового фіда можуть парсити значення по-різному) — залишила
  ІДЕНТИЧНО Google ("in_stock"/"out_of_stock"), а не змінила на пробіл,
  щоб не гадати за відсутності точного підтвердження для САМЕ цього
  шляху імпорту. Якщо Bing Merchant Center після підключення покаже
  попередження/помилку саме на availability — це перше, що варто
  звірити вручну в кабінеті.
"""
import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from parser import fetch_toysi_catalog
from generate_prom_feed_top import select_top_items
from prom_catalog_sync import fetch_prom_products
from generate_google_feed import (
    build_feed_items,
    OWN_PRODUCT_LINKS_CACHE_FILE,
    OWN_PRODUCT_LINKS_CACHE_TTL_DAYS,
    SHOP_NAME,
    SHOP_URL,
)

OUTPUT_FILE = "feeds/bing_feed.xml"


def _load_own_product_links_cache() -> dict:
    """Той самий read-only підхід, що й generate_rozetka_feed.py/
    generate_meta_feed.py — жодного власного GraphQL-пошуку."""
    if not OWN_PRODUCT_LINKS_CACHE_FILE.exists():
        return {}
    age_days = (time.time() - OWN_PRODUCT_LINKS_CACHE_FILE.stat().st_mtime) / 86400
    if age_days >= OWN_PRODUCT_LINKS_CACHE_TTL_DAYS:
        return {}
    try:
        return json.loads(OWN_PRODUCT_LINKS_CACHE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _build_xml(items: list) -> ET.Element:
    NS = "http://base.google.com/ns/1.0"
    ET.register_namespace("g", NS)
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{SHOP_NAME} — товарний фід Bing Merchant Center"
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


def generate_bing_feed(output_file: str = OUTPUT_FILE, limit: int = None) -> None:
    print("[Bing] Завантажуємо каталог Toysi...")
    catalog = fetch_toysi_catalog()
    if not catalog:
        print("[Bing] Каталог порожній — файл не створено.")
        return

    top_catalog = select_top_items(catalog)
    if limit:
        top_catalog = dict(list(top_catalog.items())[:limit])
    print(f"[Bing] У топ-970: {len(top_catalog)} товарів для обробки.")

    print("[Bing] Завантажуємо реальний список товарів Prom (для фото/self-match)...")
    prom_products = fetch_prom_products()
    prom_by_external_id = {
        str(p.get("external_id")): p for p in prom_products.values()
        if p.get("external_id")
    } if isinstance(prom_products, dict) else {}

    links = _load_own_product_links_cache()
    if not links:
        print(
            "[Bing] own_product_links_cache.json відсутній/застарів — фід матиме 0 товарів "
            "(generate_google_feed.py має відпрацювати ПЕРШИМ у цьому ж прогоні).",
            file=sys.stderr,
        )

    items, stats = build_feed_items(top_catalog, prom_by_external_id, links)

    root = _build_xml(items)
    ET.indent(root, space="  ")
    xml_str = ET.tostring(root, encoding="unicode")
    xml_str = f'<?xml version="1.0" encoding="UTF-8"?>\n{xml_str}'

    import os
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(xml_str)

    print(f"[Bing] Готово! Збережено: {output_file}")
    print(f"[Bing] У фіді: {stats['included']} з {stats['total_considered']} розглянутих")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    generate_bing_feed(limit=limit)
