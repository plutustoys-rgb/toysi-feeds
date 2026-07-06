import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from parser import fetch_toysi_catalog
from generate_prom_feed import append_clearance_notice

SHOP_NAME          = "PlutusToys"
SHOP_COMPANY       = "ФОП Чечетенко Олександр Юрійович"
SHOP_URL           = "https://rozetka.com.ua"
OUTPUT_FILE        = "feeds/rozetka_feed.xml"
MIN_SUPPLIER_PRICE = 20  # товари дешевше цієї ціни постачальника пропускаємо


def calc_price(cost: float) -> float:
    """Розраховує роздрібну ціну з наценкою залежно від собівартості.

    Враховує комісію Rozetka 22% (ФОП, категорія "Дитячі іграшки", ціна < 4000 грн).
    Цільовий мінімум прибутку: 25% від собівартості після відрахування комісії
    (ціна = собівартість / (1 - 0.22) * (1 + 0.25) ~= собівартість * 1.60).
    """
    if cost < 100:    return round(cost * 2.00)
    elif cost < 300:  return round(cost * 1.85)
    elif cost < 700:  return round(cost * 1.75)
    elif cost < 2000: return round(cost * 1.65)
    else:             return round(cost * 1.55)


def _wrap_cdata(xml_str: str) -> str:
    """Post-process: wrap <description> content in CDATA."""
    def replacer(m):
        content = html.unescape(m.group(1))
        content = content.replace("]]>", "]]]]><![CDATA[>")
        return f"<description><![CDATA[{content}]]></description>"
    return re.sub(r"<description>(.*?)</description>", replacer, xml_str, flags=re.DOTALL)


def _build_xml(catalog: dict, price_overrides: dict = None, exclude_ids: set = None) -> ET.Element:
    now  = datetime.now().strftime("%Y-%m-%d %H:%M")
    yml  = ET.Element("yml_catalog", date=now)
    shop = ET.SubElement(yml, "shop")
    ET.SubElement(shop, "name").text    = SHOP_NAME
    ET.SubElement(shop, "company").text = SHOP_COMPANY
    ET.SubElement(shop, "url").text     = SHOP_URL

    currencies = ET.SubElement(shop, "currencies")
    ET.SubElement(currencies, "currency", id="UAH", rate="1")

    # Collect unique categories from catalog items
    cat_map: dict = {}
    for item in catalog.values():
        cid   = (item.get("category_id") or "").strip()
        cname = (item.get("category_name") or "").strip()
        if cid and cid not in cat_map:
            cat_map[cid] = cname or cid  # fallback: id as name if feed has no names

    categories_el = ET.SubElement(shop, "categories")
    for cid in sorted(cat_map):
        ET.SubElement(categories_el, "category", id=cid).text = cat_map[cid]

    offers_el      = ET.SubElement(shop, "offers")
    overrides      = price_overrides or {}
    excluded       = exclude_ids or set()
    skipped        = 0
    skipped_cheap  = 0
    skipped_unprof = 0

    for item in catalog.values():
        try:
            cost = float(item.get("price") or 0)
        except (ValueError, TypeError):
            skipped += 1
            continue
        if cost <= 0:
            skipped += 1
            continue
        if cost < MIN_SUPPLIER_PRICE:
            skipped_cheap += 1
            continue

        item_id = str(item["id"])
        if item_id in excluded:
            skipped_unprof += 1
            continue
        retail    = overrides.get(item_id, calc_price(cost))
        stock     = item.get("stock", 0)
        available = "true" if stock > 0 else "false"

        offer = ET.SubElement(offers_el, "offer",
                              id=item_id,
                              available=available)

        ET.SubElement(offer, "vendorCode").text     = item.get("vendor_code") or item_id
        # name_ua не дублюємо: фід і так лише українською (parser.py тягне lang=ukr)
        ET.SubElement(offer, "name").text           = item.get("name", "")
        ET.SubElement(offer, "price").text          = f"{retail:.2f}"
        ET.SubElement(offer, "currencyId").text     = "UAH"
        ET.SubElement(offer, "stock_quantity").text = str(stock)

        if item.get("category_id"):
            ET.SubElement(offer, "categoryId").text = item["category_id"]

        for pic_url in item.get("pictures", [])[:10]:
            ET.SubElement(offer, "picture").text = pic_url

        if item.get("vendor"):
            ET.SubElement(offer, "vendor").text = item["vendor"]

        if item.get("country"):
            ET.SubElement(offer, "country_of_origin").text = item["country"]

        if item.get("barcode"):
            ET.SubElement(offer, "barcode").text = item["barcode"]

        desc = append_clearance_notice(
            item.get("description", ""),
            item.get("name", ""),
            item.get("category_name", ""),
            item.get("category_id", ""),
        )
        if desc:
            ET.SubElement(offer, "description").text = desc

        for param_name, param_val in item.get("params", []):
            ET.SubElement(offer, "param", name=param_name).text = str(param_val)

    print(f"[Rozetka] У фіді: {len(offers_el)} товарів | "
          f"пропущено (без ціни): {skipped} | дешевше {MIN_SUPPLIER_PRICE} грн: {skipped_cheap} | "
          f"виключено як збиткові (категорія C): {skipped_unprof}")
    return yml


def generate_feed(output_file: str = OUTPUT_FILE,
                  price_overrides: dict = None,
                  catalog: dict = None,
                  exclude_ids: set = None) -> None:
    if catalog is None:
        print("[Rozetka] Завантажуємо каталог Toysi...")
        catalog = fetch_toysi_catalog()
    if not catalog:
        print("[Rozetka] Каталог порожній — файл не створено.")
        return

    print(f"[Rozetka] Генеруємо XML для {len(catalog)} товарів...")
    root = _build_xml(catalog, price_overrides=price_overrides, exclude_ids=exclude_ids)

    ET.indent(root, space="  ")
    xml_str = ET.tostring(root, encoding="unicode")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
    xml_str = _wrap_cdata(xml_str)

    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(xml_str)

    print(f"[Rozetka] Готово! Збережено: {output_file}")


if __name__ == "__main__":
    generate_feed()
