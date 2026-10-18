#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_meta_domain_verification.py — 18.10.2026 (SEO, запит власника «п.2 проси»): мета-тег підтвердження домену Meta Business у <head> КОЖНОЇ сторінки, рівно один раз."""
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


TAG = '<meta name="facebook-domain-verification" content="zrxty9pynr6k0qh1y1tpgzkfyliv7s" />'
chk("константа — токен Meta Business «PlutusToys» від SEO", bs.META_DOMAIN_VERIFICATION == "zrxty9pynr6k0qh1y1tpgzkfyliv7s")
tmp = tempfile.mkdtemp()
bs.OUT = tmp
for name, kw in (("p1.html", {}), ("p2.html", {"noindex": True}), ("p3.html", {"defer_trackers": False})):
    bs._write(name, bs.page("Тест", "<p>x</p>", canonical=name, **kw))
    h = (Path(tmp) / name).read_text(encoding="utf-8")
    head = h.split("</head>")[0]
    chk(f"{name}: тег у <head>, рівно один: {kw}", head.count(TAG) == 1 and h.count("facebook-domain-verification") == 1)
    chk(f"{name}: тег Search Console лишився на місці", head.count('name="google-site-verification"') == 1)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
