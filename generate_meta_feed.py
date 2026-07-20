"""
generate_meta_feed.py — товарний фід для Meta Commerce Manager (Facebook/
Instagram Shopping). Задача #170 власниці: перевикористати логіку
generate_google_feed.py (топ-970, ціни, наявність, self-match посилання),
адаптувавши лише поля, де специфікація Meta відрізняється від Google.

Джерело специфікації: developers.facebook.com/docs/commerce-platform/
catalog/fields (перевірено напряму, не з пам'яті, 2026-07-20):
- Обов'язкові поля (id, title, description, availability, condition,
  price, link, image_link, brand) — той самий набір, що вже рахує
  build_feed_items() у generate_google_feed.py, БЕЗ ЗМІН.
- XML використовує ТУ САМУ g:-namespace (http://base.google.com/ns/1.0),
  що й Google — Meta навмисно зробила специфікацію сумісною.
- ЄДИНА реальна відмінність значень: availability — Meta вимагає
  "in stock"/"out of stock" (З ПРОБІЛОМ), тоді як Google-фід використовує
  "in_stock"/"out_of_stock" (З ПІДКРЕСЛЕННЯМ) — підтверджено напряму
  офіційною документацією обох платформ, не однакове, попри схожість
  решти специфікації.
- condition (new/refurbished/used), title (150 símb, ліміт Meta — 200),
  description (ліміт Meta — 9999, наш фід і так обрізає до 5000) —
  сумісні без змін.

НЕ рахує self-match/категорійний кеш заново (~970 послідовних HTTP-
запитів до prom.ua в resolve_own_product_links() — дороге навантаження,
яке вже й так виконує generate_google_feed.py в тому самому прогоні
update-feeds.yml, ПЕРЕД цим кроком). Натомість лише ЧИТАЄ вже готовий
own_product_links_cache.json (той самий read-only підхід, що вже
усталений у generate_rozetka_feed.py для цього ж кешу) — якщо кеш
відсутній/застарів (TTL 7 днів), фід буде порожнім для товарів без
кешованого посилання, а не вигадає його.
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
    load_prom_products_cache,
    OWN_PRODUCT_LINKS_CACHE_FILE,
    OWN_PRODUCT_LINKS_CACHE_TTL_DAYS,
    SHOP_NAME,
    SHOP_URL,
)

OUTPUT_FILE = "feeds/meta_feed.xml"


def _load_own_product_links_cache() -> dict:
    """Той самий read-only підхід, що й generate_rozetka_feed.py —
    жодного власного GraphQL-пошуку, лише читання кешу generate_google_feed.py."""
    if not OWN_PRODUCT_LINKS_CACHE_FILE.exists():
        return {}
    age_days = (time.time() - OWN_PRODUCT_LINKS_CACHE_FILE.stat().st_mtime) / 86400
    if age_days >= OWN_PRODUCT_LINKS_CACHE_TTL_DAYS:
        return {}
    try:
        return json.loads(OWN_PRODUCT_LINKS_CACHE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


# Meta вимагає "in stock"/"out of stock" (з пробілом) — build_feed_items()
# повертає "in_stock"/"out_of_stock" (Google-конвенція, з підкресленням).
_AVAILABILITY_MAP = {
    "in_stock": "in stock",
    "out_of_stock": "out of stock",
}


def _build_xml(items: list) -> ET.Element:
    NS = "http://base.google.com/ns/1.0"
    ET.register_namespace("g", NS)
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = f"{SHOP_NAME} — товарний фід Meta Commerce Manager"
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
        ET.SubElement(entry, f"{{{NS}}}availability").text = _AVAILABILITY_MAP[it["availability"]]
        ET.SubElement(entry, f"{{{NS}}}condition").text = it["condition"]
        ET.SubElement(entry, f"{{{NS}}}brand").text = it["brand"]
        if it.get("gtin"):
            ET.SubElement(entry, f"{{{NS}}}gtin").text = it["gtin"]
        ET.SubElement(entry, f"{{{NS}}}google_product_category").text = it["google_product_category"]

    return rss


def generate_meta_feed(output_file: str = OUTPUT_FILE, limit: int = None) -> None:
    print("[Meta] Завантажуємо каталог Toysi...")
    catalog = fetch_toysi_catalog()
    if not catalog:
        print("[Meta] Каталог порожній — файл не створено.")
        return

    top_catalog = select_top_items(catalog)
    if limit:
        top_catalog = dict(list(top_catalog.items())[:limit])
    print(f"[Meta] У топ-970: {len(top_catalog)} товарів для обробки.")

    # ВИПРАВЛЕНО (2026-07-20, аудит PR #109 — code_report_2026-07-20_pt11.md):
    # раніше цей крок робив ВЛАСНИЙ live-виклик fetch_prom_products() —
    # той самий важкий /groups/list + /products/list запит, що вже
    # виконав generate_google_feed.py секундами раніше в тому самому
    # прогоні. Тепер спершу читає його кеш (TTL 1 година — див.
    # load_prom_products_cache() у generate_google_feed.py); лише якщо
    # кеш відсутній/застарів (напр. цей скрипт запущено окремо, поза
    # звичайним порядком workflow) — падає на власний live-фетч, той
    # самий безпечний дефолт "працює і без кешу", що й скрізь у проєкті.
    print("[Meta] Завантажуємо реальний список товарів Prom (для фото/self-match)...")
    prom_products = load_prom_products_cache()
    if prom_products is None:
        print("[Meta] Кеш товарів Prom відсутній/застарів — власний live-фетч.", file=sys.stderr)
        prom_products = fetch_prom_products()
    prom_by_external_id = {
        str(p.get("external_id")): p for p in prom_products.values()
        if p.get("external_id")
    } if isinstance(prom_products, dict) else {}

    links = _load_own_product_links_cache()
    if not links:
        print(
            "[Meta] own_product_links_cache.json відсутній/застарів — фід матиме 0 товарів "
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

    print(f"[Meta] Готово! Збережено: {output_file}")
    print(f"[Meta] У фіді: {stats['included']} з {stats['total_considered']} розглянутих")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    generate_meta_feed(limit=limit)
