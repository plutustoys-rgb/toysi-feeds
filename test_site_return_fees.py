#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_return_fees.py — 14.10.2026 (SEO): у hasMerchantReturnPolicy returnFees = ReturnFeesCustomerResponsibility (покупець платить фактичну вартість зворотної пересилки), а НЕ ReturnShippingFees без суми
(Google читав це як «безкоштовно»: 100% із 21 851 товару в Merchant Center). Решта полів: 14 днів, ReturnByMail, UA, FiniteReturnWindow."""
import json
import os
import re
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


p = {"id": "9801", "name": "Іграшка «Лінія»", "category": "Кат", "price": 250, "contribution": 100, "stock": 3, "photo": "https://x/p.jpg", "desc": ""}
ex = bs.offer_extras(p)["hasMerchantReturnPolicy"]
chk("returnFees = ReturnFeesCustomerResponsibility; ReturnShippingFees (без суми) НЕ використовується", ex["returnFees"] == "https://schema.org/ReturnFeesCustomerResponsibility" and "ReturnShippingFees" not in json.dumps(ex))
chk("решта політики без змін: 14 днів, ReturnByMail, UA, FiniteReturnWindow", ex["merchantReturnDays"] == 14 and ex["returnMethod"] == "https://schema.org/ReturnByMail" and ex["applicableCountry"] == "UA" and ex["returnPolicyCategory"] == "https://schema.org/MerchantReturnFiniteReturnWindow")
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.write_product(p, related=None, cat_slug={}, similar=[])
h = (out / "product-9801.html").read_text(encoding="utf-8")
chk("у JSON-LD картки товару: ReturnFeesCustomerResponsibility, а ReturnShippingFees нема", "ReturnFeesCustomerResponsibility" in h and "ReturnShippingFees" not in h)
ret = re.sub(r"<[^>]+>", " ", bs._RETURNS)
chk("відповідає тексту сторінки «Повернення»: витрати на зворотну пересилку належної якості несе покупець", "несе покупець" in ret)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
