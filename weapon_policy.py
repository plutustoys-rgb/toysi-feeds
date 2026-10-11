# -*- coding: utf-8 -*-
"""weapon_policy.py — репліки вогнепальної зброї за НАЗВОЮ/ID (Meta «Правила торгівлі» і Google Merchant їх забороняють), АЛЕ не водяні/піна-пістолети.

Той самий критерій, що й `generate_google_feed.is_weapon_replica` (фіди Google/Meta/Bing): списки ТУТ — копія, щоб `site/build_site.py` (головна, «Добірка Плутуса»)
не імпортував важкий модуль фідів (requests, Prom API). Розходження ловить `test_site_home_carousel.py` (звірка на корпусі назв з `generate_google_feed`).
Наступний крок (окремий PR після #672): `generate_google_feed` імпортує звідси, копія зникає.
"""

WEAPON_REPLICA_IDS = {
    "276019", "302703", "298753", "283436", "283432", "282774",   # 6 відхилених Meta
    "298752", "298754", "283435", "283441", "283443", "283444",
    "297430", "297434", "290074", "52911", "252322", "273936", "277752", "150232",
}
WATER_WORDS = ("водн", "водян", "water", "піна", "мильн", "бульбашк")   # легальне — НІКОЛИ не чіпати
WEAPON_WORDS = ("пістолет", "револьвер", "автомат", "рушниц", "гвинтівк",
                "снайпер", "калаш", "дробовик", "кулемет")             # без "карабін" (=брелок-кліп)
PROJECTILE_WORDS = ("кульк", "пульк", "страйкбол", "металев", " мет.", "пістон")


def is_weapon_replica(item_id, title) -> bool:
    """Репліка вогнепальної зброї (заборонена Meta/Google), АЛЕ не водяний/піна-пістолет.
    Явний ID-набір + назва: (зброя-слово ∧ твердий-проєктиль) | страйкбол | (6мм ∧ кульки)."""
    if str(item_id) in WEAPON_REPLICA_IDS:
        return True
    t = (title or "").lower()
    if any(w in t for w in WATER_WORDS):
        return False
    if any(w in t for w in WEAPON_WORDS) and any(p in t for p in PROJECTILE_WORDS):
        return True
    if "страйкбол" in t:
        return True
    if ("6 мм" in t or "6мм" in t) and ("кульк" in t or "пульк" in t):
        return True
    return False
