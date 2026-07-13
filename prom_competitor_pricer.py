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
import os
import re
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from parser import fetch_toysi_catalog, assert_catalog_size_sane, CatalogSizeError
from generate_prom_feed_top import select_top_items
from generate_prom_feed import fetch_russian_text
from competitor_pricing import decide_price_for_platform, load_prom_price_state, save_prom_price_state
from telegram_notify import send_telegram_message

load_dotenv()

PROM_API_KEY  = os.environ.get("PROM_API_KEY", "")
PROM_API_URL  = "https://my.prom.ua/api/v1"
PROM_GRAPHQL_URL = "https://prom.ua/graphql"
REQUEST_TIMEOUT  = 20

# c4219597-plutustoys.html — company_id власного магазину, підтверджено
# напряму з URL кабінету/сторінки компанії. Результати пошуку з цим
# company_id — це МИ САМІ, не конкурент, завжди виключаються.
PROM_OWN_COMPANY_ID = 4219597

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

EDIT_BATCH = 100  # POST /products/edit_by_external_id, як і в prom_catalog_sync.py

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


def find_best_competitor(search_name: str, cost: float) -> dict | None:
    """Шукає на Prom.ua, виключає власні товари й товари поза розумним
    ціновим діапазоном, повертає найкращий за текстовою схожістю кандидат,
    або None, якщо жоден не проходить поріг впевненості — у цьому разі
    ціна рахується формульно (як для "no_competitor" в decide_price_for_platform),
    а НЕ вгадується з ненадійного збігу."""
    results = search_prom_products(search_name)
    candidates = []
    for p in results:
        if p.get("company_id") == PROM_OWN_COMPANY_ID:
            continue
        presence = p.get("presence") or {}
        if not presence.get("isAvailable"):
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
    candidates.sort(key=lambda c: (-c["score"], c["price"]))
    return candidates[0]


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
    if competitor and decision["category"] == "floor":
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
    args = ap.parse_args()

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
            return
    price_state.setdefault("_meta", {})["last_full_run"] = datetime.now().isoformat()
    save_prom_price_state(price_state)

    print("[Pricer] Рахую поточний відбір топ-970...")
    toysi_catalog = fetch_toysi_catalog()
    try:
        assert_catalog_size_sane(toysi_catalog)
    except CatalogSizeError as e:
        print(f"[Pricer] {e}", file=sys.stderr)
        send_telegram_message(f"🚨 prom_competitor_pricer.py зупинено: {e}")
        sys.exit(1)

    top_catalog = select_top_items(toysi_catalog)
    print(f"[Pricer] У топ-970: {len(top_catalog)} товарів.")

    print("[Pricer] Завантажуємо російськомовні назви (кращий збіг з пошуком Prom)...")
    russian_text = fetch_russian_text()

    items = list(top_catalog.items())[:args.limit]
    print(f"[Pricer] Обробляю {len(items)} товарів (--limit {args.limit})...")

    adjust_count, delist_count, no_competitor_count, error_count = 0, 0, 0, 0
    to_adjust, to_delist, delist_details = [], [], []

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

        competitor = find_best_competitor(name_rus, cost)
        decision = decide_action(cost, competitor, category_name, name_rus)
        time.sleep(SEARCH_DELAY)

        comp_desc = (
            f"конкурент {decision['competitor']['price']:.0f} грн "
            f"(score={decision['competitor']['score']:.2f}) {decision['competitor']['name'][:40]!r}"
            if decision["competitor"] else "конкурент не знайдено"
        )
        size_note = "  [РОЗМІР/ОБ'ЄМ НЕ ЗБІГАЄТЬСЯ -> delist заблоковано, залишено adjust]" if decision.get("size_conflict") else ""
        print(f"{pid}\t{name_ukr[:45]:45s}\tcost={cost:.0f}\tfloor={decision['floor']:.0f}\t"
              f"price={decision['price']:.0f}\t[{decision['action']}]\t{comp_desc}{size_note}")

        if decision["competitor"] is None:
            no_competitor_count += 1
        if decision["action"] == "adjust":
            adjust_count += 1
            to_adjust.append((pid, decision["price"]))
        elif decision["action"] == "delist":
            delist_count += 1
            to_delist.append(pid)
            delist_details.append(
                f"{pid} {name_ukr[:40]} (наша {decision['floor']:.0f} грн vs "
                f"конкурент {decision['competitor']['price']:.0f} грн)"
            )

    print(f"\n[Pricer] Підсумок: adjust={adjust_count}, delist={delist_count}, "
          f"без знайденого конкурента={no_competitor_count}")

    if not args.apply:
        print("\n[Pricer] DRY-RUN: жодних змін не внесено. Запусти з --apply, щоб реально застосувати.")
        digest = (
            f"📊 prom_competitor_pricer.py (dry-run, {len(items)} SKU): "
            f"пропоновано скоригувати ціну — {adjust_count}, "
            f"видалити як неконкурентні — {delist_count}, "
            f"конкурента не знайдено — {no_competitor_count}."
        )
        if delist_details:
            digest += "\n\nКандидати на видалення:\n" + "\n".join(delist_details[:15])
            if len(delist_details) > 15:
                digest += f"\n... та ще {len(delist_details) - 15}"
        digest += "\n\n(--apply не вмикався, це лише пропозиція)"
        send_telegram_message(digest)
        return

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
    applied_count = 0
    for pid, price in to_adjust:
        try:
            apply_price(pid, price)
            price_state[pid] = {"price": price, "timestamp": datetime.now().isoformat()}
            applied_count += 1
        except requests.exceptions.RequestException as e:
            error_count += 1
            print(f"  - {pid}: помилка зміни ціни — {e}", file=sys.stderr)
    if applied_count:
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
    send_telegram_message(digest)


if __name__ == "__main__":
    main()
