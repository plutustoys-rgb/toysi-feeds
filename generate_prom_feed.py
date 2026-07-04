import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from parser import fetch_toysi_catalog

SHOP_NAME          = "Мій магазин"
SHOP_COMPANY       = "ФОП Чечетенко Олександр Юрійович"
SHOP_URL           = "https://prom.ua"
OUTPUT_FILE        = "feeds/prom_feed.xml"
MIN_SUPPLIER_PRICE = 20  # товари дешевше цієї ціни постачальника пропускаємо


def calc_price(cost: float) -> float:
    """Розраховує роздрібну ціну з наценкою залежно від собівартості.

    до 100 грн: +60% | 100-300: +50% | 300-700: +40% | 700-2000: +35% | 2000+: +25%
    """
    if cost < 100:    return round(cost * 1.60)
    elif cost < 300:  return round(cost * 1.50)
    elif cost < 700:  return round(cost * 1.40)
    elif cost < 2000: return round(cost * 1.35)
    else:             return round(cost * 1.25)


def _wrap_cdata(xml_str: str) -> str:
    """Post-process: wrap <description> content in CDATA."""
    def replacer(m):
        content = html.unescape(m.group(1))
        content = content.replace("]]>", "]]]]><![CDATA[>")
        return f"<description><![CDATA[{content}]]></description>"
    return re.sub(r"<description>(.*?)</description>", replacer, xml_str, flags=re.DOTALL)


def _build_xml(catalog: dict, price_overrides: dict = None) -> tuple[ET.Element, dict]:
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

    offers_el     = ET.SubElement(shop, "offers")
    overrides     = price_overrides or {}
    skipped       = 0
    skipped_cheap = 0
    tier_counts   = {"<100": 0, "100-300": 0, "300-700": 0, "700-2000": 0, ">2000": 0}

    def tier_of(cost: float) -> str:
        if cost < 100:    return "<100"
        elif cost < 300:  return "100-300"
        elif cost < 700:  return "300-700"
        elif cost < 2000: return "700-2000"
        else:             return ">2000"

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

        item_id   = str(item["id"])
        retail    = overrides.get(item_id, calc_price(cost))
        stock     = item.get("stock", 0)
        available = "true" if stock > 0 else "false"
        tier_counts[tier_of(cost)] += 1

        offer = ET.SubElement(offers_el, "offer",
                              id=item_id,
                              available=available)

        # Prom.ua: пріоритет коду товару vendorCode > barcode
        vendor_code = item.get("vendor_code") or item_id
        ET.SubElement(offer, "vendorCode").text        = vendor_code
        # name_ua не дублюємо: фід і так лише українською (parser.py тягне lang=ukr)
        ET.SubElement(offer, "name").text               = item.get("name", "")
        ET.SubElement(offer, "price").text               = f"{retail:.2f}"
        ET.SubElement(offer, "currencyId").text          = "UAH"
        # Prom.ua використовує quantity_in_stock (а не stock_quantity, як Rozetka)
        ET.SubElement(offer, "quantity_in_stock").text   = str(stock)

        if item.get("category_id"):
            ET.SubElement(offer, "categoryId").text = item["category_id"]

        for pic_url in item.get("pictures", [])[:10]:
            ET.SubElement(offer, "picture").text = pic_url

        if item.get("vendor"):
            ET.SubElement(offer, "vendor").text = item["vendor"]

        if item.get("country"):
            ET.SubElement(offer, "country").text = item["country"]

        if item.get("barcode"):
            ET.SubElement(offer, "barcode").text = item["barcode"]

        # Prom.ua вимагає наявність <description>, навіть якщо порожній
        ET.SubElement(offer, "description").text = item.get("description", "")

        for param_name, param_val in item.get("params", []):
            ET.SubElement(offer, "param", name=param_name).text = str(param_val)

    stats = {
        "total_in_feed": len(offers_el),
        "skipped_no_price": skipped,
        "skipped_cheap": skipped_cheap,
        "tier_counts": tier_counts,
    }
    return yml, stats


def generate_feed(output_file: str = OUTPUT_FILE,
                  price_overrides: dict = None,
                  catalog: dict = None) -> None:
    if catalog is None:
        print("[Prom] Завантажуємо каталог Toysi...")
        catalog = fetch_toysi_catalog()
    if not catalog:
        print("[Prom] Каталог порожній — файл не створено.")
        return

    print(f"[Prom] Генеруємо XML для {len(catalog)} товарів...")
    root, stats = _build_xml(catalog, price_overrides=price_overrides)

    ET.indent(root, space="  ")
    xml_str = ET.tostring(root, encoding="unicode")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
    xml_str = _wrap_cdata(xml_str)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(xml_str)

    tc = stats["tier_counts"]
    print(f"[Prom] Готово! Збережено: {output_file}")
    print(f"[Prom] У фіді: {stats['total_in_feed']} товарів | "
          f"пропущено (без ціни): {stats['skipped_no_price']} | "
          f"дешевше {MIN_SUPPLIER_PRICE} грн: {stats['skipped_cheap']}")
    print("[Prom] Розподіл за націнкою:")
    print(f"    до 100 грн       (+60%): {tc['<100']}")
    print(f"    100-300 грн      (+50%): {tc['100-300']}")
    print(f"    300-700 грн      (+40%): {tc['300-700']}")
    print(f"    700-2000 грн     (+35%): {tc['700-2000']}")
    print(f"    більше 2000 грн  (+25%): {tc['>2000']}")


if __name__ == "__main__":
    generate_feed()
