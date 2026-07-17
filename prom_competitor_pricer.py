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

Гібридна політика (узгоджена власником, 2026-07-11):
- normal/floor -> автокоригування ціни в межах нижньої межі маржі
  (та сама формула, що й decide_price_for_platform() для інших джерел
  конкурентних цін).
- floor настільки вищий за конкурента, що навіть НАЙНИЖЧА прийнятна
  ціна не є конкурентною (floor > конкурент * MAX_FLOOR_TO_COMPETITOR_RATIO)
  -> видалення товару з каталогу (status=deleted, той самий виклик, що й
  prom_catalog_sync.py).

⚠️ MAX_FLOOR_TO_COMPETITOR_RATIO (наскільки floor може перевищувати
конкурента, лишаючись "просто дорожчим", а не "нежиттєздатним") —
КОНКРЕТНЕ число НЕ було задано власником, лише сам ПРИНЦИП гібридної
політики. Значення нижче — початковий консервативний дефолт, підлягає
підтвердженню власником ПЕРЕД тим, як --apply (особливо видалення) почне
виконуватись автономно на регулярній основі, а не лише в dry-run.

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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from parser import fetch_toysi_catalog, assert_catalog_size_sane, CatalogSizeError
from generate_prom_feed_top import select_top_items
from generate_prom_feed import fetch_russian_text
from competitor_pricing import (
    decide_price_for_platform, load_prom_price_state, save_prom_price_state,
    PROM_CATEGORY_COMMISSION, PROM_COMMISSION_DEFAULT, _BAD_NAME_MARKERS,
)
from telegram_notify import send_telegram_message

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
            "source": "buybox",
        }
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None


# 2026-07-17: 284/970 (29%) топ-970 SKU наразі йдуть на PROM_COMMISSION_
# DEFAULT (20%) — реальна ставка категорії не підтверджена (кабінет Prom
# -> Показники роботи компанії -> Комісія за замовлення). Дефолт може
# бути занижений відносно реальної ставки категорії (як-от підтверджені
# 23-25% для кількох суміжних категорій) — застосування авто-ціни на
# невірному, заниженому дефолті ризикує реальним заниженням floor і
# маржі, непомітно для жодної наявної перевірки. Такі SKU виключаються
# з --apply (ні adjust, ні delist) до ручного підтвердження реальної
# ставки категорії; дозволяється дозволити з ЯВНИМ прапорцем.
def _category_commission_is_default(category_name: str | None) -> bool:
    key = (category_name or "").strip().lower()
    return key not in PROM_CATEGORY_COMMISSION


SEARCH_LIMIT = 20  # скільки кандидатів забирати на один пошуковий запит

# ВИПРАВЛЕНО 2026-07-12 (розслідування SKU 242610/289818): попереднє значення
# 300 було помилково скопійоване "за аналогією" з DAILY_LIMIT у
# competitor_pricing.py — але ТОЙ ліміт існує для РУЧНОГО процесу (оператор
# сам перевіряє ціни конкурентів на Rozetka, немає автоматичного пошуку),
# де денний ліміт має сенс. Тут — повністю автоматизований GraphQL-пошук,
# і жодного реального обмеження часу виконання немає: живий прогін 300
# позицій зайняв 6 хв 47 с (перевірено, journalctl 2026-07-12), тобто повні
# 970 зайняли б ~22 хв — цілком нормально для сервісу з власним systemd-
# таймером (не CI з обмеженим часом). Старий ліміт 300 і БЕЗ ротації (той
# самий, стабільний за маржею зріз top_catalog.items()[:300] щодня) означав,
# що товари на позиціях 301-970 (майже 2/3 топ-970) НІКОЛИ не перевірялись —
# саме тому SKU 242610 (позиція #492) жодного разу не отримав коригування
# ціни за конкурентом, хоча реальний, вигідний конкурент (Gummy) є.
DAILY_LIMIT  = 1000  # з запасом вище будь-якого реального розміру топ-970
SEARCH_DELAY = 0.4  # секунд між пошуковими запитами — не бомбардувати ендпоінт

# Мігровано на GitHub Actions 2026-07-13 (той самий workflow update-feeds.yml,
# що й generate_prom_feed.py) — контейнер тепер тригериться раз на 4 год
# (cron), а не раз на добу systemd-таймером на VPS. Без цього гейту повний
# GraphQL-пошук конкурентів (і, при --apply, зміна живих цін) відбувався б
# 6х/добу замість 1х — і бомбардування реверс-інженерного ендпоінта Prom
# вшестеро частіше, і живі ціни смикались би щочотири години замість
# приблизно раз на день. Трохи менше доби (не 24), щоб дрейф часу тригера
# cron не пропускав день повністю.
MIN_FULL_RUN_INTERVAL_HOURS = 20

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

# Наскільки floor може перевищувати ціну конкурента, лишаючись "просто
# дорожчою пропозицією", а не кандидатом на видалення. НЕ підтверджено
# власником як конкретне число — консервативний початковий дефолт.
MAX_FLOOR_TO_COMPETITOR_RATIO = 1.5

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
CIRCUIT_BREAKER_MIN_AVG_MARGIN_PCT = 8.0
CIRCUIT_BREAKER_MAX_MARGIN_DROP_PCT = 15.0
CIRCUIT_BREAKER_PRICE_CHANGE_THRESHOLD = 0.05
CIRCUIT_BREAKER_MAX_CHANGED_FRACTION = 0.5
# Менше цього — надто мало даних, щоб частка "змінилось" щось значила
# (напр. 2 з 3 — 67%, виглядає тривожно, але це шум малої вибірки).
CIRCUIT_BREAKER_MIN_KNOWN_FOR_FRACTION_CHECK = 10

EDIT_BATCH = 100  # POST /products/edit_by_external_id, як і в prom_catalog_sync.py

# P0-5: раз на стільки успішно застосованих коригувань ціни зберігати
# price_state на диск (не лише один раз наприкінці всього циклу).
SAVE_EVERY = 25

# Мінімальна GraphQL-схема — ЛИШЕ поля, що реально використовуються тут.
# Свідомо не той величезний (11.7К символів) запит, яким сама сторінка
# пошуку тягне SEO-теги/фільтри/мотори тощо, — вужчий контракт, менший
# ризик поламатись, якщо Prom змінить поля, які нас не цікавлять.
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


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _normalize_name(a), _normalize_name(b)).ratio()


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


def find_best_competitor(search_name: str, cost: float, own_link: dict | None = None) -> dict | None:
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
    реальний варіант, який покупець міг би обрати замість нас."""
    if own_link:
        buybox = fetch_buybox_competitor(own_link["prom_id"], own_link["url_text"])
        if buybox is not None:
            return buybox

    results = search_prom_products(search_name)
    candidates = []
    for p in results:
        if p.get("company_id") == PROM_OWN_COMPANY_ID:
            continue
        presence = p.get("presence") or {}
        if not presence.get("isAvailable"):
            continue
        # ДОДАНО (2026-07-17, знайдено при точковому скані SKU 300391 поза
        # чергою): SearchListingQuery-фолбек не фільтрував конкурентів за
        # маркерами уцінки/пошкодження в НАЗВІ — уцінений/пошкоджений
        # товар конкурента (типово найдешевший, бо не в товарному вигляді)
        # легко проходить MATCH_MIN_SCORE і стає "найдешевшим кандидатом",
        # штучно занижуючи floor/margin для товару, що насправді не
        # порівнюваний. Той самий список маркерів, що вже фільтрує НАШ
        # власний каталог у competitor_pricing.py's select_batch().
        candidate_name_lower = (p.get("name") or "").lower()
        if any(marker in candidate_name_lower for marker in _BAD_NAME_MARKERS):
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
                                "id": p.get("id"), "urlText": p.get("urlText")})

    if not candidates:
        return None
    candidates.sort(key=lambda c: c["price"])
    return candidates[0]


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


def evaluate_circuit_breaker(to_adjust: list, price_state: dict) -> tuple[bool, list, float | None]:
    """P0-3: див. коментар біля CIRCUIT_BREAKER_* констант. `to_adjust` —
    список (pid, price, margin_pct) кандидатів на коригування цього
    прогону. Повертає (спрацював, причини, середня_маржа_цього_прогону) —
    середня маржа повертається завжди (навіть якщо breaker не спрацював),
    щоб main() міг зберегти її для порівняння НАСТУПНОГО прогону."""
    if not to_adjust:
        return False, [], None

    avg_margin_this_run = sum(m for _, _, m in to_adjust) / len(to_adjust)

    known_count = 0
    changed_count = 0
    for pid, price, _ in to_adjust:
        prior_entry = price_state.get(pid)
        prior_price = prior_entry.get("price") if isinstance(prior_entry, dict) else None
        if not prior_price:
            continue
        known_count += 1
        if abs(price - prior_price) / prior_price > CIRCUIT_BREAKER_PRICE_CHANGE_THRESHOLD:
            changed_count += 1
    changed_fraction = changed_count / known_count if known_count else 0.0

    prev_avg_margin = (price_state.get("_meta") or {}).get("last_avg_margin_pct")

    reasons = []
    if avg_margin_this_run < CIRCUIT_BREAKER_MIN_AVG_MARGIN_PCT:
        reasons.append(
            f"середня маржа цього прогону {avg_margin_this_run:.1f}% нижче абсолютного "
            f"порогу {CIRCUIT_BREAKER_MIN_AVG_MARGIN_PCT}%"
        )
    if prev_avg_margin is not None:
        margin_drop = prev_avg_margin - avg_margin_this_run
        if margin_drop > CIRCUIT_BREAKER_MAX_MARGIN_DROP_PCT:
            reasons.append(
                f"середня маржа впала на {margin_drop:.1f} п.п. відносно попереднього "
                f"прогону ({prev_avg_margin:.1f}% -> {avg_margin_this_run:.1f}%)"
            )
    if known_count >= CIRCUIT_BREAKER_MIN_KNOWN_FOR_FRACTION_CHECK and changed_fraction > CIRCUIT_BREAKER_MAX_CHANGED_FRACTION:
        reasons.append(
            f"{changed_fraction * 100:.0f}% товарів із уже відомою ціною ({changed_count}/{known_count}) "
            f"отримали суттєво іншу ціну (>{CIRCUIT_BREAKER_PRICE_CHANGE_THRESHOLD * 100:.0f}%) за один прогін"
        )

    return bool(reasons), reasons, avg_margin_this_run


def decide_action(cost: float, competitor: dict | None, category_name: str | None, our_name: str = "") -> dict:
    """Гібридна дія: normal/floor -> "adjust" (нова ціна = decision["price"]);
    floor настільки вищий за конкурента, що навіть він неконкурентний ->
    "delist". Без знайденого конкурента (чи `find_best_competitor` не дав
    впевненого збігу) -> "adjust" на формульну (no_competitor) ціну, як і
    завжди, НІКОЛИ не "delist" — видалення вимагає реального сигналу про
    те, що ми програємо конкретному конкуренту, не просто відсутність
    даних про нього."""
    min_competitor_prom = competitor["price"] if competitor else None
    decision = decide_price_for_platform(cost, min_competitor_prom, "prom", category_name)
    action = "adjust"
    size_conflict = False
    # competitor.get("source") == "buybox" (2026-07-14): buyBox дає лише
    # СУМАРНУ найнижчу ціну серед інших продавців, без конкретного
    # оголошення (id/urlText/name — None) — delist вимагає перевіряти
    # ЖИВІСТЬ конкретного оголошення (verify_competitor_really_available),
    # чого тут просто нема що перевіряти. УТОЧНЕНО (незалежне рев'ю PR #53):
    # це ЄДИНИЙ реальний захист, не "другий рівень" — verify_competitor_
    # really_available() тут навіть НЕ викликається для buyBox-кандидатів,
    # бо ця перевірка нижче блокує action="delist" РАНІШЕ, до виклику
    # verify_competitor_really_available() у main() (та функція гейтована
    # за `if decision["action"] == "delist"`).
    if competitor and decision["category"] == "floor" and competitor.get("source") != "buybox":
        if decision["floor"] > competitor["price"] * MAX_FLOOR_TO_COMPETITOR_RATIO:
            # Delist вимагає окремого, суворішого порогу впевненості збігу
            # (MATCH_MIN_SCORE_FOR_DELIST) — при недостатній впевненості
            # безпечний fallback: усе одно скоригувати ціну до floor
            # (найгірший наслідок хибного збігу тут — трохи неоптимальна
            # ціна, не втрата живого оголошення).
            if competitor["score"] >= MATCH_MIN_SCORE_FOR_DELIST:
                # Додатковий цільовий гейт (аудит, 2026-07-11): навіть при
                # score >= 0.85 delist блокується, якщо назви містять явно
                # різні числові/розмірні токени (див. _size_tokens_conflict) —
                # SequenceMatcher сам по собі не бачить різницю між "розмір
                # відрізняється" і "формулювання відрізняється".
                if _size_tokens_conflict(our_name, competitor["name"]):
                    size_conflict = True
                else:
                    action = "delist"
    decision["action"] = action
    decision["competitor"] = competitor
    decision["size_conflict"] = size_conflict
    return decision


def apply_price(external_id: str, price: float) -> None:
    response = requests.post(
        f"{PROM_API_URL}/products/edit_by_external_id",
        headers={"Authorization": f"Bearer {PROM_API_KEY}"},
        json=[{"id": external_id, "price": price}],
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()


def delist(external_id: str) -> None:
    response = requests.post(
        f"{PROM_API_URL}/products/edit_by_external_id",
        headers={"Authorization": f"Bearer {PROM_API_KEY}"},
        json=[{"id": external_id, "status": "deleted"}],
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                     help="Реально змінювати ціни/видаляти товари в Prom. Без цього — лише dry-run звіт.")
    ap.add_argument("--limit", type=int, default=DAILY_LIMIT,
                     help=f"Скільки SKU топ-970 обробити за цей запуск (дефолт {DAILY_LIMIT}).")
    ap.add_argument("--force", action="store_true",
                     help="Ігнорувати перевірку 'повний прогін вже був нещодавно' (для ручного/тестового запуску).")
    ap.add_argument("--allow-default-commission", action="store_true",
                     help="Дозволити --apply для SKU, чия категорія на PROM_COMMISSION_DEFAULT "
                          "(реальна ставка не підтверджена) — за замовчуванням такі SKU виключено "
                          "з auto-apply, лише позначені для ручного перегляду.")
    ap.add_argument("--force-circuit-breaker", action="store_true",
                     help="Ігнорувати circuit breaker (P0-3) і застосувати зміни навіть якщо він спрацював. "
                          "ОКРЕМИЙ прапорець від --force (той стосується лише 20-годинного гейту) — це "
                          "інша, серйозніша категорія ризику (масова зміна цін/обвал маржі), тому свідомо "
                          "не ділить один і той самий перемикач.")
    args = ap.parse_args()

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
    last_run_iso = (price_state.get("_meta") or {}).get("last_full_run")
    if last_run_iso and not args.force:
        try:
            hours_since = (datetime.now() - datetime.fromisoformat(last_run_iso)).total_seconds() / 3600
        except ValueError:
            hours_since = None
        if hours_since is not None and hours_since < MIN_FULL_RUN_INTERVAL_HOURS:
            print(f"[Pricer] Повний прогін вже був {hours_since:.1f} год тому "
                  f"(< {MIN_FULL_RUN_INTERVAL_HOURS} год) — пропускаю цей запуск, щоб не робити повний "
                  f"пошук конкурентів на кожному 4-годинному тригері GitHub Actions. Викликай з --force, "
                  f"щоб примусово запустити повний прогін зараз.")
            write_pricer_summary(
                f"20-годинний гейт: СПРАЦЮВАВ — повний прогін пропущено "
                f"(попередній був {hours_since:.1f} год тому, поріг {MIN_FULL_RUN_INTERVAL_HOURS} год)."
            )
            return

    print("[Pricer] Рахую поточний відбір топ-970...")
    toysi_catalog = fetch_toysi_catalog()
    try:
        assert_catalog_size_sane(toysi_catalog)
    except CatalogSizeError as e:
        print(f"[Pricer] {e}", file=sys.stderr)
        send_telegram_message(f"🚨 prom_competitor_pricer.py зупинено: {e}")
        write_pricer_summary(f"⚠ Зупинено: каталог Toysi порожній/замалий ({e}).", errors=1)
        sys.exit(1)

    # ВИПРАВЛЕНО (рев'ю PR #40, знахідка №1): мітку гейту пишемо ЛИШЕ після
    # успішної валідації каталогу, а не до fetch_toysi_catalog(). Раніше
    # запис відбувався одразу після перевірки "чи не рано", ДО фетчу — і
    # fetch_toysi_catalog() ковтає мережеві помилки, повертаючи {} замість
    # винятку, тож одна транзиєнтна проблема з боку Toysi "отруювала" гейт
    # на всі 20 год (sys.exit(1) нижче стався б уже ПІСЛЯ запису мітки).
    # Гейт існує, щоб не бомбардувати дорогий GraphQL-пошук конкурентів
    # частіше приблизно раз на добу — а не щоб захищати дешевий, швидко
    # відмовний фетч каталогу, тож правильне місце для мітки — тут.
    price_state.setdefault("_meta", {})["last_full_run"] = datetime.now().isoformat()
    save_prom_price_state(price_state)

    top_catalog = select_top_items(toysi_catalog)
    print(f"[Pricer] У топ-970: {len(top_catalog)} товарів.")

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

    items = list(top_catalog.items())[:args.limit]
    print(f"[Pricer] Обробляю {len(items)} товарів (--limit {args.limit})...")

    adjust_count, delist_count, no_competitor_count, error_count = 0, 0, 0, 0
    buybox_count = 0
    buybox_attempted_count = 0  # own_link був — buyBox ПРОБУВАЛИ (незалежно від результату);
                                  # різке падіння buybox_count/buybox_attempted_count сигналізує
                                  # про зламаний _BUYBOX_RE, а не просто "мало конкурентів" (рев'ю PR #53)
    to_adjust, to_delist, delist_details = [], [], []
    default_commission_skipped = []  # (pid, name, category, price) — не потрапляють у to_adjust/to_delist

    for pid, item in items:
        try:
            cost = float(item.get("price") or 0)
        except (TypeError, ValueError):
            cost = 0
        if cost <= 0:
            continue

        name_ukr = (item.get("name") or "").strip()
        name_rus = (russian_text.get(pid, {}) or {}).get("name") or name_ukr
        category_name = item.get("category_name")

        own_link = own_product_links.get(pid)
        if own_link:
            buybox_attempted_count += 1
        competitor = find_best_competitor(name_rus, cost, own_link)
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
        decision = decide_action(cost, competitor, category_name, name_rus)
        time.sleep(SEARCH_DELAY)

        # Другий, надійніший гейт ПЕРЕД фінальним delist — GraphQL-пошук
        # (presence.isAvailable у find_best_competitor) сам по собі виявився
        # ненадійним (2026-07-12, SKU 298613/299070/299071 delist'ились 2 дні
        # поспіль на основі того самого протермінованого presence-флагу).
        # Пряма HTTP-перевірка реальної сторінки конкурента — лише для
        # кандидатів, що вже пройшли текстовий і розмірний гейт, тобто рідко
        # (одиниці з ~300 SKU за прогін), тож зайвий запит тут не проблема.
        presence_unconfirmed = False
        if decision["action"] == "delist":
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

        if _category_commission_is_default(category_name) and not args.allow_default_commission:
            default_commission_skipped.append((pid, name_ukr, category_name, decision["price"]))
            print(f"  -> {pid}: категорія {category_name!r} на дефолтній комісії "
                  f"({PROM_COMMISSION_DEFAULT:.0%}, не підтверджена) — виключено з auto-apply, "
                  "потребує ручного перегляду")
        elif decision["action"] == "adjust":
            adjust_count += 1
            to_adjust.append((pid, decision["price"], decision["margin_pct"]))
        elif decision["action"] == "delist":
            delist_count += 1
            to_delist.append(pid)
            delist_details.append(
                f"{pid} {name_ukr[:40]} (наша {decision['floor']:.0f} грн vs "
                f"конкурент {decision['competitor']['price']:.0f} грн)"
            )

    print(f"\n[Pricer] Підсумок: adjust={adjust_count}, delist={delist_count}, "
          f"без знайденого конкурента={no_competitor_count}, "
          f"на дефолтній комісії (виключено з auto-apply)={len(default_commission_skipped)}, "
          f"з них через buyBox (не SearchListingQuery): {buybox_count} "
          f"(пробували buyBox для {buybox_attempted_count} SKU — різке падіння "
          f"buybox_count/buybox_attempted_count сигналізує про зламаний regex, "
          f"не просто \"мало конкурентів\")")

    default_commission_note = ""
    if default_commission_skipped:
        default_commission_note = (
            f"\n\n⚠️ {len(default_commission_skipped)} SKU на дефолтній комісії "
            f"({PROM_COMMISSION_DEFAULT:.0%}, категорія не підтверджена в кабінеті Prom) — "
            "НЕ включено в auto-apply, потребують ручного перегляду ставки:\n"
            + "\n".join(f"{pid} {name[:40]} [{cat}]" for pid, name, cat, _ in default_commission_skipped[:15])
        )
        if len(default_commission_skipped) > 15:
            default_commission_note += f"\n... та ще {len(default_commission_skipped) - 15}"

    breaker_tripped, breaker_reasons, avg_margin_this_run = evaluate_circuit_breaker(to_adjust, price_state)
    if avg_margin_this_run is not None:
        print(f"[Pricer] Circuit breaker: середня маржа цього прогону {avg_margin_this_run:.1f}%"
              + (f" — СПРАЦЮВАВ: {'; '.join(breaker_reasons)}" if breaker_tripped else " — OK"))

    if not args.apply:
        print("\n[Pricer] DRY-RUN: жодних змін не внесено. Запусти з --apply, щоб реально застосувати.")
        digest = (
            f"📊 prom_competitor_pricer.py (dry-run, {len(items)} SKU): "
            f"пропоновано скоригувати ціну — {adjust_count}, "
            f"видалити як неконкурентні — {delist_count}, "
            f"конкурента не знайдено — {no_competitor_count}."
        )
        if breaker_tripped:
            digest += (
                f"\n\n🚨 CIRCUIT BREAKER СПРАЦЮВАВ БИ на --apply: " + "; ".join(breaker_reasons)
            )
        if delist_details:
            digest += "\n\nКандидати на видалення:\n" + "\n".join(delist_details[:15])
            if len(delist_details) > 15:
                digest += f"\n... та ще {len(delist_details) - 15}"
        digest += default_commission_note
        digest += "\n\n(--apply не вмикався, це лише пропозиція)"
        send_telegram_message(digest)
        write_pricer_summary(
            "Режим: dry-run (--apply не використовувався). 20-годинний гейт: не спрацював (повний прогін виконано)."
            + (f" {len(default_commission_skipped)} SKU на дефолтній комісії виключено з auto-apply." if default_commission_skipped else "")
            + (f" Circuit breaker СПРАЦЮВАВ БИ: {'; '.join(breaker_reasons)}" if breaker_tripped else ""),
            checked=len(items), adjust=adjust_count, delist=delist_count,
            no_competitor=no_competitor_count, errors=0,
        )
        return

    if breaker_tripped and not args.force_circuit_breaker:
        message = (
            "🚨 prom_competitor_pricer.py --apply ЗУПИНЕНО circuit breaker'ом (P0-3), "
            "ЖОДНИХ змін не внесено:\n\n" + "\n".join(f"- {r}" for r in breaker_reasons)
            + "\n\nПеревір вручну і, якщо зміни дійсно виправдані, перезапусти з "
              "--force-circuit-breaker."
        )
        print(f"\n[Pricer] {message}", file=sys.stderr)
        send_telegram_message(message)
        write_pricer_summary(
            f"🚨 Circuit breaker ЗУПИНИВ --apply (нічого не застосовано): {'; '.join(breaker_reasons)}",
            checked=len(items), adjust=adjust_count, delist=delist_count,
            no_competitor=no_competitor_count, errors=0,
        )
        sys.exit(1)

    print(f"\n[Pricer] Застосовую {len(to_adjust)} коригувань ціни...")
    # ВИПРАВЛЕНО 2026-07-12: раніше apply_price() лише писав ціну напряму в
    # Prom API й ніде не зберігав рішення — generate_prom_feed.py рахував
    # ціну з нуля на кожному прогоні (кожні 4 год) і тихо повертав її до
    # дефолтної формули "немає конкурента", перекреслюючи щойно застосовану
    # ціну за лічені години. Тепер зберігаємо {pid: {price, timestamp}} у
    # спільний стан (competitor_pricing.py), який generate_prom_feed.py
    # читає як price_overrides — доки запис не застаріє (>30 год). Той самий
    # price_state, завантажений на початку main() (містить вже записаний
    # _meta.last_full_run) — не перезавантажуємо, щоб не загубити його.
    # P0-5 (2026-07-17, відновлено — раніше свідомо відкладено з коментарем
    # "виправити окремим fast-follow PR", а масштаб apply відтоді зріс у
    # сотні разів): періодичне збереження раз на SAVE_EVERY застосованих
    # позицій, а не лише один раз після всього циклу — перервання процесу
    # (таймаут/скасування job'у) посеред цього циклу тепер губить максимум
    # SAVE_EVERY-1 позицій стану, а не весь прогін.
    applied_count = 0
    for pid, price, _ in to_adjust:
        try:
            apply_price(pid, price)
            price_state[pid] = {"price": price, "timestamp": datetime.now().isoformat()}
            applied_count += 1
            if applied_count % SAVE_EVERY == 0:
                save_prom_price_state(price_state)
        except requests.exceptions.RequestException as e:
            error_count += 1
            print(f"  - {pid}: помилка зміни ціни — {e}", file=sys.stderr)
    if avg_margin_this_run is not None:
        price_state.setdefault("_meta", {})["last_avg_margin_pct"] = avg_margin_this_run
    if applied_count or avg_margin_this_run is not None:
        save_prom_price_state(price_state)

    print(f"[Pricer] Видаляю {len(to_delist)} неконкурентних товарів...")
    for pid in to_delist:
        try:
            delist(pid)
        except requests.exceptions.RequestException as e:
            error_count += 1
            print(f"  - {pid}: помилка видалення — {e}", file=sys.stderr)

    print(f"[Pricer] Готово. Помилок: {error_count}.")
    digest = (
        f"💰 prom_competitor_pricer.py --apply: скориговано цін — {adjust_count}, "
        f"видалено як неконкурентні — {delist_count} товарів. Помилок: {error_count}."
    )
    if delist_details:
        digest += "\n\nВидалено:\n" + "\n".join(delist_details[:15])
        if len(delist_details) > 15:
            digest += f"\n... та ще {len(delist_details) - 15}"
    digest += default_commission_note
    send_telegram_message(digest)
    write_pricer_summary(
        "Режим: --apply (реальні зміни застосовано). 20-годинний гейт: не спрацював (повний прогін виконано)."
        + (f" {len(default_commission_skipped)} SKU на дефолтній комісії виключено з auto-apply." if default_commission_skipped else ""),
        checked=len(items), adjust=adjust_count, delist=delist_count,
        no_competitor=no_competitor_count, errors=error_count,
    )


if __name__ == "__main__":
    main()
