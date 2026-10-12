#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_meta_capi_name_split.py — 12.10.2026 (Код-Агент): meta_conversions_client._split_name дзеркалить order_router._split_recipient_name, включно з евристикою сайту:
рівно 3 слова і останнє схоже на по-батькові → «Прізвище Ім'я По-батькові» (інакше fn/ln у хешах Meta для таких покупців переставлені). Порівнюємо з роутером напряму (не з пам'яті)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import meta_conversions_client as mc  # noqa: E402
import order_router as orr  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


CASES = [("site", "Іван Петренко"), ("site", "Петренко Іван Олексійович"), ("site", "Олена Іванівна Коваль"), ("site", "Анна Коваль-Шевченко"), ("site", "Іван"), ("site", "  "),
         ("site", "Іван Олексійович Петренко"), ("site", "Петренко Іван Петрівна Марія"), ("rozetka", "Петренко Іван Олексійович"), ("eva", "Коваль Анна"), ("prom", "Іван Петренко"), ("", "Іван Петренко"),
         ("site", "Мар’яна Дем’янчук"), ("site", "Ковальчук Марія Ігорівна"), ("site", "Шевченко Тарас Григорович"),
         ("prom", "Петренко Іван Олексійович"), ("", "Петренко Іван Олексійович"), ("eva", "Петренко"), ("rozetka", "Петренко"), ("site", "Ілля Ілліч Петренко"), ("site", "Ілля Петренко Ілліч"),
         ("site", "Петренко Іван Олексійович-Х"), ("site", "ПЕТРЕНКО ІВАН ОЛЕКСІЙОВИЧ"), ("site", "Коваль Марія Іванична"), ("site", "Коваль Андрій Васильович")]
for platform, name in CASES:
    first, last, _mid = orr._split_recipient_name(name, platform)
    got = mc._split_name(name, platform)
    exp = (first if first not in ("Клієнт", "Невідомо") else "", last if last != "Невідомо" else "")
    chk(f"{platform or '—'} «{name.strip()}»: Meta (fn, ln) = {got} збігається з роутером {exp}", got == exp)
chk("site «Петренко Іван Олексійович» → fn «Іван», ln «Петренко» (по-батькові не йде в Meta)", mc._split_name("Петренко Іван Олексійович", "site") == ("Іван", "Петренко"))
chk("site «Іван Петренко» (2 слова) — без змін: fn «Іван», ln «Петренко»", mc._split_name("Іван Петренко", "site") == ("Іван", "Петренко"))
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
