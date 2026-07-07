import os
import sys

import requests
import xml.etree.ElementTree as ET
from typing import Dict
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Конфигурация постачальника RoyalToys — редагуй лише тут
# ---------------------------------------------------------------------------
ROYALTOYS_YML_URL = os.environ.get("ROYALTOYS_YML_URL", "")

REQUEST_TIMEOUT = 90  # секунды — фід великий (~90+ МБ)


def fetch_royaltoys_catalog() -> Dict[str, dict]:
    """
    Скачує YML-фід постачальника RoyalToys і повертає словник,
    де ключ — id товару (атрибут offer/@id), значення — dict з даними товару.

    На відміну від Toysi, RoyalToys не дає штрих-код (EAN) у фіді —
    пошук/порівняння товарів між постачальниками треба робити за
    назвою+брендом, а не за barcode.
    """
    if not ROYALTOYS_YML_URL:
        print(
            "[RoyalToys] ПОМИЛКА: не заданий ROYALTOYS_YML_URL.\n"
            "  Локально: додайте у .env рядок ROYALTOYS_YML_URL=посилання_на_YML_експорт\n"
            "  Посилання генерується в кабінеті RoyalToys через «Конструктор YML» —\n"
            "  це секрет (дає доступ до оптового прайсу), не публікувати й не комітити.",
            file=sys.stderr,
        )
        return {}

    try:
        response = requests.get(ROYALTOYS_YML_URL, timeout=REQUEST_TIMEOUT, stream=True)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        print(f"[RoyalToys] Timeout: сервер не відповів за {REQUEST_TIMEOUT}с")
        return {}
    except requests.exceptions.HTTPError as e:
        print(f"[RoyalToys] HTTP помилка: {e.response.status_code} — {e.response.reason}")
        return {}
    except requests.exceptions.RequestException as e:
        print(f"[RoyalToys] Помилка з'єднання: {e}")
        return {}

    # decode_content=True — інакше raw віддає ще стиснутий (gzip) потік,
    # а ET.iterparse чекає вже розпакований XML.
    response.raw.decode_content = True
    return _parse_xml_stream(response.raw)


def _parse_xml_stream(fileobj) -> Dict[str, dict]:
    """
    Парсить YML-фід RoyalToys через iterparse (фід ~90+ МБ, ~19к товарів —
    повний ET.fromstring() у пам'яті теж працює, але iterparse дешевший).
    """
    cat_names: Dict[str, str] = {}
    catalog: Dict[str, dict] = {}

    context = ET.iterparse(fileobj, events=("end",))
    for _, elem in context:
        tag = elem.tag

        if tag == "category":
            cid = (elem.get("id") or "").strip()
            cname = (elem.text or "").strip()
            if cid:
                cat_names[cid] = cname
            elem.clear()

        elif tag == "offer":
            product_id = elem.get("id") or ""
            vendor_code = elem.findtext("vendorCode", "").strip()
            name = elem.findtext("name", "").strip()
            name_ua = elem.findtext("name_ua", "").strip()
            description = (elem.findtext("description", "") or "").strip()
            description_ua = (elem.findtext("description_ua", "") or "").strip()
            price = elem.findtext("price", "").strip()
            stock_raw = elem.findtext("stock_quantity", "0").strip()
            try:
                stock = int(stock_raw)
            except ValueError:
                stock = 0
            available = (elem.get("available") or "").strip().lower() == "true"
            vendor = elem.findtext("vendor", "").strip()
            category_id = elem.findtext("categoryId", "").strip()
            pictures = [p.text.strip() for p in elem.findall("picture") if p.text and p.text.strip()]

            catalog[product_id] = {
                "id": product_id,
                "supplier": "RoyalToys",
                "vendor_code": vendor_code,
                "name": name,
                "name_ua": name_ua,
                "description": description,
                "description_ua": description_ua,
                "price": price,
                "stock": stock,
                "available": available,
                "vendor": vendor,
                "pictures": pictures,
                "category_id": category_id,
                "category_name": cat_names.get(category_id, ""),
            }
            elem.clear()

    print(f"[RoyalToys] Завантажено товарів: {len(catalog)}")
    return catalog


if __name__ == "__main__":
    catalog = fetch_royaltoys_catalog()
    if catalog:
        example = next(
            (item for item in catalog.values() if item["stock"] > 0),
            next(iter(catalog.values()))
        )
        print(f"ID:      {example['id']}")
        print(f"Назва:   {example['name']}")
        print(f"Бренд:   {example['vendor']}")
        print(f"Ціна:    {example['price']} грн")
        print(f"Залишок: {example['stock']} шт")
