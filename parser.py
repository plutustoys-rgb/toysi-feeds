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


def _build_xml_url(api_key: str, lang: str = "ukr") -> str:
    return (
        "https://toysi.ua/feed-products-residue.php"
        f"?key={api_key}&vendor_code=prom&out_of_stock=2&picture=10"
        f"&lwh=yes&prom_cat=1&lang={lang}&vendor=yes&country=yes"
    )


TOYSI_XML_URL  = _build_xml_url(TOYSI_API_KEY) if TOYSI_API_KEY else ""

REQUEST_TIMEOUT = 60  # секунд — повний каталог ~70МБ, 30с часом замало навіть для lang=ukr

_TOYSI_TIMEOUT_MSG = (
    "[Toysi] ПОМИЛКА: не заданий TOYSI_API_KEY.\n"
    "  Локально: створіть файл .env у корені проєкту з рядком TOYSI_API_KEY=ваш_ключ\n"
    "  У GitHub Actions: додайте секрет TOYSI_API_KEY у Settings -> Secrets and variables -> Actions\n"
    "  Ключ можна знайти в особистому кабінеті toysi.ua, розділ API."
)


def fetch_toysi_catalog(lang: str = "ukr") -> Dict[str, dict]:
    """
    Скачивает XML-каталог от поставщика Toysi и возвращает словарь,
    где ключ — id товара Toysi (offer/@id, совпадает с vendorCode — см.
    _parse_xml), значение — dict с данными товара.

    lang="ukr" (за замовчуванням, як і завжди) — усі існуючі виклики без
    параметра поводяться ідентично до цього. lang="rus" — окремий, реально
    відмінний фід (перевірено 2026-07-11: 92% назв і 95% описів відрізняються
    від lang=ukr — Toysi справді надає окремий російський контент, не той
    самий текст під іншим прапорцем). Використовується ЛИШЕ в
    generate_prom_feed.py для заповнення <name>/<description> (Prom вимагає
    їх "російською" окремо від _ua-варіантів) — інші скрипти (prom_catalog_sync,
    auditor, competitor_pricing тощо) і далі викликають без lang і не
    платять зайвим ~70МБ запитом.
    """
    if not TOYSI_API_KEY:
        print(_TOYSI_TIMEOUT_MSG, file=sys.stderr)
        return {}

    url = _build_xml_url(TOYSI_API_KEY, lang) if lang != "ukr" else TOYSI_XML_URL
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
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


class CatalogSizeError(Exception):
    """Каталог Toysi виглядає підозріло малим (можливо, усічений фід) —
    небезпечно як підстава для ДЕСТРУКТИВНОЇ дії (видалення/зміна ціни в
    Prom за критерієм членства в цьому каталозі)."""


# Повний каталог Toysi — ~29 325 SKU станом на 2026-07-11 (job de facto,
# коливається день у день, але не драматично). Поріг свідомо із запасом
# нижче цього, а не впритул — щоб звичайне денне коливання каталогу не
# спрацьовувало як хибна тривога, і водночас ловило усічений фід
# (напр. Toysi віддає лише частину товарів структурно валідним XML —
# без HTTP-помилки й без ET.ParseError, тому fetch_toysi_catalog() сам
# по собі це не виявляє й мовчки поверне неповний, але "успішний" словник).
TOYSI_EXPECTED_MIN_SIZE = 25_000


def assert_catalog_size_sane(catalog: Dict[str, dict], min_expected: int = TOYSI_EXPECTED_MIN_SIZE) -> None:
    """Викликати ПЕРЕД тим, як довіряти каталогу Toysi як підставою для
    видалення товару в Prom чи зміни ціни — НЕ всередині fetch_toysi_catalog()
    самого (більшість викликачів лише генерують файл, де найгірший наслідок
    усіченого фетчу самокоригується наступним успішним запуском без побічних
    ефектів на живому кабінеті — цей запобіжник потрібен ЛИШЕ там, де
    результат веде до односторонньої дії в Prom)."""
    if len(catalog) < min_expected:
        raise CatalogSizeError(
            f"Каталог Toysi має лише {len(catalog)} товарів (очікується "
            f"щонайменше {min_expected}) — схоже на усічений фід. "
            "Дії, що спираються на членство в цьому каталозі, скасовано."
        )


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
