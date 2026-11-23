# -*- coding: utf-8 -*-
"""Регрес (SEO 23.11): видалені власні картки (product-*.html, файлу нема у site/) → 410 Gone, а не 404. Apache тут не запускається:
правило береться З ТЕКСТУ activate_site_apache.sh, патерн перевіряється Python-regex; порядок — рядки скрипта."""
import re, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

sh = (Path(__file__).parent / "deploy" / "activate_site_apache.sh").read_text(encoding="utf-8")
lines = sh.splitlines()
rule = next((i for i, l in enumerate(lines) if l.strip() == r"RewriteRule ^/(product-[A-Za-z0-9_-]+)\.html$ - [G,L]"), -1)
chk("правило 410 для видалених карток є в скрипті", rule > 0)
chk("перед ним умова «файлу нема»: RewriteCond %{DOCUMENT_ROOT}/$1.html !-f (жива сторінка, в т.ч. «немає в наявності», віддається як є)",
    lines[rule - 1].strip() == "RewriteCond %{DOCUMENT_ROOT}/$1.html !-f")
rt = next(i for i, l in enumerate(lines) if l.strip().startswith("RewriteRule ^/product-rt-"))
sg = next(i for i, l in enumerate(lines) if "RewriteRule ^/((?:product|category)-" in l)
chk("порядок: 301 product-rt-X→product-yX і 410 з мапи removed_pages — ДО загального 410 (інакше rt-адреса з готовою y-сторінкою дістала б 410 замість 301)", sg < rt < rule)
pat = re.compile(r"^/(product-[A-Za-z0-9_-]+)\.html$")
for u in ("/product-300946.html", "/product-x59309.html", "/product-x56639.html", "/product-yAB_1.html", "/product-rt-12.html", "/product-k123.html"):
    chk(f"патерн ловить {u}", bool(pat.match(u)))
for u in ("/product-1.html/extra", "/category-x.html", "/productx.html", "/assets/product-1.html", "/product-1.jpg"):
    chk(f"патерн НЕ ловить {u}", not pat.match(u))
print("\nALL OK" if not F else f"\nFAIL: {len(F)}")
sys.exit(1 if F else 0)
