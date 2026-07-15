"""
generate_rozetka_feed.py — генерує YML-фід для Rozetka Marketplace.

Вимоги звірено напряму з офіційною документацією Rozetka
(https://sellerhelp.rozetka.com.ua/p185-pricelist-requirements.html,
оновлено 29.06.2026, перевірено 2026-07-12):
- offer id — лише латиниця/цифри (Toysi id вже суто цифрові, підходить без змін);
  id товарів і категорій НЕ повинні змінюватись після першого додавання —
  ми завжди використовуємо той самий Toysi id, тож це вже дотримано.
- Обов'язкові теги: price, currencyId, categoryId, picture (1-15, https,
  без кирилиці/пробілів/плюсів в URL, до 10 МБ кожне), vendor, name,
  description, param. available="true/false" на offer, stock_quantity
  обов'язковий (товар доступний лише якщо >0).
- name — максимум 255 символів, description — максимум 50 000.
- Заборонені керівні ASCII-символи (0-31, крім 9/10/13) — фільтруємо самі,
  щоб один "брудний" символ десь у Toysi-даних не зламав увесь фід.
- rz_id (на <category>) і paramid/valueid (на <param>) — РЕКОМЕНДОВАНІ
  Rozetka для прямого зв'язку з довідником категорій/характеристик
  ("Priority of rz_id is higher than category name") замість зіставлення
  за назвою. Довідник доступний ЛИШЕ в кабінеті продавця (Управління
  товарами -> Довідники) — без інтерактивного логіну (login+password,
  який ми свідомо не автоматизуємо) отримати ці ID програмно неможливо.
  Тому зараз фід працює на фолбеку "зіставлення за назвою" (Rozetka явно
  підтримує це, лише з нижчим пріоритетом) — якщо власниця експортує
  довідник зі свого кабінету в ROZETKA_CATEGORY_RZ_ID_MAP_FILE
  ({toysi_category_id: rozetka_rz_id}), rz_id підхопиться автоматично.

ВАЖЛИВО — жодної російської мови: на відміну від Prom (де name/description
РІВНОПРАВНІ рос./укр. поля, бо Prom вимагає окреме російське поле), Rozetka
не вимагає цього, і власниця прямо попросила: тільки українська, без
паттерну Prom. <name>/<description> заповнюються УКРАЇНСЬКИМ текстом з
Toysi (lang=ukr, той самий, що й завжди) — НЕ викликаємо lang=rus, як
робить generate_prom_feed.py.

<name_ua>/<description_ua> СВІДОМО не дублюємо (перша версія цього файлу
робила це явно, "про всяк випадок") — на живих даних (2026-07-12) це
ледь не подвоїло розмір усього фіду (~60 МБ -> ~101 МБ), впритул до
жорсткого ліміту GitHub 100 МБ/файл, який уже й так ламає prom_feed.xml
(див. .github/workflows/update-feeds.yml). Документація Rozetka прямо
описує порожнє _ua-поле як задокументовану, підтримувану поведінку
("automatic translation applied if omitted") — не прогалину, яку
обов'язково закривати ручним дублюванням ціною подвоєння розміру фіду.

vendor — обов'язкове поле Rozetka; станом на 2026-07-12 повний каталог
Toysi фактично не має SKU без визначеного бренду (parser.py вже й сам
підставляє vendor із params, коли основне поле порожнє) — фільтр нижче
лишається на випадок, якщо це зміниться, а не тому що зараз щось реально
відсіює.

ЗВУЖЕНО ДО КУРУВАНОГО ВІДБОРУ (2026-07-13, задача власника): раніше цей
фід вивантажував ПОВНИЙ каталог Toysi (~28 000+ SKU) без жодного відбору
— на відміну від Prom, де в кабінет реально йде куруваний топ-970
(generate_prom_feed_top.py). Тепер `generate_feed()` застосовує ТУ САМУ
select_top_items() (не рахує незалежний відбір з нуля) — Rozetka отримує
еквівалентний куруваний список (~970 SKU), той самий, що й Prom. Формула
ціноутворення (decide_price_for_platform(platform="rozetka")) не
змінилась — міняється лише те, ЯКІ SKU взагалі потрапляють у фід.

ВІДОМИЙ ТИМЧАСОВИЙ КОМПРОМІС (задокументовано за рекомендацією рев'ю
PR #45): select_top_items()/_margin() ранжує товари за МАРЖЕЮ PROM
(через default_retail_price() у generate_prom_feed.py, категорійна
комісія саме Prom), не за Rozetka-маржею — тобто "куруваний топ-970" для
Rozetka зараз означає "топ-970 за прибутковістю на Prom", не незалежний
Rozetka-специфічний відбір. Сама РОЗДРІБНА ЦІНА для кожного SKU нижче
рахується коректно, платформо-специфічно (decide_price_for_platform(
platform="rozetka")) — торкається лише ТОГО, ЯКІ САМЕ SKU взагалі
потрапляють у список. Причина: категорійні ставки комісії Rozetka
програмно/публічно ще не знайдено (кабінет на стадії "Підготовка"), тож
незалежний Rozetka-ранжований відбір поки не на чому побудувати. Не
змінювати зараз — якщо колись з'явиться реальна Rozetka-комісія по
категоріях, варто порахувати "куруваний топ-970" для Rozetka незалежно,
а не й далі перевикористовувати Prom-ранжування.

<url> (необов'язковий тег, до 500 символів) — посилання на сторінку
товару. Самозіставлення з реальним лістингом на Prom (той самий механізм,
що й generate_google_feed.py — GraphQL-пошук, company_id-фільтр, захист
від плутанини розмірних варіантів) рахується лише для топ-970 — тепер, коли
й сам цей фід звужений до того самого топ-970, це фактично покриває ВЕСЬ
фід, а не лише частину. Цей файл СВОЇХ пошукових запитів НЕ робить — лише
ЧИТАЄ вже готовий кеш (own_product_links_cache.json), який пише
generate_google_feed.py під час власного прогону. <url> додається лише
для товарів, які (а) є в цьому кеші (впевнений self-match), і (б) в
наявності (stock > 0). Немає кеша, кеш застарів, чи товару в ньому немає
— <url> просто не додається для цієї позиції (тег і так необов'язковий)
— жодних вигаданих посилань.

ВИПРАВЛЕНО (2026-07-14, задача власника: "35 блокуючих помилок валідації"):
- Дублікати <name> у межах топ-970: Rozetka блокує фід, якщо дві позиції
  мають однакову назву. Двопрохідна логіка (_qualifies_for_feed() рахує
  дублікати ЛИШЕ серед товарів, що реально потраплять у фід) додає
  відмінник — спершу пробує структурований param "Колір"/"Цвет", інакше
  offer id. На живих даних (2026-07-13) знайдено 22 групи дублікатів
  (~47 SKU) і ЖОДНОГО "Колір"-параметра серед них — тобто гілка з ID
  зараз спрацьовує в 100% випадків, Колір-гілка лишається на майбутнє.
- Товари з порожнім params (269871, 270287, 270288, 271731, 294130,
  298624 та кілька їхніх дублікат-сусідів) — підтверджено прямим запитом
  до Toysi API: це прогалина в даних постачальника (params=[] по факту),
  не баг parser.py. Замість виключення з топ-970 підставляємо один
  <param name="Виробник"> зі значенням vendor (уже відоме, не вигадане
  значення) — Rozetka вимагає хоча б один <param>.
- http(s)-посилання в <description>: знайдено на SKU 294130 —
  прихований (opacity:0/position:absolute) <span id="ctrlcopy"> з
  посиланням на сторонній сайт (igrushki7.ua), лишок від джерела, звідки
  Toysi скопіювали опис. Загальний regex (_strip_urls()) прибирає будь-
  який http(s)-текст з опису ПЕРЕД truncate() — не точковий фікс лише
  під цей один SKU.
"""
import json
import os
import re
import html
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

from competitor_pricing import decide_price_for_platform
from generate_prom_feed import append_clearance_notice
from generate_prom_feed_top import select_top_items
from parser import fetch_toysi_catalog

# ЗМІНЕНО 2026-07-14 (вимога Rozetka, передана напряму менеджером по
# телефону): назва магазину на Rozetka має ВІДРІЗНЯТИСЯ від Prom
# ("PlutusToys" лишається назвою на Prom, тут — НЕ те саме поле/бренд).
# Власник обрав нову назву для Rozetka: "Plutonix".
SHOP_NAME          = "Plutonix"
SHOP_COMPANY       = "ФОП Чечетенко Олександр Юрійович"
SHOP_URL           = "https://plutustoys.com.ua"  # сайт компанії, не rozetka.com.ua
                                                    # (попередня версія помилково
                                                    # вказувала домен маркетплейсу)
OUTPUT_FILE        = "feeds/rozetka_feed.xml"
MIN_SUPPLIER_PRICE = 20  # товари дешевше цієї ціни постачальника пропускаємо

ROZETKA_NAME_MAX_LEN        = 255     # https://sellerhelp.rozetka.com.ua/p185-pricelist-requirements.html
ROZETKA_DESCRIPTION_MAX_LEN = 50_000
ROZETKA_MAX_PICTURES        = 15

# {toysi_category_id: rozetka_rz_id} — опційний файл, заповнюється вручну
# власницею з довідника категорій у власному кабінеті (Управління товарами ->
# Довідники). Якщо файл відсутній чи категорія в ньому не знайдена — фід
# просто не додає rz_id для цієї категорії (Rozetka зіставить за назвою,
# як і зараз, лише повільніше/з нижчим пріоритетом при модерації).
ROZETKA_CATEGORY_RZ_ID_MAP_FILE = "rozetka_category_rz_id_map.json"

# Лише ЧИТАЄМО цей кеш (пише generate_google_feed.py, own product_links_cache.json —
# та сама назва файлу, той самий каталог) — жодних власних GraphQL-запитів тут.
# TTL звірено з OWN_PRODUCT_LINKS_CACHE_TTL_DAYS у generate_google_feed.py (7 днів) —
# застарілий кеш просто ігнорується (<url> тоді не додається взагалі), не
# перераховується.
OWN_PRODUCT_LINKS_CACHE_FILE = Path(__file__).parent / "own_product_links_cache.json"
OWN_PRODUCT_LINKS_CACHE_TTL_DAYS = 7
ROZETKA_URL_MAX_LEN = 500  # https://sellerhelp.rozetka.com.ua/p185-pricelist-requirements.html
_URL_TEMPLATE = SHOP_URL + "/ua/p{prom_id}-{url_text}.html"

# Заборонені керівні ASCII-символи (0-31, крім 9=tab/10=LF/13=CR) —
# Rozetka явно забороняє їх у фіді; чистимо самі, а не покладаємось на те,
# що Toysi-дані завжди чисті (одиничний "сирий" символ десь усередині міг
# би відхилити ВЕСЬ фід при валідації).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# ВИПРАВЛЕНО (задача про 35 блокуючих помилок валідатора Rozetka,
# 2026-07-13): знайдено на SKU 294130 — Toysi-опис містить прихований
# span (opacity:0, position:absolute, той самий клас "невидимого
# атрибуційного посилання", що іноді лишають скрейпери постачальника)
# з "Детальніше: <a href='https://igrushki7.ua/...'>https://igrushki7.ua/...</a>"
# — пряме посилання на СТОРОННІЙ (конкурентний) сайт-першоджерело в
# описі товару, яке Rozetka блокує. Загальний regex, а не точковий фікс
# лише під ctrlcopy-span — на випадок, якщо схожі посилання є і в інших
# SKU за межами перевірених 35 помилок валідатора.
#
# ВИПРАВЛЕНО (незалежне рев'ю PR #50): \S+ жадібно захоплював і
# оточуючу HTML-розмітку (лапки атрибута href, закривні теги) —
# наприклад, у href="https://...html">текст</a></span> захоплював
# усе аж до </span> включно, бо там немає пробілів. Результат — не
# просто "видалили посилання", а понівечений, незакритий HTML у
# CDATA-описі. Звужено до символів, що реально складають URL (без
# пробілів, кутових дужок і лапок) — зупиняється рівно на межі
# лапки/тега, залишаючи саму розмітку неушкодженою.
_URL_RE = re.compile(r'https?://[^\s<>"]+')

# ВИПРАВЛЕНО (2026-07-15, знайдено валідатором Rozetka: offer_id 292911/
# 292915 заблоковані як "назва не унікальна"): дедуп PR #50 рахує ЛИШЕ
# побайтовий збіг <name> — але ці два SKU мають РІЗНІ рядки ("...світяться
# (ФІОЛЕТОВИЙ)" проти "...(СИНІЙ)", перевірено прямим запитом до Toysi API,
# 64 проти 59 символів). Rozetka, судячи з усього, при перевірці унікальності
# ігнорує кінцеве кольорове уточнення в дужках — тобто "базова" назва без
# нього збігається, і саме це Rozetka вважає дублікатом, не наш побайтовий
# збіг. Живий скан поточного топ-970 (2026-07-15) показав, що це системна
# прогалина, не одиничний випадок: 43 такі групи, 116 SKU.
#
# Список навмисно вузький (лише кольори, не будь-яка кінцева дужка) —
# стрипати ДОВІЛЬНИЙ вміст у дужках ризиковано: "(з батарейками)" проти
# "(без батарейок)" — це реальна відмінність товару, не варто штучно
# зводити такі пари в одну групу дублікатів.
_COLOR_WORDS = {
    "фіолетовий", "синій", "червоний", "зелений", "жовтий", "рожевий",
    "чорний", "білий", "сірий", "помаранчевий", "оранжевий", "бежевий",
    "коричневий", "блакитний", "бірюзовий", "салатовий", "бордовий",
    "золотистий", "золотий", "срібний", "мультиколор", "хакі",
}
_TRAILING_COLOR_PAREN_RE = re.compile(r"\s*\(([^)]+)\)\s*$")


def _dedup_key(name: str) -> str:
    """Ключ для підрахунку дублікатів <name> — базова назва БЕЗ кінцевого
    кольорового уточнення в дужках, якщо воно там є (регістр не має
    значення — Toysi вживає і "Червоний", і "ЧЕРВОНИЙ"). Назви без такої
    дужки чи з некольоровим вмістом у дужках повертаються без змін —
    поведінка старого (точний збіг) дедупу для них не міняється."""
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
    if last_space > max_len * 0.6:  # не обрізати до майже нічого, якщо пробіл дуже рано
        cut = cut[:last_space]
    return cut.rstrip(" ,.-")


def _qualifies_for_feed(item: dict, excluded: set) -> bool:
    """Ті самі skip-фільтри, що й основний цикл _build_xml() (ціна,
    excluded id, vendor, https-фото) — винесено окремо, щоб рахувати
    дублікати <name> ЛИШЕ серед товарів, які реально потраплять у фід
    (двопрохідна логіка для відмінника при однакових назвах, задача про
    35 блокуючих помилок валідатора Rozetka, 2026-07-13). Без цього
    підрахунок міг би зайво додати відмінник товару, чий "дублікат"
    насправді відсіється раніше (наприклад, без бренду) і в фід не
    потрапить."""
    try:
        cost = float(item.get("price") or 0)
    except (ValueError, TypeError):
        return False
    if cost <= 0 or cost < MIN_SUPPLIER_PRICE:
        return False
    if str(item["id"]) in excluded:
        return False
    if not (item.get("vendor") or "").strip():
        return False
    pictures = [p for p in item.get("pictures", []) if p.startswith("https://")][:ROZETKA_MAX_PICTURES]
    if not pictures:
        return False
    return True


def _load_own_product_links_cache() -> dict:
    """Читає кеш self-match, який пише generate_google_feed.py — ЛИШЕ
    читання, без власного GraphQL-пошуку (див. докстрінг файлу вище).
    Кеш стосується лише топ-970 (Google-фід не обробляє решту каталогу),
    тож для абсолютної більшості товарів повного Rozetka-каталогу тут
    просто не буде запису — це очікувано, не помилка. Порожній словник,
    якщо кеш відсутній чи старіший за OWN_PRODUCT_LINKS_CACHE_TTL_DAYS —
    у цьому разі жоден offer не отримає <url>, тег і так необов'язковий."""
    if not OWN_PRODUCT_LINKS_CACHE_FILE.exists():
        return {}
    age_days = (time.time() - OWN_PRODUCT_LINKS_CACHE_FILE.stat().st_mtime) / 86400
    if age_days >= OWN_PRODUCT_LINKS_CACHE_TTL_DAYS:
        return {}
    try:
        return json.loads(OWN_PRODUCT_LINKS_CACHE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _load_category_rz_id_map() -> dict:
    if not os.path.exists(ROZETKA_CATEGORY_RZ_ID_MAP_FILE):
        return {}
    try:
        with open(ROZETKA_CATEGORY_RZ_ID_MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def _wrap_cdata(xml_str: str) -> str:
    """Post-process: wrap <description> content in CDATA."""
    def replacer(m):
        content = html.unescape(m.group(1))
        content = content.replace("]]>", "]]]]><![CDATA[>")
        return f"<description><![CDATA[{content}]]></description>"
    return re.sub(r"<description>(.*?)</description>", replacer, xml_str, flags=re.DOTALL)


def _build_xml(catalog: dict, price_overrides: dict = None, exclude_ids: set = None) -> tuple[ET.Element, dict]:
    now  = datetime.now().strftime("%Y-%m-%d %H:%M")
    yml  = ET.Element("yml_catalog", date=now)
    shop = ET.SubElement(yml, "shop")
    ET.SubElement(shop, "name").text    = SHOP_NAME
    ET.SubElement(shop, "company").text = SHOP_COMPANY
    ET.SubElement(shop, "url").text     = SHOP_URL

    currencies = ET.SubElement(shop, "currencies")
    ET.SubElement(currencies, "currency", id="UAH", rate="1")

    rz_id_map = _load_category_rz_id_map()
    own_product_links = _load_own_product_links_cache()

    # Collect unique categories from catalog items
    cat_map: dict = {}
    for item in catalog.values():
        cid   = (item.get("category_id") or "").strip()
        cname = (item.get("category_name") or "").strip()
        if cid and cid not in cat_map:
            cat_map[cid] = cname or cid  # fallback: id as name if feed has no names

    categories_el = ET.SubElement(shop, "categories")
    for cid in sorted(cat_map):
        attrs = {"id": cid}
        rz_id = rz_id_map.get(cid)
        if rz_id:
            attrs["rz_id"] = str(rz_id)
        ET.SubElement(categories_el, "category", **attrs).text = _clean_text(cat_map[cid])

    offers_el       = ET.SubElement(shop, "offers")
    overrides       = price_overrides or {}
    excluded        = exclude_ids or set()

    # Прохід 1/2: рахуємо, скільки товарів, що РЕАЛЬНО потраплять у фід
    # (ті самі фільтри, що й нижче), мають однакову <name> — Rozetka
    # блокує фід з дублюючими назвами (задача про 35 блокуючих помилок
    # валідатора, 2026-07-13). Рахуємо саме тут, а не постфактум над
    # уже згенерованим XML, бо після труncate() різні "довгі" назви
    # можуть штучно збігтись/розійтись.
    name_counts = Counter(
        _dedup_key(_clean_text(item.get("name", "")))
        for item in catalog.values()
        if _qualifies_for_feed(item, excluded)
    )

    skipped_no_price  = 0
    skipped_cheap     = 0
    skipped_unprof    = 0
    skipped_no_vendor = 0
    skipped_no_pics   = 0
    truncated_name_count = 0
    url_added_count   = 0

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

        # Rozetka вимагає vendor обов'язково — товари постачальника без
        # бренду (parser.py не визначив vendor ні з <vendor>, ні з params)
        # природно не потрапляють у фід. Очікувано (~30 SKU з ~29 тис. на
        # 2026-07-12), не помилка.
        vendor = (item.get("vendor") or "").strip()
        if not vendor:
            skipped_no_vendor += 1
            continue

        # https, без кирилиці/пробілів — Toysi-URL вже відповідають цьому
        # формату за конструкцією, але перевіряємо явно замість припущення.
        # ВИПРАВЛЕНО (незалежне рев'ю PR #39): фільтруємо на https ПЕРЕД
        # обмеженням до ROZETKA_MAX_PICTURES, не після — інакше валідне
        # https-фото на позиції 16+ могло й не потрапити в розгляд, і
        # товар з дійсним фото десь глибше в списку мовчки випав би з
        # фіда через те, що перші 15 сирих записів випадково не https.
        pictures = [
            p for p in item.get("pictures", [])
            if p.startswith("https://")
        ][:ROZETKA_MAX_PICTURES]
        if not pictures:
            skipped_no_pics += 1
            continue

        if item_id in overrides:
            retail = overrides[item_id]
        else:
            decision = decide_price_for_platform(cost, None, "rozetka", item.get("category_name"))
            retail = decision["price"]

        stock     = item.get("stock", 0)
        available = "true" if stock > 0 else "false"

        offer = ET.SubElement(offers_el, "offer", id=item_id, available=available)

        # <url> — необов'язковий, лише коли є впевнений self-match (кеш з
        # generate_google_feed.py, топ-970 only) І товар в наявності — не
        # додаємо посилання на офер, якого й так немає сенсу відкривати.
        link_info = own_product_links.get(item_id)
        if link_info and stock > 0:
            offer_url = _URL_TEMPLATE.format(prom_id=link_info["prom_id"], url_text=link_info["url_text"])
            ET.SubElement(offer, "url").text = offer_url[:ROZETKA_URL_MAX_LEN]
            url_added_count += 1

        ET.SubElement(offer, "vendorCode").text     = _clean_text(item.get("vendor_code") or item_id)

        # Лише українська (з Toysi lang=ukr) — жодного окремого рос.
        # запиту, на відміну від generate_prom_feed.py. НЕ дублюємо в
        # name_ua/description_ua (перша версія це робила явно "про всяк
        # випадок" — на практиці це ледь не ПОДВОЇЛО розмір усього фіду,
        # ~60МБ -> ~100МБ, впритул до жорсткого ліміту GitHub 100 МБ/файл,
        # який уже й так ламає prom_feed.xml). Документація Rozetka
        # прямо каже: "automatic translation applied if omitted" — тобто
        # порожнє _ua-поле є ЗАДОКУМЕНТОВАНОЮ, підтримуваною поведінкою,
        # не прогалиною, яку треба явно закривати дублюванням.
        name = _clean_text(item.get("name", ""))

        # ВИПРАВЛЕНО (задача про 35 блокуючих помилок валідатора Rozetka,
        # 2026-07-13): Rozetka блокує фід, де дві позиції мають однакову
        # <name>. Відмінник додаємо лише тим товарам, чия назва РЕАЛЬНО
        # дублюється серед інших офферів фіда (name_counts, прохід 1/2
        # вище) — спершу пробуємо значення параметра "Колір"/"Цвет" (якщо
        # є в структурованих params), інакше offer id.
        if name_counts.get(_dedup_key(name), 0) > 1:
            color_val = None
            for param_name, param_val in item.get("params", []):
                if "колір" in param_name.lower() or "цвет" in param_name.lower():
                    color_val = str(param_val).strip()
                    break
            disambiguator = color_val or item_id
            suffix = f" ({disambiguator})"

            # ВИПРАВЛЕНО (незалежне рев'ю PR #50): раніше суфікс додавався
            # ДО _truncate(), тож при базовій назві ~253+ символів обрізання
            # на межі ROZETKA_NAME_MAX_LEN могло з'їсти суфікс повністю (або
            # частково) — саме той відмінник, що мав розрізнити дублікати,
            # зникав, і назви знову зіштовхувались. Тепер ріжемо БАЗОВУ
            # частину до (ліміт - довжина суфікса), суфікс додаємо ПІСЛЯ —
            # він гарантовано лишається в межах ROZETKA_NAME_MAX_LEN.
            if len(name) + len(suffix) > ROZETKA_NAME_MAX_LEN:
                truncated_name_count += 1
            name = _truncate(name, ROZETKA_NAME_MAX_LEN - len(suffix)) + suffix
        elif len(name) > ROZETKA_NAME_MAX_LEN:
            truncated_name_count += 1
            name = _truncate(name, ROZETKA_NAME_MAX_LEN)
        ET.SubElement(offer, "name").text = name

        ET.SubElement(offer, "price").text          = f"{retail:.2f}"
        ET.SubElement(offer, "currencyId").text     = "UAH"
        ET.SubElement(offer, "stock_quantity").text = str(stock)

        if item.get("category_id"):
            attrs = {}
            rz_id = rz_id_map.get(item["category_id"])
            if rz_id:
                attrs["rz_id"] = str(rz_id)
            ET.SubElement(offer, "categoryId", **attrs).text = item["category_id"]

        for pic_url in pictures:
            ET.SubElement(offer, "picture").text = pic_url

        ET.SubElement(offer, "vendor").text = _clean_text(vendor)

        if item.get("country"):
            ET.SubElement(offer, "country_of_origin").text = _clean_text(item["country"])

        if item.get("barcode"):
            ET.SubElement(offer, "barcode").text = _clean_text(item["barcode"])

        desc = append_clearance_notice(
            item.get("description", ""),
            item.get("name", ""),
            item.get("category_name", ""),
            item.get("category_id", ""),
        )
        # ВИПРАВЛЕНО (задача про 35 блокуючих помилок валідатора, 2026-07-13):
        # прибираємо http(s)-посилання ДО truncate(), знайдено на SKU 294130
        # (прихований <span id="ctrlcopy"> зі стороннім посиланням на
        # igrushki7.ua, лишок від того, звідки Toysi самі скопіювали опис) —
        # Rozetka блокує описи з посиланнями на сторонні сайти.
        desc = _strip_urls(desc)
        desc = _truncate(_clean_text(desc), ROZETKA_DESCRIPTION_MAX_LEN)
        if desc:
            ET.SubElement(offer, "description").text = desc

        params = item.get("params", [])
        if params:
            for param_name, param_val in params:
                ET.SubElement(offer, "param", name=_clean_text(param_name)).text = _clean_text(str(param_val))
        else:
            # ВИПРАВЛЕНО (та сама задача): деякі SKU (напр. 269871, 270287,
            # 270288, 271731, 294130, 298624) у Toysi справді мають
            # params=[] — підтверджено прямим запитом до Toysi API, це
            # прогалина в даних постачальника, не баг парсера. Замість
            # виключення цих (інакше продаваних) позицій з топ-970,
            # підставляємо мінімальний param з уже відомим (не вигаданим)
            # значенням vendor — Rozetka вимагає хоча б один <param>.
            ET.SubElement(offer, "param", name="Виробник").text = _clean_text(vendor)

    print(f"[Rozetka] У фіді: {len(offers_el)} товарів | "
          f"без ціни: {skipped_no_price} | дешевше {MIN_SUPPLIER_PRICE} грн: {skipped_cheap} | "
          f"виключено вручну: {skipped_unprof} | без бренду (vendor обов'язковий): {skipped_no_vendor} | "
          f"без валідного фото: {skipped_no_pics} | назв обрізано (>{ROZETKA_NAME_MAX_LEN} симв.): {truncated_name_count}")
    print(f"[Rozetka] <url> додано для {url_added_count} з {len(offers_el)} товарів "
          f"(лише топ-970 з впевненим self-match на Prom, кеш {'знайдено' if own_product_links else 'відсутній/застарілий'})")
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

    # ЗВУЖЕНО (задача власника, 2026-07-13): раніше цей фід вивантажував
    # ПОВНИЙ каталог Toysi (28 000+ офферів) без жодного відбору — на
    # відміну від Prom, де в кабінет реально йде куруваний топ-970
    # (generate_prom_feed_top.py, select_top_items() — відбір за маржею,
    # тепер з балансом попит/маржа). Перевикористовуємо ТУ САМУ функцію
    # відбору (не рахуємо незалежний список з нуля) — Rozetka отримує
    # еквівалентний куруваний список, той самий SELECT_COUNT (~970), що
    # й Prom. Ціна/маржа й далі рахуються окремо через
    # decide_price_for_platform(platform="rozetka") нижче — міняється
    # лише ВІДБІР товарів, не формула ціноутворення.
    top_catalog = select_top_items(catalog)
    print(f"[Rozetka] Куруваний відбір: {len(top_catalog)} з {len(catalog)} товарів повного каталогу.")

    print(f"[Rozetka] Генеруємо XML для {len(top_catalog)} товарів...")
    root = _build_xml(top_catalog, price_overrides=price_overrides, exclude_ids=exclude_ids)

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
