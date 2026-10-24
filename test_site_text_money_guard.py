#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_text_money_guard.py — 24.10.2026 (Консультант: ціни в статичних текстах застарівають із фідом; правило мусить триматись у коді, а не на пам'яті).
Інваріанти:
(1) category_texts.json і product_texts.json у репо НЕ містять слів про гроші (₴, грн, «ціна/ціни/коштує»): збірка їх не друкує попереджень;
(2) текст зі знаком валюти, «грн» або словом «ціна/ціни/коштує» дає попередження в stderr з ключем (slug/id) — і для категорій, і для карток; текст при цьому лишається завантаженим;
(3) «вартість» (очки за літеру в «Ерудиті») і слова на кшталт «цінний»/«принцип» НЕ дають хибного попередження;
(4) чисті тексти — тиша."""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def run(loader, attr, data):
    """Підставляє тимчасовий файл у модуль, викликає завантажувач, повертає (результат, stderr)."""
    tmp = Path(tempfile.mkdtemp()) / "t.json"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    old = getattr(bs, attr)
    setattr(bs, attr, str(tmp))
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            res = loader()
    finally:
        setattr(bs, attr, old)
    return res, err.getvalue()


# (1) файли репо
for name, loader in (("category_texts.json", bs._load_category_texts), ("product_texts.json", bs._load_product_texts)):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = loader()
    chk(f"{name} у репо: {len(res)} текстів, жодного слова про гроші (попереджень про гроші нема)", len(res) > 0 and "гроші" not in err.getvalue())

# (2) категорії
cat_bad = {"a": "Ціни на першій сторінці — від 107 до 326 ₴.", "b": "Типова ціна — 150 грн.", "c": "Модель коштує дорожче.", "d": "Чистий текст без чисел.", "e": "Вартість літери — очки."}
res, err = run(bs._load_category_texts, "_CATEGORY_TEXTS_FILE", cat_bad)
chk("категорії: попередження названо ключі a, b, c", "category_texts.json" in err and "'a'" in err and "'b'" in err and "'c'" in err and "(усього 3)" in err)
chk("категорії: чисті ключі d і e не названі, текст усе одно завантажено", "'d'" not in err and "'e'" not in err and set(res) == set(cat_bad))

# (2) картки
prod_bad = {"101": "Коштують недорого: 99 ₴.", "102": "Ціна — 50 гривень.", "103": "Гра з очками: вартість літери залежить від мови.", "104": "Принцип гри цінний для всіх."}
res, err = run(bs._load_product_texts, "_PRODUCT_TEXTS_FILE", prod_bad)
chk("картки: попередження названо id 101 і 102", "product_texts.json" in err and "'101'" in err and "'102'" in err and "(усього 2)" in err)
chk("картки: «вартість», «принцип», «цінний» не дають хибного спрацювання (103, 104)", "'103'" not in err and "'104'" not in err and set(res) == set(prod_bad))

# (4) тиша на чистому
res, err = run(bs._load_category_texts, "_CATEGORY_TEXTS_FILE", {"x": "Чистий текст про категорію без грошей."})
chk("чистий текст категорії: без попередження про гроші", "гроші" not in err)
res, err = run(bs._load_product_texts, "_PRODUCT_TEXTS_FILE", {"1": "Чистий текст картки."})
chk("чистий текст картки: без попередження про гроші", "гроші" not in err)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
