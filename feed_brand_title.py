# -*- coding: utf-8 -*-
"""feed_brand_title.py — марка виробника в g:title фідів Google/Meta/Bing (запит SEO 19.11.2026).

ЧОМУ: ШІ-агенти (ChatGPT/Gemini/AI Overviews) і Google шукають товар за «марка + назва»; у фіді бренд був лише в g:brand
і майже ніколи не в назві (~10 %). Перелік справжніх марок дає SEO (feed_title_brands.json, джерело й дата — у самому файлі).

ПРАВИЛО (SEO): марка йде в назву, лише коли vendor товару ТОЧНО (з урахуванням регістру) є в include і марки ще нема в назві.
Інші vendor (≈930 рідкісних) НЕ додаємо, поки SEO не розширить перелік.

ЛІЦЕНЗІЙНІ ФІЛЬТРИ (чому тут, а не лише в генераторі): ліцензійний фільтр перевіряє НАЗВУ ДО додавання марки. Марка з include
може сама бути ліцензійною (Hasbro, Funko, Squishmallows, L.O.L. Surprise!, BABY born). Тому марка НЕ додається, якщо:
  • vendor у чорному списку (MIC/Bambi та ін.) або в exclude;
  • марка збігається з ліцензійним переліком, а vendor НЕ в білому списку (інакше ми показали б марку, якої фільтр не пропустив би).
Вимкнути цілком без деплою: FEED_BRAND_IN_TITLE=0 у .env VPS (за замовчуванням увімкнено, якщо файл переліку є).
"""
from __future__ import annotations

import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

BRANDS_FILE = Path(__file__).parent / "feed_title_brands.json"
_NON_ALNUM = re.compile(r"[^0-9a-zа-яіїєґё]+")


def _norm(s: str) -> str:
    """Нижній регістр без пробілів/розділових: «Danko toys» ≈ «Dankotoys»; для перевірки «марка вже в назві»."""
    return _NON_ALNUM.sub("", str(s or "").lower())


@lru_cache(maxsize=1)
def _load() -> tuple[frozenset, frozenset]:
    """(include, exclude) з feed_title_brands.json. Нема файла/зіпсований → порожні (марка не додається) + stderr: збій не ламає фід."""
    try:
        d = json.loads(BRANDS_FILE.read_text(encoding="utf-8"))
        inc = frozenset(str(v).strip() for v in d.get("include", []) if str(v).strip())
        exc_raw = d.get("exclude", [])
        exc = frozenset(str(v[0] if isinstance(v, (list, tuple)) else v).strip() for v in exc_raw)
        return inc, exc
    except (OSError, ValueError, TypeError) as e:
        print(f"[feed_brand_title] перелік марок недоступний ({type(e).__name__}) — марка в назву не додається", file=sys.stderr)
        return frozenset(), frozenset()


def enabled() -> bool:
    return os.environ.get("FEED_BRAND_IN_TITLE", "1").strip() != "0"


def brand_for_title(vendor, *, vendor_key=lambda v: str(v or ""), licensed_rx=None,
                    licensed_wl=frozenset(), licensed_bl=frozenset()) -> str:
    """Марка для додавання в назву або "" (не додавати). Не залежить від самої назви: «вже є в назві» перевіряє with_brand()."""
    if not enabled():
        return ""
    v = str(vendor or "").strip()
    inc, exc = _load()
    if not v or v not in inc or v in exc:
        return ""
    vk = vendor_key(v)
    if vk in licensed_bl:
        return ""
    if licensed_rx is not None and licensed_rx.search(v) and vk not in licensed_wl:
        return ""
    return v


def with_brand(name: str, brand: str) -> str:
    """«<марка> <назва>», якщо brand непорожня і її ще нема в назві (порівняння без регістру/пробілів/розділових); інакше назва без змін."""
    name = (name or "").strip()
    brand = (brand or "").strip()
    if not brand or not name:
        return name
    if _norm(brand) and _norm(brand) in _norm(name):
        return name
    return f"{brand} {name}"
