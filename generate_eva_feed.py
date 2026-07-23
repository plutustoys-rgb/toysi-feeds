"""
generate_eva_feed.py — генерує YML-фід для EVA Маркетплейс (категорія
"Товари для дітей", комісія іграшок 15%/13%).

Вимоги звірено напряму з офіційною документацією EVA (2026-07-21):
- https://sellersupport.eva.ua/category/upravlinnia-tovaramy/vymohy-do-oformlennia-informatsii-pro-tovary
- https://sellersupport.eva.ua/article/pidhotovka-prays-listu-xml

Формат — YML (той самий стандарт, що й Prom/Rozetka), корінь
`<yml_catalog><shop><offers><offer available="true/false" id="...">`.
Обов'язкові поля offer: price, currencyId, categoryId, picture (1-15,
https, мін. 512x512px — розмір зображення не перевіряємо тут, лише
https/кількість, як і в generate_rozetka_feed.py), vendor, name_ua
(укр. назва, макс. 255 симв.), stock_quantity (ЦІЛЕ число залишку, не
bool — та сама специфіка, що й у Rozetka), description_ua (укр. опис,
30-60 000 симв.), param (характеристики, хоча б один).

НАВМИСНО НЕ пишемо `<name>`/`<description>` (російські, опційні поля
EVA) — той самий принцип, що вже встановлено для Rozetka
(generate_rozetka_feed.py): лише українською, без пари rus/ukr, як
робить Prom. EVA явно позначає _ua-поля обов'язковими, а не-суфіксовані
— опційними (дзеркальна структура до Rozetka, де навпаки "без _ua
означає авто-переклад").

СТОП-БРЕНДИ (пряме завдання власниці, 2026-07-21, категорія KIDS EVA):
EVA_STOP_BRANDS — той самий патерн, що вже є для Rozetka
(ROZETKA_BRAND_STOP_LIST) — SKU з цих брендів виключаються з фіда ДО
генерації, а не лишаються на модерацію EVA. Перелік звірено проти
живого каталогу Toysi (2026-07-21): з 36 заявлених брендів у нашому
каталозі реально зустрічаються 12 — решта 24 (LEGO, Barbie, Mattel,
Hasbro, Hot Wheels, Fisher-Price, Spin Master тощо) взагалі не наш
асортимент (Toysi здебільшого не постачає ліцензійні світові бренди).
Знайдено й виправлено РЕАЛЬНИЙ пропуск при первинній звірці:
"TechnoK" (стоп-бренд) і "Технок" (кирилицею — реальний бренд у
нашому каталозі, 494 SKU повного обсягу) — той самий бренд у двох
скриптах, нормалізація за самим лише регістром/розділювачем цього не
ловить. EVA_STOP_BRANDS нижче явно містить ОБИДВА варіанти написання
для TechnoK з цієї причини — якщо колись знайдеться ще один такий
кирило-латинський дублікат серед інших 35 брендів, він так само не
буде спійманий автоматично (перевірено вручну лише конкретні
ймовірні кандидати, не вичерпний список усіх можливих транслітерацій).

КУРУВАНИЙ ВІДБІР (не повний каталог): той самий select_top_items()
(топ-970 за маржею/попитом), що й Prom/Rozetka — свідомий, консервативний
старт для абсолютно нового, ще не протестованого каналу продажів, а
не спроба одразу вивантажити весь каталог (~28 000 SKU) на платформу
без жодної історії продажів на ній.

НЕ ПОКРИТО (свідомо, поза межами цього завдання — власниця сама подає
заявку/анкету/договір): реєстрація продавця, підключення категорії,
отримання довідника categoryId EVA (як і з Rozetka rz_id — без
інтерактивного логіну в кабінет EVA програмно недосяжний, фід працює
на фолбеку "зіставлення категорії за назвою").

ЖИВЕ ПОРІВНЯННЯ КОНКУРЕНТІВ НА EVA: досліджено окремо (WebSearch,
2026-07-21) — EVA НЕ має публічного/кабінетного механізму на кшталт
Prom buyBox (немає фільтра за продавцем, немає видимого порівняння
цін між продавцями того самого товару в кабінеті). Офіційні правила
EVA лише РЕКОМЕНДУЮТЬ продавцю самостійно звіряти ціни зі схожими
товарами й не виставляти необґрунтовано завищену ціну — жодного API
чи фіда конкурентних цін від самої EVA не існує. Якщо знадобиться
конкурентний моніторинг для EVA — доведеться будувати окремий
зовнішній механізм (як GraphQL-пошук для Prom), не готовий фід від
маркетплейсу.
"""
import os
import re
import html
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

from competitor_pricing import (
    compute_floor, get_platform_commission, load_description_overrides,
    load_fresh_prom_price_overrides, real_toysi_cost, MIN_PROFIT,
)
from generate_prom_feed import append_clearance_notice, normalize_vendor
from generate_prom_feed_top import select_top_items
from parser import fetch_toysi_catalog

SHOP_NAME          = "PlutusToys"
SHOP_COMPANY       = "ФОП Чечетенко Олександр Юрійович"
SHOP_URL           = "https://plutustoys.com.ua"
OUTPUT_FILE        = "feeds/eva_feed.xml"
MIN_SUPPLIER_PRICE = 20  # той самий поріг, що й Prom/Rozetka — товари дешевше собівартості постачальника пропускаємо

# Пряме завдання власниці (2026-07-21) — стоп-бренди EVA, категорія KIDS.
# Порівняння регістронезалежне/без урахування розділювача (див. _normalize_brand
# нижче) — включно з обома написаннями TechnoK/Технок (див. докстрінг файлу).
EVA_STOP_BRANDS = {
    "akuku", "avent", "baby team", "baby nova", "barbie", "bright spring",
    "canpol babies", "danko toys", "dodo", "feelo toys", "fisher price",
    "frozen", "hasbro", "hot wheels", "jaki", "kids hits", "lego", "lindo",
    "lovi", "lovin", "mattel", "mattel games", "nuk", "philips avent",
    "play doh", "spin master", "strateg", "suavinex", "technok", "технок",
    "tigres", "tiny love", "trefl", "vladi toys", "енергія плюс",
    "київська фабрика іграшок", "країна іграшок", "курносики",
}


def _normalize_brand(vendor: str) -> str:
    """Той самий нормалізаційний принцип, що вже є для normalize_vendor()
    (MIC/MiC/МІС), але тут — лише для порівняння зі стоп-листом: регістр
    і розділювач (-/_/пробіл) не мають значення, кирилиця й латиниця НЕ
    транслітеруються одне в одне автоматично (окрім explicit TechnoK/
    Технок пари в EVA_STOP_BRANDS вище)."""
    return re.sub(r"[-_\s]+", " ", (vendor or "").strip().lower())


EVA_NAME_MAX_LEN        = 255       # https://sellersupport.eva.ua/article/pidhotovka-prays-listu-xml
EVA_DESCRIPTION_MAX_LEN = 60_000
EVA_DESCRIPTION_MIN_LEN = 30        # заявлено документацією EVA — НЕ ВИМІРЮВАНО живо (немає ще підключеного кабінету
                                     # для перевірки); якщо опис коротший, EVA може відхилити конкретний offer при
                                     # модерації — не блокуємо генерацію фіда через це, лише документуємо ризик.
EVA_MAX_PICTURES        = 15

# Ті самі "заборонені керівні ASCII-символи"/URL-у-описі фільтри, що й
# у generate_rozetka_feed.py — той самий клас ризику (валідатор
# маркетплейсу блокує фід через один "брудний" символ/стороннє посилання
# десь у сирих Toysi-даних), не підтверджено конкретно для EVA, але
# дешева, безпечна перевірка, яка нічого не коштує, якщо EVA насправді
# толерантніша.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_URL_RE = re.compile(r'https?://[^\s<>"]+')

# Той самий дедуп-механізм за кольором у дужках, що й Rozetka
# (generate_rozetka_feed.py::_dedup_key) — уніфікованість назв не
# підтверджена як вимога EVA конкретно, але дешева, вже перевірена
# захисна міра проти класу помилки, знайденого на Rozetka.
_COLOR_WORDS = {
    "фіолетовий", "синій", "червоний", "зелений", "жовтий", "рожевий",
    "чорний", "білий", "сірий", "помаранчевий", "оранжевий", "бежевий",
    "коричневий", "блакитний", "бірюзовий", "салатовий", "бордовий",
    "золотистий", "золотий", "срібний", "мультиколор", "хакі",
}
_TRAILING_COLOR_PAREN_RE = re.compile(r"\s*\(([^)]+)\)\s*$")


def _dedup_key(name: str) -> str:
    match = _TRAILING_COLOR_PAREN_RE.search(name)
    if match and match.group(1).strip().lower() in _COLOR_WORDS:
        return name[:match.start()].rstrip()
    return name


def _clean_text(text: str) -> str:
    return _CONTROL_CHARS_RE.sub("", text or "")


def _strip_urls(text: str) -> str:
    return _URL_RE.sub("", text or "")


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    last_space = cut.rfind(" ")
    if last_space > max_len * 0.6:
        cut = cut[:last_space]
    return cut.rstrip(" ,.-")


def _qualifies_for_feed(item: dict, excluded: set, prom_price_overrides: dict) -> bool:
    """Дзеркало основного циклу _build_xml() нижче — винесено окремо для
    підрахунку дублікатів <name_ua> ЛИШЕ серед товарів, що реально
    потраплять у фід (той самий принцип, що й Rozetka).

    ЦІНА ЕВА = ЦІНА PROM (2026-07-23, пряме рішення власниці): EVA більше
    не рахує свою ціну незалежно через decide_price_for_platform() —
    товар без свіжого prom_price_overrides запису (ще не пройшов через
    репрайсер Prom цього ж циклу update-feeds.yml) чи з ціною Prom, що не
    проходить floor рентабельності EVA (реальна комісія EVA, real_toysi_cost),
    просто не потрапляє у фід — див. дзеркальну логіку в _build_xml()."""
    try:
        cost = real_toysi_cost(item)
    except (ValueError, TypeError):
        return False
    if cost <= 0 or cost < MIN_SUPPLIER_PRICE:
        return False
    item_id = str(item["id"])
    if item_id in excluded:
        return False
    prom_price = prom_price_overrides.get(item_id)
    if prom_price is None:
        return False
    eva_floor = compute_floor(cost, get_platform_commission("eva"), MIN_PROFIT)
    if prom_price < eva_floor:
        return False
    vendor = (item.get("vendor") or "").strip()
    if not vendor:
        return False
    if _normalize_brand(vendor) in EVA_STOP_BRANDS:
        return False
    pictures = [p for p in item.get("pictures", []) if p.startswith("https://")][:EVA_MAX_PICTURES]
    if not pictures:
        return False
    return True


def _wrap_cdata(xml_str: str) -> str:
    def replacer(m):
        content = html.unescape(m.group(1))
        content = content.replace("]]>", "]]]]><![CDATA[>")
        return f"<description_ua><![CDATA[{content}]]></description_ua>"
    return re.sub(r"<description_ua>(.*?)</description_ua>", replacer, xml_str, flags=re.DOTALL)


def _build_xml(
    catalog: dict,
    price_overrides: dict = None,
    exclude_ids: set = None,
    description_overrides: dict = None,
) -> ET.Element:
    now  = datetime.now().strftime("%Y-%m-%d %H:%M")
    yml  = ET.Element("yml_catalog", date=now)
    shop = ET.SubElement(yml, "shop")
    ET.SubElement(shop, "name").text    = SHOP_NAME
    ET.SubElement(shop, "company").text = SHOP_COMPANY
    ET.SubElement(shop, "url").text     = SHOP_URL

    currencies = ET.SubElement(shop, "currencies")
    ET.SubElement(currencies, "currency", id="UAH", rate="1")

    cat_map: dict = {}
    for item in catalog.values():
        cid   = (item.get("category_id") or "").strip()
        cname = (item.get("category_name") or "").strip()
        if cid and cid not in cat_map:
            cat_map[cid] = cname or cid

    categories_el = ET.SubElement(shop, "categories")
    for cid in sorted(cat_map):
        ET.SubElement(categories_el, "category", id=cid).text = _clean_text(cat_map[cid])

    offers_el      = ET.SubElement(shop, "offers")
    overrides      = price_overrides or {}
    excluded       = exclude_ids or set()
    desc_overrides = description_overrides or {}
    described_count = 0

    name_counts = Counter(
        _dedup_key(_clean_text(item.get("name", "")))
        for item in catalog.values()
        if _qualifies_for_feed(item, excluded, overrides)
    )

    skipped_no_price      = 0
    skipped_cheap         = 0
    skipped_unprof        = 0
    skipped_no_prom_price = 0
    skipped_no_vendor     = 0
    skipped_stop_brand    = 0
    skipped_no_pics       = 0
    skipped_short_desc    = 0
    truncated_name_count  = 0

    for item in catalog.values():
        try:
            cost = real_toysi_cost(item)
        except (ValueError, TypeError):
            skipped_no_price += 1
            continue
        if cost <= 0:
            skipped_no_price += 1
            continue
        if cost < MIN_SUPPLIER_PRICE:
            skipped_cheap += 1
            continue

        item_id = str(item["id"])
        if item_id in excluded:
            skipped_unprof += 1
            continue

        # ЦІНА EVA = ЦІНА PROM (2026-07-23, пряме рішення власниці): EVA
        # більше НЕ рахує ціну незалежно (decide_price_for_platform) —
        # копіює свіжу ціну, яку репрайсер щойно застосував на Prom у
        # ТОМУ Ж прогоні update-feeds.yml (load_fresh_prom_price_overrides,
        # <=PROM_PRICE_STATE_MAX_AGE_HOURS годин). Товар без свіжого запису
        # (ще не торкнутий репрайсером) просто не потрапляє в фід EVA —
        # немає окремої "ціни EVA" без Prom як джерела істини.
        retail = overrides.get(item_id)
        if retail is None:
            skipped_no_prom_price += 1
            continue

        # Профіт-запобіжник: якщо ціна Prom під РЕАЛЬНОЮ комісією EVA
        # (get_platform_commission("eva"), 15%) з реальною собівартістю
        # (real_toysi_cost) не проходить той самий floor рентабельності,
        # що й Prom "без конкурента" (MIN_PROFIT=25%+MIN_PROFIT_UAH) —
        # виключаємо з фіда EVA, а не публікуємо збитковим/на межі.
        eva_floor = compute_floor(cost, get_platform_commission("eva"), MIN_PROFIT)
        if retail < eva_floor:
            skipped_unprof += 1
            continue

        vendor = (item.get("vendor") or "").strip()
        if not vendor:
            skipped_no_vendor += 1
            continue

        if _normalize_brand(vendor) in EVA_STOP_BRANDS:
            skipped_stop_brand += 1
            continue

        pictures = [
            p for p in item.get("pictures", [])
            if p.startswith("https://")
        ][:EVA_MAX_PICTURES]
        if not pictures:
            skipped_no_pics += 1
            continue

        stock     = item.get("stock", 0)
        available = "true" if stock > 0 else "false"

        offer = ET.SubElement(offers_el, "offer", id=item_id, available=available)

        name = _clean_text(item.get("name", ""))
        if name_counts.get(_dedup_key(name), 0) > 1:
            color_val = None
            for param_name, param_val in item.get("params", []):
                if "колір" in param_name.lower() or "цвет" in param_name.lower():
                    color_val = str(param_val).strip()
                    break
            disambiguator = color_val or item_id
            suffix = f" ({disambiguator})"
            if len(name) + len(suffix) > EVA_NAME_MAX_LEN:
                truncated_name_count += 1
            name = _truncate(name, EVA_NAME_MAX_LEN - len(suffix)) + suffix
        elif len(name) > EVA_NAME_MAX_LEN:
            truncated_name_count += 1
            name = _truncate(name, EVA_NAME_MAX_LEN)
        ET.SubElement(offer, "name_ua").text = name

        ET.SubElement(offer, "price").text          = f"{retail:.2f}"
        ET.SubElement(offer, "currencyId").text     = "UAH"
        ET.SubElement(offer, "stock_quantity").text = str(stock)

        if item.get("category_id"):
            ET.SubElement(offer, "categoryId").text = item["category_id"]

        for pic_url in pictures:
            ET.SubElement(offer, "picture").text = pic_url

        ET.SubElement(offer, "vendor").text = _clean_text(normalize_vendor(vendor))

        desc_override = desc_overrides.get(item_id)
        raw_description = item.get("description", "")
        country = item.get("country")
        if desc_override:
            described_count += 1
            raw_description = desc_override.get("description") or raw_description
            country = desc_override.get("country") or country

        if country:
            ET.SubElement(offer, "country_of_origin").text = _clean_text(country)

        if item.get("barcode"):
            ET.SubElement(offer, "barcode").text = _clean_text(item["barcode"])

        desc = append_clearance_notice(
            raw_description,
            item.get("name", ""),
            item.get("category_name", ""),
            item.get("category_id", ""),
        )
        desc = _strip_urls(desc)
        desc = _truncate(_clean_text(desc), EVA_DESCRIPTION_MAX_LEN)
        if desc and len(desc) < EVA_DESCRIPTION_MIN_LEN:
            skipped_short_desc += 1  # лише лічильник — НЕ виключаємо offer, документована, не підтверджена вимога
        if desc:
            ET.SubElement(offer, "description_ua").text = desc

        params = item.get("params", [])
        if params:
            for param_name, param_val in params:
                ET.SubElement(offer, "param", name=_clean_text(param_name)).text = _clean_text(str(param_val))
        else:
            ET.SubElement(offer, "param", name="Виробник").text = _clean_text(vendor)

    print(f"[EVA] У фіді: {len(offers_el)} товарів | "
          f"без ціни: {skipped_no_price} | дешевше {MIN_SUPPLIER_PRICE} грн: {skipped_cheap} | "
          f"немає свіжої ціни Prom (ще не торкнуто репрайсером цього циклу): {skipped_no_prom_price} | "
          f"виключено вручну/нерентабельно під комісією EVA: {skipped_unprof} | без бренду (vendor обов'язковий): {skipped_no_vendor} | "
          f"бренд у стоп-листі EVA: {skipped_stop_brand} | "
          f"без валідного фото: {skipped_no_pics} | назв обрізано (>{EVA_NAME_MAX_LEN} симв.): {truncated_name_count}")
    if skipped_short_desc:
        print(f"[EVA] УВАГА: {skipped_short_desc} offer(и) мають опис коротший за задокументований мінімум EVA "
              f"({EVA_DESCRIPTION_MIN_LEN} симв.) — НЕ виключено з фіда, ризик відхилення при модерації EVA.")
    print(f"[EVA] Vis-9: {described_count} SKU отримали вручну написаний опис (description_overrides.json)")
    return yml


def generate_feed(output_file: str = OUTPUT_FILE,
                  price_overrides: dict = None,
                  catalog: dict = None,
                  exclude_ids: set = None,
                  description_overrides: dict = None) -> None:
    if catalog is None:
        print("[EVA] Завантажуємо каталог Toysi...")
        catalog = fetch_toysi_catalog()
    if not catalog:
        print("[EVA] Каталог порожній — файл не створено.")
        return

    top_catalog = select_top_items(catalog)
    print(f"[EVA] Куруваний відбір: {len(top_catalog)} з {len(catalog)} товарів повного каталогу.")

    root = _build_xml(
        top_catalog, price_overrides=price_overrides, exclude_ids=exclude_ids,
        description_overrides=description_overrides,
    )

    ET.indent(root, space="  ")
    xml_str = ET.tostring(root, encoding="unicode")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
    xml_str = _wrap_cdata(xml_str)

    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(xml_str)

    print(f"[EVA] Готово! Збережено: {output_file}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    description_overrides = load_description_overrides()
    generate_feed(description_overrides=description_overrides, price_overrides=load_fresh_prom_price_overrides())
