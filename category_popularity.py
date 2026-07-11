"""
category_popularity.py — відносний рейтинг популярності КАТЕГОРІЙ товару
(не окремих SKU) через Google Trends (`pytrends`, безкоштовно, без
реєстрації/акаунту Google). На рівні SKU (~29 000 у повному каталозі
Toysi) Trends технічно непридатний — ліміт 5 ключових слів на запит,
і "популярність конкретного артикулу" — не те, що люди шукають у Google
(шукають "дитячий самокат", не код товару). Категорія — правильна
гранулярність.

МЕХАНІКА НОРМАЛІЗАЦІЇ (важливо): Google Trends повертає числа 0-100
ВІДНОСНО ОДИН ДО ОДНОГО лише В МЕЖАХ ОДНОГО запиту (макс. 5 ключових
слів) — "100" в одному запиті й "100" в іншому НЕ порівнювані напряму,
бо кожен запит масштабується під свій власний максимум. При 292
категоріях (повний каталог Toysi) це означає ~73 окремих запити
(батчі по 4 категорії) — без прив'язки результати різних батчів
не можна було б покласти на одну шкалу для сортування.

Рішення — спільний "якір" (ANCHOR_KEYWORD = "іграшки", загальне й
завідомо ненульове слово) у КОЖНОМУ батчі поруч із 4 категоріями:
відносний скор категорії = (її середнє значення / середнє значення
якоря в ТОМУ САМОМУ батчі) * 100. Якщо реальна популярність якоря
приблизно стабільна між батчами (а вона має бути — це той самий
запит з тим самим періодом/регіоном, статистичний шум від видачі
Google, не системне зміщення), це робить скори категорій із РІЗНИХ
батчів порівнюваними одне з одним.

Кешується на CACHE_TTL_DAYS (30) — Trends-запит повільний (мінути на
повний каталог) і легко тригерить rate-limit Google при частих
повторах, а популярність категорій і так не змінюється швидко
("кілька місяців" — сама постановка задачі).
"""

import json
import random
import time
from pathlib import Path

from pytrends.request import TrendReq

CACHE_FILE = Path(__file__).parent / "category_popularity_cache.json"
CACHE_TTL_DAYS = 30

ANCHOR_KEYWORD = "іграшки"
BATCH_SIZE = 4  # + 1 якір = 5, максимум pytrends на один запит
TIMEFRAME = "today 3-m"
GEO = "UA"
REQUEST_DELAY_RANGE = (3.0, 6.0)  # секунд між батчами — не тригерити rate-limit Google,
                                   # той самий підхід (джитер, не фіксований інтервал),
                                   # що вже усталений у repricer.py цього репо
MAX_RETRIES = 3


def _fetch_batch(pytrends: TrendReq, keywords: list) -> dict:
    """Один запит (якір + до 4 категорій), середнє значення за період на
    кожне ключове слово. Порожній dict, якщо всі спроби провалились —
    виклик далі просто пропускає цей батч, не падає."""
    for attempt in range(MAX_RETRIES):
        try:
            pytrends.build_payload(keywords, cat=0, timeframe=TIMEFRAME, geo=GEO)
            df = pytrends.interest_over_time()
            if df.empty:
                return {}
            return {kw: float(df[kw].mean()) for kw in keywords if kw in df.columns}
        except Exception as e:
            wait = 10 * (attempt + 1)
            print(f"[Trends] Помилка запиту (спроба {attempt + 1}/{MAX_RETRIES}): {e} — чекаю {wait}с")
            time.sleep(wait)
    return {}


def fetch_category_popularity(category_names: list, force_refresh: bool = False) -> dict:
    """Повертає {category_name: відносний_скор} — скори порівнювані МІЖ
    категоріями (див. докстрінг модуля про якір), НЕ прив'язані до
    жодної абсолютної шкали (не "0-100 популярності світу", а "у скільки
    разів популярніше/менш популярне за якір 'іграшки' в Україні за
    останні 3 місяці")."""
    unique_categories = sorted({c for c in category_names if c})

    if not force_refresh and CACHE_FILE.exists():
        age_days = (time.time() - CACHE_FILE.stat().st_mtime) / 86400
        if age_days < CACHE_TTL_DAYS:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            cached_scores = cached.get("scores", {})
            if set(unique_categories) <= set(cached_scores):
                print(f"[Trends] Використовую кеш ({age_days:.1f} днів старий, {len(cached_scores)} категорій)")
                return cached_scores

    print(f"[Trends] Запитую популярність {len(unique_categories)} категорій "
          f"({(len(unique_categories) - 1) // BATCH_SIZE + 1} батчів)...")
    pytrends = TrendReq(hl="uk-UA", tz=180)
    scores: dict = {}
    failed_batches = 0

    for i in range(0, len(unique_categories), BATCH_SIZE):
        batch = unique_categories[i:i + BATCH_SIZE]
        keywords = [ANCHOR_KEYWORD] + batch
        result = _fetch_batch(pytrends, keywords)
        anchor_value = result.get(ANCHOR_KEYWORD)
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(unique_categories) - 1) // BATCH_SIZE + 1
        if not anchor_value:
            failed_batches += 1
            print(f"[Trends] Батч {batch_num}/{total_batches}: якір не повернув даних — пропущено {len(batch)} категорій")
        else:
            for cat in batch:
                raw = result.get(cat, 0.0)
                scores[cat] = round((raw / anchor_value) * 100, 2)
            print(f"[Trends] Батч {batch_num}/{total_batches}: {', '.join(batch)}")
        time.sleep(random.uniform(*REQUEST_DELAY_RANGE))

    print(f"[Trends] Готово: {len(scores)}/{len(unique_categories)} категорій отримано "
          f"({failed_batches} батчів провалились)")

    CACHE_FILE.write_text(
        json.dumps({"fetched_at": time.time(), "scores": scores}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return scores


def get_popularity_score(category_name: str, scores: dict) -> float:
    """Скор категорії, або МЕДІАНА всіх відомих скорів як нейтральний
    fallback (НЕ 0 — 0 хибно трактував би "не вдалось запитати" як
    "точно непопулярно", підмішуючи шум запиту в реальне ранжування)."""
    if category_name in scores:
        return scores[category_name]
    if scores:
        values = sorted(scores.values())
        return values[len(values) // 2]
    return 50.0  # цілковитий fallback, якщо взагалі нічого не отримано (напр. Trends недоступний)
