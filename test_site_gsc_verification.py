#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_gsc_verification.py — 18.10.2026 (SEO, термінове): мета-тег підтвердження Search Console у <head> КОЖНОЇ сторінки (без нього «У вас нет доступа к этому ресурсу»)."""
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


TAG = '<meta name="google-site-verification" content="AzAID7jv3z26c8wFyrtnzjRTa_2xDyEnFx_R-neaoZY">'
chk("константа — код власника з 17.07 (збігається з записом у плані)", bs.GSC_VERIFICATION == "AzAID7jv3z26c8wFyrtnzjRTa_2xDyEnFx_R-neaoZY")
tmp = tempfile.mkdtemp()
bs.OUT = tmp
for name, kw in (("p1.html", {}), ("p2.html", {"noindex": True}), ("p3.html", {"defer_trackers": False})):
    bs._write(name, bs.page("Тест", "<p>x</p>", canonical=name, **kw))
    h = (Path(tmp) / name).read_text(encoding="utf-8")
    head = h.split("</head>")[0]
    chk(f"{name}: тег у <head>, рівно один: {kw}", head.count(TAG) == 1 and h.count("google-site-verification") == 1)
chk("тег після viewport, до решти (одразу в початку head)", (Path(tmp) / "p1.html").read_text(encoding="utf-8").index('name="viewport"') < (Path(tmp) / "p1.html").read_text(encoding="utf-8").index("google-site-verification") < 600)
# HTML-файл підтвердження (другий спосіб, SEO 18.10): лежить у site/ (у git), крок прибирання збірки його не чіпає, у .gitignore не потрапляє
f = HERE / "site" / "googled732b3f7a0271df0.html"
chk("файл підтвердження site/googled732b3f7a0271df0.html: точний вміст (один рядок)", f.exists() and f.read_text(encoding="utf-8").strip() == "google-site-verification: googled732b3f7a0271df0.html")
import subprocess  # noqa: E402
ig = subprocess.run(["git", "check-ignore", str(f)], cwd=str(HERE), capture_output=True, text=True)
chk("файл не ігнорується git (інакше не потрапить на VPS)", ig.returncode == 1)
src = (HERE / "site" / "build_site.py").read_text(encoding="utf-8")
m = re.search(r'for fn in os\.listdir\(OUT\):\s*if \(fn\.startswith\(\((.*?)\)\)', src, re.S)
chk("крок прибирання застарілих сторінок чіпає лише свої префікси — файл «googled…» під нього не підпадає", m is not None and not any("googled".startswith(x.strip().strip('"')) or x.strip().strip('"') == "google" for x in m.group(1).split(",")))
print()
if F:
    print(f"FAILED ({len(F)}):")
    sys.exit(1)
print("ALL OK")
