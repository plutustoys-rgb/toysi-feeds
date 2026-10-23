# -*- coding: utf-8 -*-
"""site_auto_text.py — автоматичний текст картки САЙТУ (plutustoys.com.ua) з реальних атрибутів Toysi (23.10.2026, запит SEO + слово власника «зробіть їх вже щоб вони нікуди не зникали»).

ЧОМУ ОКРЕМО ВІД seo_content_generator (маркетплейси): ФІДИ (Prom/EVA/ALLO/Rozetka) і так генерують опис НА ЛЬОТУ для КОЖНОГО товару (`seo_description.description_for` → `build_seo`, рішення власника 31.08);
змінювати там словник = змінювати опис живих товарів на маркетплейсах (ризик нової хвилі модерації), тому словник САЙТУ — окремий і НЕ впливає на фіди. Сайт же `build_seo` не вживав: його тексти — лише
ручні `site/product_texts.json`. Цей модуль закриває розрив: текст для КОЖНОЇ картки автоматично (нові товари підхоплюються самі, нічого не «зникає» при зміні асортименту).

ПРАВИЛО ТЕКСТУ САЙТУ (SEO + аудит Shopify-code 23.10): лише факти й словникове ВИЗНАЧЕННЯ типу «X — це Y»; БЕЗ призначення/користі («розвиває…»), оцінок («якісна»), порівнянь, віку, матеріалу, безпеки, ціни,
чужих торгових марок. Тому тут НЕМАЄ CLOSERS/GENERIC_BENEFIT/«користь категорії» з marketplace-генератора. Склад тексту (одним абзацом): «Категорія: <назва>.» + визначення (лише з `seo_site_definitions.json`,
ключ = підрядок назви категорії в нижньому регістрі) + «Виробник — …» + «Країна походження — …» + «Розмір: L×W×H см.» (лише реальні значення). Змінні НЕ відмінюються (ставляться фактами в називному).
Категорія без визначення (або без жодного факту крім назви категорії) → текст порожній, а не вигаданий.

ГЕЙТ: build_site показує цей текст лише коли `SITE_AUTO_TEXTS_ENABLED=True` (за замовчуванням ВИМКНЕНО) — вмикається окремим PR після затвердження семплу власником. Пріоритет на картці: ручний
`product_texts.json` > автотекст.
"""
import hashlib
import json
from pathlib import Path

DEFINITIONS_FILE = Path(__file__).resolve().parent / "seo_site_definitions.json"
BANNED_COUNTRY = ("рф", "росія", "россия", "russia")

# Категорії (нижній регістр, ПОВНА назва), під які визначення зі словника звірено з живим каталогом сайту 23.10.2026.
# Підрядковий збіг ключа надто широкий: «метале» зачіпає «Металеві конструктори», «книг» — «Художні книги», «магніт» — «Магнітні конструктори»,
# «інтерактивн» — «Плакати інтерактивні»; визначення від іншого товару на картці — гірше, ніж без нього. Тому категорія поза списком → лише факти.
# Нова категорія з ключем у назві автоматично визначення НЕ отримає, поки її не додано сюди після перевірки.
VERIFIED_CATEGORIES = frozenset({
    "клейонки, килимки і підгузники", "розвиваючі килимки", "каремати і килимки", "соски і прорізувачі", "спецтехніка",
    "розважальні інтерактивні іграшки", "брелоки", "картини за номерами", "металеві моделі", "персонажі ігор та мультфільмів",
    "косметика", "книги для малят", "пенали та папки", "прикраси", "заводні іграшки", "подушки", "маскарадні костюми",
    "блокноти", "парасольки і дощовики", "день народження", "набори для фокусів",
})


def load_definitions(path=None) -> dict:
    """{підрядок категорії (нижній регістр): [речення…]}. Ключі, що починаються з «_», — службові (пояснення), пропускаються; значення — лише непорожні списки рядків. Збій файлу → {}."""
    try:
        data = json.loads(Path(path or DEFINITIONS_FILE).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    out = {}
    for k, v in (data.items() if isinstance(data, dict) else []):
        if str(k).startswith("_") or not isinstance(v, list):
            continue
        sents = [s.strip() for s in v if isinstance(s, str) and s.strip()]
        if sents:
            out[str(k).lower()] = sents
    return out


def _pick(lst: list, sku: str) -> str:
    return lst[int(hashlib.sha256((str(sku) + "d").encode("utf-8")).hexdigest(), 16) % len(lst)]


def definition_for(category: str, sku: str, definitions: dict, name: str = "", verified=None) -> str:
    """Речення-визначення або ''. Ключ = підрядок назви категорії (нижній регістр). verified (множина повних назв категорій) — якщо задано, категорія поза нею визначення не отримує.
    Якщо категорії відповідає ОДИН ключ — його визначення. Якщо КІЛЬКА (категорія змішана: «Пенали та папки», «Клейонки, килимки і підгузники») —
    лише ключ, що є і в НАЗВІ товару («Папка…» → про папку); жоден не збігся з назвою → '' (краще без речення, ніж визначення іншого товару)."""
    c = (category or "").strip().lower()
    if verified is not None and c not in verified:
        return ""
    keys = [k for k in definitions if k in c]
    if not keys:
        return ""
    if len(keys) > 1:
        n = (name or "").lower()
        keys = [k for k in keys if k in n]
        if not keys:
            return ""
    return _pick(definitions[keys[0]], sku)


def _dimensions(params) -> str:
    """«Розмір: L×W×H см.» — лише з розмірів БЕЗ упаковки (всі три); якщо їх нема — «Розмір упаковки: …» з розмірів В упаковці (всі три); змішувати набори не можна."""
    d = {}
    for pair in (params or []):
        try:
            k, v = pair
        except (TypeError, ValueError):
            continue
        d[k] = v
    for suffix, label in (("без упаковки", "Розмір"), ("в упаковці", "Розмір упаковки")):
        L, W, H = (d.get(f"{axis} {suffix} (см)") for axis in ("Довжина", "Ширина", "Висота"))
        if L and W and H:
            return f"{label}: {L}×{W}×{H} см."
    return ""


def site_text(item: dict, definitions: dict = None, verified=VERIFIED_CATEGORIES) -> str:
    """Plain-text абзац для картки сайту або '' (нема визначення й нема жодного факту крім назви категорії). item — рядок каталогу Toysi."""
    defs = load_definitions() if definitions is None else definitions
    sku = str(item.get("id") or "")
    category = (item.get("category_name") or "").strip()
    definition = definition_for(category, sku, defs, item.get("name") or "", verified)
    facts = []
    vendor = (item.get("vendor") or "").strip()
    country = (item.get("country") or "").strip()
    if vendor:
        facts.append(f"Виробник — {vendor}.")
    if country and country.lower() not in BANNED_COUNTRY:
        facts.append(f"Країна походження — {country}.")
    dims = _dimensions(item.get("params"))
    if dims:
        facts.append(dims)
    if not definition and not facts:
        return ""
    bits = []
    if category:
        bits.append(f"Категорія: {category}.")
    if definition:
        bits.append(definition)
    bits.extend(facts)
    return " ".join(bits)
