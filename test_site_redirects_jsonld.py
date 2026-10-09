# -*- coding: utf-8 -*-
"""Регрес (SEO-замовлення 2026-10-06 + рішення власника): розширена 301-мапа старих Prom-URL у Apache-vhost, JSON-LD головної,
HSTS лише в HTTPS-vhost, таймер перебудови кожні 2 год і його рестарт у скрипті активації. Apache тут не запускається:
патерни RewriteRule беруться З ТЕКСТУ скрипта й перевіряються Python-regex (синтаксис patterns сумісний з PCRE)."""
import json, re, sys
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "site"))
import build_site as bs

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

sh = (HERE / "deploy" / "activate_site_apache.sh").read_text(encoding="utf-8")
rules = [l.strip() for l in sh.splitlines() if l.strip().startswith("RewriteRule ^/(?:")]
pats = []
for r in rules:
    m = re.match(r"RewriteRule (\S+) (\S+) \[R=301,L\]", r)
    pats.append((m.group(1), m.group(2)) if m else None)
chk("5 правил старих Prom-URL у скрипті (p/g/product_list/site_/корінь мови), усі 301", len(rules) == 5 and all(pats))
def hit(path):
    for p, tgt in pats:
        if re.search(p, path):
            return tgt
    return None
chk("/ua/p… → мапа промід", hit("/ua/p3122611441-vodnyj-pistolet.html") == "${promredir:$1|/catalog.html}")
chk("/ru/p… (рос. версія) → та сама мапа", hit("/ru/p3122611441-vodnyj-pistolet.html") == "${promredir:$1|/catalog.html}")
chk("/ua/g… і /ru/g… (групи) → /catalog.html", hit("/ua/g155144964-vodnyj.html") == "/catalog.html" and hit("/ru/g1-x") == "/catalog.html")
chk("/ua/product_list → /catalog.html", hit("/ua/product_list") == "/catalog.html" and hit("/ru/product_list/") == "/catalog.html")
chk("/ua, /ua/, /ru/ → /", hit("/ua/") == "/" and hit("/ru/") == "/" and hit("/ru") == "/")
chk("НЕ чіпає наші адреси", all(hit(p) is None for p in ("/product-1.html", "/catalog.html", "/category-x.html", "/api/np/city", "/ua/other-page.html", "/index.html")))
chk("мапа відповідає лише цифровому prom_id (не плутає /ua/page)", hit("/ua/page12-x") is None)
# 09.10.2026: мовний префікс опційний — Google тримає й старі Prom-URL БЕЗ /ua/ (GMC «Не указана цена»; було 404)
chk("/p…-slug БЕЗ /ua/ → та сама мапа промід", hit("/p3138857147-nabor-dlya-tvorchestva.html") == "${promredir:$1|/catalog.html}")
chk("/g… і /product_list БЕЗ префікса → /catalog.html", hit("/g155144964-vodnyj.html") == "/catalog.html" and hit("/product_list") == "/catalog.html")
chk("/site_… (сторінки Prom) з префіксом і без → /", hit("/site_3517399-oplata.html") == "/" and hit("/ua/site_3517399-oplata.html") == "/" and hit("/ru/site_1-x") == "/")
chk("НЕ чіпає наші: /product-168072.html, /p.html, /products.html, /page1.html, /privacy.html, /sitemap.xml, /site.webmanifest",
    all(hit(p) is None for p in ("/product-168072.html", "/p.html", "/products.html", "/page1.html", "/privacy.html", "/sitemap.xml", "/site.webmanifest", "/g.html")))

# HSTS: тільки в https-гілці render_vhost (в :80 і http-режимі його нема)
http_part = sh.split('if [ "$mode" = "http" ]; then', 1)[1].split("else", 1)[0]
https_part = sh.split('if [ "$mode" = "http" ]; then', 1)[1].split("else", 1)[1]
chk("HSTS лише в HTTPS-гілці, під <IfModule mod_headers.c>", "Strict-Transport-Security" not in http_part and "Strict-Transport-Security" in https_part and "mod_headers.c" in https_part)
chk("HSTS обережний: max-age = 7 діб без includeSubDomains/preload", "max-age=604800" in sh and "includeSubDomains" not in sh and "preload" not in sh)

# таймер
timer = (HERE / "deploy" / "site-rebuild.timer").read_text(encoding="utf-8")
chk("таймер кожні 2 год", "OnCalendar=*-*-* 00/2:20:00" in timer)
chk("скрипт активації перезапускає таймер після enable (підхопити новий розклад)", "systemctl restart site-rebuild.timer" in sh)

# JSON-LD головної
x = bs.home_jsonld()
blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', x)
data = [json.loads(b.replace(chr(92) + "u003c", "<")) for b in blocks]
types = {d["@type"] for d in data}
chk("головна: Organization + WebSite", types == {"Organization", "WebSite"})
org = next(d for d in data if d["@type"] == "Organization")
chk("Organization: name/url/logo/телефон/соцмережі", org["name"] == "PlutusToys" and org["url"].startswith("https://plutustoys.com.ua")
    and org["contactPoint"][0]["telephone"] == "+380730150815" and len(org["sameAs"]) == 2)
chk("WebSite без SearchAction (пошук клієнтський, сторінки результатів нема)", "potentialAction" not in next(d for d in data if d["@type"] == "WebSite"))
chk("JSON-LD безпечний у <script> (жодного літерального '<' всередині)", all("<" not in b for b in blocks))
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ редіректи/HSTS/таймер/JSON-LD — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
