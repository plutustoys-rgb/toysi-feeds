# -*- coding: utf-8 -*-
"""rozetka_stop_brands.py — стоп-бренди Rozetka (таблиця правил Rozetka, збережена в репо як rozetka_stop_brands_reference.csv).

ЧОМУ (незалежний Аудитор кабінетів, ЗАВДАННЯ_КОДУ_з_аудиту_2026-10-03.md п.2): у rozetka_feed.xml були 11 активних офферів брендів зі стоп-списку
Rozetka (DreamMakers ×9, Genio Kids, Fancy) — ризик санкцій/зняття. Раніше фід знав лише один бренд (wader, ручний набір). Таблиця має 588 брендів:
  • «Для всіх категорій» (≈248) — бренд заборонений скрізь → виключаємо завжди;
  • «Категорії: …» (≈340) — заборона у перелічених категоріях Rozetka. У бренда з великим переліком (≥ BLANKET_MIN_CATEGORIES, у таблиці такий
    перелік — фактично УСІ іграшкові/дитячі категорії, напр. DreamMakers/Genio Kids/Fancy/Wader: 201 категорія) виключаємо ВЕСЬ бренд;
    з коротким — лише коли категорія Toysi товару збігається з категорією правила (нормалізована рівність/входження ≥ 5 символів).
Виняток-колонка (авторизаційні листи тощо) НЕ застосовується: у нас таких документів нема → бренд лишається виключеним.
Ключ бренду: нижній регістр без пробілів/дефісів/пунктуації («DreamMakers» у Toysi = «Dream Makers» у таблиці).
Таблицю тримати свіжою: `drift_check.py --tables` (Аудитор, щотижня) → оновлений CSV сюди.
"""
import csv
import re
import sys
from pathlib import Path

REFERENCE = Path(__file__).parent / "rozetka_stop_brands_reference.csv"
BLANKET_MIN_CATEGORIES = 100
_MIN_CAT_MATCH_LEN = 5

_cache = None


def _key(s) -> str:
    return re.sub(r"[\W_]+", "", (s or "").lower(), flags=re.UNICODE)


def _split_categories(rule: str) -> list:
    body = re.sub(r"^\s*Категорії\s*:", "", rule.strip(), flags=re.IGNORECASE)
    return [c.strip() for c in body.replace("\n", " ").split(",") if c.strip()]


def load(path: Path = REFERENCE) -> dict:
    """{ключ_бренду: {"brand", "mode": "all"|"cats", "cats": {ключі категорій}, "ncats": n}}. Файл відсутній → {} + попередження."""
    global _cache
    if path == REFERENCE and _cache is not None:
        return _cache
    rules = {}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))[1:]
    except OSError as e:
        print(f"[RozetkaStopBrands] таблицю {path.name} не прочитано ({e}) — стоп-бренди таблиці НЕ застосовано.", file=sys.stderr)
        return {}
    for r in rows:
        if not r or not r[0].strip():
            continue
        rule = r[1] if len(r) > 1 else ""
        k = _key(r[0])
        if not k:
            continue
        if rule.strip().startswith("Для всіх"):
            rules[k] = {"brand": r[0].strip(), "mode": "all", "cats": set(), "ncats": 0}
        else:
            cats = _split_categories(rule)
            prev = rules.get(k)
            if prev and prev["mode"] == "all":
                continue                      # суворіше правило не послаблюємо
            keyset = {_key(c) for c in cats if _key(c)} | (prev["cats"] if prev else set())
            rules[k] = {"brand": r[0].strip(), "mode": "cats", "cats": keyset, "ncats": len(keyset)}
    if path == REFERENCE:
        _cache = rules
    return rules


def stop_reason(vendor, category_name=None) -> str | None:
    """Причина виключення бренду з Rozetka-фіду або None."""
    rec = load().get(_key(vendor))
    if not rec:
        return None
    if rec["mode"] == "all":
        return f"стоп-бренд Rozetka «{rec['brand']}» (для всіх категорій)"
    if rec["ncats"] >= BLANKET_MIN_CATEGORIES:
        return f"стоп-бренд Rozetka «{rec['brand']}» (заборона в {rec['ncats']} категоріях — усі іграшкові/дитячі)"
    ck = _key(category_name)
    if ck and any(len(c) >= _MIN_CAT_MATCH_LEN and (c in ck or ck in c) for c in rec["cats"]):
        return f"стоп-бренд Rozetka «{rec['brand']}» у категорії «{category_name}»"
    return None
