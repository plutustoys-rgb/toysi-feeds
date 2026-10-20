#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_thanks_url.py — адреса повернення з LiqPay несе номер замовлення (20.10.2026): /thanks.html?o=PT-… (формат PT-YYYYMMDD-HHMMSS-xxxxxx), інакше — без параметра (не підставляємо сміття в URL)."""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "site"))
import site_order_api as so  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


b = so.BASE_URL
chk("валідний номер → /thanks.html?o=PT-…", so.thanks_url("PT-20261020-153620-193ef9") == f"{b}/thanks.html?o=PT-20261020-153620-193ef9")
bad = ["", None, "abc", "PT-1", "PT-20261020-153620-193ef9&x=1", 'PT-20261020-153620-193ef9"><script>', "../etc",
       "PT-20261020-153620-193ef9\n", "PT-20261020-153620-193EF9", "PT-20261020-153620-193ef9 "]
chk("сміття/порожнє/None/ін'єкція/«\\n» в кінці/верхній регістр hex/пробіл → без параметра", all(so.thanks_url(x) == f"{b}/thanks.html" for x in bad))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
