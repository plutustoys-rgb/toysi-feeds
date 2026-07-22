"""
generate_allo_feed.py — генерує YML-фід для АЛЛО Маркетплейс.

ПІДГОТОВКА КОДУ, НЕ РЕЄСТРАЦІЯ: той самий статус, що й
generate_eva_feed.py на момент своєї появи (PR #118) — власниця сама
подає анкету/договір/поповнення балансу через сервіс "Вчасно" (переписка
з менеджеркою Аліною Бондаренко, 2026-07-22). Цей файл готує код заздалегідь,
щоб фід можна було одразу підключити, щойно кабінет буде активовано.

Вимоги звірено НАПРЯМУ з офіційною документацією АЛЛО (2026-07-22,
живий парсинг сирого HTML https://allo.ua/ua/marketplace-faq/ — сторінка
віддає ВСІ статті довідки одним важким SPA-документом; звичайний
WebFetch-саммарізер контент пропускає, знадобився прямий HTTP-запит +
розбір тегів):

ФОРМАТ ФІДА: той самий YML-стандарт, що й Prom/Rozetka/EVA — корінь
<yml_catalog><shop><offers><offer available="true/false" id="...">.
Прийняті формати: "Hotline XML; XML, YML; XLSX" — обираємо YML (як і
для решти майданчиків, той самий генератор без переписування).

ОБОВ'ЯЗКОВІ поля offer (з офіційного прикладу документації):
categories/categoryId (числовий ID категорії — той самий "довідник
недосяжний без живого кабінету" клас проблеми, що rz_id у Rozetka й
categoryId у EVA — фолбек на зіставлення за НАЗВОЮ категорії, як і в
обох тих файлах), name, id, price, currencyId=UAH, picture (1-12,
перше фото — обов'язково білий фон без сторонніх елементів, лише
JPG/JPEG/PNG), description (макс. 50 000 симв., теги <description> ТА
<description_ua> — документація АЛЛО використовує обидва в різних
місцях прикладу, на відміну від чіткого EVA-розрізняння "лише _ua";
пишемо ОБИДВА теги з тим самим текстом, безпечний максимум сумісності),
vendor (бренд), param (хоча б одна характеристика).

НОВЕ ОБОВ'ЯЗКОВЕ ПОЛЕ, якого нема в Prom/Rozetka/EVA: "Гарантія" —
"Поле гарантія має бути обов'язково заповненим" (документація), для
деяких категорій діє мінімальний гарантійний термін (посилання на деталі
в документації не завантажилось статично, JS-контент). Toysi-фід
(parser.py) НЕ містить даних про гарantію жодного товару — ALLO_WARRANTY_
DEFAULT_MONTHS нижче лишається орієнтовним заповнювачем (12 місяців,
стандартна споживча норма для непродовольчих товарів в Україні), НЕ
звіреним по категоріях з реальною мінімальною вимогою АЛЛО.

ДОСТУПНІСТЬ/ЗАЛИШКИ: `available` — головний атрибут; якщо відсутній,
фолбек на <stock_quantity>/<quantity> як БУЛЕВЕ значення (не точна
кількість!) — '0'/'Немає'/'FALSE' = немає в наявності, будь-яке число
>0 = є в наявності (документація). Пишемо available НАПРЯМУ (як і в
EVA/Rozetka) — той самий фолбек від АЛЛО спрацює коректно й для
stock_quantity, якщо available з якоїсь причини не прочитається.

ЦІНИ: АЛЛО показує лише цілі гривні (копійки округляються автоматично на
їхньому боці) — пишемо з копійками, як і решта фідів, округлення не наша
турбота.

🔴 РИЗИК ЦІНОВОГО ПАРИТЕТУ (документація, не з'ясовано остаточно): "ціна
на ресурсі партнера = ціні на allo.ua" — відмінність у БІЛЬШИЙ бік і
особливо різниця 20%+ призводить до пониження рейтингу і, у критичному
випадку, статусу "Призупинено". Незрозуміло, що саме документація
вважає "ресурсом партнера" для дропшипера без власного окремого сайту з
живими цінами (SHOP_URL нижче — лише номінальний ідентифікатор, не
реальний працюючий магазин з власними цінами) — можливо, йдеться про
найнижчу ціну серед ІНШИХ каналів продавця (Prom тощо). Це ОКРЕМИЙ
ризик від звичайного per-platform ціноутворення (Prom/Rozetka/EVA
свідомо мають РІЗНІ ціни одна від одної) — вимагає уточнення в Аліни
Бондаренко чи в самому кабінеті ПІСЛЯ реєстрації, перш ніж вмикати цей
фід у продакшн. НЕ вирішено в цьому файлі.

СТОП-БРЕНДИ/КАТЕГОРІЇ: документація прямо каже "Перевірити свої бренди
можна після заповнення реєстраційної форми" — реальний список
недоступний публічно (той самий клас недоступності, що спричинив би
помилку, якби ми вигадали список без звірки — див. попередження в
docstring generate_eva_feed.py про TechnoK/Технок). ALLO_STOP_BRANDS
нижче — ПОРОЖНІЙ хук, навмисно: на відміну від EVA (де власниця дала
конкретний перелік 2026-07-21), для АЛЛО жодного переліку ще нема.
Загальна (не брендова) заборона з документації — контрафакт, зброя,
неліцензійне ПЗ, засоби стеження тощо — жодного перетину з дитячими
іграшками Toysi, тому не потребує окремого фільтра тут.

ЗАМОВЛЕННЯ (order routing) — ДОСЛІДЖЕНО, НЕ ЗАКОДОВАНО в цьому файлі
(поза межами generate_*_feed.py, стосується order_router.py/
order_status_tracker.py): документація описує ВИКЛЮЧНО РУЧНИЙ
кабінетний процес ("Список замовлень" -> статус "Прийнято" -> вручну
"Комплектується на складі" -> вручну ввести номер ТТН -> "Доставляється"),
жодного публічного REST/API-опису для замовлень не знайдено (пункт меню
"API" присутній у навігації документації, але його вміст не завантажився
статично — ймовірно, JS-контент чи потребує авторизованого кабінету).
Working-припущення: ALLO для замовлень — КАБІНЕТ-ONLY, той самий клас,
що вже підтверджено для EVA (НЕ як Rozetka Seller API) — якщо це
підтвердиться живо після реєстрації, order_router.py/
order_status_tracker.py потребуватимуть НОВОГО клієнта на кшталт
rozetka_client.py лише тоді, коли з'явиться реальний API-опис чи
підтвердження його відсутності.

ЗАЛИШКИ/ОНОВЛЕННЯ (stock sync) — ДОСЛІДЖЕНО: жодного окремого API для
активної деактивації товарів (як prom_catalog_sync.py для Prom) не
знайдено — "Керування товарами" описує лише ПОВТОРНЕ ЗАВАНТАЖЕННЯ фіда
(або ручний "Імпорт" через кабінет), фід перечитується цілком щоразу.
Частота власного циклу імпорту АЛЛО НЕ вказана в доступній документації
— той самий підхід, що вже застосований до EVA (генеруємо фід на тому ж
розкладі, що й решта, конкретну частоту зі сторони АЛЛО з'ясувати після
підключення кабінету).

КОМІСІЯ: див. ALLO_COMMISSION_DEFAULT у competitor_pricing.py — реальна
таблиця (K.pdf, надіслана Аліною Бондаренко) НЕ вдалось прочитати
автоматично (перевірено: прямий HTTP GET, HTTP/HTTPS, з User-Agent
браузера, з Referer, через Wayback Machine — всюди стабільний 404, файл
або вимагає авторизованої сесії партнерського кабінету, або посилання
персоналізоване/з обмеженим строком дії). Живий пошук дав СУПЕРЕЧЛИВІ
орієнтовні цифри (6-25% залежно від категорії за одним джерелом, плюс
окрема фіксована 5% ставка для нових мерчантів на перші 2 місяці) —
жодна не є офіційною таблицею. Дефолт нижче — НАЙВИЩЕ з озвученого
власницею діапазону (5-12%), той самий принцип асиметрії ризику, що вже
застосований для EVA_COMMISSION_TOYS.

КУРУВАНИЙ ВІДБІР: той самий select_top_items() (топ-970), що й
Prom/Rozetka/EVA — консервативний старт для нового, ще не протестованого
каналу.
"""
import os
import re
import html
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

from competitor_pricing import decide_price_for_platform, load_description_overrides
from generate_prom_feed import append_clearance_notice, normalize_vendor
from generate_prom_feed_top import select_top_items
from parser import fetch_toysi_catalog

SHOP_NAME          = "PlutusToys"
SHOP_COMPANY       = "ФОП Чечетенко Олександр Юрійович"
SHOP_URL           = "https://plutustoys.com.ua"
OUTPUT_FILE        = "feeds/allo_feed.xml"
# Одна константа платформи на файл — той самий захист від копіпаст-помилки
# "prom"/"eva", що вже стався один раз (див. коментар у
# generate_eva_feed.py, ВИПРАВЛЕНО 2026-07-21, code_report_2026-07-21_pt17.md).
PLATFORM           = "allo"
MIN_SUPPLIER_PRICE = 20  # той самий поріг, що й Prom/Rozetka/EVA

# Порожній хук (2026-07-22) — жодного переліку стоп-брендів АЛЛО не
# опубліковано без реєстрації (див. докстрінг файлу). Заповнити, щойно
# буде реальний перелік від Аліни Бондаренко чи з кабінету — той самий
# механізм, що EVA_STOP_BRANDS у generate_eva_feed.py.
ALLO_STOP_BRANDS: set[str] = set()


def _normalize_brand(vendor: str) -> str:
    """Той самий нормалізаційний принцип, що й _normalize_brand() у
    generate_eva_feed.py — лишається тут навіть з порожнім стоп-листом,
    щоб увімкнення реального переліку пізніше не вимагало інших змін."""
    return re.sub(r"[-_\s]+", " ", (vendor or "").strip().lower())


ALLO_NAME_MAX_LEN        = 255       # не задокументовано явно АЛЛО — той самий безпечний дефолт, що й EVA/Rozetka
ALLO_DESCRIPTION_MAX_LEN = 50_000    # ПІДТВЕРДЖЕНО документацією: "Максимально припустима кількість символів – 50 000"
ALLO_MAX_PICTURES        = 12        # ПІДТВЕРДЖЕНО документацією: "Максимальна кількість зображень – 12"
ALLO_WARRANTY_DEFAULT_MONTHS = 12    # НЕ звірено — заповнювач, див. докстрінг файлу ("НОВЕ ОБОВ'ЯЗКОВЕ ПОЛЕ")

# Той самий клас "дешевий, безпечний фільтр" контенту опису, що й у
# generate_rozetka_feed.py/generate_eva_feed.py — тут ДОДАТКОВО
# підтверджено документацією АЛЛО: "В описі заборонено розміщувати:
# посилання на сторонні ресурси; ціни та інформацію про інші товари...".
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_URL_RE = re.compile(r'https?://[^\s<>"]+')

# Той самий дедуп-механізм за кольором у дужках, що й Rozetka/EVA.
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


def _qualifies_for_feed(item: dict, excluded: set) -> bool:
    """Дзеркало основного циклу _build_xml() нижче — той самий принцип, що
    в generate_eva_feed.py::_qualifies_for_feed()."""
    try:
        cost = float(item.get("price") or 0)
    except (ValueError, TypeError):
        return False
    if cost <= 0 or cost < MIN_SUPPLIER_PRICE:
        return False
    if str(item["id"]) in excluded:
        return False
    vendor = (item.get("vendor") or "").strip()
    if not vendor:
        return False
    if _normalize_brand(vendor) in ALLO_STOP_BRANDS:
        return False
    pictures = [p for p in item.get("pictures", []) if p.startswith("https://")][:ALLO_MAX_PICTURES]
    if not pictures:
        return False
    return True


def _wrap_cdata(xml_str: str) -> str:
    def replacer(m):
        content = html.unescape(m.group(1))
        content = content.replace("]]>", "]]]]><![CDATA[>")
        return f"<description><![CDATA[{content}]]></description><description_ua><![CDATA[{content}]]></description_ua>"
    return re.sub(r"<description>(.*?)</description>", replacer, xml_str, flags=re.DOTALL)


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
        if _qualifies_for_feed(item, excluded)
    )

    skipped_no_price      = 0
    skipped_cheap         = 0
    skipped_unprof        = 0
    skipped_no_vendor     = 0
    skipped_stop_brand    = 0
    skipped_no_pics       = 0
    truncated_name_count  = 0

    for item in catalog.values():
        try:
            cost = float(item.get("price") or 0)
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

        vendor = (item.get("vendor") or "").strip()
        if not vendor:
            skipped_no_vendor += 1
            continue

        if _normalize_brand(vendor) in ALLO_STOP_BRANDS:
            skipped_stop_brand += 1
            continue

        pictures = [
            p for p in item.get("pictures", [])
            if p.startswith("https://")
        ][:ALLO_MAX_PICTURES]
        if not pictures:
            skipped_no_pics += 1
            continue

        if item_id in overrides:
            retail = overrides[item_id]
        else:
            decision = decide_price_for_platform(cost, None, PLATFORM, item.get("category_name"))
            retail = decision["price"]

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
            if len(name) + len(suffix) > ALLO_NAME_MAX_LEN:
                truncated_name_count += 1
            name = _truncate(name, ALLO_NAME_MAX_LEN - len(suffix)) + suffix
        elif len(name) > ALLO_NAME_MAX_LEN:
            truncated_name_count += 1
            name = _truncate(name, ALLO_NAME_MAX_LEN)
        ET.SubElement(offer, "name").text = name

        ET.SubElement(offer, "price").text          = f"{retail:.2f}"
        ET.SubElement(offer, "currencyId").text     = "UAH"
        ET.SubElement(offer, "stock_quantity").text = str(stock)

        if item.get("category_id"):
            ET.SubElement(offer, "categoryId").text = item["category_id"]

        for pic_url in pictures:
            ET.SubElement(offer, "picture").text = pic_url

        ET.SubElement(offer, "vendor").text = _clean_text(normalize_vendor(vendor))

        # "Гарантія" — ОБОВ'ЯЗКОВЕ поле документацією АЛЛО, немає джерела
        # даних у Toysi-фіді — заповнювач ALLO_WARRANTY_DEFAULT_MONTHS
        # (див. докстрінг файлу).
        ET.SubElement(offer, "warranty_months").text = str(ALLO_WARRANTY_DEFAULT_MONTHS)

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
        desc = _truncate(_clean_text(desc), ALLO_DESCRIPTION_MAX_LEN)
        if desc:
            ET.SubElement(offer, "description").text = desc

        params = item.get("params", [])
        if params:
            for param_name, param_val in params:
                ET.SubElement(offer, "param", name=_clean_text(param_name)).text = _clean_text(str(param_val))
        else:
            ET.SubElement(offer, "param", name="Виробник").text = _clean_text(vendor)

    print(f"[ALLO] У фіді: {len(offers_el)} товарів | "
          f"без ціни: {skipped_no_price} | дешевше {MIN_SUPPLIER_PRICE} грн: {skipped_cheap} | "
          f"виключено вручну: {skipped_unprof} | без бренду (vendor обов'язковий): {skipped_no_vendor} | "
          f"бренд у стоп-листі АЛЛО: {skipped_stop_brand} | "
          f"без валідного фото: {skipped_no_pics} | назв обрізано (>{ALLO_NAME_MAX_LEN} симв.): {truncated_name_count}")
    print(f"[ALLO] Vis-9: {described_count} SKU отримали вручну написаний опис (description_overrides.json)")
    return yml


def generate_feed(output_file: str = OUTPUT_FILE,
                  price_overrides: dict = None,
                  catalog: dict = None,
                  exclude_ids: set = None,
                  description_overrides: dict = None) -> None:
    if catalog is None:
        print("[ALLO] Завантажуємо каталог Toysi...")
        catalog = fetch_toysi_catalog()
    if not catalog:
        print("[ALLO] Каталог порожній — файл не створено.")
        return

    top_catalog = select_top_items(catalog)
    print(f"[ALLO] Куруваний відбір: {len(top_catalog)} з {len(catalog)} товарів повного каталогу.")

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

    print(f"[ALLO] Готово! Збережено: {output_file}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    description_overrides = load_description_overrides()
    generate_feed(description_overrides=description_overrides)
