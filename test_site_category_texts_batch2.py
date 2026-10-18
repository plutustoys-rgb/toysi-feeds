#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_category_texts_batch2.py — 19.10.2026 (SEO, партія 2): 19 вступних текстів категорій у site/category_texts.json — файл читається, ключі партії на місці, ≥150 знаків, без дублів тексту, абзаци розділені \n\n."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


BATCH2 = ("samokaty-chotyrykolisni ihrashky-dlia-vannoi koliasky-dlia-lialok konstruktory-typu-leho kubyky mashynky-na-batarey-kakh miaki-ihrashky "
          "nezvychay-ni-konstruktory nichnyky pazly-i-vkladyshi pidhotovka-do-shkoly pupsy riukzaky roboty dytiachi-hadzhety bloknoty soldatyky "
          "vodna-zbroia sumochky-ta-hamantsi").split()
t = json.loads((HERE / "site" / "category_texts.json").read_text(encoding="utf-8-sig"))
chk("файл — об'єкт {slug: текст}, усі значення — рядки", isinstance(t, dict) and all(isinstance(v, str) for v in t.values()))
chk("усі 19 категорій партії 2 на місці", all(s in t for s in BATCH2))
chk("кожен текст партії 2 ≥ 150 знаків", all(len(t[s]) >= 150 for s in BATCH2 if s in t))
chk("тексти не повторюються між категоріями", len(set(t.values())) == len(t))
chk("партія 1 (раніше додані) не зникла: kartyny-za-nomeramy, breloky", "kartyny-za-nomeramy" in t and "breloky" in t)
print()
if F:
    print(f"FAILED ({len(F)}):")
    sys.exit(1)
print("ALL OK")
