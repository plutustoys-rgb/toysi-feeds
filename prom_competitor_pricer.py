"""
prom_competitor_pricer.py — Крок 2 репрайсера конкурентів: пошук конкурента
НАПРЯМУ на Prom.ua для кожного SKU топ-970, порівняння з нашою ціною через
decide_price_for_platform() (competitor_pricing.py), гібридна дія.

АРХІТЕКТУРА ПОШУКУ — internal GraphQL, не Playwright/Selenium
Reverse-engineering завершено 2026-07-11: `POST https://prom.ua/graphql`,
операція `SearchListingQuery` — та сама, якою власна SPA-сторінка пошуку
тягне результати (перехоплено патчингом `window.fetch` у живій сесії).
Підтверджено НАПРЯМУ curl-ом з VPS (без жодних cookies/авторизації,
той самий IP, що виконуватиме цей скрипт 24/7): чиста 200 OK-відповідь із
реальними товарами (id/name/price/company_id/presence). Це ЗНАЧНО дешевше
й надійніше за браузерну автоматизацію (жоден Chrome не запускається,
жодного бот-фінгерпринтингу) — тому Playwright/Selenium (другий варіант,
запропонований як fallback) тут НЕ використовується.

Гібридна політика (ВИПРАВЛЕНО 2026-07-18, точне правило прямо підтверджене
власником після живого підтвердження системного розриву 10-31% проти
buyBox-конкурентів, code_report_2026-07-18_pt3.md):
- floor = собівартість + наші витрати (комісія майданчика+оплати) + 3%
  цільової маржі (decide_price_for_platform(), Шлях 2).
- кандидат = ціна конкурента - PRICE_STEP (competitor_pricing.py, 3₴ —
  підвищено з 1₴ 2026-07-22, рішення власниці: помітніша різниця для
  покупця, не лічені копійки).
- якщо floor <= кандидат -> "adjust", ціна = кандидат (undercut, підрізаємо
  конкурента рівно на PRICE_STEP).
- якщо floor > кандидат (навіть на 1 копійку — ЖОДНОЇ "грації") -> "delist",
  ПОВНЕ видалення товару з каталогу (status=deleted — див. докстрінг
  delist() нижче: власниця прямо підтвердила остаточне рішення, не
  тримати неконкурентний товар "про запас"). Раніше тут була "грація"
  1.5x і повне виключення buyBox-джерела з delist — обидва прибрано,
  вони і були причиною системного розриву 10-31%.
- Без знайденого конкурента -> "adjust" на формульну (no_competitor) ціну,
  як і завжди, НІКОЛИ не "delist".

Зіставлення товару з конкурентом — за текстовою схожістю назв (той самий
клас ризику, що вже описаний у competitor_pricing.py's select_batch() —
"пошук за назвою" може хибно зіставити). Пошук виконується РОСІЙСЬКОЮ
назвою (fetch_russian_text() з generate_prom_feed.py, PR #23) — результати
пошуку на Prom переважно російськомовні, кращий лексичний збіг, ніж з
українською назвою Toysi.

Безпека:
- assert_catalog_size_sane() (parser.py) — той самий запобіжник, що й
  prom_catalog_sync.py: усічений, але структурно валідний фід Toysi більше
  не є підставою для дій (ні коригування ціни, ні видалення).
- За замовчуванням DRY-RUN. Реальні зміни в кабінеті Prom — лише з --apply.
- Денний ліміт (як і в competitor_pricing.py) — не бомбардувати пошуковий
  ендпоінт Prom тисячами запитів одномоментно.

Запуск:
    python prom_competitor_pricer.py --limit 50            # dry-run, перші 50 SKU топ-970
    python prom_competitor_pricer.py --limit 50 --apply     # реальні зміни
"""

import argparse
import difflib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from io import BytesIO
from PIL import Image
import imagehash

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from parser import fetch_toysi_catalog, assert_catalog_size_sane, CatalogSizeError
from generate_prom_feed_top import select_top_items, load_scan_state
from generate_prom_feed import fetch_russian_text
from competitor_pricing import (
    decide_price_for_platform, load_prom_price_state, save_prom_price_state,
    PROM_CATEGORY_COMMISSION, PROM_CATEGORY_ID_COMMISSION, PROM_COMMISSION_DEFAULT, _BAD_NAME_MARKERS,
    is_bundle_listing, MIN_PROFIT_COMPETITOR_FLOOR, real_toysi_cost,
)
from telegram_notify import send_telegram_message
from prom_api_client import PromEditError, apply_price, delist

load_dotenv()

PROM_API_KEY  = os.environ.get("PROM_API_KEY", "")
PROM_API_URL  = "https://my.prom.ua/api/v1"
PROM_GRAPHQL_URL = "https://prom.ua/graphql"
REQUEST_TIMEOUT  = 20

# Стислий підсумок для людини (не для коду) — той самий патерн, що й
# prom_catalog_audit_summary.md у спільній Windows-теці PlutusToys_avtonomiya,
# але той файл оновлюється вручну Claude-сесією; цей — публікується сюди,
# в репо (гілка feed-data, поряд з prom_competitor_price_state.json), бо
# GitHub Actions не має доступу до локальної теки власника. Синхронізація
# в PlutusToys_avtonomiya — вручну, коли активна сесія.
SUMMARY_FILE = Path(__file__).parent / "prom_competitor_pricer_summary.md"


def write_pricer_summary(note: str, *, checked: int = 0, adjust: int = 0,
                          delist: int = 0, no_competitor: int = 0,
                          errors: int = 0) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Репрайсер конкурентів Prom — стислий підсумок (перезаписується щоразу)",
        "",
        f"Оновлено: {now}",
        note,
    ]
    if checked or adjust or delist or no_competitor or errors:
        lines.append(f"Перевірено товарів: {checked} | Скориговано цін: {adjust} | "
                      f"Delist: {delist} | Без конкурента: {no_competitor} | Помилки: {errors}")
    SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

# c4219597-plutustoys.html — company_id власного магазину, підтверджено
# напряму з URL кабінету/сторінки компанії. Результати пошуку з цим
# company_id — це МИ САМІ, не конкурент, завжди виключаються.
PROM_OWN_COMPANY_ID = 4219597

# Пряма сторінка товару конкурента на Prom.ua — використовується ЛИШЕ для
# перевірки перед delist (див. verify_competitor_really_available), не для
# самого пошуку/зіставлення.
COMPETITOR_PRODUCT_URL_TEMPLATE = "https://prom.ua/ua/p{id}-{url_text}.html"

# ДОДАНО 2026-07-14 (три незалежні аудити 07-13/07-14 показали: SearchListingQuery
# + текстовий fuzzy-збіг пропускає реальних конкурентів у 42-95% перевірених
# SKU, включно з 242610/289818, — сам пошук за назвою просто не знаходить
# те, що видно на нашій ж сторінці товару). Знайдено надійніше джерело:
# кожна сторінка товару Prom.ua (у т.ч. НАША власна) містить у своєму SSR-
# HTML вбудований блок `"buyBox":{"count":N,"modelId":"...","minPrice":X,
# "maxPrice":Y,"companyCount":N}` — той самий блок, що рендерить видиму
# секцію "Цей товар у інших продавців". Це власна ВНУТРІШНЯ система
# зіставлення товарів Prom (product "model", ймовірно за GTIN/штрихкодом),
# НЕ текстовий пошук — набагато надійніше за SequenceMatcher. Живо
# перевірено на 2 SKU (242610: minPrice=1364 < наша 1543.40; 289818:
# minPrice=336.74 > наша 310.36) — обидва підтверджують, що minPrice
# ВИКЛЮЧАЄ нашу власну ціну (інакше на 289818, де ми найдешевші, minPrice
# дорівнював би нашій ціні, а не був вищим).
#
# Дістається ЗВИЧАЙНИМ `requests.get()` на сторінку НАШОГО Ж товару (без
# GraphQL-запиту, без браузера) — URL береться з own_product_links_cache.json
# (пише generate_google_feed.py, той самий кеш, що вже читає
# generate_rozetka_feed.py для <url>). Товари поза цим кешем (self-match
# ще не знайдено) просто не отримують buyBox-дані цього прогону — не
# помилка, це той самий "необов'язковий" підхід, що й скрізь з цим кешем.
OWN_PRODUCT_LINKS_CACHE_FILE = Path(__file__).parent / "own_product_links_cache.json"
OWN_PRODUCT_LINKS_CACHE_TTL_DAYS = 7
_BUYBOX_RE = re.compile(r'"buyBox":(\{[^{}]*\})')

# Autonomy-11/Vis-11: {external_id: {"category_id": int, "category_caption": str}},
# пише generate_google_feed.py (build_prom_category_cache, побічний ефект
# fetch_prom_products() без додаткових запитів) — той самий кеш-патерн і
# TTL, що й OWN_PRODUCT_LINKS_CACHE_FILE вище.
PROM_CATEGORY_CACHE_FILE = Path(__file__).parent / "prom_category_cache.json"
PROM_CATEGORY_CACHE_TTL_DAYS = 7


def _load_own_product_links_cache() -> dict:
    """{item_id: {"prom_id": int, "url_text": str}} — той самий кеш і те
    саме читання (без власного запису), що вже робить
    generate_rozetka_feed.py для <url>. Порожній словник, якщо кеш
    відсутній чи застарів (>OWN_PRODUCT_LINKS_CACHE_TTL_DAYS) — у цьому
    разі buyBox просто не використовується для жодного SKU цього прогону,
    fallback на SearchListingQuery нижче лишається робочим."""
    if not OWN_PRODUCT_LINKS_CACHE_FILE.exists():
        return {}
    age_days = (time.time() - OWN_PRODUCT_LINKS_CACHE_FILE.stat().st_mtime) / 86400
    if age_days >= OWN_PRODUCT_LINKS_CACHE_TTL_DAYS:
        return {}
    try:
        return json.loads(OWN_PRODUCT_LINKS_CACHE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _load_prom_category_cache() -> dict:
    """{item_id: {"category_id": int, "category_caption": str}} — лише
    читання (запис — build_prom_category_cache() у generate_google_feed.py),
    та сама TTL-логіка, що й _load_own_product_links_cache() вище. Порожній
    словник, якщо кеш відсутній/застарів — decide_action() тоді просто
    падає на Toysi-based PROM_CATEGORY_COMMISSION за назвою (стара
    поведінка, без змін)."""
    if not PROM_CATEGORY_CACHE_FILE.exists():
        return {}
    age_days = (time.time() - PROM_CATEGORY_CACHE_FILE.stat().st_mtime) / 86400
    if age_days >= PROM_CATEGORY_CACHE_TTL_DAYS:
        return {}
    try:
        return json.loads(PROM_CATEGORY_CACHE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def fetch_buybox_competitor(prom_id: int, url_text: str) -> dict | None:
    """Читає вбудований `buyBox` блок із СИРОГО HTML нашої власної сторінки
    товару (regex, той самий стиль, що й _AVAILABILITY_RE нижче — легкий
    парсинг одного JSON-подібного поля, не повний DOM/JSON парсер сторінки).
    Повертає None, якщо сторінка недоступна, поле відсутнє, чи companyCount/
    minPrice відсутні/нульові (немає жодного іншого продавця цього ж
    товару) — НІКОЛИ не кидає виняток, той самий контракт, що й
    search_prom_products()."""
    url = COMPETITOR_PRODUCT_URL_TEMPLATE.format(id=prom_id, url_text=url_text)
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "uk-UA,uk;q=0.9",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        match = _BUYBOX_RE.search(response.text)
        if not match:
            # ВИПРАВЛЕНО (незалежне рев'ю PR #53): раніше тихо повертало None
            # тут само, як і на "є buyBox, але немає інших продавців" —
            # обидва виглядали ІДЕНТИЧНО зовні (buybox=None), тож падіння
            # success rate через зламаний regex (Prom змінив структуру
            # сторінки) було б невідрізнюваним від звичайного "мало
            # конкурентів сьогодні". Друкуємо ЛИШЕ для випадку "поле
            # взагалі не знайдено" (можлива зміна сторінки) — не для
            # "знайдено, але порожньо" (нормально, очікувано, друк був би
            # шумом на кожному SKU без конкурентів).
            print(f"[Pricer] buyBox: поле не знайдено на {url} — можлива зміна структури сторінки Prom", file=sys.stderr)
            return None
        buybox = json.loads(match.group(1))
        company_count = buybox.get("companyCount") or 0
        min_price = float(buybox.get("minPrice") or 0)
        if company_count <= 0 or min_price <= 0:
            return None
        # "name"/"id"/"urlText" — None: buyBox дає лише СУМАРНУ найнижчу
        # ціну серед інших продавців, не конкретне оголошення. "source":
        # "buybox" — явний маркер для decide_action(): не дозволяти delist
        # без реального оголошення для перевірки presence (verify_
        # competitor_really_available вимагає конкретний id/urlText, яких
        # тут немає) — навіть попри високу впевненість збігу.
        return {
            "score": 1.0,
            "price": min_price,
            "name": f"BuyBox: {company_count} інших продавців цього ж товару",
            "id": None,
            "urlText": None,
            "image": None,  # buyBox не дає фото КОНКРЕТНОГО оголошення — не потрібне, score=1.0 і так довірений
            "source": "buybox",
        }
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None


# ДОДАНО (2026-07-24, живе мережеве перехоплення на реальній сторінці
# товару, пряме прохання власниці "може ще щось придумаєте для
# покращення пошуку конкурентів"): той самий /graphql-ендпоінт, що вже
# дає SearchListingQuery (search_prom_products нижче), також обслуговує
# блок "Дивись також" на сторінці товару — запит ConvProductPremiumAdvQuery,
# recommended(product_id, quantity). Підтверджено живо: працює прямим
# requests.post() без жодної автентифікації/сесії, повертає до 10
# товарів із реальними ціною/назвою/company_id.
#
# ВАЖЛИВО: це НЕ гарантовано "той самий товар, інший продавець", як
# buyBox — серед 10 результатів на тестовому SKU були і точні аналоги
# (та сама назва, інша компанія), і просто схожі, але ІНШІ товари.
# Фільтрація/скоринг (_rank_competitor_candidates нижче) так само
# обов'язкова, як і для текстового пошуку.
#
# Викликається ЛИШЕ як проміжний фолбек, коли buyBox нічого не дав (не
# для кожного SKU) — buyBox вимагає інших продавців НА ТІЙ САМІЙ,
# точній сторінці (вужчий, рідший збіг), тоді як recommended() дає
# ширший пул. Той самий prom_id, що вже резолвлено для buyBox — жодного
# нового резолву own_link не треба. Це ДОДАЄ навантаження лише на частку
# SKU, де buyBox не вистачило, а не подвоює його для всього каталогу.
# PROM_GRAPHQL_URL вже визначено вище (той самий ендпоінт, що й для
# SearchListingQuery/search_prom_products()) — перевикористовуємо, не
# дублюємо.

_RECOMMENDED_QUERY = """query ConvProductPremiumAdvQuery($productId: Long, $limit: Int = 10) {
  recommended(visited_product_ids: [], product_id: $productId, boostPremium: true, quantity: $limit) {
    product {
      id
      name
      priceOriginal
      urlText
      company_id
      image(width: 200, height: 200)
    }
  }
}"""

RECOMMENDED_QUANTITY = 10


def fetch_recommended_products(prom_id: int) -> list:
    """Сирі кандидати з блоку "Дивись також" нашої власної сторінки —
    той самий формат полів (price/name/id/urlText/company_id/image), що й
    search_prom_products(), щоб обидва джерела проходили через ОДНУ
    спільну фільтрацію (_rank_competitor_candidates). Немає поля presence
    (GraphQL не дає isAvailable для цього запиту) — викликач має явно
    пропустити цю перевірку (check_presence=False). Мережева помилка чи
    несподіваний формат відповіді -> порожній список, той самий безпечний
    контракт, що й у fetch_buybox_competitor (ніколи не кидає виняток)."""
    payload = {
        "operationName": "ConvProductPremiumAdvQuery",
        "variables": {"limit": RECOMMENDED_QUANTITY, "productId": prom_id},
        "query": _RECOMMENDED_QUERY,
    }
    try:
        response = requests.post(
            PROM_GRAPHQL_URL,
            json=payload,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        items = (data.get("data") or {}).get("recommended") or []
        products = []
        for item in items:
            p = item.get("product") or {}
            if not p:
                continue
            products.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "price": p.get("priceOriginal"),
                "urlText": p.get("urlText"),
                "company_id": p.get("company_id"),
                "image": p.get("image"),
            })
        return products
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return []


def _rank_competitor_candidates(
    raw_products: list, search_name: str, cost: float,
    own_pictures: list | None = None, check_presence: bool = True,
) -> dict | None:
    """Спільна фільтрація/ранжування кандидатів — той самий ланцюжок
    перевірок, що раніше був лише в текстовому пошуку (SearchListingQuery),
    винесений сюди (2026-07-24), щоб і нове джерело (fetch_recommended_
    products) проходило через ту саму логіку, без дублювання ~40 рядків.

    check_presence=False для джерел без поля presence (recommended —
    GraphQL не дає isAvailable для цього запиту; сам запит і так вертає
    обмежений, курований пул, а не повний каталог, тож ризик застарілого
    "в наявності" тут менший, ніж був би для повного пошуку)."""
    candidates = []
    for p in raw_products:
        if p.get("company_id") == PROM_OWN_COMPANY_ID:
            continue
        if check_presence:
            presence = p.get("presence") or {}
            if not presence.get("isAvailable"):
                continue
        candidate_name_lower = (p.get("name") or "").lower()
        if any(marker in candidate_name_lower for marker in _BAD_NAME_MARKERS):
            continue
        if is_bundle_listing(p.get("name")):
            continue
        try:
            price = float(p.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        if not (cost * PRICE_SANITY_MIN_RATIO <= price <= cost * PRICE_SANITY_MAX_RATIO):
            continue
        score = _similarity(search_name, p.get("name", ""))
        if score >= MATCH_MIN_SCORE:
            candidates.append({"score": score, "price": price, "name": p.get("name"),
                                "id": p.get("id"), "urlText": p.get("urlText"),
                                "image": p.get("image")})

    if not candidates:
        return None
    trusted = [c for c in candidates if c["score"] >= MATCH_MIN_SCORE_FOR_PRICING]
    pool = trusted if trusted else candidates
    pool.sort(key=lambda c: c["price"])

    if own_pictures and trusted:
        for candidate in pool[:PHOTO_VERIFY_CANDIDATE_LIMIT]:
            if _photo_confirms_match(own_pictures, candidate.get("image")):
                return candidate

    return pool[0]


# 2026-07-17: 284/970 (29%) топ-970 SKU наразі йдуть на PROM_COMMISSION_
# DEFAULT (20%) — реальна ставка категорії не підтверджена (кабінет Prom
# -> Показники роботи компанії -> Комісія за замовлення). Дефолт може
# бути занижений відносно реальної ставки категорії (як-от підтверджені
# 23-25% для кількох суміжних категорій) — застосування авто-ціни на
# невірному, заниженому дефолті ризикує реальним заниженням floor і
# маржі, непомітно для жодної наявної перевірки. Такі SKU виключаються
# з --apply (ні adjust, ні delist) до ручного підтвердження реальної
# ставки категорії; дозволяється дозволити з ЯВНИМ прапорцем.
#
# Autonomy-11/Vis-11: якщо prom_category_id передано й закешовано
# (prom_category_cache.json, generate_google_feed.py) І присутній у
# PROM_CATEGORY_ID_COMMISSION — комісія ПІДТВЕРДЖЕНА напряму по товару,
# незалежно від того, чи Toysi category_name є в PROM_CATEGORY_COMMISSION
# (закриває саме ті 39 категорій/198 SKU, де назва Toysi неоднозначна —
# розпадається на кілька різних Prom-категорій, напр. "рюкзаки").
def _category_commission_is_default(category_name: str | None, prom_category_id: int | None = None) -> bool:
    if prom_category_id is not None and prom_category_id in PROM_CATEGORY_ID_COMMISSION:
        return False
    key = (category_name or "").strip().lower()
    return key not in PROM_CATEGORY_COMMISSION


SEARCH_LIMIT = 20  # скільки кандидатів забирати на один пошуковий запит

# ЗМІНЕНО (2026-07-24, розширення вітрини до 6000 SKU — пряме рішення
# власниці, "усі 6000 отримують живий пошук, як сьогоднішні ~1000, а не
# 2-й рівень з тижневою застарілістю"): DAILY_LIMIT/MIN_FULL_RUN_INTERVAL_
# HOURS (нижче) замінені на ротаційні партії. Причина: живий вимір
# (run 30062202019) — ~3.8с/SKU живого пошуку. Один прогін на всі 6000
# зайняв би ~6.2-6.3 год — впритул/вище за жорстку 6-годинну стелю
# виконання job'у GH Actions на хостованих раннерах. Замість переїзду на
# VPS (розглядався, PR #149, відхилено — публічне репо робить self-hosted
# раннер небезпечним, а сам переїзд виявився непотрібним): щочотири
# години (кожен тик update-feeds.yml) репрайсер обробляє РОТАЦІЙНУ
# партію в ROTATION_BATCH_SIZE SKU — найстаріші за часом останньої
# живої перевірки (price_state[pid]["timestamp"]), а не завжди одні й ті
# самі перші N.
#
# ЗМЕНШЕНО 3000→1500 (2026-07-24, живе спостереження): перші два реальні
# прогони нової ротаційної логіки (після мержу вище) зайняли 5+ год
# кожен — суттєво більше за оцінку 3.2 год з малого тестового зразка
# (1270 SKU за 80 хв). Через це весь день жоден прогін не доходив до
# кроку публікації фіда (feed-data не оновлювався з 09:39 UTC, попри
# п'ять змержених PR розширення до 6000) — черга update-feeds.yml
# (concurrency cancel-in-progress:false) просто накопичувалась. 1500
# SKU — навіть при реальній швидкості вдвічі гіршій за оцінку (7.6с/SKU)
# все одно ~3.2 год, з запасом під 4-годинний тик. Повна ротація 6000
# тепер займає 4 цикли (~16 год замість ~8) — свідомий компроміс
# швидкості ротації заради надійної публікації фіда щочотири години.
ROTATION_BATCH_SIZE = 1500

# ДОДАНО (2026-07-26, пряме прохання власниці — "давай прибирати цей
# ризик"): усі стелі партій вище (ROTATION_BATCH_SIZE, ROTATED_OUT_
# BATCH_LIMIT, LIVE_LOOKUP_EXTRA_BATCH_LIMIT, RECHECK_DELISTED_BATCH_
# LIMIT) — це ОЦІНКИ "має влізти в 4-годинний тик", підібрані вручну
# методом проб і помилок (див. коментар над ROTATION_BATCH_SIZE:
# 3000→1500 після реального інциденту з переповненням). Жодна з них не
# захищає від структурного ризику: якщо оцінка виявиться неточною
# (повільніша мережа, більший catalog, нова партія делистед), прогін
# впирається в ЗОВНІШНІЙ 6-годинний timeout GitHub Actions, який просто
# ВБИВАЄ job — жоден крок ПІСЛЯ репрайсера (генерація фідів, публікація)
# не встигає виконатись, і вся вже зроблена робота (ціни, delist) губиться,
# бо periodic-save пише лише ЛОКАЛЬНИЙ файл на ефемерному runner'і, а
# публікація в git відбувається лише в самому кінці job'у (живий
# інцидент 2026-07-25, прогін 30156977509 — 5.5г роботи, 0 опубліковано).
#
# Це виправляє РИЗИК СТРУКТУРНО, а не підбором "безпечнішого" числа:
# репрайсер сам стежить за витраченим часом і ДОБРОВІЛЬНО зупиняє
# ПОДАЛЬШУ обробку (зберігши вже зроблене й дозволивши процесу дійти до
# природного кінця — а отже, і решті workflow'у дійти до кроку
# публікації), щойно наближається до safe-запасу під зовнішній ліміт.
# Це означає: жодну зі стель партій вище більше не потрібно підбирати
# консервативно "про всяк випадок" — можна сміливо піднімати --limit
# для прискорених прогонів, бо структурний запобіжник нижче гарантує
# graceful-завершення незалежно від фактичного розміру партії.
#
# 5 годин = 6-годинна стеля GitHub Actions мінус 1 година запасу (крок
# репрайсера — не єдиний у job'і: setup/checkout/інші феєди теж займають
# час до й після нього, тож запас рахується від старту JOB'у, не кроку).
MAX_RUNTIME_SECONDS = 5 * 3600

# Встановлюється в main() на старті ({time.monotonic()} + MAX_RUNTIME_
# SECONDS) — модульний рівень (не локальна змінна main()), щоб усі
# довгі цикли нижче (основний decide-цикл, rotated_out, apply, delist,
# і _recheck_delisted_pids() в ОКРЕМІЙ функції) могли звірятись з ОДНІЄЮ
# спільною межею без протягування параметра через кожен виклик.
_RUN_DEADLINE: float | None = None


def _time_budget_exceeded() -> bool:
    return _RUN_DEADLINE is not None and time.monotonic() >= _RUN_DEADLINE
SEARCH_DELAY = 0.4  # секунд між пошуковими запитами — не бомбардувати ендпоінт

# ДОДАНО (2026-07-20, пряме прохання власниці — "постійно конкурентні
# товари, раз і назавжди", живий приклад SKU 275962 "Little Milly":
# репрайсер поставив конкурентну ціну 1073.28 грн 17.07, товар відтоді
# випав із топ-970 (select_top_items() — щоденна ротація), і жоден
# наступний прогін репрайсера більше НЕ торкався цього SKU (цикл нижче
# обробляє лише top_catalog). Через 30 год (PROM_PRICE_STATE_MAX_AGE_HOURS,
# competitor_pricing.py) стара конкурентна ціна застаріла, і фід тихо
# відкотився на наївну формульну ціну (1346.15 грн) — товар і далі живий
# на сайті, просто вже НЕконкурентний, поки жива людина випадково не
# помітить різницю. code_report_2026-07-20_pt14.md — повний розбір.
#
# Фікс: КОЖЕН прогін репрайсера тепер додатково обробляє SKU, які
# full_catalog_competitor_scan.py (нічний скан VPS, окремий процес) уже
# оцінив, але які випали з поточного топ-970 — _rotated_out_scan_candidates()
# нижче. Ліміт на пакет за прогін (не безлімітно за раз, той самий
# принцип, що вже є в full_catalog_competitor_scan.py: пораційна обробка,
# не один гігантський прогін) — за 6 прогонів на добу (кожні 4 години)
# дає запас 6000/добу, з надлишком навіть коли скан дійде до повного
# покриття ~17768 SKU.
ROTATED_OUT_BATCH_LIMIT = 1000

# ДОДАНО (2026-07-20, аудит PR #111 — code_report_2026-07-20_pt15.md):
# _rotated_out_needing_live_lookup() (Шлях 2 — SKU поза топ-970 БЕЗ
# даних нічного скану, обробляються повним живим пайплайном) НЕ мав
# жодного явного ліміту розміру, на відміну від ROTATED_OUT_BATCH_LIMIT
# вище. Обґрунтування "малий, самообмежений набір — скан наздожене" не
# гарантоване структурно: сам нічний скан (full-catalog-scan.service)
# провалювався кілька ночей поспіль того самого тижня (виправлено
# окремо, не тут) — якщо це повториться, набір міг би зростати без
# стелі, повністю мережевий/SEARCH_DELAY-гейтований пайплайн. Менший за
# ROTATED_OUT_BATCH_LIMIT (той — дешевий, без мережі; цей — з живими
# запитами до пошуку/buyBox/presence-перевірки на кожен SKU) — за
# ~1-2с/SKU (SEARCH_DELAY + мережеві виклики) 300 — це до ~10 хв
# додатково на прогін, прийнятно для сервісу кожні 4 години.
LIVE_LOOKUP_EXTRA_BATCH_LIMIT = 300

# ДОДАНО (2026-07-25, живий root-cause скасованого 5.5-годинного прогону
# 30156977509): _recheck_delisted_pids() не мав ЖОДНОГО ліміту партії —
# докстрінг обіцяв "типово в рази менша за весь топ-970", але це
# припущення структурно зламалось того ж дня: PR #163 додав механізм,
# що може позначити ТИСЯЧІ SKU delisted за один прогін (живо: 5192 за
# раз), а ця функція робить find_best_competitor() (жива мережа) +
# SEARCH_DELAY на КОЖЕН pid у всій множині без стелі. З delisted-
# множиною, що тепер може налічувати тисячі записів, повний прохід сам
# по собі займає години — незалежно від основної ротаційної партії
# (ROTATION_BATCH_SIZE) — і саме це, а не описана раніше гіпотеза про
# застарілий кеш, і є справжньою причиною, чому прогін впирається в
# 6-годинний timeout GitHub Actions і ніколи не публікує результат.
# Той самий патерн ротації, що й ROTATION_BATCH_SIZE/LIVE_LOOKUP_EXTRA_
# BATCH_LIMIT — найстаріші позначки (за _delisted_since timestamp)
# першими, гарантований прогрес по всій множині за кілька прогонів.
RECHECK_DELISTED_BATCH_LIMIT = 500

# ДОДАНО (2026-07-22, живий приклад SKU 302166/302185, живо знайдено власницею
# через скріншоти "звичайної рандомної вибірки", де наша ціна помітно вища за
# видимих конкурентів): own_product_links_cache.json (джерело buyBox для
# find_best_competitor()) ДОСІ РАХУЄ ЛИШЕ generate_google_feed.py — цей файл
# лише ЧИТАЄ його (_load_own_product_links_cache() вище, пасивно). Живо
# підтверджено: 969/970 (99.9%!) поточного топ-970 НЕ мають own_link, тобто
# find_best_competitor() для майже всього каталогу йде в НЕНАДІЙНИЙ fallback
# (текстовий GraphQL-пошук), не в buyBox — саме той клас помилки, що вже
# ставався мені самій цієї сесії (перевірка конкурентності без own_link).
# generate_google_feed.py рахує кеш лише для СВОГО топ-970 знімку на СВІЙ
# розклад — рідко збігається з топ-970 САМЕ цього прогону (ротація щодня).
#
# Фікс: цей файл ТЕПЕР сам донараховує own_link для SKU з ПОТОЧНОГО
# батчу (items нижче), яких нема в кеші — той самий детермінований механізм
# (fetch_prom_products_by_external_ids + resolve_own_product_links), що вже
# перевірений і використовується generate_google_feed.py, лише importовано
# (лінивий імпорт УСЕРЕДИНІ функції — generate_google_feed.py імпортує
# SEARCH_DELAY ЗВІДСИ на своєму верхньому рівні, тож прямий top-level імпорт
# звідти сюди дав би циклічний імпорт; лінивий імпорт всередині main()
# уникає цього, бо на момент виклику цей модуль уже повністю завантажений).
#
# Обмежено батчем за прогін (не всі 969 одразу) — той самий принцип
# self-throttling, що й ROTATED_OUT_BATCH_LIMIT/LIVE_LOOKUP_EXTRA_BATCH_LIMIT
# вище: кожен запис — 2 додаткові живі HTTP-запити (by_external_id +
# urlText-редирект).
#
# ПІДВИЩЕНО 200->400 (2026-07-22, пряме прохання власниці "чіткіше
# знаходити конкурентів" — швидше покриття buyBox): за 6 прогонів/добу
# (кожні 4 год) 400/прогін покриває ~970 SKU менш ніж за добу (раніше —
# трохи більше доби), удвічі швидше закриває прогалину "969/970 без
# buyBox". Навантаження все одно бюджетоване (SEARCH_DELAY-джитер між
# запитами, той самий механізм, що вже прийнятний для 970 SKU/добу
# основного циклу).
OWN_LINK_RESOLVE_BATCH_LIMIT = 400

MATCH_MIN_SCORE = 0.4          # SequenceMatcher ratio — поріг для "adjust" (низька ставка: помилковий
                                # збіг лише трохи спотворює ціну, самокоригується наступним прогоном)
PRICE_SANITY_MIN_RATIO = 0.15  # кандидат дешевший за 15% нашої собівартості — швидше за все, інший товар/акс.
PRICE_SANITY_MAX_RATIO = 6.0   # кандидат дорожчий у 6x — так само, ймовірно інший товар/гурт-лот

# Окремий, СУВОРІШИЙ поріг для "delist" — підтверджено на реальному dry-run
# (150 SKU топ-970, 2026-07-11): при MATCH_MIN_SCORE=0.4 кілька "delist"-
# кандидатів виявились хибними збігами — SKU 254197 (наш товар 11 см)
# зіставлено з конкурентом 7 см (score=0.67, різний розмір); SKU 267139
# зіставлено з конкурентом за 28 грн (score=0.83) — підозріло дешево для
# водяного пістолета, ймовірно інший товар чи хибна ціна в конкурента.
# Коригування ціни при хибному збігу — дешева помилка (самокоригується
# наступним прогоном). Видалення живого оголошення при хибному збігу —
# НЕ дешева помилка (втрата рейтингу/відгуків, не відкочується
# автоматично) — тому delist вимагає майже точного текстового збігу.
MATCH_MIN_SCORE_FOR_DELIST = 0.85

# ДОДАНО (2026-07-21, живий ручний аудит власниці — 4 SKU дорожчі за
# ринок): коментар біля MATCH_MIN_SCORE вище стверджував "помилковий
# збіг... самокоригується наступним прогоном" — це виявилось НЕ так на
# практиці. Живо перевірено 4 SKU (209101/239803/275296/298613): для
# 3 з 4 наш пошук знайшов явно ІНШИЙ товар (score 0.44-0.56) —
# найяскравіший приклад: SKU 275296 (електронний планшет Paw Patrol)
# зіставлено з "Дитячий пазл Paw Patrol... 70 елементів" (score=0.44) —
# ПАЗЛ, не планшет. Ціна на основі цього "конкурента" застаріла ще з
# 07-18 і жодного разу не "самокоригувалась" — бо повний прогін
# репрайсера відбувається раз на ~20 год (MIN_FULL_RUN_INTERVAL_HOURS),
# а не на кожному 4-годинному тригері, і топ-970 ротується, тож той
# самий SKU часто НЕ потрапляє в наступний прогін узагалі, поки не
# повернеться. Квантифіковано: 3055/8982 (34%) просканованих SKU мають
# score<0.6 — той самий ризик у масштабі.
#
# Рішення власниці: підняти поріг ІМЕННО для розрахунку ЦІНИ (не для
# самого прийняття "чи взагалі є кандидат" — MATCH_MIN_SCORE=0.4 і далі
# визначає, що find_best_competitor() взагалі розглядає). Нижче цього
# порогу конкурент трактується як "не знайдено достатньо впевнено" для
# ЦІНОУТВОРЕННЯ — decide_price_for_platform() отримує None замість його
# ціни, падає на безпечну "немає конкурента" формулу, а НЕ на floor,
# розрахований проти, можливо, зовсім іншого товару. НЕ впливає на
# MATCH_MIN_SCORE_FOR_DELIST (0.85) — це окремий, суворіший поріг саме
# для delist, лишається без змін.
MATCH_MIN_SCORE_FOR_PRICING = 0.6

# ДОДАНО (2026-07-21, пряме прохання власниці): "рятує" кандидатів, що не
# пройшли MATCH_MIN_SCORE_FOR_PRICING за текстом (0.4-0.59), якщо ФОТО
# підтверджує той самий товар — фото зазвичай ідентичне між продавцями
# одного товару (той самий постачальник), тоді як назва в кожного своя.
# Перцептивний хеш (imagehash.phash, 64-біт) — стійкий до масштабування/
# перестиснення/легкого кадрування, на відміну від точного побайтового
# порівняння. Поріг звірено на реальному прикладі (SKU 275296, 2026-07-21):
# справжні конкуренти (той самий планшет, інший колір) — відстань 2;
# хибно знайдений текстом пазл (зовсім інший товар) — відстань 22-28.
# 10 — з великим запасом нижче найменшої спостереженої "різний товар"
# відстані (22), і з великим запасом вище найбільшої спостереженої
# "той самий товар" відстані (2) у цій вибірці.
PHOTO_MATCH_MAX_DISTANCE = 10

# ДОДАНО (2026-07-22, "чіткіше знаходити конкурентів"): скільки найдешевших
# ДОВІРЕНИХ (score>=MATCH_MIN_SCORE_FOR_PRICING) кандидатів у
# find_best_competitor() перевіряти фото-збігом, перш ніж здатися й
# повернутись до сліпого "найдешевший за текстом". Кожна перевірка —
# 1-2 живих мережевих запити (own_pictures × кандидат, з раннім виходом
# на першому збігу) — 5 кандидатів обмежує додатковий час на SKU
# розумною межею (гірший випадок ~5x вартості однієї фото-перевірки),
# не перевіряючи весь пул (може бути й 20+ кандидатів).
PHOTO_VERIFY_CANDIDATE_LIMIT = 5


def _fetch_image_phash(url: str) -> "imagehash.ImageHash | None":
    """Завантажує зображення й рахує перцептивний хеш. None (не виняток)
    на БУДЬ-яку помилку (мережа/таймаут/невалідний формат зображення) —
    той самий безпечний дефолт, що й скрізь у цьому файлі: не можемо
    підтвердити — не довіряємо, а не вгадуємо."""
    if not url:
        return None
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        image = Image.open(BytesIO(response.content))
        return imagehash.phash(image)
    except Exception as e:
        print(f"[Pricer] Не вдалось порахувати хеш зображення {url!r}: {e}", file=sys.stderr)
        return None


def _photo_confirms_match(own_pictures: list | None, competitor_image: str | None) -> bool:
    """True якщо перцептивна відстань МІЖ БУДЬ-ЯКИМ з own_pictures і фото
    кандидата <= PHOTO_MATCH_MAX_DISTANCE (перший збіг зупиняє перебір).
    Перевіряємо всі фото, а не лише own_pictures[0]: перше фото Toysi
    часто колаж/демо-фото з рукою (саме так було пропущено реальний
    збіг для SKU 302166 — акуратне одиночне фото конкурента не
    співпало з композиційно іншим першим фото), а пізніше фото в тому
    ж списку буває чистим одиночним кадром товару. На відміну від
    generate_google_feed.py, де prom_product.get("main_image")
    пріоритизується НАД Toysi-фото, тут такої пріоритизації немає —
    беремо own_pictures напряму. Будь-яка невизначеність (немає жодного
    свого фото, немає фото кандидата, усі хеші не порахувались) ->
    False — не рятуємо кандидата "про всяк випадок", лишаємо на
    безпечний дефолт MATCH_MIN_SCORE_FOR_PRICING (немає конкурента для
    ціноутворення)."""
    if not own_pictures or not competitor_image:
        return False
    competitor_hash = _fetch_image_phash(competitor_image)
    if competitor_hash is None:
        return False
    for own_picture in own_pictures:
        own_hash = _fetch_image_phash(own_picture)
        if own_hash is None:
            continue
        if (own_hash - competitor_hash) <= PHOTO_MATCH_MAX_DISTANCE:
            return True
    return False


# ВИДАЛЕНО (2026-07-18, пряме рішення власника): "грація" 1.5x (floor міг
# перевищувати конкурента аж на 50%, лишаючись просто "дорожчою
# пропозицією", не delist) — власник прямо підтвердив: жодної грації,
# якщо floor вище за (конкурент - 1 грн), товар видаляється з вітрини,
# без порогу "наскільки вище". MAX_FLOOR_TO_COMPETITOR_RATIO більше не
# використовується.

# P0-3 (2026-07-17): circuit breaker для --apply. Прецедент, що спричинив
# цю задачу: один прогін змінив ціну на 949/970 позицій одразу, середня
# маржа каталогу впала з 35.72% до 3% — жодна наявна перевірка цього не
# ловила, бо кожна окрема ціна проходила формулу decide_price_for_platform()
# коректно; проблема була в МАСШТАБІ й АГРЕГАТІ за один прогін, не в
# окремому рішенні. Три незалежні сигнали (будь-який один зупиняє --apply
# ПЕРЕД тим, як щось реально застосується):
#   1. Середня маржа цього прогону нижче абсолютного порогу.
#   2. Середня маржа впала занадто різко відносно попереднього прогону
#      (зберігається в price_state["_meta"]["last_avg_margin_pct"]).
#   3. Завелика частка товарів із ВЖЕ відомою ціною (price_state) раптом
#      отримує СУТТЄВО іншу ціну за один прогін.
#
# ВИПРАВЛЕНО (2026-07-22, пряма вимога власниці "вирішить системно" —
# сигнал 3 спрацьовував ВДРУГЕ поспіль одразу після легітимної зміни
# ціноутворення: вперше після PR #122 (ProSale econom-множник,
# 2026-07-21, форсовано через --force-circuit-breaker за прямим
# дозволом власниці), вдруге одразу після PR #126/#127 (два-рівневий
# вибір конкурента + token-sort схожість): 73% товарів змінили ціну
# >5% за прогін, СЕРЕДНЯ МАРЖА ПРИ ЦЬОМУ БУЛА ЗДОРОВОЮ (47%, далеко
# вище порогу 8%) — сигнали 1/2 (реальна небезпека: маржа завалюється)
# НЕ спрацювали, спрацював лише сигнал 3 (масштаб/кількість змін), який
# не розрізняв "знайшли КРАЩОГО конкурента" (мета самого фіксу) від
# "той самий конкурент раптом видає геть іншу ціну" (реальна ознака
# бага парсингу/скрапінгу). Тепер сигнал 3 порівнює ціну лише коли
# ОБИДВА прогони мали ту саму ІДЕНТИЧНІСТЬ джерела конкурента
# (price_state[pid]["competitor_key"], _competitor_identity_key() —
# "buybox" чи конкретний Prom id) — новий/інший/раніше відсутній
# конкурент більше не рахується як "суттєва зміна", а лише РЕАЛЬНИЙ
# стрибок ціни ТОГО САМОГО оголошення далі зупиняє --apply. Сигнали 1/2
# (маржа) НЕ чіпались — вони й далі ловлять справжню небезпеку (каталог
# стає збитковим) незалежно від того, чи змінилась ідентичність
# конкурента.
#
# ВИПРАВЛЕНО (2026-07-22, друге питання власниці в тій самій розмові —
# "чого 8% наче у нас 3% було?"): поріг сигналу 1 раніше був відірваним
# магічним числом 8.0, вписаним 2026-07-17 — через 4 дні ПІСЛЯ того, як
# власниця напряму вирішила знизити floor для конкурентних SKU з 25% до
# 3% (MIN_PROFIT_COMPETITOR_FLOOR, competitor_pricing.py, 2026-07-13),
# і ніколи не звірявся з цим рішенням. Наслідок: коли РЕАЛЬНО багато
# SKU законно впираються у власний дозволений floor 3% (саме те, для
# чого MIN_PROFIT_COMPETITOR_FLOOR і існує — агресивна конкурентна
# ціна), середня маржа прогону структурно опиняється БЛИЗЬКО до 3%,
# набагато нижче старого 8% — сигнал 1 бив тривогу на здоровій,
# очікуваній роботі системи, не на реальній проблемі. Тепер поріг
# ПРИВ'ЯЗАНИЙ напряму до MIN_PROFIT_COMPETITOR_FLOOR (той самий
# коефіцієнт, що й у compute_floor()) — якщо власниця колись знову
# свідомо змінить floor, цей поріг автоматично оновиться разом з ним,
# без окремого PR. `compute_floor()` тепер повертає ЧИСТО відсотковий
# floor (абсолютний гривневий шар видалено 2026-07-23, знахідка
# "cheap-sku-floor-margin-conflict") — тобто РЕАЛЬНА маржа окремого SKU
# НІКОЛИ не має структурної причини впасти НИЖЧЕ цього відсотка; сигнал 1
# тепер ловить САМЕ це — середню маржу, що впала нижче гарантованого
# мінімуму (ознака реального бага у формулі/застосуванні), а не "просто
# багато SKU на своєму ж дозволеному floor".
CIRCUIT_BREAKER_MIN_AVG_MARGIN_PCT = MIN_PROFIT_COMPETITOR_FLOOR * 100
CIRCUIT_BREAKER_MAX_MARGIN_DROP_PCT = 15.0
CIRCUIT_BREAKER_PRICE_CHANGE_THRESHOLD = 0.05
CIRCUIT_BREAKER_MAX_CHANGED_FRACTION = 0.5
# Менше цього — надто мало даних, щоб частка "змінилось" щось значила
# (напр. 2 з 3 — 67%, виглядає тривожно, але це шум малої вибірки).
CIRCUIT_BREAKER_MIN_KNOWN_FOR_FRACTION_CHECK = 10

# ДОДАНО (2026-07-18, незалежний аудит PR #95 — code_report_2026-07-
# 18_pt4.md, критична знахідка): три сигнали вище рахують ЛИШЕ
# to_adjust — жоден з них НІКОЛИ не бачить delist взагалі. PR #95 (той
# самий прогін, після якого сесія додала цей фікс) зробив delist
# структурно можливим у масовому масштабі (361/970 = 37% у реальному
# dry-run) — без цього сигналу такий сплеск ВИДАЛЕНЬ пройшов би повз
# breaker непоміченим, навіть якщо середня маржа й частка змінених ЦІН
# (у to_adjust) виглядають абсолютно нормально. Поріг НЕ підтверджений
# власником як конкретне число (та сама категорія, що й
# MAX_FLOOR_TO_COMPETITOR_RATIO раніше) — консервативний початковий
# дефолт, підлягає підтвердженню.
CIRCUIT_BREAKER_MAX_DELIST_FRACTION = 0.15

# ДОДАНО (2026-07-19, живий інцидент + пряма знахідка Cowork): окремий,
# ЖОРСТКИЙ запобіжник ПОВЕРХ circuit breaker — не частка сигналів, які
# --force-circuit-breaker свідомо може обійти (той механізм ІСНУЄ саме
# для того, щоб власниця могла оглянути причини й свідомо продовжити).
# Цей ліміт — інша категорія: forced-прогін 29677266900 видалив 722
# товари за раз (76% > 15%, форсовано за прямим дозволом власниці) —
# кабінет обвалився з ~970 до 244 живих товарів на кілька годин, бо Prom
# створює товари-замінники значно повільніше, ніж видаляє. Навіть
# свідомий, поінформований override circuit breaker'а не повинен мати
# можливість повторити рівно цей сценарій одним прапорцем командного
# рядка — тому ця перевірка НЕ входить у evaluate_circuit_breaker() і НЕ
# перевіряє args.force_circuit_breaker взагалі (див. виклик у main()).
# Підняти ліміт можна лише прямою зміною цього числа в коді (новий PR,
# новий аудит, нова свідома власницька перевірка) — не прапорцем.
# ПОВЕРНУТО до 150 (2026-07-19, одразу після контрольованого прогону
# 29697359677): тимчасове підняття до 750 виконало свою одноразову
# задачу — повторно видалило 706 товарів, воскреслих через рейс-стан
# (PR #102, вже закрито), живо підтверджено (усі 10 початкових SKU —
# 404, `_delisted_since` персистентно містить 706 записів, без
# повторного обнулення). 150 знову захищає від повторення оригінального
# інциденту (722 видалення за один прогін, обвал каталогу 970→244).
# 100-150 — рекомендований діапазон Cowork, звичайний природний
# щоденний цикл рідко видаляє більше кількох десятків товарів за раз.
#
# ПІДНЯТО до 250 (2026-07-21, пряме рішення власниці, run 29762553500,
# 2026-07-20 ~21:19): прогін дав adjust=469, delist=213 — 150 зупинив
# УВЕСЬ прогін (жоден із 469 легітимних коригувань ціни теж НЕ
# застосувався, не лише delist). На відміну від інциденту 2026-07-19
# (722 видалення — воскреслі через баг рейс-стану, PR #102, штучний
# сплеск), ці 213 — переважно (300/682 через buyBox, тобто читання
# напряму з ВЛАСНОЇ сторінки товару, structurally не може бути хибним
# збігом) РЕАЛЬНІ, підтверджені неконкурентні позиції: середня маржа
# прогону впала з 43.7% до 28.6% — найімовірніша причина: PR #111/#112
# (фікс ротації топ-970) щойно почав переоцінювати SKU, які місяцями
# "їхали" на застарілій конкурентній ціні, і частина виявилась давно
# неконкурентною за поточним ринком. 250 — з запасом покриває поточний
# бэклог (213) плюс природне коливання наступних прогонів, лишаючись
# на порядок нижче за 722, що спричинило обвал каталогу 970→244.
# ТИМЧАСОВО ПІДНЯТО до 350 (2026-07-22, пряме рішення власниці, run
# 29895848220): той самий прогін дав delist=324 — 250 зупинив ВСІ 324
# видалення одразу (жорсткий ліміт не розрізняє "трохи більше" від
# "набагато більше", блокує повністю). На відміну від інциденту
# 2026-07-19 (722 видалення — воскреслі через баг рейс-стану, PR #102,
# штучний сплеск), ці 324 — переважно buyBox-джерело (structurally не
# може бути хибним збігом, читання напряму з ВЛАСНОЇ сторінки товару,
# приклад SKU 113932 — floor 322₴ проти реального мінімуму 289₴ серед
# 13 живих продавців) — прямий, очікуваний наслідок сьогоднішніх фіксів
# ідентифікації (PR #126/#127/#128): вперше видно СПРАВЖНІЙ масштаб
# накопиченої неконкурентності, який раніше ховався за гіршим
# зіставленням конкурентів. 350 — з невеликим запасом покриває поточний
# бэклог (324), лишаючись на порядок нижче за 722, що спричинило обвал
# каталогу 970→244. ОДИН контрольований прогін — повернути назад до 250
# одразу після (той самий патерн, що й 750->150 2026-07-19).
MAX_DELIST_PER_RUN = 350

EDIT_BATCH = 100  # POST /products/edit_by_external_id, як і в prom_catalog_sync.py

# P0-5: раз на стільки успішно застосованих коригувань ціни зберігати
# price_state на диск (не лише один раз наприкінці всього циклу).
SAVE_EVERY = 25

# Мінімальна GraphQL-схема — ЛИШЕ поля, що реально використовуються тут.
# Свідомо не той величезний (11.7К символів) запит, яким сама сторінка
# пошуку тягне SEO-теги/фільтри/мотори тощо, — вужчий контракт, менший
# ризик поламатись, якщо Prom змінить поля, які нас не цікавлять.
#
# ДОДАНО (2026-07-21, пряме прохання власниці — живий ручний аудит
# виявив хибні текстові збіги, MATCH_MIN_SCORE_FOR_PRICING/PR #113):
# `image(width, height)` — Introspection на цьому GraphQL вимкнено
# (перевірено напряму), тож поле знайдено пробним запитом полів-
# кандидатів; виявилось скалярним полем з ОБОВ'ЯЗКОВИМИ width/height
# (не типом-вузлом із власними під-полями, як спершу здавалось із
# помилки "Required option... is not specified"). Дає URL фото
# кандидата — фото зазвичай ідентичне між різними продавцями того
# самого товару (той самий постачальник/фото від виробника), тоді як
# назва в кожного продавця своя (переклад/скорочення/порядок слів) —
# надійніший сигнал збігу за текстову схожість, підтверджено живо
# (SKU 275296: наш планшет vs справжні конкуренти-планшети інших
# кольорів — phash-відстань 2; vs хибно знайдений пазл — 22-28).
SEARCH_QUERY = """
query SearchListingQuery($search_term: String!, $offset: Int, $limit: Int, $params: Any, $company_id: Int, $sort: String, $regionId: Int = null, $subdomain: String = null) {
  listing: searchListing(search_term: $search_term, limit: $limit, offset: $offset, params: $params, company_id: $company_id, sort: $sort, region: {id: $regionId, subdomain: $subdomain}) {
    page {
      total
      products {
        product {
          id
          name
          price
          priceCurrency
          company_id
          urlText
          presence { presence isAvailable }
          image(width: 200, height: 200)
        }
      }
    }
  }
}
""".strip()


def search_prom_products(search_term: str, limit: int = SEARCH_LIMIT) -> list:
    """Пошук на Prom.ua напряму через internal GraphQL (без браузера,
    без авторизації — підтверджено, ендпоінт публічний, той самий, яким
    користується власна SPA-сторінка пошуку)."""
    payload = {
        "operationName": "SearchListingQuery",
        "variables": {
            "search_term": search_term,
            "limit": limit,
            "offset": 0,
            "params": {"binary_filters": []},
            "regionId": None,
        },
        "query": SEARCH_QUERY,
    }
    try:
        response = requests.post(
            PROM_GRAPHQL_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "uk-UA,uk;q=0.9",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if "errors" in data:
            print(f"[Pricer] GraphQL помилка для {search_term!r}: {data['errors']}", file=sys.stderr)
            return []
        products = data.get("data", {}).get("listing", {}).get("page", {}).get("products", [])
        return [p["product"] for p in products if p.get("product")]
    except (requests.exceptions.RequestException, ValueError, KeyError) as e:
        print(f"[Pricer] Помилка пошуку {search_term!r}: {e}", file=sys.stderr)
        return []


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (name or "").lower(), flags=re.UNICODE)).strip()


def _token_sort_key(name: str) -> str:
    """Токени нормалізованої назви, відсортовані алфавітно й склеєні назад
    у рядок — той самий трюк, що й "token sort ratio" в бібліотеках на
    кшталт fuzzywuzzy, без нової залежності (лише stdlib difflib)."""
    return " ".join(sorted(_normalize_name(name).split()))


def _similarity(a: str, b: str) -> float:
    """СИСТЕМНЕ ПОКРАЩЕННЯ (2026-07-22, пряма вимога власниці "виправити
    системно" — за тиждень практично не бачили конкурентів): чистий
    посимвольний SequenceMatcher (як було) катастрофічно занижує схожість,
    коли дві назви описують ТОЙ САМИЙ товар іншим порядком слів чи з
    зайвими/відсутніми словами-наповнювачами (типово для різних продавців
    одного товару Toysi) — SequenceMatcher бачить це як велику "вставку/
    видалення" в середині рядка, хоча зміст ідентичний. Живий приклад
    (SKU 145422 "Каталка Мотоцикл, рожевий"): конкурент "Каталка Мотоцикл,
    розовый" мав би score близько 1.0 при порівнянні за словами, але
    порядок символів трохи інший через кирилицю/розкладку.

    Береться МАКСИМУМ із двох метрик:
    - "сирий" посимвольний ratio (стара поведінка, не гіршає ні для
      одного випадку, де вона й так була найкращою);
    - ratio між версіями з відсортованими словами (не бачить різниці в
      ПОРЯДКУ слів взагалі, все ще чутливий до РІЗНИХ слів — два
      набори токенів, що не перетинаються, дадуть низький ratio навіть
      посортовані).

    Обидва компоненти — ПОСИМВОЛЬНІ метрики на нормалізованому тексті,
    жодна семантика/синоніми не додається (і не мала б — "лялька" й
    "пупс" лишаються різними словами, як і мають бути) — це вужче,
    контрольованіше покращення, ніж повна заміна на embedding-подібний
    підхід, з передбачуваним, тестованим ефектом лише на порядок/зайві
    слова.

    ВАЖЛИВО: підвищення score саме собою МОЖЕ інфляціювати оцінку для
    товарів з тими самими "родовими" словами (типу "іграшка антистрес
    для дітей"), але РІЗНИМ конкретним розміром/об'ємом — той ризик
    покриває окремий гейт _size_tokens_conflict(), тепер застосований і
    до довіри ЦІНІ (не лише delist, як раніше), див. decide_action()."""
    raw_ratio = difflib.SequenceMatcher(None, _normalize_name(a), _normalize_name(b)).ratio()
    token_ratio = difflib.SequenceMatcher(None, _token_sort_key(a), _token_sort_key(b)).ratio()
    return max(raw_ratio, token_ratio)


# Цільовий гейт проти конкретного класу хибних збігів, знайденого аудитом
# (2026-07-11): SequenceMatcher — посимвольна метрика, не має жодного
# розуміння, що "45 см" -> "25 см" — суттєва відмінність товару, а не
# просто інший символ у довгому, інакше майже ідентичному рядку. Назва
# ~50 символів, що відрізняється лише цим токеном, легко дає ratio
# ~0.96-0.98 — набагато вище MATCH_MIN_SCORE_FOR_DELIST (0.85), хоча
# товари можуть бути суттєво різними за розміром/об'ємом/вагою/кількістю.
#
# ВАЖЛИВО: обидва регулярні вирази застосовуються до СИРОГО (лише lower(),
# без _normalize_name) тексту — _normalize_name знищує "."/"," як
# розділові знаки, через що "45.5 см" і "25.5 см" губили цілу частину й
# давали ІДЕНТИЧНИЙ токен "5см" (знахідка аудиту, code_report pt14,
# пункт 1). Розділник дробової частини має лишатись у вихідному тексті на
# момент застосування регулярки.
SIZE_UNIT = r"(?:см|мм|м|л|мл|кг|г|шт|штук|дюйм)"

# Багатовимірні розміри ("30х20х15 см", "10x20 см") — окремий патерн ДО
# простого, бо простий SIZE_TOKEN_RE сам по собі бачить лише ОСТАННЄ число
# перед одиницею ("15" з "30х20х15 см"), тож "30х20х15 см" і "10х20х15 см"
# давали однаковий токен (знахідка аудиту, pt14, пункт 2). Захоплює 2 або
# 3 числа, з'єднані x/х, разом з одним спільним юнітом наприкінці.
MULTI_DIM_RE = re.compile(
    rf"(\d+(?:[.,]\d+)?)\s*[xх]\s*(\d+(?:[.,]\d+)?)(?:\s*[xх]\s*(\d+(?:[.,]\d+)?))?\s*({SIZE_UNIT})\b",
    re.IGNORECASE,
)
SIZE_TOKEN_RE = re.compile(rf"(\d+(?:[.,]\d+)?)\s*({SIZE_UNIT})\b", re.IGNORECASE)


def _extract_size_tokens(name: str) -> set:
    text = (name or "").lower()
    tokens = set()
    consumed_spans = []

    # Багатовимірний збіг стає ОДНИМ комбінованим токеном ("30x20x15см"),
    # НЕ трьома окремими одновимірними ("30см","20см","15см") — інакше
    # порівняння множин через isdisjoint() дає хибний НЕГАТИВ, якщо виміри
    # частково збігаються не по позиції: "30х20х15 см" і "10х20х15 см" як
    # окремі токени {15см,20см,30см} і {10см,20см,15см} ПЕРЕТИНАЮТЬСЯ
    # (спільні "15см"/"20см"), тож isdisjoint() хибно повернув би False
    # (не конфліктує), хоча перший вимір реально інший. Об'єднаний рядок
    # зберігає порядок вимірів, тож "30x20x15см" != "10x20x15см" коректно.
    for m in MULTI_DIM_RE.finditer(text):
        nums = [g for g in (m.group(1), m.group(2), m.group(3)) if g]
        unit = m.group(4)
        combined = "x".join(n.replace(",", ".") for n in nums) + unit
        tokens.add(combined)
        consumed_spans.append(m.span())

    # Простий "число+юніт" — лише поза вже захопленими багатовимірними
    # збігами (інакше останній вимір з "30х20х15 см" додався б іще раз
    # окремим одновимірним токеном "15см", повертаючи ту саму проблему).
    for m in SIZE_TOKEN_RE.finditer(text):
        if any(start <= m.start() < end for start, end in consumed_spans):
            continue
        num, unit = m.group(1), m.group(2)
        tokens.add(f"{num.replace(',', '.')}{unit}")

    return tokens


def _size_tokens_conflict(our_name: str, competitor_name: str) -> bool:
    """True, якщо в ОБОХ назвах є числові/розмірні токени (см/мм/л/мл/кг/
    г/шт/дюйм), і ці набори НЕ перетинаються — ознака, що товари різняться
    суттєвою характеристикою, навіть при високій текстовій схожості всього
    рядка. Якщо токенів немає з одного чи обох боків — конфлікт НЕ
    підтверджений (не можемо довести розбіжність із відсутніх даних), тому
    delist не блокується цим гейтом — рішення лишається на MATCH_MIN_SCORE_FOR_DELIST."""
    our_tokens = _extract_size_tokens(our_name)
    comp_tokens = _extract_size_tokens(competitor_name)
    if not our_tokens or not comp_tokens:
        return False
    return our_tokens.isdisjoint(comp_tokens)


def find_best_competitor(
    search_name: str, cost: float, own_link: dict | None = None, own_pictures: list | None = None,
) -> dict | None:
    """Шукає конкурента для SKU. ОСНОВНЕ джерело (2026-07-14) — buyBox
    нашої ж сторінки товару (fetch_buybox_competitor), якщо `own_link`
    ({"prom_id", "url_text"} з own_product_links_cache.json) заданий і дає
    результат — це власне зіставлення товарів Prom (product "model"),
    надійніше за текстовий пошук. ФОЛБЕК — стара логіка SearchListingQuery
    + fuzzy-збіг нижче, коли buyBox недоступний (own_link відсутній —
    self-match ще не знайдено для цього SKU generate_google_feed.py, чи
    buyBox не дав жодного іншого продавця).

    Пошукова гілка: виключає власні товари й товари поза розумним ціновим
    діапазоном, повертає НАЙДЕШЕВШОГО серед підтверджених (score >=
    MATCH_MIN_SCORE) кандидатів, або None, якщо жоден не проходить поріг
    впевненості — у цьому разі ціна рахується формульно (як для
    "no_competitor" в decide_price_for_platform), а НЕ вгадується з
    ненадійного збігу.

    ВИПРАВЛЕНО (підозра власника, підтверджена на SKU 242610 — 2026-07-13):
    раніше сортування було `(-score, price)` — серед кандидатів, що
    пройшли поріг впевненості MATCH_MIN_SCORE, обирався НАЙВИЩИЙ за
    текстовою схожістю, а ціна була лише тай-брейкером для РІВНИХ score
    (що з float score практично ніколи не трапляється). Це означало:
    справді найдешевший конкурент (напр. Gummy, 1362 грн) міг мати трохи
    нижчий score, ніж дорожчий (1830 грн), і програвати вибір — хоча обидва
    вже "той самий товар" за порогом впевненості. Score тут — лише фільтр
    "чи це взагалі той самий товар", не критерій вибору МІЖ підтвердженими
    збігами; серед них правильний орієнтир для ціноутворення — найдешевший
    реальний варіант, який покупець міг би обрати замість нас.

    ВИПРАВЛЕНО ЗНОВУ (2026-07-22, живий приклад SKU 302166, дослідження
    Cowork + Code Desktop): "найдешевший серед score>=MATCH_MIN_SCORE"
    (0.4) — недостатньо суворо. Серед 20 кандидатів для 302166 найдешевший
    (180₴) мав score лише 0.55 (нижче MATCH_MIN_SCORE_FOR_PRICING=0.6, тож
    decide_action() коректно НЕ довіряв би його ціні) — АЛЕ серед ТИХ САМИХ
    20 кандидатів були точні/майже точні збіги (score 0.90-1.00) лише
    трохи дорожчі (193-198₴ проти 180₴), які функція раніше НІКОЛИ не
    бачила — повертався лише ОДИН, найдешевший загалом кандидат, і коли
    він провалював поріг довіри, конкурент вважався "не знайденим"
    ПОВНІСТЮ, хоча надійна дешева альтернатива була просто на відстані
    одного кроку в тому самому списку. Тепер: спершу шукаємо найдешевшого
    СЕРЕД довірених (score>=MATCH_MIN_SCORE_FOR_PRICING) — і лише якщо
    жодного немає, падаємо назад на стару поведінку (найдешевший серед
    score>=MATCH_MIN_SCORE, як і раніше, все одно піде через звичайний
    гейт довіри в decide_action()). Для випадків, де найдешевший ВЖЕ
    пройшов би поріг довіри, результат ідентичний попередній поведінці —
    зміна впливає лише на випадки на кшталт 302166."""
    if own_link:
        buybox = fetch_buybox_competitor(own_link["prom_id"], own_link["url_text"])
        if buybox is not None:
            return buybox

        # ДОДАНО (2026-07-24): recommended() як проміжний фолбек, лише коли
        # buyBox нічого не дав — див. коментар біля fetch_recommended_products()
        # вище щодо чому саме тут і чому це не подвоює навантаження на всі SKU.
        time.sleep(SEARCH_DELAY)
        recommended_raw = fetch_recommended_products(own_link["prom_id"])
        recommended = _rank_competitor_candidates(
            recommended_raw, search_name, cost, own_pictures, check_presence=False,
        )
        if recommended is not None:
            recommended["source"] = "recommended"
            return recommended

    # ДОДАНО (2026-07-17, знайдено при точковому скані SKU 300391 поза
    # чергою): SearchListingQuery-фолбек не фільтрував конкурентів за
    # маркерами уцінки/пошкодження в НАЗВІ — уцінений/пошкоджений
    # товар конкурента (типово найдешевший, бо не в товарному вигляді)
    # легко проходить MATCH_MIN_SCORE і стає "найдешевшим кандидатом",
    # штучно занижуючи floor/margin для товару, що насправді не
    # порівнюваний. Той самий список маркерів, що вже фільтрує НАШ
    # власний каталог у competitor_pricing.py's select_batch(). Той самий
    # ланцюжок перевірок тепер спільний з recommended() у
    # _rank_competitor_candidates() вище — включно з фото-підтвердженням
    # серед довірених кандидатів (2026-07-22, пряме прохання власниці
    # "чіткіше знаходити конкурентів").
    results = search_prom_products(search_name)
    return _rank_competitor_candidates(results, search_name, cost, own_pictures, check_presence=True)


# Знайдено в JSON-LD (schema.org Product) на реальній сторінці товару,
# напр. `"availability":"http://schema.org/OutOfStock"`. Простий regex по
# сирому HTML — свідомо не через BeautifulSoup: поле завжди в одному й
# тому ж JSON-подібному вигляді в <script type="application/ld+json">,
# повний DOM-парсинг заради одного поля не потрібен.
_AVAILABILITY_RE = re.compile(r'"availability"\s*:\s*"([^"]+)"')


def verify_competitor_really_available(competitor: dict) -> bool:
    """Пряма перевірка сторінки товару конкурента ПЕРЕД delist — GraphQL-
    пошук (presence.isAvailable) виявився ненадійним (2026-07-12): 2 дні
    поспіль SKU 298613/299070/299071 зіставлялись з тим самим конкурентом
    (company_id=3068206, "Toy and Joy"), чий presence.isAvailable в
    пошуковій видачі стабільно True, хоча РЕАЛЬНА сторінка товару містить
    schema.org `"availability":"http://schema.org/OutOfStock"` (підтверджено
    напряму curl-ом з VPS). Пошуковий індекс Prom, судячи з усього, не
    оновлює presence для окремих лістингів вчасно — покладатись лише на
    нього для НЕВІДКОТНОЇ дії (delist) недостатньо.

    Повертає True ЛИШЕ якщо сторінка конкурента прямо підтверджує
    "InStock". Будь-яка невизначеність (мережева помилка, відсутнє поле,
    інший статус на кшталт PreOrder/LimitedAvailability) -> False, тобто
    delist БЛОКУЄТЬСЯ — той самий принцип безпечного дефолту, що й у
    _size_tokens_conflict(): видалення живого оголошення при хибному
    сигналі значно дорожча помилка за просто неоптимальну ціну, тож
    вимагає позитивного підтвердження, а не лише відсутності заперечення.

    ПОРТОВАНО НА MASTER 2026-07-13 (задача про повний конкурентний скан
    каталогу): функція була написана, технічно перевірена (аудит-сесія,
    2026-07-12) і отримала пряму санкцію власника на деплой ще
    2026-07-12 — але сиділа на PR #32, що базувався на ІНШОМУ, теж не
    змердженому PR #31 (add-google-merchant-feed), тож НІКОЛИ фактично
    не потрапила в master. Через це щоденні прогони репрайсера сьогодні
    (2026-07-13, PR #40-48) виконувались БЕЗ цього захисту — той самий
    ненадійний presence.isAvailable, що спричинив баг 2026-07-12, міг
    знову вплинути на сьогоднішні delist-рішення. Портовано напряму
    (не через merge старих гілок — за день code в prom_competitor_pricer.py
    змінився занадто суттєво, конфлікт був би гарантований)."""
    product_id = competitor.get("id")
    url_text = competitor.get("urlText")
    if not product_id or not url_text:
        return False
    url = COMPETITOR_PRODUCT_URL_TEMPLATE.format(id=product_id, url_text=url_text)
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "uk-UA,uk;q=0.9",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[Pricer] Перевірка сторінки конкурента {url} не вдалась ({e}) — delist заблоковано", file=sys.stderr)
        return False
    match = _AVAILABILITY_RE.search(response.text)
    if not match:
        print(f"[Pricer] Сторінка конкурента {url}: поле availability не знайдено — delist заблоковано", file=sys.stderr)
        return False
    return match.group(1).endswith("/InStock")


def _competitor_identity_key(competitor: dict | None) -> str | None:
    """Стабільний ідентифікатор ДЖЕРЕЛА конкурентної ціни — не самої ціни,
    а ТОГО Ж САМОГО оголошення/сигналу, з якого вона порахована. "buybox"
    для buyBox (сумарний, без id конкретного оголошення, але завжди той
    самий ТИП сигналу для цього SKU), Prom `id` для текстового пошуку.
    None, якщо конкурента немає взагалі, чи ідентичність не збережена
    (шлях reuse-скану поза топ-970 не має id) — "не можемо довести" тут
    навмисно НЕ трактується як "те саме", див. use у evaluate_circuit_breaker()."""
    if not competitor:
        return None
    if competitor.get("source") == "buybox":
        return "buybox"
    cid = competitor.get("id")
    return str(cid) if cid is not None else None


def evaluate_circuit_breaker(to_adjust: list, to_delist: list, price_state: dict) -> tuple[list, list, float | None]:
    """P0-3: див. коментар біля CIRCUIT_BREAKER_* констант. `to_adjust` —
    список (pid, price, margin_pct, competitor_key, category, competitor_price,
    cost) кандидатів на коригування цього прогону (competitor_key — див.
    _competitor_identity_key(), ДОДАНО 2026-07-22; category/competitor_price/
    cost — ДОДАНО 2026-07-26, повний слід рішення для price_state, пряме
    прохання власниці "ми повинні записувати усі данні для майбутніх
    коригувань... що знати як формувалась ціна"). `to_delist` — список
    (pid, price, ...), той самий 7-елементний формат, що й `to_adjust`
    (ДОДАНО 2026-07-27, пряме зауваження власниці — "нехай вона більше
    показувала би та чекала на видалення, алеж не демпінгувала би": ціна
    тепер піднімається до безпечної межі ПЕРЕД спробою видалення,
    незалежно від того, чи саме видалення відбудеться цього прогону) —
    тут використовується лише `len()`, формат елементів не важливий.
    Призначених на видалення цього прогону (ДОДАНО 2026-07-18, незалежний
    аудит PR #95 —
    code_report_2026-07-18_pt4.md: до цього фіксу жоден із трьох сигналів
    нижче взагалі не бачив delist, і масовий сплеск видалень проходив би
    повз circuit breaker непоміченим). Повертає (delist_reasons,
    adjust_reasons, середня_маржа_цього_прогону) — середня маржа
    повертається завжди (навіть якщо обидва списки причин порожні чи
    to_adjust порожній), щоб main() міг зберегти її для порівняння
    НАСТУПНОГО прогону.

    ВИПРАВЛЕНО (2026-07-21, пряме прохання власниці — "чому блокувалось
    ПОВНА кількість... а не пропускав дозволену, а інші залишав"):
    раніше повертався ОДИН спільний bool/список причин, тож будь-яка
    причина (навіть суто про delist — CIRCUIT_BREAKER_MAX_DELIST_FRACTION)
    блокувала і to_adjust теж, хоча коригування ціни й видалення —
    структурно різні за ризиком дії (див. коментар біля
    MATCH_MIN_SCORE_FOR_DELIST: помилка в ціні дешева/самокоригується,
    помилка у видаленні — ні). Тепер причини розділені за тим, ЯКОГО
    саме кошика вони стосуються: частка delist — лише delist_reasons;
    середня маржа/її падіння/частка суттєвих змін ціни — усі рахуються
    ВИКЛЮЧНО з to_adjust, тож логічно належать adjust_reasons. main()
    гейтує кожен кошик своїми причинами окремо — аномалія в масштабі
    delist більше не тримає заручником непов'язаний, здоровий to_adjust.

    Перевірка масштабу delist рахується ПЕРШОЮ, до раннього виходу при
    порожньому to_adjust — інакше прогін, де ВСІ рішення виявились
    "delist" (найгірший можливий сценарій для цього сигналу), взагалі
    ніколи б не дійшов до перевірки нижче."""
    delist_reasons = []
    adjust_reasons = []
    total_decided = len(to_adjust) + len(to_delist)
    if total_decided and len(to_delist) / total_decided > CIRCUIT_BREAKER_MAX_DELIST_FRACTION:
        delist_reasons.append(
            f"{len(to_delist) / total_decided * 100:.0f}% товарів із визначеним конкурентом "
            f"({len(to_delist)}/{total_decided}) призначено на видалення за один прогін "
            f"(поріг {CIRCUIT_BREAKER_MAX_DELIST_FRACTION * 100:.0f}%)"
        )

    if not to_adjust:
        return delist_reasons, adjust_reasons, None

    avg_margin_this_run = sum(m for _, _, m, *_ in to_adjust) / len(to_adjust)

    known_count = 0
    changed_count = 0
    for pid, price, _, competitor_key, *_ in to_adjust:
        prior_entry = price_state.get(pid)
        prior_price = prior_entry.get("price") if isinstance(prior_entry, dict) else None
        if not prior_price:
            continue
        # ДОДАНО (2026-07-22, пряма вимога власниці "вирішити системно" —
        # цей сигнал двічі поспіль хибно спрацював саме тоді, коли фікс
        # ідентифікації РЕАЛЬНО знаходив кращих/нових конкурентів, не
        # коли щось зламалось): порівнювати ціну як "суттєву зміну" має
        # сенс лише якщо обидва прогони рахували її з ОДНОГО й того
        # самого джерела конкурента — інакше "змінилась ціна" насправді
        # означає "знайшли кращого конкурента", саме те, для чого й
        # існують find_best_competitor()/decide_action(). Стабільна
        # ідентичність джерела ("buybox" чи конкретний Prom `id`)
        # зберігається в price_state[pid]["competitor_key"] з
        # попереднього прогону (_competitor_identity_key()); None з
        # будь-якого боку (buyBox без id, reuse-скан поза топ-970, запис
        # ще ДО цього поля) чи розбіжність ключів -> НЕ можемо довести,
        # що це те саме джерело, тож виключаємо пару з обчислення замість
        # вгадування в будь-який бік (ні "known", ні "changed").
        prior_key = prior_entry.get("competitor_key")
        if prior_key is None or competitor_key is None or prior_key != competitor_key:
            continue
        known_count += 1
        if abs(price - prior_price) / prior_price > CIRCUIT_BREAKER_PRICE_CHANGE_THRESHOLD:
            changed_count += 1
    changed_fraction = changed_count / known_count if known_count else 0.0

    prev_avg_margin = (price_state.get("_meta") or {}).get("last_avg_margin_pct")

    if avg_margin_this_run < CIRCUIT_BREAKER_MIN_AVG_MARGIN_PCT:
        adjust_reasons.append(
            f"середня маржа цього прогону {avg_margin_this_run:.1f}% нижче абсолютного "
            f"порогу {CIRCUIT_BREAKER_MIN_AVG_MARGIN_PCT}%"
        )
    if prev_avg_margin is not None:
        margin_drop = prev_avg_margin - avg_margin_this_run
        if margin_drop > CIRCUIT_BREAKER_MAX_MARGIN_DROP_PCT:
            adjust_reasons.append(
                f"середня маржа впала на {margin_drop:.1f} п.п. відносно попереднього "
                f"прогону ({prev_avg_margin:.1f}% -> {avg_margin_this_run:.1f}%)"
            )
    if known_count >= CIRCUIT_BREAKER_MIN_KNOWN_FOR_FRACTION_CHECK and changed_fraction > CIRCUIT_BREAKER_MAX_CHANGED_FRACTION:
        adjust_reasons.append(
            f"{changed_fraction * 100:.0f}% товарів із уже відомою ціною ({changed_count}/{known_count}) "
            f"отримали суттєво іншу ціну (>{CIRCUIT_BREAKER_PRICE_CHANGE_THRESHOLD * 100:.0f}%) за один прогін"
        )

    return delist_reasons, adjust_reasons, avg_margin_this_run


def decide_action(
    cost: float,
    competitor: dict | None,
    category_name: str | None,
    our_name: str = "",
    prom_category_id: int | None = None,
    own_pictures: list | None = None,
) -> dict:
    """Гібридна дія: undercut/no_competitor -> "adjust" (нова ціна =
    decision["price"]); floor вищий за (конкурент - PRICE_STEP), тобто ми
    НЕ можемо продавати конкурентно навіть на нижній межі маржі (3%) ->
    "delist". Без знайденого конкурента (чи `find_best_competitor` не дав
    впевненого збігу) -> "adjust" на формульну (no_competitor) ціну, як і
    завжди, НІКОЛИ не "delist" — видалення вимагає знання РЕАЛЬНОЇ ціни
    конкурента, не просто відсутність даних про нього.

    ВИПРАВЛЕНО (2026-07-18, пряме рішення власника — реальна проблема:
    ціни системно на 10-31% вище живих buyBox-конкурентів, підтверджено
    live-перевіркою code_report_2026-07-18_pt3.md): раніше delist був
    структурно заблокований для buyBox-джерела (найпоширіше зараз джерело
    конкурентних цін — 257/362 цього прогону) І додатково вимагав floor >
    конкурент * 1.5 (50% "грації", ніколи не підтвердженої власником).
    Обидва гейти прибрано: власник прямо підтвердив просте правило — якщо
    floor вищий за (конкурент - 1 грн), товар видаляється з вітрини, без
    порогу "наскільки вище" і без винятку для buyBox.

    buyBox читається НАПРЯМУ з ВЛАСНОЇ сторінки товару (реальні живі інші
    продавці ТОГО САМОГО оголошення, не текстовий пошук) — на відміну від
    SearchListingQuery, тут структурно НЕМАЄ ризику хибного зіставлення
    іншого товару/розміру/варіанту, тож MATCH_MIN_SCORE_FOR_DELIST/
    _size_tokens_conflict/verify_competitor_really_available (які існують
    САМЕ для захисту від цього ризику при текстовому пошуку) buyBox-у не
    потрібні — delist на його основі приймається напряму.

    ДОДАНО (2026-07-22, "треба ці обмеження фіксити" — живий спот-чек 20
    SKU показав товари з конкурентом score 0.6-0.85: достатньо довіреним
    для ЦІНИ, недостатньо для DELIST, тож товар лишався не конкурентним
    на вітрині невизначено довго): якщо ФОТО незалежно підтверджує той
    самий товар (_photo_confirms_match) — ця зона теж дозволяє delist,
    той самий принцип, що вже рятує довіру ЦІНІ в зоні 0.4-0.6. Розмірний
    конфлікт і далі блокує цей шлях.

    prom_category_id (Autonomy-11/Vis-11): реальна Prom-категорія товару
    з prom_category_cache.json, якщо є — передається в
    decide_price_for_platform(), де перевіряється ПЕРШОЮ (PROM_CATEGORY_ID_
    COMMISSION), з фолбеком на Toysi-based category_name."""
    # ДОДАНО (2026-07-22, системний фікс ідентифікації — пряма вимога
    # власниці "виправити системно, щоб більше не повертатися"): _similarity()
    # тепер бере максимум із посимвольного й "token sort" ratio (див.
    # коментар там-таки) — це підвищує score для назв з іншим порядком
    # слів, АЛЕ так само підвищило б score для двох РІЗНИХ розмірів/об'ємів
    # одного родового товару ("іграшка антистрес 5см" проти "іграшка
    # антистрес 14см" — багато спільних токенів, високий token-ratio).
    # _size_tokens_conflict() уже існував, але застосовувався ЛИШЕ до
    # delist — довіра ЦІНІ (adjust) досі не мала цього захисту. Тепер
    # конфлікт розмірів блокує довіру ціні так само, як і delist, для
    # ОБОХ шляхів нижче (прямий поріг і фото-підтвердження). BuyBox не
    # має реальної "назви" (лише службовий рядок "BuyBox: N ...") — там
    # немає розмірних токенів для конфлікту, гейт для нього мовчки не
    # спрацьовує, як і задумано (buyBox структурно не може мати цей
    # ризик).
    size_conflict_for_pricing = bool(competitor) and _size_tokens_conflict(our_name, competitor.get("name") or "")

    # ВИПРАВЛЕНО (2026-07-21, MATCH_MIN_SCORE_FOR_PRICING — див. коментар
    # там-таки): конкурент з score нижче цього порогу НЕ трактується як
    # "знайдений" для розрахунку ЦІНИ (лишається "знайденим" для решти
    # логіки нижче — delist-гейт і логування бачать реальний competitor,
    # просто ціна рахується так, ніби конкурента для формули немає).
    # BuyBox (score=1.0 завжди) цим порогом не зачіпається взагалі.
    trusted_competitor_price = (
        competitor["price"]
        if competitor and not size_conflict_for_pricing and competitor["score"] >= MATCH_MIN_SCORE_FOR_PRICING
        else None
    )
    # ДОДАНО (2026-07-21, PHOTO_MATCH_MAX_DISTANCE — пряме прохання
    # власниці: порівнювати ще й по фото, бо назва відрізняється частіше,
    # ніж фото). "Рятує" лише кандидатів у зоні 0.4-0.59 (нижче
    # MATCH_MIN_SCORE взагалі не дійде до decide_action — find_best_competitor
    # відкидає їх раніше), і лише якщо фото підтверджує той самий товар.
    if (
        trusted_competitor_price is None
        and competitor
        and not size_conflict_for_pricing
        and competitor["score"] >= MATCH_MIN_SCORE
        and _photo_confirms_match(own_pictures, competitor.get("image"))
    ):
        trusted_competitor_price = competitor["price"]
    decision = decide_price_for_platform(cost, trusted_competitor_price, "prom", category_name, prom_category_id)
    action = "adjust"
    size_conflict = False
    if competitor and decision["category"] == "floor":
        if competitor.get("source") == "buybox":
            action = "delist"
        elif competitor["score"] >= MATCH_MIN_SCORE_FOR_DELIST:
            # Додатковий цільовий гейт (аудит, 2026-07-11): навіть при
            # score >= 0.85 delist блокується, якщо назви містять явно
            # різні числові/розмірні токени (див. _size_tokens_conflict) —
            # SequenceMatcher сам по собі не бачить різницю між "розмір
            # відрізняється" і "формулювання відрізняється". Це лишається
            # актуальним ЛИШЕ для НЕ-buyBox (текстовий пошук) джерела.
            # Перевикористовуємо вже обчислений size_conflict_for_pricing
            # (той самий гейт, порахований один раз) — не рахуємо вдруге.
            if size_conflict_for_pricing:
                size_conflict = True
            else:
                action = "delist"
        elif (
            competitor["score"] >= MATCH_MIN_SCORE_FOR_PRICING
            and not size_conflict_for_pricing
            and _photo_confirms_match(own_pictures, competitor.get("image"))
        ):
            # ДОДАНО (2026-07-22, пряма вимога власниці — "треба ці
            # обмеження фіксити", після живого спот-чеку 20 SKU показав
            # SKU (299579, 300808 та ін.), де конкурент score 0.6-0.85
            # достатньо довірений для ЦІНИ (>=MATCH_MIN_SCORE_FOR_PRICING),
            # але НЕ для delist (>=MATCH_MIN_SCORE_FOR_DELIST=0.85) — товар
            # лишався на вітрині за ціною вище конкурента (floor), не
            # конкурентний, але й не видалений, невизначено довго.
            #
            # Той самий принцип, що вже застосований до ЦІНИ (фото-
            # підтвердження рятує зону 0.4-0.6 для довіри ціні, вище) —
            # тепер поширений на DELIST для зони 0.6-0.85: якщо ТЕКСТОВИЙ
            # score вже достатній для довіри ціні (тобто вже пройшов
            # MATCH_MIN_SCORE_FOR_PRICING, не голий "можливо" з нижньої
            # зони) І ФОТО незалежно підтверджує той самий товар — обидва
            # незалежні сигнали разом дають вищу впевненість, ніж сам
            # score 0.85 без фото-підтвердження вимагав би. Розмірний
            # конфлікт (size_conflict_for_pricing) блокує цей шлях так
            # само, як і прямий поріг вище — фото не рятує явний конфлікт
            # розміру/об'єму.
            #
            # НЕ рахуємо size_conflict тут повторно (уже блокує через
            # умову not size_conflict_for_pricing вище) — на відміну від
            # гілки MATCH_MIN_SCORE_FOR_DELIST, де size_conflict_for_pricing
            # ЩЕ дозволяє дійти до decide_action з action="adjust"
            # (позначаючи size_conflict=True для видимості в логах), тут
            # ми просто НЕ входимо в цю гілку взагалі — action лишається
            # дефолтним "adjust", той самий кінцевий результат.
            action = "delist"
    decision["action"] = action
    decision["competitor"] = competitor
    decision["size_conflict"] = size_conflict
    return decision


def _rotated_out_scan_candidates(top_catalog: dict, toysi_catalog: dict, scan_state: dict,
                                  live_prom_ids: set | None = None) -> dict:
    """SKU, які full_catalog_competitor_scan.py вже оцінив (є конкурентні
    дані в full_catalog_scan_state.json), але які ЗАРАЗ поза топ-970
    (select_top_items() ротує його щодня) — звичайний цикл нижче їх
    більше не торкається, тож раніше застосована конкурентна ціна рано
    чи пізно застаріє (PROM_PRICE_STATE_MAX_AGE_HOURS) і фід відкотиться
    на наївну формулу. Живий приклад: SKU 275962, code_report_2026-07-20_pt14.md.

    Повертає {pid: item} лише для SKU, що:
    - НЕ в top_catalog (уникнути подвійної, дорожчої обробки нижче);
    - досі валідні в живому каталозі Toysi (cost>0, stock>0 — не гає
      зусиль на товар, що взагалі зник/скінчився з тих пір);
    - скан дав РЕАЛЬНЕ рішення (price_category != "invalid_cost");
    - ЖИВИЙ у Prom (live_prom_ids, якщо переданий) — ВИПРАВЛЕНО
      (2026-07-25, живий root-cause: 1987/1987 SKU з "Продукт не найден"
      цього прогону виявились рівно тут). full_catalog_scan_state.json
      покриває ввесь каталог Toysi (18183 SKU на момент фіксу) незалежно
      від того, чи SKU колись реально був створений у Prom (наразі лише
      847/6000 заповнено) — без цієї перевірки код слав live price-update
      на явно неіснуючий у Prom товар, гарантовано отримуючи помилку й
      марно витрачаючи час прогону (внесок у перевищення оцінки 3.2 год).
      live_prom_ids=None (кеш відсутній/застарів) — стара поведінка, без
      фільтра, той самий best-effort принцип, що й prom_category_cache."""
    candidates = {}
    for pid, scan_entry in scan_state.items():
        if pid in top_catalog:
            continue
        if scan_entry.get("price_category") == "invalid_cost":
            continue
        if live_prom_ids is not None and pid not in live_prom_ids:
            continue
        item = toysi_catalog.get(pid)
        if not item:
            continue
        try:
            cost = real_toysi_cost(item)  # 2026-07-22: реальна собівартість з урахуванням знижки Toysi, не сира каталожна ціна
        except (TypeError, ValueError):
            cost = 0
        if cost <= 0 or (item.get("stock") or 0) <= 0:
            continue
        candidates[pid] = item
    return candidates


def _rotated_out_needing_live_lookup(top_catalog: dict, toysi_catalog: dict, scan_state: dict,
                                      price_state: dict, live_prom_ids: set | None = None) -> dict:
    """SKU з попередньою конкурентною ціною (уже колись потрапляв у
    топ-970, отже, має запис у prom_competitor_price_state.json), що
    зараз ПОЗА топ-970 і ЩЕ НЕ охоплений нічним сканом
    (full_catalog_scan_state.json) — тобто немає навіть застарілих
    даних конкурента для дешевого reuse-шляху
    (_rotated_out_scan_candidates/_decide_from_scan_entry вище). Живий
    приклад, що виявив ЦЮ прогалину: SKU 275962 ("Little Milly") мав
    price_state-запис від 17.07, але ніколи не потрапляв у
    full_catalog_scan_state.json — перша версія фіксу (лише scan-based
    reuse) його б пропустила.

    Малий, САМООБМЕЖЕНИЙ набір: нічний скан рухається по ВСЬОМУ каталогу
    незалежно від топ-970, тож щойно він дійде до конкретного SKU, той
    перейде в дешевший reuse-шлях і зникне звідси природним чином —
    жодного додаткового механізму для цього не потрібно.

    На відміну від reuse-шляху, тут НЕМАЄ жодних кешованих даних про
    конкурента взагалі — обробляється ПОВНИМ, живим пайплайном
    (find_best_competitor + decide_action, включно з можливим delist,
    buyBox, presence-verification) — той самий пайплайн, що й топ-970,
    просто додаткові кандидати в тому самому основному циклі нижче."""
    tracked = {k for k in price_state if not k.startswith("_") and k != "last_full_run"}
    candidates = {}
    for pid in tracked:
        if pid in top_catalog or pid in scan_state:
            continue
        if live_prom_ids is not None and pid not in live_prom_ids:
            continue
        item = toysi_catalog.get(pid)
        if not item:
            continue
        try:
            cost = real_toysi_cost(item)  # 2026-07-22: реальна собівартість з урахуванням знижки Toysi, не сира каталожна ціна
        except (TypeError, ValueError):
            cost = 0
        if cost <= 0 or (item.get("stock") or 0) <= 0:
            continue
        candidates[pid] = item
    return candidates


def _decide_from_scan_entry(cost: float, category_name: str | None, prom_category_id: int | None,
                             scan_entry: dict, own_pictures: list | None = None) -> dict:
    """Рахує коригування ціни для SKU поза топ-970 БЕЗ живого пошуку
    конкурента — використовує вже наявні дані з full_catalog_scan_state.json
    (competitor_price/competitor_alive), рахуючи decide_price_for_platform()
    напряму (чиста функція, без мережевого виклику).

    НАВМИСНО завжди повертає action="adjust", НІКОЛИ "delist": дані скану
    йдуть лише з текстового пошуку (find_best_competitor() у
    full_catalog_competitor_scan.py викликається БЕЗ own_link, тобто без
    buyBox) і не зберігають достатньо деталей конкурента (id/urlText/
    повна назва) для живої verify_competitor_really_available() перед
    видаленням — того самого захисту, що звичайний цикл нижче обов'язково
    робить перед КОЖНИМ delist. Без цієї перевірки видаляти лістинг на
    основі можливо-днями-старих даних необґрунтовано ризиковано; натомість
    decide_price_for_platform() сама піднімає ціну до безпечної нижньої
    межі, якщо конкурент диктував би нижчу — ніколи не сигналізує
    "неможливо", завжди повертає дійсну ціну з гарантованою мінімальною
    маржею."""
    competitor_price = scan_entry.get("competitor_price")
    # competitor_alive=False -> конкурент, якого бачив скан, уже НЕ живий
    # на момент сканування — трактуємо як "конкурента немає" (той самий
    # безпечний дефолт, що presence_unconfirmed у звичайному циклі: не
    # довіряти протермінованому сигналу про наявність).
    competitor_confirmed_dead = scan_entry.get("competitor_alive") is False
    if competitor_confirmed_dead:
        competitor_price = None
    # ДОДАНО (2026-07-21, MATCH_MIN_SCORE_FOR_PRICING — той самий гейт,
    # що й у decide_action() для основного циклу): дані скану зберігають
    # competitor_score, отриманий тим самим (без buyBox) текстовим
    # пошуком, що й тут ризикує хибним збігом. Той самий поріг, той самий
    # захист — не довіряти слабкому збігу для розрахунку ціни.
    competitor_score = scan_entry.get("competitor_score")
    if competitor_score is not None and competitor_score < MATCH_MIN_SCORE_FOR_PRICING:
        competitor_price = None
        # ДОДАНО (2026-07-21, фото-рятування — той самий механізм, що й у
        # decide_action()): скан зберігає competitor_image лише з дати
        # цього фіксу — старі записи (до розгортання) не матимуть цього
        # поля взагалі, .get() поверне None, _photo_confirms_match() сам
        # безпечно поверне False. НЕ рятує, якщо конкурент уже підтверджено
        # неживим вище (competitor_confirmed_dead) — фото не скасовує факт,
        # що самого оголошення конкурента вже не існує.
        if (
            not competitor_confirmed_dead
            and competitor_score >= MATCH_MIN_SCORE
            and _photo_confirms_match(own_pictures, scan_entry.get("competitor_image"))
        ):
            competitor_price = scan_entry.get("competitor_price")
    decision = decide_price_for_platform(cost, competitor_price, "prom", category_name, prom_category_id)
    decision["action"] = "adjust"
    return decision


# PromEditError/apply_price()/delist() — ПЕРЕНЕСЕНО у prom_api_client.py
# (2026-07-21, "роби фікси глобальні" — той самий мовчазний відхил Prom API
# виявлено ЗНОВУ в паралельному приводі видалення, prom_catalog_sync.py,
# бо там була ОКРЕМА, без цієї перевірки, реалізація того самого виклику).
# Імпортовано на початку файлу.


def _recheck_delisted_pids(
    delisted_since: dict,
    toysi_catalog: dict,
    russian_text: dict,
    own_product_links: dict,
    prom_category_cache: dict,
    limit: int = RECHECK_DELISTED_BATCH_LIMIT,
) -> int:
    """ДОДАНО (2026-07-18, знахідка незалежного аудиту PR #97 —
    code_report_2026-07-18_pt9.md): заявлене "самоочищення" delisted_since
    (запис прибирається, щойно SKU знову потрапляє в to_adjust) НІКОЛИ не
    спрацьовує на практиці — select_top_items() вже виключає позначені
    SKU РАНІШЕ, ніж вони можуть дістатись до основного циклу нижче, тож
    цикл, який мав прибирати позначку, ніколи їх знову не бачить. Напрямок
    помилки безпечний (забагато виключень, не хибне повернення
    неконкурентного товару), але без цієї функції позначка де-факто стає
    ПОСТІЙНОЮ — навіть коли конкурент згодом подорожчає чи зникне.

    Ця функція — окрема, ЦІЛЕСПРЯМОВАНА перевірка САМЕ delisted-множини
    (типово в рази менша за весь топ-970), НЕ частина основного циклу:
    бере товар напряму з toysi_catalog (не top_catalog, бо delisted SKU
    там структурно відсутній), рахує ту саму decide_price_for_platform()
    з тим самим кешем buyBox/категорій, і прибирає позначку, якщо
    результат більше НЕ "floor" (конкурент подешевшав/зник, чи ми
    подешевшали через нижчу собівартість Toysi). Мутує delisted_since
    на місці (той самий патерн, що й основний цикл: pop() на
    посилання, яке зрештою зберігається як price_state["_delisted_since"]).

    Повертає кількість прибраних позначок (для логу/дайджесту)."""
    cleared = 0
    pids_to_check = sorted(delisted_since.keys(), key=lambda p: delisted_since[p])[:limit]
    for pid in pids_to_check:
        if _time_budget_exceeded():
            print(f"[Pricer] Часовий бюджет вичерпано — зупиняю перевірку delisted достроково "
                  f"({cleared} знято з {len(pids_to_check)} у цій партії).")
            break
        item = toysi_catalog.get(pid)
        if item is None:
            # SKU більше немає в каталозі Toysi взагалі (закінчився
            # назавжди чи товар знято) — позначка неактуальна, але й
            # питання "чи знову конкурентний" не стоїть; лишаємо як є,
            # наступний прогін просто пропустить (той самий safe-default,
            # що й скрізь у цьому файлі: не гадаємо, коли даних нема).
            continue
        try:
            cost = real_toysi_cost(item)  # 2026-07-22: реальна собівартість з урахуванням знижки Toysi, не сира каталожна ціна
        except (TypeError, ValueError):
            continue
        if cost <= 0 or item.get("stock", 0) <= 0:
            continue

        name_ukr = (item.get("name") or "").strip()
        name_rus = (russian_text.get(pid, {}) or {}).get("name") or name_ukr
        category_name = item.get("category_name")
        prom_category_id = (prom_category_cache.get(pid) or {}).get("category_id")
        own_link = own_product_links.get(pid)

        competitor = find_best_competitor(name_rus, cost, own_link, item.get("pictures"))
        time.sleep(SEARCH_DELAY)
        decision = decide_price_for_platform(cost, competitor["price"] if competitor else None,
                                              "prom", category_name, prom_category_id)
        if decision["category"] != "floor":
            delisted_since.pop(pid, None)
            cleared += 1
            print(f"[Pricer] Знову конкурентний, знято позначку delisted: {pid} {name_ukr[:40]}")

    return cleared


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                     help="Реально змінювати ціни/видаляти товари в Prom. Без цього — лише dry-run звіт.")
    ap.add_argument("--limit", type=int, default=ROTATION_BATCH_SIZE,
                     help=f"Скільки найстаріших-за-перевіркою SKU топ-970 обробити за цей запуск "
                          f"(дефолт {ROTATION_BATCH_SIZE}).")
    ap.add_argument("--allow-default-commission", action="store_true",
                     help="Дозволити --apply для SKU, чия категорія на PROM_COMMISSION_DEFAULT "
                          "(реальна ставка не підтверджена) — за замовчуванням такі SKU виключено "
                          "з auto-apply, лише позначені для ручного перегляду.")
    ap.add_argument("--force-circuit-breaker", action="store_true",
                     help="Ігнорувати circuit breaker (P0-3) і застосувати зміни навіть якщо він спрацював. "
                          "Використовувати ЛИШЕ для свідомо очікуваного разового стрибка — не залишати "
                          "true за замовчуванням, інакше breaker перестає захищати від справжніх аномалій.")
    args = ap.parse_args()

    global _RUN_DEADLINE
    _RUN_DEADLINE = time.monotonic() + MAX_RUNTIME_SECONDS

    # Надійність, п.3: SAFETY_HOLD — ручний, миттєвий стоп-кран, незалежний
    # від git merge/deploy циклу. Прецедент, якого це запобігає: відомий
    # баг безпеки виявлено, фікс ще НЕ готовий/не змержено — досі жодного
    # способу негайно зупинити --apply, окрім видалення/паузи самого
    # workflow вручну. Це env var (не файл у репо), щоб увімкнути/вимкнути
    # можна було ОДНІЄЮ командою (`gh variable set SAFETY_HOLD --body true`),
    # без PR/мержу/чекання на CI — швидше за будь-який кодовий фікс.
    # Значення env var (не просто "1"/"true") друкується як причина, якщо
    # задано власником — щоб було видно, ЧОМУ саме зараз hold, не лише що.
    safety_hold_reason = os.environ.get("SAFETY_HOLD", "").strip()
    if safety_hold_reason and args.apply:
        message = (
            f"🛑 prom_competitor_pricer.py: SAFETY_HOLD активний — примусовий dry-run, "
            f"--apply проігноровано. Причина: {safety_hold_reason}"
        )
        print(f"[Pricer] {message}", file=sys.stderr)
        send_telegram_message(message)
        args.apply = False

    if not PROM_API_KEY:
        print("[Pricer] PROM_API_KEY не задано — зупиняюсь.", file=sys.stderr)
        sys.exit(1)

    price_state = load_prom_price_state()

    print("[Pricer] Рахую поточний відбір топ-970...")
    toysi_catalog = fetch_toysi_catalog()
    try:
        assert_catalog_size_sane(toysi_catalog)
    except CatalogSizeError as e:
        print(f"[Pricer] {e}", file=sys.stderr)
        send_telegram_message(f"🚨 prom_competitor_pricer.py зупинено: {e}")
        write_pricer_summary(f"⚠ Зупинено: каталог Toysi порожній/замалий ({e}).", errors=1)
        sys.exit(1)

    # ЗМІНЕНО (2026-07-24, ротаційні партії замість гейту "не частіше
    # ~20 год"): мітка last_batch_run пишеться на КОЖНОМУ прогоні (не
    # лише раз на добу) — суто діагностична, "коли репрайсер востаннє
    # реально запускався", жоден код більше не читає її для гейтування.
    price_state.setdefault("_meta", {})["last_batch_run"] = datetime.now().isoformat()
    save_prom_price_state(price_state)

    top_catalog = select_top_items(toysi_catalog)
    print(f"[Pricer] У топ-970: {len(top_catalog)} товарів.")

    # ДОДАНО (2026-07-25, живий root-cause 1987 помилок "Продукт не
    # найден" за один прогін): той самий внутрішньопрогонний кеш живих
    # товарів Prom, що вже пише generate_google_feed.py на кроках Meta/
    # Bing-феєдів ЦЬОГО Ж прогону workflow (TTL 1 год — на момент кроку
    # репрайсера кеш свіжий, без жодного додаткового HTTP-запиту).
    # Використовується нижче, щоб відфільтрувати з rotated_out/
    # live_lookup_extra SKU, яких Prom реально не має — див. докстрінги
    # _rotated_out_scan_candidates()/_rotated_out_needing_live_lookup().
    from generate_google_feed import load_prom_products_cache  # лінивий імпорт, той самий патерн, що й вище
    _live_prom_products = load_prom_products_cache()
    live_prom_ids = set(_live_prom_products.keys()) if _live_prom_products is not None else None
    print(f"[Pricer] Кеш живих товарів Prom для фільтра rotated_out: "
          f"{'відсутній/застарілий — фільтр вимкнено' if live_prom_ids is None else f'{len(live_prom_ids)} SKU'}.")

    # ДОДАНО (2026-07-25, друга хвиля "чому кабінет не росте" — прямий
    # запит власниці "копай"): звіт імпорту Prom того ж дня показав 542
    # SKU з "Поле status: Позиція недоступна для оновлення" — ЖОДЕН не
    # був позначений _delisted_since, на відміну від кейсу PR #151 (там
    # prom_catalog_sync.py::deactivate() САМ ІНІЦІЮВАВ видалення, тому й
    # міг одразу писати позначку). Ці зникли з Prom ЯКИМОСЬ ІНШИМ шляхом
    # (можлива дедуплікація/модерація на боці Prom, не наш код) — жоден
    # наш delist()/deactivate() ніколи не викликався для них, тому
    # позначку ставити не було кому: select_top_items() щоразу знову
    # обирав їх у топ-6000, назавжди займаючи слоти, які могли б зайняти
    # реальні кандидати (живо підтверджено: 0/20 вибірки реально існують
    # у Prom, fetch_prom_products_by_external_ids). Тут — загальний
    # запобіжник: БУДЬ-ЯКИЙ SKU з top_catalog, якого немає в live_prom_ids
    # (той самий кеш, що вже вище фільтрує rotated_out), отримує позначку
    # delisted — не лише ці конкретні 542, а й будь-які майбутні випадки
    # того самого класу.
    #
    # ВИПРАВЛЕНО (2026-07-25, повторний перегляд аудиту PR #163, знахідка
    # pt6): live_prom_ids сам походить від fetch_prom_products() (/groups/
    # list) — структурно схильний до відомого класу бага "невидима
    # група" (той самий інцидент "Сквіші" 07-12, коли /groups/list
    # мовчки не повертав реальну групу товарів). Позначення _delisted_
    # since — реальна, майже деструктивна дія (структурно виключає SKU з
    # select_top_items() надалі), тому спиратись лише на кеш, схильний до
    # сліпих плям, для НЕЇ недостатньо надійно — навіть попри існуючий
    # самозцілювальний _recheck_delisted_pids(). Кандидатів (дешевий
    # перший прохід через live_prom_ids) тепер підтверджуємо ще раз
    # детермінованим, per-ID запитом fetch_prom_products_by_external_ids()
    # (той самий метод, яким власне це розслідування підтвердило 542
    # SKU) — структурно імунний до "невидимої групи", бо не йде через
    # /groups/list узагалі. indeterminate (мережевий збій/timeout під час
    # ЦІЄЇ перевірки) навмисно НЕ позначаємо — "не вдалось перевірити" це
    # не доказ відсутності.
    if live_prom_ids is not None:
        _ghost_candidates = [pid for pid in top_catalog if pid not in live_prom_ids]
        _delisted_since_early = price_state.setdefault("_delisted_since", {})
        _to_check = [pid for pid in _ghost_candidates if pid not in _delisted_since_early]
        if _to_check:
            from prom_catalog_sync import fetch_prom_products_by_external_ids  # лінивий імпорт, той самий патерн, що й нижче
            _found, _indeterminate = fetch_prom_products_by_external_ids(set(_to_check))
            _newly_ghost = [pid for pid in _to_check if pid not in _found and pid not in _indeterminate]
            print(f"[Pricer] Кандидати-привиди з топ-6000 (немає в live_prom_ids): {len(_to_check)}, "
                  f"з них підтверджено детерміновано відсутні: {len(_newly_ghost)} "
                  f"(спростовано хибне спрацювання кешу: {len(_found)}, не вдалось перевірити: {len(_indeterminate)}).")
            if _newly_ghost:
                _now_iso = datetime.now().isoformat()
                for _pid in _newly_ghost:
                    _delisted_since_early[_pid] = _now_iso
                save_prom_price_state(price_state)
                print(f"[Pricer] УВАГА: {len(_newly_ghost)} SKU з топ-6000 підтверджено відсутні в живому Prom "
                      f"(не наш delist/deactivate) — позначено delisted, звільнено місце для реальних кандидатів "
                      f"з наступного прогону select_top_items().")

    # ДОДАНО (2026-07-20, "постійно конкурентні, раз і назавжди" —
    # див. коментар біля ROTATED_OUT_BATCH_LIMIT вище): SKU, що вже мають
    # дані скану, але випали з топ-970, обробляються ОКРЕМИМ, дешевим
    # проходом нижче (після основного циклу) — без живого пошуку
    # конкурента, лише adjust, ніколи delist.
    scan_state = load_scan_state()
    rotated_out = _rotated_out_scan_candidates(top_catalog, toysi_catalog, scan_state, live_prom_ids)
    print(f"[Pricer] Додатково поза топ-970 (є дані скану, ще в наявності): {len(rotated_out)} товарів "
          f"(оброблю до {ROTATED_OUT_BATCH_LIMIT} цього прогону).")

    # Менший, самообмежений залишок: раніше відстежені (мають запис у
    # price_state), поза топ-970, АЛЕ ще не охоплені нічним сканом —
    # обробляються ПОВНИМ живим пайплайном нижче (не окремим дешевим
    # проходом), додані просто як ще один шматок `items`. Див. докстрінг
    # _rotated_out_needing_live_lookup() — чому цей набір малий і
    # самоскорочується з часом.
    live_lookup_extra_all = _rotated_out_needing_live_lookup(top_catalog, toysi_catalog, scan_state, price_state, live_prom_ids)
    live_lookup_extra = dict(list(live_lookup_extra_all.items())[:LIVE_LOOKUP_EXTRA_BATCH_LIMIT])
    print(f"[Pricer] Додатково поза топ-970 (без даних скану — живий пошук): "
          f"{len(live_lookup_extra_all)} товарів, оброблю до {LIVE_LOOKUP_EXTRA_BATCH_LIMIT} цього прогону.")

    print("[Pricer] Завантажуємо російськомовні назви (кращий збіг з пошуком Prom)...")
    russian_text = fetch_russian_text()

    # 2026-07-14: own_product_links_cache.json ({item_id: {prom_id, url_text}},
    # пише generate_google_feed.py) — джерело для buyBox-пошуку конкурента
    # (fetch_buybox_competitor), основного джерела тепер. Завантажуємо ОДИН
    # раз тут (не всередині find_best_competitor на кожен виклик), той самий
    # патерн, що й russian_text вище.
    own_product_links = _load_own_product_links_cache()
    print(f"[Pricer] Кеш власних посилань: {len(own_product_links)} SKU "
          f"({'знайдено' if own_product_links else 'відсутній/застарілий — увесь прогін на fallback-пошуку'}).")

    # Autonomy-11/Vis-11: та сама механіка кешу, для РЕАЛЬНОЇ Prom-категорії
    # (замість здогаду за назвою Toysi) — закриває категорії, неоднозначні
    # за назвою (напр. "рюкзаки", що розпадається на 3 різні Prom-категорії).
    prom_category_cache = _load_prom_category_cache()
    print(f"[Pricer] Кеш Prom-категорій: {len(prom_category_cache)} SKU "
          f"({'знайдено' if prom_category_cache else 'відсутній/застарілий — фолбек на Toysi-категорію'}).")

    # ДОДАНО (2026-07-18, знахідка аудиту PR #97): delisted_since сам по
    # собі ніколи не самоочищується через основний цикл нижче (select_top_
    # items() вже виключає ці SKU з top_catalog/items) — окрема,
    # цілеспрямована перевірка САМЕ delisted-множини, до основного циклу.
    delisted_since = price_state.get("_delisted_since", {})
    if delisted_since:
        print(f"[Pricer] Перевіряю до {RECHECK_DELISTED_BATCH_LIMIT} найстаріших позначок з "
              f"{len(delisted_since)} раніше видалених SKU — чи не подешевшав конкурент...")
        cleared = _recheck_delisted_pids(delisted_since, toysi_catalog, russian_text, own_product_links, prom_category_cache)
        print(f"[Pricer] Знято позначку delisted: {cleared}/{min(RECHECK_DELISTED_BATCH_LIMIT, len(delisted_since))} "
              f"перевірених (з {len(delisted_since)} загалом).")
        if cleared:
            save_prom_price_state(price_state)

    # ЗМІНЕНО (2026-07-24, ротаційні партії): раніше — завжди перші N
    # top_catalog.items() (стабільний, рангований зріз). Тепер, коли
    # SELECT_COUNT (generate_prom_feed_top.py) перевищує --limit (SKU
    # 1971-1970 і далі при topКаталог=1970+ на кроках 4000/6000), брати
    # завжди першу тисячу(-і) означало б, що решта НІКОЛИ не отримає
    # живого пошуку. Натомість сортуємо за часом ОСТАННЬОЇ живої
    # перевірки (price_state[pid]["timestamp"]) — найстаріші (чи взагалі
    # без запису — трактується як "ніколи не перевірявся", найвищий
    # пріоритет) обробляються першими. За кілька прогонів це природно
    # ротує через увесь top_catalog, а не залипає на першому зрізі.
    def _last_checked(pid: str) -> str:
        return price_state.get(pid, {}).get("timestamp") or ""

    # ВИПРАВЛЕНО (2026-07-25, живий root-cause "чому досі 846 товарів" —
    # прохання власниці "шукай"): порожній timestamp сортується РАНІШЕ
    # будь-якої реальної дати — тобто "ніколи не перевірявся" завжди мав
    # абсолютний пріоритет над "перевірявся давно". Живо підтверджено на
    # реальному фіді сьогодні: 2787 SKU з топ-6000 НІКОЛИ не перевірялись
    # (майже вдвічі більше за ROTATION_BATCH_SIZE) — тобто КОЖНА партія
    # без винятку повністю з'їдалась саме ними, а 3213 вже перевірених
    # SKU (включно з тими, кому вже 190+ годин) НІКОЛИ не отримували
    # повторної черги — структурне голодування, не рідкісний виняток.
    # Наслідок: конкурентна ціна тихо застарівала (>PROM_PRICE_STATE_
    # MAX_AGE_HOURS=30г) і відкочувалась на неконкурентну наївну формулу
    # (живий приклад: SKU 302183 — мали 347.54₴, у фіді 456.72₴; випадкова
    # вибірка 20 SKU підтвердила 7/20 (35%) з тим самим патерном).
    # Фікс: партія ділиться навпіл — половина на найстаріші "ніколи не
    # перевірені" (нові товари з розширення каталогу теж мають отримати
    # хоч один живий пошук), половина ГАРАНТОВАНО на найстаріші З
    # РЕАЛЬНОЮ датою (оновлення) — незалежно від того, скільки нових
    # кандидатів чекає в черзі. Обидва боки тепер прогресують щоразу.
    never_checked_items = {pid: item for pid, item in top_catalog.items() if not _last_checked(pid)}
    previously_checked_items = {pid: item for pid, item in top_catalog.items() if _last_checked(pid)}

    never_checked_share = args.limit // 2
    never_checked_batch = dict(list(never_checked_items.items())[:never_checked_share])
    refresh_slots = args.limit - len(never_checked_batch)
    refresh_batch = dict(
        sorted(previously_checked_items.items(), key=lambda kv: _last_checked(kv[0]))[:refresh_slots]
    )
    rotation_batch = {**never_checked_batch, **refresh_batch}
    items = list(rotation_batch.items()) + list(live_lookup_extra.items())
    print(f"[Pricer] Обробляю {len(items)} товарів (ротаційна партія: {len(never_checked_batch)} нових "
          f"(ніколи не перевірених з {len(never_checked_items)}) + {len(refresh_batch)} на оновлення "
          f"(найстаріші з {len(previously_checked_items)} вже перевірених) + {len(live_lookup_extra)} поза "
          f"топ-970 без даних скану)...")

    # Донарахування own_link для ПОТОЧНОГО батчу — див. коментар біля
    # OWN_LINK_RESOLVE_BATCH_LIMIT вище (чому цей крок взагалі потрібен).
    missing_own_link_ids = [pid for pid, _ in items if pid not in own_product_links][:OWN_LINK_RESOLVE_BATCH_LIMIT]
    if missing_own_link_ids:
        from generate_google_feed import resolve_own_product_links  # лінивий імпорт, див. коментар вище
        from prom_catalog_sync import fetch_prom_products_by_external_ids

        print(f"[Pricer] Кеш власних посилань не покриває {len(missing_own_link_ids)} SKU цього "
              f"батчу (з {sum(1 for pid, _ in items if pid not in own_product_links)} загалом) — "
              f"донараховую детермінованим запитом (не пошуком)...")
        found_products, _indeterminate = fetch_prom_products_by_external_ids(set(missing_own_link_ids))
        if found_products:
            missing_catalog_subset = {pid: item for pid, item in items if pid in found_products}
            newly_resolved = resolve_own_product_links(missing_catalog_subset, found_products)
            own_product_links.update(newly_resolved)
            print(f"[Pricer] Донараховано {len(newly_resolved)}/{len(missing_own_link_ids)} нових "
                  f"посилань (buyBox тепер доступний для них; решта — товар ще не імпортований у "
                  f"Prom чи urlText не вдалось визначити).")

    adjust_count, delist_count, no_competitor_count, error_count = 0, 0, 0, 0
    buybox_count = 0
    buybox_attempted_count = 0  # own_link був — buyBox ПРОБУВАЛИ (незалежно від результату);
                                  # різке падіння buybox_count/buybox_attempted_count сигналізує
                                  # про зламаний _BUYBOX_RE, а не просто "мало конкурентів" (рев'ю PR #53)
    to_adjust, to_delist = [], []
    default_commission_skipped = []  # (pid, name, category, price) — не потрапляють у to_adjust/to_delist
    # ДОДАНО (2026-07-21, живий приклад SKU 260299 "Тварини і птахи",
    # findings_log.md): SKU з непідтвердженою комісією раніше НІКОЛИ не
    # отримували запис у price_state (лише apply_price()-цикл нижче його
    # пише, а ці SKU туди не потрапляють). За PROM_PRICE_STATE_MAX_AGE_HOURS
    # (30г) "свіже перевизначення" протухало, і generate_prom_feed_top.py
    # відкочував ФІД (не лише прямий API) на найгіршу з можливих формул
    # "конкурента нема" (1.75×cost) — підтверджено живо: 163/970 SKU топ-970
    # (17%) саме зараз показують цю наївну ціну замість уже порахованої
    # конкурентної. Тут лише ЗБИРАЄМО (pid, price) — сам запис у price_state
    # робиться нижче, ПІСЛЯ гейту `if not args.apply`, щоб dry-run і надалі
    # не мав жодного побічного ефекту на файл стану. Прямий API-патч
    # (apply_price()) і delist лишаються заблокованими для цих SKU так само,
    # як і раніше — змінюється лише те, що бачить ФІД.
    feed_only_price_updates = []  # (pid, price, margin_pct, competitor_key, category, competitor_price, cost) — лише для price_state, БЕЗ apply_price()
    competitor_scores = []  # Автономність, п.7: score КОЖНОГО використаного конкурента

    for pid, item in items:
        if _time_budget_exceeded():
            print(f"[Pricer] Часовий бюджет ({MAX_RUNTIME_SECONDS/3600:.0f}г) вичерпано — "
                  "зупиняю основний цикл достроково, решта партії зачекає наступного прогону.")
            break
        try:
            cost = real_toysi_cost(item)  # 2026-07-22: реальна собівартість з урахуванням знижки Toysi, не сира каталожна ціна
        except (TypeError, ValueError):
            cost = 0
        if cost <= 0:
            continue

        name_ukr = (item.get("name") or "").strip()
        name_rus = (russian_text.get(pid, {}) or {}).get("name") or name_ukr
        category_name = item.get("category_name")
        prom_category_id = (prom_category_cache.get(pid) or {}).get("category_id")

        own_link = own_product_links.get(pid)
        if own_link:
            buybox_attempted_count += 1
        competitor = find_best_competitor(name_rus, cost, own_link, item.get("pictures"))
        if competitor and competitor.get("source") == "buybox":
            buybox_count += 1
            # Легкий sanity-моніторинг (рекомендація рев'ю PR #53): minPrice
            # підозріло нижчий за собівартість — не блокуємо (сама ціна все
            # одно проходить через нижню межу decide_price_for_platform),
            # лише друкуємо попередження на випадок, якщо семантика поля
            # colись зміниться (напр. Prom почне включати нашу ціну в
            # розрахунок minPrice).
            if competitor["price"] < cost * 0.5:
                print(f"[Pricer] buyBox: підозріло низька ціна для {pid} "
                      f"(minPrice={competitor['price']:.0f} < 50% собівартості {cost:.0f}) — "
                      "перевір вручну", file=sys.stderr)
        decision = decide_action(cost, competitor, category_name, name_rus, prom_category_id, item.get("pictures"))
        time.sleep(SEARCH_DELAY)

        # Другий, надійніший гейт ПЕРЕД фінальним delist — GraphQL-пошук
        # (presence.isAvailable у find_best_competitor) сам по собі виявився
        # ненадійним (2026-07-12, SKU 298613/299070/299071 delist'ились 2 дні
        # поспіль на основі того самого протермінованого presence-флагу).
        # Пряма HTTP-перевірка реальної сторінки конкурента — лише для
        # кандидатів, що вже пройшли текстовий і розмірний гейт, тобто рідко
        # (одиниці з ~300 SKU за прогін), тож зайвий запит тут не проблема.
        #
        # ПРОПУСКАЄТЬСЯ для buyBox-джерела (2026-07-18): competitor["id"]/
        # ["urlText"] тут завжди None (buyBox дає лише сумарну ціну, не
        # конкретне оголошення) — виклик з None-полями будував би зламаний
        # URL і завжди повертав presence_unconfirmed=True, мовчки скасовуючи
        # КОЖЕН buyBox-delist назад на "adjust" (саме так delist для buyBox
        # був де-факто заблокований і ДО явного гейту в decide_action()).
        # buyBox читається напряму з ЖИВОЇ сторінки нашого ж товару в МОМЕНТ
        # цього прогону — сам по собі є актуальнішим підтвердженням
        # наявності, ніж окрема перевірка конкретного оголошення конкурента.
        presence_unconfirmed = False
        if decision["action"] == "delist" and decision["competitor"].get("source") != "buybox":
            if not verify_competitor_really_available(decision["competitor"]):
                decision["action"] = "adjust"
                presence_unconfirmed = True
            time.sleep(SEARCH_DELAY)

        comp_desc = (
            f"конкурент {decision['competitor']['price']:.0f} грн "
            f"(score={decision['competitor']['score']:.2f}) {decision['competitor']['name'][:40]!r}"
            if decision["competitor"] else "конкурент не знайдено"
        )
        size_note = "  [РОЗМІР/ОБ'ЄМ НЕ ЗБІГАЄТЬСЯ -> delist заблоковано, залишено adjust]" if decision.get("size_conflict") else ""
        presence_note = "  [СТОРІНКА КОНКУРЕНТА НЕ ПІДТВЕРДЖУЄ InStock -> delist заблоковано, залишено adjust]" if presence_unconfirmed else ""
        # category/margin_pct додано в лог (рішення власника, 2026-07-13,
        # плаваюча межа MIN_PROFIT_COMPETITOR_FLOOR для Шляху 2) — щоб звіт
        # про те, скільки SKU реально впало під новою межею й наскільки,
        # можна було витягти напряму з логу цього прогону, не вручну.
        print(f"{pid}\t{name_ukr[:45]:45s}\tcost={cost:.0f}\tfloor={decision['floor']:.0f}\t"
              f"price={decision['price']:.0f}\tmargin={decision['margin_pct']:.1f}%\t"
              f"[{decision['action']}/{decision['category']}]\t{comp_desc}{size_note}{presence_note}")

        if decision["competitor"] is None:
            no_competitor_count += 1
        else:
            competitor_scores.append(decision["competitor"]["score"])

        # ДОДАНО (2026-07-26, пряме прохання власниці — "ми повинні
        # записувати усі данні для майбутніх коригувань, як конкурента так
        # і тойсі, що знати як формувалась ціна"): category/competitor_price/
        # cost йдуть у price_state поруч із price/margin_pct, щоб пізніше
        # МОЖНА було достовірно відрізнити "undercut" (реально продиктовано
        # живим конкурентом) від "floor" (наша власна нижня межа, ціна
        # конкурента могла бути суттєво іншою) — без цього відтворити
        # candidate заднім числом небезпечно (живий приклад SKU 292858:
        # 37.71 реконструйовано проти 69 реальної ціни конкурента).
        # decision["competitor_price"] (не сирий competitor["price"]!) — те
        # саме значення, яке РЕАЛЬНО пішло у формулу decide_price_for_platform():
        # decide_action() вище могло занулити довіру до competitor (низький
        # score/конфлікт розміру) ще до цього виклику.
        if _category_commission_is_default(category_name, prom_category_id) and not args.allow_default_commission:
            default_commission_skipped.append((pid, name_ukr, category_name, decision["price"]))
            feed_only_price_updates.append((
                pid, decision["price"], decision["margin_pct"],
                _competitor_identity_key(decision["competitor"]),
                decision["category"], decision["competitor_price"], cost,
            ))
            print(f"  -> {pid}: категорія {category_name!r} на дефолтній комісії "
                  f"({PROM_COMMISSION_DEFAULT:.0%}, не підтверджена) — виключено з auto-apply, "
                  "потребує ручного перегляду (ціна все одно піде у фід — Vis-11.1)")
        elif decision["action"] == "adjust":
            adjust_count += 1
            to_adjust.append((
                pid, decision["price"], decision["margin_pct"],
                _competitor_identity_key(decision["competitor"]),
                decision["category"], decision["competitor_price"], cost,
            ))
        elif decision["action"] == "delist":
            delist_count += 1
            # ДОДАНО (2026-07-27, пряме зауваження власниці — "нехай вона
            # більше показувала би та чекала на видалення, алеж не
            # демпінгувала би"): decision["price"] тут — той самий
            # безпечний floor (уже включає net_revenue>=cost запобіжник),
            # рахований для delist-рішення так само, як і для adjust —
            # несемо ту саму інформацію, що й to_adjust, щоб застосувати
            # ціну НЕЗАЛЕЖНО від того, коли (чи чи) реально відбудеться
            # видалення (див. коментар біля циклу видалення нижче).
            to_delist.append((
                pid, decision["price"], decision["margin_pct"],
                _competitor_identity_key(decision["competitor"]),
                decision["category"], decision["competitor_price"], cost,
            ))

    # ДОДАНО (2026-07-20, "постійно конкурентні, раз і назавжди"):
    # окремий, дешевий прохід над SKU поза топ-970 — БЕЗ живого пошуку
    # конкурента (reuse даних скану), тому й без SEARCH_DELAY-пауз між
    # ітераціями. Ніколи не додає до to_delist (див. докстрінг
    # _decide_from_scan_entry). Той самий default-commission гейт, що й
    # основний цикл вище — не auto-apply на непідтверджену комісію.
    rotated_out_adjust_count = 0
    for pid, item in list(rotated_out.items())[:ROTATED_OUT_BATCH_LIMIT]:
        if _time_budget_exceeded():
            print("[Pricer] Часовий бюджет вичерпано — зупиняю rotated_out-цикл достроково.")
            break
        try:
            cost = real_toysi_cost(item)  # 2026-07-22: реальна собівартість з урахуванням знижки Toysi, не сира каталожна ціна
        except (TypeError, ValueError):
            cost = 0
        if cost <= 0:
            continue
        name_ukr = (item.get("name") or "").strip()
        category_name = item.get("category_name")
        prom_category_id = (prom_category_cache.get(pid) or {}).get("category_id")
        scan_entry = scan_state.get(pid, {})

        decision = _decide_from_scan_entry(cost, category_name, prom_category_id, scan_entry, item.get("pictures"))

        if _category_commission_is_default(category_name, prom_category_id) and not args.allow_default_commission:
            default_commission_skipped.append((pid, name_ukr, category_name, decision["price"]))
            # None: цей шлях (reuse даних нічного скану) не зберігає id
            # конкретного оголошення конкурента — немає стабільної
            # ідентичності, яку можна було б порівняти між прогонами.
            feed_only_price_updates.append((
                pid, decision["price"], decision["margin_pct"], None,
                decision["category"], decision["competitor_price"], cost,
            ))
            continue

        adjust_count += 1
        rotated_out_adjust_count += 1
        to_adjust.append((
            pid, decision["price"], decision["margin_pct"], None,
            decision["category"], decision["competitor_price"], cost,
        ))

    if rotated_out.items():
        print(f"[Pricer] Поза топ-970 (reuse даних скану): {rotated_out_adjust_count} "
              f"коригувань ціни додано (ніколи delist для цього шляху).")

    print(f"\n[Pricer] Підсумок: adjust={adjust_count}, delist={delist_count}, "
          f"без знайденого конкурента={no_competitor_count}, "
          f"на дефолтній комісії (виключено з auto-apply)={len(default_commission_skipped)}, "
          f"з них через buyBox (не SearchListingQuery): {buybox_count} "
          f"(пробували buyBox для {buybox_attempted_count} SKU — різке падіння "
          f"buybox_count/buybox_attempted_count сигналізує про зламаний regex, "
          f"не просто \"мало конкурентів\")")

    # Автономність, п.7: розподіл score обраних конкурентів. find_best_
    # competitor() вибирає НАЙДЕШЕВШОГО серед кандидатів, що пройшли поріг
    # MATCH_MIN_SCORE (0.4) — не найсхожішого. Якщо низький-score-збіги
    # (близько порогу) з часом стають частішими, це системний ризик:
    # "найдешевший, а не найсхожіший" конкурент усе частіше виявляється
    # НЕПОРІВНЯНИМ товаром (як SKU 300391 з уценкою, знайдено 2026-07-17),
    # а не просто рідкісним винятком. Жодного окремого запиту не потрібно —
    # score уже обчислюється для кожного рішення, тут лише агрегація.
    low_score_threshold = 0.6
    if competitor_scores:
        low_score_count = sum(1 for s in competitor_scores if s < low_score_threshold)
        avg_score = sum(competitor_scores) / len(competitor_scores)
        print(
            f"[Pricer] Розподіл score конкурентів: середній={avg_score:.2f}, "
            f"мін={min(competitor_scores):.2f}, макс={max(competitor_scores):.2f}, "
            f"нижче {low_score_threshold} (ризик \"найдешевший, не найсхожіший\"): "
            f"{low_score_count}/{len(competitor_scores)} ({low_score_count / len(competitor_scores) * 100:.0f}%)"
        )

    # ЗМІНЕНО (2026-07-23, пряме прохання власниці — "я не буду звіряти,
    # це повинні робити ви. Мені в телеграм тільки результати"): категорія
    # на дефолтній комісії — це МОЯ backlog-задача (дослідити реальну
    # ставку ProSale і додати в PROM_CATEGORY_COMMISSION/
    # PROM_CATEGORY_ID_COMMISSION), а не завдання для власниці. Тому
    # деталізація за категоріями йде ТІЛЬКИ у write_pricer_summary()
    # (внутрішній артефакт, який переглядаю я між сесіями) — у Telegram
    # (власниці) лишається лише сирий факт-результат: скільки SKU
    # виключено з auto-apply, без жодного заклику щось перевірити.
    default_commission_category_detail = ""
    if default_commission_skipped:
        category_counts: dict[str, int] = {}
        for _, _, cat, _ in default_commission_skipped:
            category_counts[cat or "(без категорії)"] = category_counts.get(cat or "(без категорії)", 0) + 1
        top_categories = sorted(category_counts.items(), key=lambda x: -x[1])[:10]
        default_commission_category_detail = (
            " Категорії на дефолтній комісії (backlog для дослідження реальної ставки, "
            "не дія власниці): " + ", ".join(f"{cat} ({count})" for cat, count in top_categories)
        )
        if len(category_counts) > 10:
            default_commission_category_detail += f" та ще {len(category_counts) - 10} категорій"

    hard_cap_tripped = len(to_delist) > MAX_DELIST_PER_RUN

    delist_breaker_reasons, adjust_breaker_reasons, avg_margin_this_run = evaluate_circuit_breaker(to_adjust, to_delist, price_state)
    if avg_margin_this_run is not None:
        print(f"[Pricer] Circuit breaker (adjust): середня маржа цього прогону {avg_margin_this_run:.1f}%"
              + (f" — СПРАЦЮВАВ: {'; '.join(adjust_breaker_reasons)}" if adjust_breaker_reasons else " — OK"))
    if delist_breaker_reasons:
        print(f"[Pricer] Circuit breaker (delist): СПРАЦЮВАВ: {'; '.join(delist_breaker_reasons)}")
    if hard_cap_tripped:
        print(f"[Pricer] MAX_DELIST_PER_RUN: {len(to_delist)} > {MAX_DELIST_PER_RUN} — "
              "жорсткий ліміт, --force-circuit-breaker його НЕ обходить.")

    # ВИПРАВЛЕНО (2026-07-21, пряме прохання власниці — "чому блокувалось
    # ПОВНА кількість... а не пропускав дозволену, а інші залишав"):
    # раніше БУДЬ-яка причина (навіть суто про масштаб delist) блокувала
    # ОБИДВА кошики одним спільним sys.exit(1) — учора 469 легітимних
    # коригувань ціни не застосувались лише тому, що delist-кандидатів
    # виявилось забагато. Тепер кожен кошик гейтується ЛИШЕ своїми
    # причинами (див. docstring evaluate_circuit_breaker): delist_blocked
    # реагує на MAX_DELIST_PER_RUN і частку delist (обидва — виключно про
    # масштаб видалень); adjust_blocked реагує лише на середню
    # маржу/її падіння/частку суттєвих змін ціни (усі рахуються виключно
    # з to_adjust). --force-circuit-breaker обходить ОБИДВА (як і раніше,
    # це свідомий, разовий override для власниці) — MAX_DELIST_PER_RUN
    # і надалі НЕ обходиться нічим, окрім прямої зміни коду.
    delist_blocked = hard_cap_tripped or (bool(delist_breaker_reasons) and not args.force_circuit_breaker)
    adjust_blocked = bool(adjust_breaker_reasons) and not args.force_circuit_breaker

    if not args.apply:
        print("\n[Pricer] DRY-RUN: жодних змін не внесено. Запусти з --apply, щоб реально застосувати.")
        digest = (
            f"📊 prom_competitor_pricer.py (dry-run, {len(items)} SKU): "
            f"пропоновано скоригувати ціну — {adjust_count}, "
            f"видалити як неконкурентні — {delist_count}, "
            f"конкурента не знайдено — {no_competitor_count}"
            + (f", виключено через непідтверджену комісію — {len(default_commission_skipped)}" if default_commission_skipped else "")
            + "."
        )
        if adjust_blocked:
            digest += (
                f"\n\n🚨 CIRCUIT BREAKER (adjust) ЗУПИНИВ БИ коригування цін: " + "; ".join(adjust_breaker_reasons)
            )
        if hard_cap_tripped:
            digest += (
                f"\n\n⛔ ЖОРСТКИЙ ЛІМІТ MAX_DELIST_PER_RUN ({MAX_DELIST_PER_RUN}) ЗУПИНИВ БИ видалення "
                f"({len(to_delist)} на видалення) — цей ліміт --force-circuit-breaker НЕ обходить, "
                "потрібна пряма зміна коду."
            )
        elif delist_breaker_reasons:
            digest += (
                f"\n\n🚨 CIRCUIT BREAKER (delist) ЗУПИНИВ БИ видалення: " + "; ".join(delist_breaker_reasons)
            )
        digest += "\n\n(--apply не вмикався, це лише пропозиція)"
        send_telegram_message(digest)
        write_pricer_summary(
            "Режим: dry-run (--apply не використовувався). Ротаційна партія оброблена."
            + (f" {len(default_commission_skipped)} SKU на дефолтній комісії виключено з auto-apply."
               + default_commission_category_detail if default_commission_skipped else "")
            + (f" Circuit breaker (adjust) СПРАЦЮВАВ БИ: {'; '.join(adjust_breaker_reasons)}" if adjust_blocked else "")
            + (f" Circuit breaker (delist) СПРАЦЮВАВ БИ: {'; '.join(delist_breaker_reasons)}" if (delist_breaker_reasons and not hard_cap_tripped) else ""),
            checked=len(items), adjust=adjust_count, delist=delist_count,
            no_competitor=no_competitor_count, errors=0,
        )
        return

    # Записуємо feed_only_price_updates у price_state ТІЛЬКИ тут (--apply
    # гілка, після return вище) — dry-run не має жодного побічного ефекту
    # на файл стану. Без apply_price(): прямий API-патч і delist лишаються
    # заблокованими для цих SKU, як і раніше — оновлюється лише те, що
    # побачить generate_prom_feed_top.py на наступній генерації фіда.
    if feed_only_price_updates:
        now_iso = datetime.now().isoformat()
        for pid, price, margin_pct, competitor_key, category, competitor_price, cost in feed_only_price_updates:
            price_state[pid] = {
                "price": price, "timestamp": now_iso, "competitor_key": competitor_key,
                "category": category, "competitor_price": competitor_price,
                "cost": cost, "margin_pct": margin_pct,
            }
        print(f"[Pricer] {len(feed_only_price_updates)} SKU на непідтвердженій комісії — "
              "ціна оновлена лише в price_state для фіда (без прямого API-патчу).")

    if delist_blocked and to_delist:
        if hard_cap_tripped:
            message = (
                f"🚨 prom_competitor_pricer.py --apply: видалення ЗУПИНЕНО жорстким лімітом "
                f"MAX_DELIST_PER_RUN ({len(to_delist)} > {MAX_DELIST_PER_RUN}).\n\n"
                "Цей ліміт НЕ обходиться --force-circuit-breaker навмисно — після інциденту "
                "2026-07-19 (обвал каталогу Prom з ~970 до ~244 живих товарів через 722 "
                "видалення за один прогін; Prom видаляє миттєво, але створює заміну значно "
                "повільніше). Якщо видалення такого масштабу дійсно виправдане — потрібна "
                "пряма зміна MAX_DELIST_PER_RUN у коді (новий PR, новий аудит), не прапорець "
                "командного рядка."
            )
        else:
            message = (
                "🚨 prom_competitor_pricer.py --apply: видалення ЗУПИНЕНО circuit breaker'ом (P0-3):\n\n"
                + "\n".join(f"- {r}" for r in delist_breaker_reasons)
                + "\n\nПеревір вручну і, якщо видалення дійсно виправдані, перезапусти з "
                  "--force-circuit-breaker."
            )
        message += f"\n\nКоригування ціни (to_adjust, {len(to_adjust)} шт.) НЕ заблоковано — обробляються нижче незалежно."
        print(f"\n[Pricer] {message}", file=sys.stderr)
        send_telegram_message(message)

    if adjust_blocked and to_adjust:
        message = (
            "🚨 prom_competitor_pricer.py --apply: коригування ЦІНИ ЗУПИНЕНО circuit breaker'ом (P0-3):\n\n"
            + "\n".join(f"- {r}" for r in adjust_breaker_reasons)
            + "\n\nПеревір вручну і, якщо зміни дійсно виправдані, перезапусти з "
              "--force-circuit-breaker.\n\n"
            f"Видалення (to_delist, {len(to_delist)} шт.) НЕ заблоковано цим сигналом — "
            "обробляються нижче незалежно (якщо не заблоковані окремо)."
        )
        print(f"\n[Pricer] {message}", file=sys.stderr)
        send_telegram_message(message)

    if not to_adjust and not to_delist:
        print("[Pricer] Немає що коригувати чи видаляти цього прогону.")
        write_pricer_summary(
            "Режим: --apply (0 коригувань, 0 видалень цього прогону).",
            checked=len(items), adjust=0, delist=0,
            no_competitor=no_competitor_count, errors=0,
        )
        return

    if adjust_blocked and delist_blocked:
        write_pricer_summary(
            "🚨 --apply: І коригування ціни, І видалення заблоковано (див. Telegram). Нічого не застосовано.",
            checked=len(items), adjust=adjust_count, delist=delist_count,
            no_competitor=no_competitor_count, errors=0,
        )
        sys.exit(1)

    print(f"\n[Pricer] Застосовую {0 if adjust_blocked else len(to_adjust)} коригувань ціни..."
          + (" (ЗАБЛОКОВАНО circuit breaker'ом)" if adjust_blocked else ""))
    # ВИПРАВЛЕНО 2026-07-12: раніше apply_price() лише писав ціну напряму в
    # Prom API й ніде не зберігав рішення — generate_prom_feed.py рахував
    # ціну з нуля на кожному прогоні (кожні 4 год) і тихо повертав її до
    # дефолтної формули "немає конкурента", перекреслюючи щойно застосовану
    # ціну за лічені години. Тепер зберігаємо {pid: {price, timestamp}} у
    # спільний стан (competitor_pricing.py), який generate_prom_feed.py
    # читає як price_overrides — доки запис не застаріє (>30 год). Той самий
    # price_state, завантажений на початку main() (містить вже записаний
    # _meta.last_batch_run) — не перезавантажуємо, щоб не загубити його.
    # P0-5 (2026-07-17, відновлено — раніше свідомо відкладено з коментарем
    # "виправити окремим fast-follow PR", а масштаб apply відтоді зріс у
    # сотні разів): періодичне збереження раз на SAVE_EVERY застосованих
    # позицій, а не лише один раз після всього циклу — перервання процесу
    # (таймаут/скасування job'у) посеред цього циклу тепер губить максимум
    # SAVE_EVERY-1 позицій стану, а не весь прогін.
    applied_count = 0
    delisted_since = price_state.setdefault("_delisted_since", {})
    # ЗАБЛОКОВАНО (adjust_blocked) -> порожній список, той самий безпечний
    # шаблон, що вже застосований нижче для to_delist.
    for pid, price, margin_pct, competitor_key, category, competitor_price, cost in ([] if adjust_blocked else to_adjust):
        if _time_budget_exceeded():
            print(f"[Pricer] Часовий бюджет вичерпано — зупиняю застосування цін достроково "
                  f"({applied_count} застосовано з {len(to_adjust)}).")
            break
        try:
            apply_price(pid, price)
            price_state[pid] = {
                "price": price, "timestamp": datetime.now().isoformat(), "competitor_key": competitor_key,
                "category": category, "competitor_price": competitor_price,
                "cost": cost, "margin_pct": margin_pct,
            }
            # ВИПРАВЛЕНО (2026-07-18, той самий інцидент, що й нижче): якщо
            # SKU раніше було підтверджено видаленим, а тепер знову
            # конкурентний (opinion "adjust", не "delist") — прибираємо
            # позначку, інакше select_top_items() назавжди виключав би
            # товар, який РЕАЛЬНО повернувся до конкурентності.
            delisted_since.pop(pid, None)
            applied_count += 1
            if applied_count % SAVE_EVERY == 0:
                save_prom_price_state(price_state)
        except (requests.exceptions.RequestException, PromEditError) as e:
            error_count += 1
            print(f"  - {pid}: помилка зміни ціни — {e}", file=sys.stderr)
    # avg_margin_this_run зберігається для порівняння НАСТУПНОГО прогону
    # лише якщо adjust реально виконувався цього разу — інакше заблокований
    # прогін (де to_adjust взагалі не торкались) спотворив би базу
    # порівняння для наступного circuit breaker.
    if avg_margin_this_run is not None and not adjust_blocked:
        price_state.setdefault("_meta", {})["last_avg_margin_pct"] = avg_margin_this_run
    if applied_count or (avg_margin_this_run is not None and not adjust_blocked):
        save_prom_price_state(price_state)

    # КРИТИЧНИЙ ФІКС (2026-07-18, реальний інцидент — SKU 266990/265230 та
    # ще ~357 інших): live-видалення через delist() відбувається ЛИШЕ в
    # Prom API — select_top_items() (generate_prom_feed_top.py, наступний
    # крок ТОГО САМОГО workflow-прогону) про це нічого не знав і одразу
    # знову включав щойно видалений SKU в prom_feed_top.xml, бо рахує
    # топ-970 виключно з даних Toysi/scan_state. Коли Prom періодично
    # імпортує цей прайс-лист — він сам відновлював "видалене" оголошення.
    # Тепер підтверджено видалені pid записуються в price_state
    # ("_delisted_since", round-tripped через feed-data, як і решта цього
    # файлу) — generate_prom_feed_top.py::_margin() виключає їх з відбору,
    # доки вони не з'являться в to_adjust вище (конкурент подешевшав чи
    # зник) — лише тоді запис прибирається і SKU знову претендує на топ.
    confirmed_delist_count = 0
    print(f"[Pricer] Видаляю {0 if delist_blocked else len(to_delist)} неконкурентних товарів..."
          + (" (ЗАБЛОКОВАНО)" if delist_blocked else "")
          + f" Спершу піднімаю ціну до безпечної межі для всіх {len(to_delist)} — "
            "незалежно від circuit breaker чи успіху самого видалення.")
    for pid, price, margin_pct, competitor_key, category, competitor_price, cost in to_delist:
        if _time_budget_exceeded():
            print(f"[Pricer] Часовий бюджет вичерпано — зупиняю видалення достроково "
                  f"({confirmed_delist_count} видалено з {len(to_delist)}).")
            break
        # ДОДАНО (2026-07-27, пряме зауваження власниці — "нехай вона
        # більше показувала би та чекала на видалення, алеж не
        # демпінгувала би і не була би для нас збитковою"): ціна
        # піднімається до безпечної межі (price — той самий floor, що вже
        # включає net_revenue>=cost запобіжник) ЗАВЖДИ, навіть якщо
        # delist_blocked (circuit breaker) чи саме видалення нижче
        # провалиться — захист маржі не повинен чекати на успішне
        # видалення чи на зняття circuit breaker'а.
        try:
            apply_price(pid, price)
            price_state[pid] = {
                "price": price, "timestamp": datetime.now().isoformat(), "competitor_key": competitor_key,
                "category": category, "competitor_price": competitor_price, "cost": cost, "margin_pct": margin_pct,
            }
        except (requests.exceptions.RequestException, PromEditError) as e:
            error_count += 1
            print(f"  - {pid}: помилка підняття ціни перед видаленням — {e}", file=sys.stderr)

        if delist_blocked:
            continue
        try:
            delist(pid)
            delisted_since[pid] = datetime.now().isoformat()
            confirmed_delist_count += 1
        except (requests.exceptions.RequestException, PromEditError) as e:
            error_count += 1
            print(f"  - {pid}: помилка видалення — {e}", file=sys.stderr)
    save_prom_price_state(price_state)

    if not delist_blocked and confirmed_delist_count != len(to_delist):
        print(
            f"[Pricer] УВАГА: підтверджено видалено {confirmed_delist_count} з {len(to_delist)} "
            "запланованих — решта не пройшла перевірку processed_ids (див. помилки вище).",
            file=sys.stderr,
        )

    print(f"[Pricer] Готово. Помилок: {error_count}.")
    digest = (
        f"💰 prom_competitor_pricer.py --apply: скориговано цін — "
        f"{'0 (ЗАБЛОКОВАНО circuit breaker)' if adjust_blocked else applied_count}, "
        f"видалено як неконкурентні — "
        f"{'0 (ЗАБЛОКОВАНО)' if delist_blocked else confirmed_delist_count} товарів"
        + (f", виключено через непідтверджену комісію — {len(default_commission_skipped)}" if default_commission_skipped else "")
        + f". Помилок: {error_count}."
    )
    send_telegram_message(digest)
    write_pricer_summary(
        "Режим: --apply"
        + (" (частково заблоковано circuit breaker'ом — див. Telegram)." if (adjust_blocked or delist_blocked) else " (реальні зміни застосовано).")
        + " Ротаційна партія оброблена."
        + (f" {len(default_commission_skipped)} SKU на дефолтній комісії виключено з auto-apply."
           + default_commission_category_detail if default_commission_skipped else ""),
        checked=len(items), adjust=(0 if adjust_blocked else applied_count),
        delist=(0 if delist_blocked else confirmed_delist_count),
        no_competitor=no_competitor_count, errors=error_count,
    )
    if adjust_blocked or delist_blocked:
        sys.exit(1)


if __name__ == "__main__":
    main()
