"""Спільний хелпер ЧИСТИХ фото для фідів EVA/ALLO (проти «Асортимент на фото» й вотермарок).

Rozetka вже уникає цих відхилень модерації через `_clean_pictures` (generate_rozetka_feed.py):
бере ЧИСТЕ головне фото товару з `images.prom.ua` (без вотермарки, один товар у кадрі) зі свіжого
денного знімка Prom-каталогу, фолбек на сирі `toysi.ua` фото лише якщо товару на Prom нема. EVA і
ALLO цього не мали й брали сирі `item["pictures"]` з Toysi напряму (мульти-товарні кадри +
вотермарки) — головний драйвер їхніх відхилень «Асортимент на фото». Цей модуль виносить ту саму
логіку в одне місце для обох генераторів (Rozetka лишає власну галерейну версію без змін).

Джерело чистого фото — той самий денний знімок, що годує Rozetka/Google/Meta: `.local_secrets/
prom_full_catalog.json`, поле `image_url` per sku (чисте головне images.prom.ua-фото, R4 2026-08-21).
Знімок дає ОДНЕ чисте головне фото (не галерею) для товарів, що Є на Prom; для решти — фолбек на
сирі Toysi (не гірше, ніж було). Одне чисте фото проходить модерацію краще за кілька брудних.
"""
import json
import re
from pathlib import Path

PROM_SNAPSHOT_FILE = Path(__file__).parent / ".local_secrets" / "prom_full_catalog.json"
_PROM_IMAGE_SIZE_RE = re.compile(r"_w\d+_h\d+_")

_cache = None


def load_clean_photos() -> dict:
    """{sku: <чисте головне images.prom.ua-фото>} з денного Prom-знімка. Memoized (один знімок на
    прогін процесу). sku = external_id = наш vendor_code = ключ item у Toysi-каталозі. Best-effort:
    знімка нема/битий → {} (усі товари підуть на сирий Toysi-фолбек, як до фіксу)."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        items = json.loads(PROM_SNAPSHOT_FILE.read_text(encoding="utf-8")).get("items") or {}
    except (ValueError, OSError, AttributeError):
        _cache = {}
        return _cache
    out = {}
    for sku, rec in items.items():
        iu = (rec.get("image_url") or "").strip() if isinstance(rec, dict) else ""
        if iu.startswith("https://images.prom.ua"):
            out[str(sku)] = iu
    _cache = out
    return out


def _reset_cache() -> None:
    """Лише для тестів — скинути memoized-знімок."""
    global _cache
    _cache = None


def _upscale(url: str) -> str:
    if not url or "images.prom.ua" not in url:
        return url
    return _PROM_IMAGE_SIZE_RE.sub("_w1024_h1024_", url, count=1)


def pictures_for(item: dict, max_pictures: int) -> list:
    """Фото товару для фіду: ЧИСТЕ головне фото images.prom.ua (без вотермарки, один товар, апскейл
    1024) з Prom-знімка, якщо товар є на Prom; інакше — сирі toysi.ua `pictures` (як раніше,
    обмежені max_pictures). Ключ пошуку — vendor_code (фолбек id), як у Rozetka `_clean_pictures`."""
    sku = str(item.get("vendor_code") or item.get("id") or "")
    clean = load_clean_photos().get(sku)
    if clean and clean.startswith("https://images.prom.ua"):
        return [_upscale(clean)]
    return [p for p in item.get("pictures", []) if isinstance(p, str) and p.startswith("https://")][:max_pictures]
