#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_rt_cabinet.py — rt_cabinet.safe_link (аудит PR #871): режим читання не відкриває небезпечних посилань. Офлайн, без Playwright."""
import sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))
import rt_cabinet as rc  # noqa: E402
F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)
B = "https://royaltoys.com.ua"
for ok in ("/ua/my/", "/ua/my/orders/", "/ua/my/orders/?page=2", "/ua/my/orders/12/", "/ua/my/profile/", "/ua/dropshipping/"):
    chk(f"дозволено: {ok}", rc.safe_link(B + ok))
bad = ["/orders/12/del", "/orders/12/drop", "/my/orders/repeat/12", "/my/order/reorder/12", "/my/orders/pay/12", "/my/orders/confirm/12", "/my/orders/12/send", "/my/orders/12/close", "/my/orders/12/status/2",
       "/my/signout", "/my/logout/", "/my/wishlist/add/3", "/my/orders/12/annul", "/my/orders/create", "/my/orders/add", "/order/delete/12", "/cart/add/5", "/ua/auth/", "/my/orders/12/скасувати", "/my/orders/видалити", "/my/orders/отмена",
       "/my/orders/?action=remove", "/my/orders/?token=abc", "/my/orders/?do=cancel", "/my/orders/?status=2", "/blog/", "/ua/category/igrushki/"]
for b in bad:
    chk(f"заблоковано: {b}", not rc.safe_link(B + b))
chk("чужий хост і підрядок у query: заблоковано", not rc.safe_link("https://evil.tld/?r=royaltoys.com.ua/my/") and not rc.safe_link("https://royaltoys.com.ua.evil.tld/my/") and not rc.safe_link("http://royaltoys.com.ua/my/"))
chk("_norm лишає лише ?page=N", rc._norm(B + "/my/orders/?page=3&sort=x") == B + "/my/orders/?page=3" and rc._norm(B + "/my/orders/?sort=x") == B + "/my/orders/")
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
