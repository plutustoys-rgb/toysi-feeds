#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_eva_selection_min_stock.py — рішення власника 19.10.2026 «знизь до >1»: EVA бере товар із залишком СТРОГО більше 1 (≥2 шт). Перевірка: константа = 1; відсів у відборі саме `stock <= EVA_SELECTION_MIN_STOCK` (залишок 1 відсіюється, 2 — ні); інші канали (Prom/Rozetka) цієї константи не читають."""
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
import generate_eva_feed as ge  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


chk("EVA_SELECTION_MIN_STOCK == 1 (рішення власника 19.10: «знизь до >1»)", ge.EVA_SELECTION_MIN_STOCK == 1)
src = (HERE / "generate_eva_feed.py").read_text(encoding="utf-8")
chk("відбір лишається суворо «більше»: item.get(\"stock\", 0) <= EVA_SELECTION_MIN_STOCK → відсів", 'if item.get("stock", 0) <= EVA_SELECTION_MIN_STOCK:' in src)
for stock, passes in ((0, False), (1, False), (2, True), (3, True), (10, True)):
    chk(f"залишок {stock}: {'у фіді' if passes else 'відсіяно'}", (stock > ge.EVA_SELECTION_MIN_STOCK) == passes)
users = [p.name for p in HERE.glob("*.py") if p.name not in ("generate_eva_feed.py", Path(__file__).name) and "EVA_SELECTION_MIN_STOCK" in p.read_text(encoding="utf-8", errors="ignore")]
chk(f"інші модулі константу не читають (поріг лише для EVA): {users}", not [u for u in users if not u.startswith("test_")])
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
