import os
import sys

import requests
import xml.etree.ElementTree as ET
from typing import Dict
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Конфигурация поставщика Toysi — редактируй только здесь
# ---------------------------------------------------------------------------
TOYSI_API_KEY  = os.environ.get("TOYSI_API_KEY", "")


def _build_xml_url(api_key: str) -> str:
    return (
        "https://toysi.ua/feed-products-residue.php"
        f"?key={api_key}&vendor_code=prom&out_of_stock=2&picture=10"
        "&lwh=yes&prom_cat=1&lang=ukr&vendor=yes&country=yes"
    )


TOYSI_XML_URL  = _build_xml_url(TOYSI_API_KEY) if TOYSI_API_KEY else ""

REQUEST_TIMEOUT = 30  # секунды


def fetch_toysi_catalog() -> Dict[str, dict]:
    """
    Скачивает XML-каталог от поставщика Toysi и возвращает словарь,
    где ключ — id товара Toysi (offer/@id, совпадает с vendorCode — см.
    _parse_xml), значение — dict с данными товара.
    """
    if not TOYSI_API_KEY:
        print(
            "[Toysi] ПОМИЛКА: не заданий TOYSI_API_KEY.\n"
            "  Локально: створіть файл .env у корені проєкту з рядком TOYSI_API_KEY=ваш_ключ\n"
            "  У GitHub Actions: додайте секрет TOYSI_API_KEY у Settings -> Secrets and variables -> Actions\n"
            "  Ключ можна знайти в особистому кабінеті toysi.ua, розділ API.",
            file=sys.stderr,
        )
        return {}

    try:
        response = requests.get(TOYSI_XML_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        print(f"[Toysi] Timeout: сервер не ответил за {REQUEST_TIMEOUT}с")
        return {}
    except requests.exceptions.HTTPError as e:
        print(f"[Toysi] HTTP ошибка: {e.response.status_code} — {e.response.reason}")
        return {}
    except requests.exceptions.RequestException as e:
        print(f"[Toysi] Ошибка соединения: {e}")
        return {}

    return _parse_xml(response.content)


def _parse_ostatok(raw: str) -> int:
    """
    Перетворює рядок залишку Toysi на ціле число.
    "1 шт." -> 1,  "10...50 шт." -> 10 (беремо мінімум діапазону),  "" -> 0
    """
    import re
    nums = re.findall(r"\d+", raw)
    return int(nums[0]) if nums else 0


_VENDOR_PARAM_NAMES = {"бренд", "торгова марка", "виробник", "марка", "brand"}

def _extract_vendor_from_params(params: list) -> str:
    for name, value in params:
        if name.strip().lower() in _VENDOR_PARAM_NAMES:
            return value.strip()
    return ""


def _parse_xml(xml_content: bytes) -> Dict[str, dict]:
    """
    Парсит YML-совместимый XML от Toysi.
    Ключ возвращаемого словаря — id товара Toysi (offer/@id).
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        print(f"[Toysi] Ошибка парсинга XML: {e}")
        return {}

    # YML: <yml_catalog><shop><offers><offer id="..."> — ищем на любой глубине
    offers = root.findall(".//offer")
    if not offers:
        print("[Toysi] Тег <offer> не найден — проверь структуру XML")
        return {}

    # Parse <categories> block for name lookup
    cat_names: Dict[str, str] = {}
    for cats_el in root.findall(".//categories"):
        for cat in cats_el.findall("category"):
            cid   = (cat.get("id") or "").strip()
            cname = (cat.text or "").strip()
            if cid:
                cat_names[cid] = cname

    catalog: Dict[str, dict] = {}

    for offer in offers:
        product_id  = offer.get("id") or offer.findtext("vendorCode", "").strip()
        vendor_code = offer.findtext("vendorCode", "").strip()
        name        = offer.findtext("name", "").strip()
        price       = offer.findtext("price", "").strip()

        ostatok_raw = offer.findtext("ostatok", "0").strip()
        stock       = _parse_ostatok(ostatok_raw)

        pictures    = [p.text.strip() for p in offer.findall("picture") if p.text and p.text.strip()]
        description = offer.findtext("description", "").strip()
        barcode     = offer.findtext("barcode", "").strip()
        category_id = offer.findtext("categoryId", "").strip()
        params      = [(p.get("name", ""), p.text or "") for p in offer.findall("param") if p.get("name")]

        vendor = offer.findtext("vendor", "").strip()
        if not vendor:
            vendor = _extract_vendor_from_params(params)

        country = offer.findtext("country", "").strip()

        catalog[product_id] = {
            "id":            product_id,
            "supplier":      "Toysi",
            "vendor_code":   vendor_code,
            "name":          name,
            "price":         price,
            "stock":         stock,
            "pictures":      pictures,
            "description":   description,
            "vendor":        vendor,
            "country":       country,
            "barcode":       barcode,
            "category_id":   category_id,
            "category_name": cat_names.get(category_id, ""),
            "params":        params,
        }

    print(f"[Toysi] Загружено товаров: {len(catalog)}")
    return catalog


if __name__ == "__main__":
    catalog = fetch_toysi_catalog()
    if catalog:
        example = next(
            (item for item in catalog.values() if item["stock"] > 0),
            next(iter(catalog.values()))
        )
        print(f"ID:      {example['id']}")
        print(f"Назва:   {example['name']}")
        print(f"Ціна:    {example['price']} грн")
        print(f"Залишок: {example['stock']} шт")
