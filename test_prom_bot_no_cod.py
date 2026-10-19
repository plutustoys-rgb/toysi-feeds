#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prom_bot_no_cod.py — рішення власника 19.10.2026: післяплату (накладений платіж) вимкнено у всіх кабінетах → бот Prom не має її пропонувати. Інваріанти: (1) готова відповідь про доставку DELIVERY_ANSWER не містить «накладен/післяплат/при отриманні/готівк/на вибір»;
(2) довідник STORE_POLICY (контекст LLM/шаблонів) не стверджує, що накладений доступний («обидва варіанти доступні», «накладений платіж (оплата при отриманні…) або»), а явно каже, що оплата лише онлайн; (3) відповідь про доставку згадує онлайн-оплату."""
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
import prom_chat_bot as pb  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


BAD = re.compile(r"накладен|післяплат|при отриманні|готівк|на вибір|наложен", re.I)
chk("DELIVERY_ANSWER не містить жодної згадки накладеного/післяплати/«на вибір»", not BAD.search(pb.DELIVERY_ANSWER))
chk("DELIVERY_ANSWER каже про онлайн-оплату", "онлайн" in pb.DELIVERY_ANSWER.lower())
chk("STORE_POLICY: нема тверджень «обидва варіанти доступні» / «накладений платіж (оплата при отриманні … ) або передоплата»", "обидва варіанти" not in pb.STORE_POLICY and not re.search(r"накладений платіж \(оплата при отриманні[^)]*\)\s*або", pb.STORE_POLICY))
chk("STORE_POLICY: явно сказано, що оплата лише онлайн і що оплату при отриманні не пропонувати", "лише онлайн" in pb.STORE_POLICY and "НЕМАЄ" in pb.STORE_POLICY)
chk("STORE_POLICY не обіцяє конкретних способів (картка/частинами) як гарантованих", "не гарантуй" in pb.STORE_POLICY)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
