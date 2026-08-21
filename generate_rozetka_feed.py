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

НЕЗАЛЕЖНИЙ, СТАТИЧНИЙ ВІДБІР (2026-07-27, пряме, двічі повторене й
посилене рішення власниці — перше "перевикористання select_top_items()
з Prom неправильне, немає жодних реальних Rozetka-продажів, щоб
обґрунтувати ранжування чи ротацію", друге, жорсткіше: "повністю
статичний, навіть без перевірки складу, поки не пройшли модерацію"):

Раніше (2026-07-13—2026-07-26) `generate_feed()` перевикористовував
Prom-функцію `select_top_items()`/`_margin()` — ранжування за МАРЖЕЮ
PROM (тодішня причина: Rozetka-комісія по категоріях ще не була відома,
не було на чому побудувати незалежний відбір). Відколи
`ROZETKA_CATEGORY_COMMISSION` існує (Vis-задача), цей компроміс
структурно застарів — і власниця прямо відхилила його продовження.

Тепер `_build_rozetka_static_selection()` нижче:
1. Рахує список ОДИН ЄДИНИЙ РАЗ (перший запуск, коли
   `ROZETKA_STATIC_SELECTION_FILE` ще не існує) — критерій: вимоги
   Rozetka до даних (`_qualifies_for_feed()`, без змін), stock>0 РІВНО
   на момент формування, і прибутковість під ВЛАСНОЮ Rozetka-комісією
   (`decide_price_for_platform(cost, None, "rozetka", ...)` — БЕЗ
   конкурента, Prom-формула тут узагалі не бере участі). Розмір —
   `ROZETKA_STATIC_LIST_SIZE = 2000` (пряме число від власниці, не моя
   екстраполяція з Prom-970/6000) — сортуємо кандидатів за margin_pct
   (Rozetka) спадно й беремо перших 2000; це ОДНОРАЗОВЕ сортування для
   побудови списку, не перманентна ротація/переранжування.
2. ⚠️ ОНОВЛЕНО 2026-08-16 (РОЗМОРОЗКА, КРОК 1 — задача власника найновіше-53,
   модерацію по суті пройдено: 1882 активні). Раніше список повертався
   БЕЗ ЖОДНОГО перерахунку (ні ціна, ні склад, ні фото не мінялись). ТЕПЕР:
   `generate_feed()` бере з цього файлу ЛИШЕ MEMBERSHIP (набір pid, що вже
   подані на Rozetka → жодної нової хвилі модерації), а всі ДАНІ товару —
   ЖИВІ щопрогону: `available`/`stock_quantity` за реальним stock Toysi,
   ціна через `decide_price_for_platform(cost, None, "rozetka", ...)`
   (Rozetka-флор), фото — ЧИСТІ `images.prom.ua` без вотермарки (див.
   `_clean_pictures`, фолбек на toysi). Тобто `_build_rozetka_static_selection`
   тепер служить ЛИШЕ джерелом membership (через `_load_rozetka_approved_ids`),
   а не замороженим знімком даних. КРОК 2 (розкатати повний живий каталог,
   а не лише цей набір) — окреме рішення, коли переконаємось, що чисті фото
   проходять модерацію Rozetka.

ЗАМІНЕНО ЦИМ ЖЕ ФІКСОМ: `_apply_rozetka_oos_grace()`/
`ROZETKA_MEMBERSHIP_STATE_FILE`/`rozetka_feed_membership_state.json`
(grace-період "тимчасово немає в наявності" для товарів, що випадали з
select_top_items() через stock=0) — увесь цей механізм ставав зайвим:
статичний список тепер узагалі не виключає товари за stock=0 в
принципі, тож більше нема що "тимчасово тримати" через грейс-період.

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
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

from competitor_pricing import decide_price_for_platform, load_description_overrides
from generate_prom_feed import append_clearance_notice
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
# Одна константа платформи на файл — див. коментар біля PLATFORM у
# generate_prom_feed.py (той самий фікс, 2026-07-21, привід — плутанина
# комісій між феєдами).
PLATFORM           = "rozetka"
MIN_SUPPLIER_PRICE = 20  # товари дешевше цієї ціни постачальника пропускаємо

# Пряме число від власниці (2026-07-27) — НЕ екстрапольоване з Prom
# (970/6000), окреме рішення для Rozetka. Розмір ПІСЛЯ модерації —
# окреме майбутнє рішення, не вирішується цим числом.
ROZETKA_STATIC_LIST_SIZE = 2000
ROZETKA_STATIC_SELECTION_FILE = Path(__file__).parent / "rozetka_static_selection.json"

# ВИПРАВЛЕНО (2026-07-15, знайдено валідатором Rozetka: попередження на
# бренд "Wader", 9 товарів) — Rozetka відхиляє окремі бренди за власною
# політикою маркетплейсу (не помилка даних чи коду). Власниця вирішила:
# виключити з фіда Rozetka повністю (ці SKU й далі продаються на Prom —
# фільтр стосується лише цього файлу). Порівняння без урахування регістру
# (Toysi vendor трапляється і як "Wader", і як "WADER").
ROZETKA_BRAND_STOP_LIST = {"wader"}

# ВИПРАВЛЕНО (2026-07-15, знайдено валідатором Rozetka: попередження на
# категорію "Рюкзаки", 52 товари) — Toysi category_id 98923. На відміну
# від бренду вище, тут НЕМАЄ підтвердженої дозволеної альтернативи (не
# перевірила "Управління товарами -> Довідники" в кабінеті — потрібен
# логін, який свідомо не автоматизую; rozetka_client.search_categories()
# готовий знайти альтернативу програмно, щойно з'являться
# ROZETKA_USERNAME/ROZETKA_PASSWORD). preflight() нижче лише ПОПЕРЕДЖАЄ
# про цю категорію (Rozetka сама класифікує це як "Попередження", не
# "Помилка" — не блокує публікацію), не виключає товари з фіда.
#
# 🔴 ТИМЧАСОВИЙ, ЗАХАРДКОДЖЕНИЙ ЗНІМОК одного прогону валідатора
# (2026-07-15) — НЕ живий список. Новий стоп-бренд/стоп-категорія, якої
# тут немає, так само непомітно проскочить, як і "Рюкзаки"/"Wader"
# проскочили до цього прогону. Планове рішення — sync_stop_lists_from_
# goods_errors() у rozetka_client.py (Крок 3 задачі про preflight,
# 2026-07-15), яке замінить обидва списки на щоденний живий запит через
# Seller API, щойно з'являться облікові дані.
ROZETKA_CATEGORY_STOP_LIST = {"98923"}

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

# Rozetka вимагає offer id лише з латиниці/цифр (докстрінг файлу вище) —
# Toysi id вже суто цифрові, тож цей regex завжди мав би проходити; існує
# як safety-net перевірка в rozetka_feed_preflight(), не тому що зараз
# щось реально відсіює.
_OFFER_ID_RE = re.compile(r"^[A-Za-z0-9]+$")

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


RZ_DELIVERY_MAX_SIDE_CM = 120.0  # ліміт габаритів ROZETKA Delivery (лист Анна Марченко 2026-08-18)


def _package_max_side_cm(item: dict):
    """Макс. сторона упаковки (см) з Toysi params (Довжина/Ширина/Висота в упаковці).
    None, якщо розмірів нема."""
    sides = []
    for p in (item.get("params") or []):
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        key = str(p[0]).lower()
        if "в упаковці" in key and ("довжина" in key or "ширина" in key or "висота" in key):
            try:
                sides.append(float(str(p[1]).replace(",", ".").split()[0]))
            except (ValueError, IndexError):
                pass
    return max(sides) if sides else None


def _within_rz_delivery_dims(item: dict) -> bool:
    """True якщо товар вписується в габарити ROZETKA Delivery (макс. сторона ≤120 см) АБО
    розмірів нема (невідомо — не блокуємо, іграшки майже всі малі). False лише для ЯВНО
    великогабаритних. Зовнішній фільтр товарознавця (рішення власника 2026-08-18): не
    додавати на Rozetka великогабаритні, бо ROZETKA Delivery їх не приймає."""
    side = _package_max_side_cm(item)
    return side is None or side <= RZ_DELIVERY_MAX_SIDE_CM


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
    # D3 (2026-08-21, фідбек модерації Rozetka): «Уцінка …» (пошкоджена упаковка) — модератор
    # відхиляє як «товар занесено як новий, вкажіть стан used». Ми дропшип, уцінені одиничні позиції
    # не варті модерації/скарг — виключаємо з фіда (рекомендація SEO, звірено: 10/11 «некор. хар-ка»).
    if (item.get("name") or "").strip().lower().startswith("уцінка"):
        return False
    vendor = (item.get("vendor") or "").strip()
    if not vendor:
        return False
    if vendor.lower() in ROZETKA_BRAND_STOP_LIST:
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


# ДОДАНО (2026-07-30, STATUS.md — Rozetka перейшла на групування карток "М'які
# іграшки" за param "Колір" (rz paramid 22611) і "Висота іграшки" (35419); товари
# без цих param НЕ показуються в групі = пряма загроза продажам). Toysi не дає
# структурованого param "Колір" для м'яких іграшок (живо: 0/73), тож добуваємо колір
# із СУФІКСА назви (де Toysi за конвенцією пише колір).
#
# "Висота іграшки" тут СВІДОМО не заповнюється: аудит (code_report_2026-07-30_pt8)
# живо показав, що Toysi-поле "Висота без упаковки" містить сміттєве значення (напр.
# "1" для 100-см ляльки — головний розмір лежить у "Ширині без упаковки" / числі в
# назві). Надійне джерело висоти й формат — на рішення власника (окремий follow-up).
_COLOR_STEMS = {
    "червон": "Червоний", "помаранч": "Помаранчевий", "оранж": "Помаранчевий",
    "жовт": "Жовтий", "зелен": "Зелений", "салатов": "Салатовий",
    "блакит": "Блакитний", "бірюз": "Бірюзовий", "синь": "Синій", "синій": "Синій",
    "фіолет": "Фіолетовий", "бузков": "Бузковий", "рожев": "Рожевий", "малинов": "Малиновий",
    "бордов": "Бордовий", "коричнев": "Коричневий", "бежев": "Бежевий",
    "чорн": "Чорний", "білий": "Білий", "біла": "Білий", "біле": "Білий",
    "сірий": "Сірий", "сіра": "Сірий", "сіре": "Сірий",
    "золот": "Золотий", "срібл": "Срібний", "різнокол": "Різнокольоровий", "мікс": "Мікс",
}
_COLOR_PAREN_RE = re.compile(r"\(([^()]{2,40})\)")


def _extract_color_from_name(name: str) -> str | None:
    """Колір із СУФІКСНИХ позицій назви — у дужках `(жовтий)` або після останньої
    коми в кінці `, рожева` (де Toysi за конвенцією пише колір). Повертає канонічну
    назву кольору або None. Розмір (є цифра, напр. "45 см") ігнорується. Свідомо НЕ
    шукаємо колірне слово будь-де в назві — щоб не сплутати з назвою персонажа
    (напр. "Червона Шапочка")."""
    name = name or ""
    candidates = list(_COLOR_PAREN_RE.findall(name))
    if "," in name:
        candidates.append(name.rsplit(",", 1)[-1])
    for frag in candidates:
        fl = frag.lower()
        if any(ch.isdigit() for ch in fl):   # розмір/кількість, не колір
            continue
        for stem, canon in _COLOR_STEMS.items():
            if stem in fl:
                return canon
    return None


PROM_PRODUCTS_CACHE_FILE = Path(__file__).parent / "prom_products_raw_cache.json"
ROZETKA_PRICE_OVERRIDES_FILE = Path(__file__).parent / "rozetka_price_overrides.json"
_PROM_IMAGE_SIZE_RE = re.compile(r"_w\d+_h\d+_")


ROZETKA_MERCHANT_PRICES_FILE = Path(__file__).parent / "rozetka_merchant_prices.json"


def _load_json_prices(path: Path) -> dict:
    """{our_offer_id: retail} з JSON-файлу, нормалізовано у float>0. Немає/битий → {}."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    out = {}
    for k, v in (raw.items() if isinstance(raw, dict) else []):
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            out[str(k)] = fv
    return out


def _load_price_overrides() -> dict:
    """Конкурентні ціни для фіду. ДВА джерела, чіткий пріоритет:
      1) `rozetka_merchant_prices.json` — стартова конкурентна ціна ТОВАРОЗНАВЦЯ для щойно доданих
         товарів (їх ще нема в моніторингу репрайсера) — БАЗА.
      2) `rozetka_price_overrides.json` — ЖИВІ ціни репрайсера (Фаза 2, моніторинг кабінету) —
         НАКРИВАЮТЬ merchant (щойно товар потрапляє в моніторинг, керує репрайсер).
    Немає/биті файли → {} (фід рахує формулу сам). Значення float."""
    out = _load_json_prices(ROZETKA_MERCHANT_PRICES_FILE)      # база (нові товари)
    out.update(_load_json_prices(ROZETKA_PRICE_OVERRIDES_FILE))  # репрайсер зверху (живі)
    return out


def _load_prom_products_cache() -> dict:
    """Кеш товарів Prom (`prom_products_raw_cache.json`, оновлює prom_catalog_sync щодня) —
    key = external_id (= наш vendor_code), value містить ЧИСТУ галерею `images.prom.ua` без
    вотермарки. Потрібен, щоб на Rozetka НЕ публікувати фото `toysi.ua` з вотермаркою постачальника
    (модератор Rozetka блокує такі — 98 товарів, найновіше-52). Немає/битий кеш → {} (фолбек на
    toysi-фото, стара поведінка)."""
    try:
        return json.loads(PROM_PRODUCTS_CACHE_FILE.read_text(encoding="utf-8")) or {}
    except (ValueError, OSError):
        return {}


PROM_SNAPSHOT_FILE = Path(__file__).parent / ".local_secrets" / "prom_full_catalog.json"


def _load_snapshot_clean_photos() -> dict:
    """D2 (2026-08-21): {sku: {"main_image": <чистий images.prom.ua>}} зі СВІЖОГО денного знімка Prom
    (`prom_full_catalog.json`, поле `image_url` — R4). Заміна ЗАМЕРЗЛОМУ `prom_products_raw_cache`
    (23.07, оновлювач prom-catalog-sync вимкнено з 30.07). Дає ЧИСТЕ ГОЛОВНЕ фото (не галерею) для
    товарів, що Є на Prom (~513 із 2364 Rozetka-набору). Ключ = sku = external_id = наш vendor_code
    (той самий, за яким шукає _clean_pictures). Best-effort → {}."""
    try:
        items = json.loads(PROM_SNAPSHOT_FILE.read_text(encoding="utf-8")).get("items") or {}
    except (ValueError, OSError, AttributeError):
        return {}
    out = {}
    for sku, rec in items.items():
        iu = (rec.get("image_url") or "").strip() if isinstance(rec, dict) else ""
        if iu.startswith("https://images.prom.ua"):
            out[str(sku)] = {"main_image": iu}
    return out


def _upscale_prom_image(url: str) -> str:
    if not url or "images.prom.ua" not in url:
        return url
    return _PROM_IMAGE_SIZE_RE.sub("_w1024_h1024_", url, count=1)


def _clean_pictures(item: dict, prom_products: dict) -> list:
    """Фото товару для Rozetka: спершу ЧИСТА галерея з Prom (`images.prom.ua`, без вотермарки,
    апскейл до 1024), фолбек — сирі `toysi.ua` pictures (з вотермаркою) лише якщо на Prom товару
    нема. Обмежуємо до ROZETKA_MAX_PICTURES. Той самий чистий бренд-нейтральний ряд фото, що вже
    годує Google/Meta-фіди."""
    prod = prom_products.get(str(item.get("vendor_code") or item.get("id"))) if prom_products else None
    clean = []
    if prod:
        for im in (prod.get("images") or []):
            u = (im.get("url") or "").strip()
            if u.startswith("https://images.prom.ua"):
                clean.append(_upscale_prom_image(u))
        if not clean:
            mi = (prod.get("main_image") or "").strip()
            if mi.startswith("https://images.prom.ua"):
                clean.append(_upscale_prom_image(mi))
    if clean:
        return clean[:ROZETKA_MAX_PICTURES]
    return [p for p in item.get("pictures", []) if p.startswith("https://")][:ROZETKA_MAX_PICTURES]


def _load_rozetka_approved_ids(catalog: dict) -> set:
    """Множина pid, які ВЖЕ подані на Rozetka (промодерований набір). Крок 1 розморозки
    (2026-08-16, задача власника найновіше-53): membership лишається СТАЛИМ (жодної нової хвилі
    модерації), але дані товарів — ЖИВІ (склад/ціна/фото), не заморожені. Джерело membership —
    ключі `rozetka_static_selection.json` (round-trip через feed-data). Файлу нема (перший запуск
    / не підтягнутий) → одноразово відтворюємо той самий набір через _build_rozetka_static_selection,
    щоб НЕ вивалити раптом увесь каталог у модерацію (це був би Крок 2, окреме рішення)."""
    if ROZETKA_STATIC_SELECTION_FILE.exists():
        try:
            data = json.loads(ROZETKA_STATIC_SELECTION_FILE.read_text(encoding="utf-8"))
            ids = set((data.get("items") or {}).keys())
            if ids:
                return ids
        except (ValueError, OSError):
            pass
    items, _ = _build_rozetka_static_selection(catalog)
    return set(items.keys())


def _build_xml(
    catalog: dict,
    price_overrides: dict = None,
    exclude_ids: set = None,
    description_overrides: dict = None,
    prom_products: dict = None,
) -> tuple[ET.Element, dict]:
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
    desc_overrides  = description_overrides or {}
    described_count = 0  # Vis-9: SKU, що отримали вручну написаний опис замість сирого Toysi

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

    skipped_no_price     = 0
    skipped_cheap        = 0
    skipped_unprof       = 0
    skipped_no_vendor    = 0
    skipped_stop_brand   = 0
    skipped_no_pics      = 0
    truncated_name_count = 0
    url_added_count      = 0

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

        # D3 (2026-08-21, фідбек модерації Rozetka): «Уцінка …» (пошкоджена упаковка) — модератор
        # відхиляє «занесено як новий, вкажіть used». Дропшип, одиничні позиції — виключаємо (той самий
        # фільтр, що в _qualifies_for_feed вище — ОБИДВА місця, бо фільтри дубльовані: тут реальний гейт).
        if (item.get("name") or "").strip().lower().startswith("уцінка"):
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

        # ВИПРАВЛЕНО (2026-07-15, попередження валідатора Rozetka, 9 SKU) —
        # окремі бренди Rozetka відхиляє за власною політикою маркетплейсу
        # (стоп-лист, не помилка даних). Рішення власниці: виключити з
        # фіда Rozetka повністю, ці SKU й далі йдуть на Prom без змін.
        if vendor.lower() in ROZETKA_BRAND_STOP_LIST:
            skipped_stop_brand += 1
            continue

        # https, без кирилиці/пробілів — Toysi-URL вже відповідають цьому
        # формату за конструкцією, але перевіряємо явно замість припущення.
        # ВИПРАВЛЕНО (незалежне рев'ю PR #39): фільтруємо на https ПЕРЕД
        # обмеженням до ROZETKA_MAX_PICTURES, не після — інакше валідне
        # https-фото на позиції 16+ могло й не потрапити в розгляд, і
        # товар з дійсним фото десь глибше в списку мовчки випав би з
        # фіда через те, що перші 15 сирих записів випадково не https.
        # ЧИСТІ фото (images.prom.ua, без вотермарки) з фолбеком на toysi — див. _clean_pictures.
        # Rozetka блокувала 98 товарів за вотермарку на toysi-фото (найновіше-52).
        pictures = _clean_pictures(item, prom_products or {})
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

        # Vis-9: override — той самий механізм і той самий файл
        # (description_overrides.json), що й generate_prom_feed.py: коли
        # Toysi дає лише мінімальний "Бренд+Країна" boilerplate, вручну
        # написаний текст ПОВНІСТЮ замінює сирий опис Toysi (і, якщо
        # заданий у записі, <country_of_origin>).
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
        # ВИПРАВЛЕНО (задача про 35 блокуючих помилок валідатора, 2026-07-13):
        # прибираємо http(s)-посилання ДО truncate(), знайдено на SKU 294130
        # (прихований <span id="ctrlcopy"> зі стороннім посиланням на
        # igrushki7.ua, лишок від того, звідки Toysi самі скопіювали опис) —
        # Rozetka блокує описи з посиланнями на сторонні сайти.
        desc = _strip_urls(desc)
        desc = _truncate(_clean_text(desc), ROZETKA_DESCRIPTION_MAX_LEN)
        if desc:
            ET.SubElement(offer, "description").text = desc

        params = list(item.get("params", []) or [])

        # Збагачення для м'яких іграшок (Rozetka групує їх за "Колір", rz paramid 22611,
        # 2026-07-30) — Toysi не дає структурованого param "Колір"; додаємо з СУФІКСА
        # назви, ЛИШЕ якщо його ще немає й колір справді розпізнано. Без "Колір" товар не
        # показується в груповій вітрині Rozetka. ("Висота іграшки" НЕ додаємо: аудит
        # code_report_2026-07-30_pt8 показав, що Toysi-поле "Висота без упаковки" містить
        # сміттєве значення — головний розмір лежить у "Ширині без упаковки"/назві —
        # питання джерела висоти винесено на рішення власника, див. звіт.)
        cat_l = (item.get("category_name", "") or "").lower()
        if "м'як" in cat_l or "мʼяк" in cat_l or "мяк" in cat_l:
            existing = {(pn or "").strip().lower() for pn, _ in params}
            if not any(("колір" in e or "цвет" in e) for e in existing):
                color = _extract_color_from_name(item.get("name", ""))
                if color:
                    params.append(("Колір", color))

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
          f"бренд у стоп-листі Rozetka: {skipped_stop_brand} | "
          f"без валідного фото: {skipped_no_pics} | назв обрізано (>{ROZETKA_NAME_MAX_LEN} симв.): {truncated_name_count}")
    print(f"[Rozetka] <url> додано для {url_added_count} з {len(offers_el)} товарів "
          f"(лише топ-970 з впевненим self-match на Prom, кеш {'знайдено' if own_product_links else 'відсутній/застарілий'})")
    print(f"[Rozetka] Vis-9: {described_count} SKU отримали вручну написаний опис "
          "(description_overrides.json) замість сирого Toysi")
    return yml


def _build_rozetka_static_selection(catalog: dict) -> tuple[dict, dict]:
    """Незалежний від Prom, СТАТИЧНИЙ відбір для Rozetka (2026-07-27,
    пряме рішення власниці — див. докстрінг файлу вище). Повертає
    (items, prices): items — {pid: item} заморожений на момент першого
    формування знімок каталогу (усі поля — назва/опис/фото/stock/тощо —
    рівно такі, якими вони були ТОДІ, не live), prices — {pid: retail}
    заморожена ціна, порахована ОДИН РАЗ через decide_price_for_platform()
    з Rozetka-комісією, без конкурента.

    Перший виклик (файл ще не існує) — рахує й зберігає. Кожен наступний
    виклик — читає збережене й повертає БЕЗ ЖОДНОГО перерахунку: жодне
    поле жодного SKU з цього списку не змінюється, доки хтось явно не
    вирішить інакше (видаливши файл чи додавши майбутній механізм
    "SKU X пройшов модерацію")."""
    if ROZETKA_STATIC_SELECTION_FILE.exists():
        try:
            saved = json.loads(ROZETKA_STATIC_SELECTION_FILE.read_text(encoding="utf-8"))
            return saved["items"], saved["prices"]
        except (ValueError, OSError, KeyError):
            pass  # пошкоджений/неповний файл — сформувати заново нижче, як при першому запуску

    candidates = []
    for pid, item in catalog.items():
        if not _qualifies_for_feed(item, excluded=set()):
            continue
        if item.get("stock", 0) <= 0:
            continue
        cost = float(item.get("price") or 0)
        decision = decide_price_for_platform(cost, None, PLATFORM, item.get("category_name"))
        candidates.append((pid, item, decision["price"], decision["margin_pct"]))

    candidates.sort(key=lambda c: c[3], reverse=True)
    selected = candidates[:ROZETKA_STATIC_LIST_SIZE]

    items  = {pid: item for pid, item, _, _ in selected}
    prices = {pid: price for pid, _, price, _ in selected}

    ROZETKA_STATIC_SELECTION_FILE.write_text(
        json.dumps({"items": items, "prices": prices, "built_at": datetime.now().isoformat()},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"[Rozetka] Статичний список сформовано ВПЕРШЕ: {len(items)} з {len(candidates)} "
          f"прибуткових/якісних кандидатів (з {len(catalog)} товарів каталогу Toysi). "
          "Список ЗАМОРОЖЕНИЙ — наступні прогони використовуватимуть той самий, без змін "
          "(ні ціна, ні наявність, ні сам факт присутності), доки не буде окремого рішення "
          "про товари, що пройшли модерацію Rozetka.")
    return items, prices


ROZETKA_REJECTED_IDS_FILE = Path(__file__).parent / ".local_secrets" / "rozetka_rejected_ids.json"


def _load_rozetka_rejected_ids() -> set:
    """D1 (2026-08-21): id товарів, ВІДХИЛЕНИХ модератором Rozetka (сховані, /goods/hidden) — щоб НЕ
    подавати їх у фід знову щогодини. Джерело — `.local_secrets/rozetka_rejected_ids.json` (структура
    {'at':..., 'ids': {id: {reasons, comment}}}), який оновлює `rozetka_price_monitor.py --rejected`.
    Best-effort: нема файлу/битий → порожньо (фід не ламається)."""
    try:
        data = json.loads(ROZETKA_REJECTED_IDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    ids = data.get("ids") if isinstance(data, dict) else None
    if isinstance(ids, dict):
        return set(str(k) for k in ids.keys())
    if isinstance(ids, list):
        return set(str(x.get("id") if isinstance(x, dict) else x) for x in ids)
    return set()


def generate_feed(output_file: str = OUTPUT_FILE,
                  price_overrides: dict = None,
                  catalog: dict = None,
                  exclude_ids: set = None,
                  description_overrides: dict = None) -> None:
    if catalog is None:
        print("[Rozetka] Завантажуємо каталог Toysi...")
        catalog = fetch_toysi_catalog()
    if not catalog:
        print("[Rozetka] Каталог порожній — файл не створено.")
        return

    # D1 (2026-08-21): додаємо у виключення id, ВІДХИЛЕНІ модератором Rozetka — інакше щогодини
    # подавали б їх назад (звірено живо: 91/91 відхилених були в живому rozetka_feed.xml).
    rejected = _load_rozetka_rejected_ids()
    if rejected:
        exclude_ids = (exclude_ids or set()) | rejected
        print(f"[Rozetka] Виключено {len(rejected)} відхилених модератором (фідбек /goods/hidden).")

    # РОЗМОРОЗКА, КРОК 1 (2026-08-16, задача власника найновіше-53). Раніше тут був заморожений
    # знімок (static_items + static_prices) — усі поля (склад/ціна/фото) заморожені на 2026-07-27.
    # Тепер: membership промодерованого набору лишається СТАЛИМ (ключі rozetka_static_selection.json
    # — жодної нової хвилі модерації), але дані товарів ЖИВІ — склад, ціна (через decide_price_for_
    # platform у _build_xml) і фото (чисті images.prom.ua) актуалізуються щопрогону. Крок 2 (повний
    # каталог) — окремо, коли переконаємось, що чисті фото проходять модерацію Rozetka.
    prom_products = _load_prom_products_cache()
    # D2 (2026-08-21): доливаємо ЧИСТЕ головне фото зі свіжого знімка каталогу — raw-кеш замерз 23.07
    # (34 чистих), знімок покриває ~513 on-Prom SKU. main_image ставимо ЛИШЕ якщо в raw-кеші його нема
    # (галерея raw-кеша, де вона є, лишається пріоритетною). Для ~1850 не-Prom SKU чистого джерела нема.
    _snap_photos = _load_snapshot_clean_photos()
    for _sku, _ph in _snap_photos.items():
        prom_products.setdefault(_sku, {}).setdefault("main_image", _ph["main_image"])
    print(f"[Rozetka] Чисті головні фото зі знімка каталогу: +{len(_snap_photos)} SKU.")
    approved_ids = _load_rozetka_approved_ids(catalog)
    live_items = {pid: catalog[pid] for pid in approved_ids if pid in catalog}
    print(f"[Rozetka] Оживлений промодерований набір: {len(live_items)} з {len(approved_ids)} "
          f"затверджених (решта відсутня в поточному каталозі Toysi). "
          f"Чисті фото images.prom.ua з кешу на {len(prom_products)} товарів; склад/ціна — живі.")

    # КОНКУРЕНТНІ ЦІНИ (Фаза 2, rozetka_competitor_repricer.py): підбиті під рекомендовану ціну
    # Rozetka в межах флору. Файл {our_id: retail}; явний price_overrides (напр. з тесту) — вище.
    repricer = _load_price_overrides()
    if repricer or price_overrides:
        merged = dict(repricer)
        if price_overrides:
            merged.update(price_overrides)
        price_overrides = merged
        print(f"[Rozetka] Цінових override-ів: {len(price_overrides)} "
              f"(репрайсер {len(repricer)}).")

    root = _build_xml(
        live_items, price_overrides=price_overrides, exclude_ids=exclude_ids,
        description_overrides=description_overrides, prom_products=prom_products,
    )

    ET.indent(root, space="  ")
    xml_str = ET.tostring(root, encoding="unicode")
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
    xml_str = _wrap_cdata(xml_str)

    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(xml_str)

    print(f"[Rozetka] Готово! Збережено: {output_file}")


def rozetka_feed_preflight(feed_file: str = OUTPUT_FILE) -> dict:
    """
    Один прогін самоперевірки ВЖЕ ЗГЕНЕРОВАНОГО фіда, ПЕРЕД публікацією —
    зводить усі відомі класи помилок валідатора Rozetka в одну функцію
    (задача власниці 2026-07-15: "системне рішення замість реактивних
    патчів"), замість того, щоб дізнаватись про кожен новий клас помилки
    окремим прогоном валідатора в кабінеті вже ПІСЛЯ публікації.

    Навмисно перевіряє СЕРЕДНІЙ РЕЗУЛЬТАТ (уже записаний XML-файл), а не
    внутрішню логіку _build_xml() — це ловить регресії в майбутньому,
    якщо хтось випадково зламає один з existing фільтрів генерації, а не
    лише підтверджує, що генерація зробила те, що сама від себе очікує.

    Повертає {"errors": [...], "warnings": [...]}:
    - errors — те, що Rozetka сама називає "Помилка валідації" й блокує
      публікацію ВСЬОГО фіда (підтверджено прогоном 2026-07-15: дублікат
      назв). Непорожній errors -> публікацію зупиняти.
    - warnings — те, що Rozetka називає "Попередження" (той самий прогін
      2026-07-15: категорія/бренд у стоп-листі) — НЕ блокує подачу за
      спостереженням Rozetka, лише знижує видимість конкретних SKU.
      Публікацію не зупиняємо через warnings, лише звітуємо.

    ⚠️ Обмеження, чесно визнане, не приховане: перевірка "дублікат назв"
    тут — точний побайтовий збіг <name> у ВЖЕ згенерованому файлі. Раніше
    (SKU 292911/292915) Rozetka вважала дублікатом і НЕ побайтово
    однакові рядки (ігноруючи кольорове уточнення в дужках) — цей клас
    уже закрито на етапі ГЕНЕРАЦІЇ (_dedup_key() у _build_xml()), тому
    після фіксу вихідні рядки й так гарантовано унікальні (перевірено
    живим тестом: 0 дублікатів на 970/961 офферів). Preflight тут ловить
    РЕГРЕСІЮ цього фіксу (перевіряючи побайтовий збіг ще раз незалежно
    від _build_xml), а не намагається наперед вгадати ЩЕ НЕВІДОМИЙ клас
    "Rozetka вважає ці два рядки схожими" — жодна локальна евристика не
    відтворить точно непублічний алгоритм валідатора Rozetka. Нові класи
    так само, як і "Рюкзаки"/"Wader", виявлятимуться реактивно через
    прогони валідатора — це FUNDAMENTAL обмеження, не недогляд.
    """
    errors: list[str] = []
    warnings: list[str] = []

    tree = ET.parse(feed_file)
    offers = tree.getroot().find("shop").find("offers")

    names = []
    for offer in offers:
        offer_id = offer.get("id") or ""
        name_el = offer.find("name")
        name = name_el.text or "" if name_el is not None else ""
        names.append(name)

        if not _OFFER_ID_RE.match(offer_id):
            errors.append(f"offer {offer_id}: id не відповідає формату (лише латиниця/цифри)")

        if len(name) > ROZETKA_NAME_MAX_LEN:
            errors.append(f"offer {offer_id}: назва {len(name)} символів (>{ROZETKA_NAME_MAX_LEN})")
        if _CONTROL_CHARS_RE.search(name):
            errors.append(f"offer {offer_id}: заборонений керівний ASCII-символ у назві")

        desc_el = offer.find("description")
        desc = desc_el.text or "" if desc_el is not None else ""
        if len(desc) > ROZETKA_DESCRIPTION_MAX_LEN:
            errors.append(f"offer {offer_id}: опис {len(desc)} символів (>{ROZETKA_DESCRIPTION_MAX_LEN})")
        if _CONTROL_CHARS_RE.search(desc):
            errors.append(f"offer {offer_id}: заборонений керівний ASCII-символ в описі")
        if _URL_RE.search(desc):
            errors.append(f"offer {offer_id}: http(s)-посилання лишилось в описі")

        pictures = offer.findall("picture")
        if not pictures:
            errors.append(f"offer {offer_id}: немає жодного фото")
        elif len(pictures) > ROZETKA_MAX_PICTURES:
            errors.append(f"offer {offer_id}: {len(pictures)} фото (>{ROZETKA_MAX_PICTURES})")
        for pic in pictures:
            if not (pic.text or "").startswith("https://"):
                errors.append(f"offer {offer_id}: фото не https ({pic.text})")

        if not offer.findall("param"):
            errors.append(f"offer {offer_id}: немає жодного param")

        for required_tag in ("price", "currencyId", "stock_quantity", "vendor"):
            tag_el = offer.find(required_tag)
            if tag_el is None or not (tag_el.text or "").strip():
                errors.append(f"offer {offer_id}: відсутній/порожній обов'язковий тег <{required_tag}>")

        vendor_el = offer.find("vendor")
        vendor = (vendor_el.text or "").strip() if vendor_el is not None else ""
        if vendor.lower() in ROZETKA_BRAND_STOP_LIST:
            warnings.append(f"offer {offer_id}: бренд '{vendor}' у стоп-листі Rozetka")

        category_el = offer.find("categoryId")
        category_id = (category_el.text or "").strip() if category_el is not None else ""
        if not category_id:
            errors.append(f"offer {offer_id}: немає categoryId")
        elif category_id in ROZETKA_CATEGORY_STOP_LIST:
            warnings.append(f"offer {offer_id}: категорія '{category_id}' у стоп-листі Rozetka")

    name_counts = Counter(names)
    for name, count in name_counts.items():
        if count > 1:
            errors.append(f"назва \"{name}\" повторюється {count} разів (побайтовий збіг)")

    return {"errors": errors, "warnings": warnings}


def _preflight_cli() -> int:
    """Викликається окремим кроком update-feeds.yml, ПІСЛЯ генерації,
    ПЕРЕД публікацією — `python generate_rozetka_feed.py --preflight`.
    Ненульовий вихідний код зупиняє крок (і, за замовчуванням GitHub
    Actions, весь job) — публікація фіда з errors ПРОСТО НЕ ВІДБУВАЄТЬСЯ,
    а не публікується мовчки, як траплялось до цієї задачі."""
    from telegram_notify import send_telegram_message

    result = rozetka_feed_preflight()
    errors, warns = result["errors"], result["warnings"]

    for w in warns:
        print(f"[Rozetka][preflight] ПОПЕРЕДЖЕННЯ: {w}")

    if errors:
        for e in errors:
            print(f"[Rozetka][preflight] ПОМИЛКА: {e}", file=sys.stderr)
        message = (
            f"🚨 Rozetka preflight: фід НЕ пройшов перевірку, публікацію зупинено "
            f"({len(errors)} помилок):\n" + "\n".join(f"- {e}" for e in errors[:20])
        )
        if len(errors) > 20:
            message += f"\n... і ще {len(errors) - 20}"
        if not send_telegram_message(message):
            print("[Rozetka][preflight] Не вдалося надіслати сповіщення в Telegram", file=sys.stderr)
        return 1

    print(f"[Rozetka][preflight] OK — 0 помилок, {len(warns)} попереджень.")
    return 0


if __name__ == "__main__":
    if "--preflight" in sys.argv:
        sys.exit(_preflight_cli())
    generate_feed(description_overrides=load_description_overrides())
