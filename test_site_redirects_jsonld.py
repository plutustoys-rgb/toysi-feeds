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
pats = []        # 301-правила (pattern, target)
gone_pats = []   # 410-правила [G,L] (pattern)
for r in rules:
    m = re.match(r"RewriteRule (\S+) (\S+) \[R=301,L(?:,QSD)?\]", r)
    g = re.match(r"RewriteRule (\S+) - \[G,L\]", r)
    if m:
        pats.append((m.group(1), m.group(2)))
    elif g:
        gone_pats.append(g.group(1))
    else:
        pats.append(None)
chk("9 правил старих Prom-URL у скрипті: 7 з 301 (p/g з мапи груп/g-решта/product_list/site_contacts/site_/корінь мови) + 2 з 410 Gone (p<id> без запису в мапі; /create_order_mobile)",
    len(rules) == 9 and len(gone_pats) == 2 and all(pats))
def hit(path):
    for p, tgt in pats:
        if re.search(p, path):
            return tgt
    return None
def gone(path):
    return any(re.search(p, path) for p in gone_pats)
# 410 стоїть ПЕРЕД 301-правилом p<id> і захищене умовою «ключа нема в мапі» (11.10.2026, Консультант: 301 на /catalog.html = soft-404 у Google)
_i_cond = sh.find("RewriteCond ${promredir:$1|-} =-")
_i_gone = sh.find("- [G,L]")
_i_301 = sh.find("${promredir:$1} [R=301,L]")
chk("410-правило p<id> іде безпосередньо після RewriteCond ${promredir:$1|-} =- і ПЕРЕД 301-правилом із мапи",
    -1 < _i_cond < _i_gone < _i_301 and sh[_i_cond:_i_gone].count("RewriteRule") == 1 and sh[_i_cond:_i_gone].count("RewriteCond") == 1)
chk("410: /create_order_mobile, /ua/create_order_mobile (SEO/Код 15.10: 647 запитів Googlebot) → Gone; наші сторінки — ні", gone("/create_order_mobile") and gone("/ua/create_order_mobile") and not gone("/cart.html") and not gone("/checkout.html"))
chk("мапа БЕЗ дефолту /catalog.html (непокритий ключ більше не веде на каталог)", "${promredir:$1|/catalog.html}" not in sh)
chk("410: /ua/p…, /ru/p…, /p… без префікса підпадають під [G]; /product-1.html, /ua/g…, /catalog.html — ні",
    gone("/ua/p3122611441-x.html") and gone("/ru/p3122611441-x.html") and gone("/p3138857147-x.html")
    and not gone("/product-1.html") and not gone("/ua/g155144964-x.html") and not gone("/catalog.html") and not gone("/privacy.html"))
chk("/ua/p… → мапа промід (покритий ключ → 301 на картку)", hit("/ua/p3122611441-vodnyj-pistolet.html") == "${promredir:$1}")
chk("/ru/p… (рос. версія) → та сама мапа", hit("/ru/p3122611441-vodnyj-pistolet.html") == "${promredir:$1}")
# 12.10.2026: групи Prom — спершу мапа груп (301 на категорію, лише коли gid у мапі), далі завжди /catalog.html (деталі й порядок — test_prom_group_redirects.py)
chk("/ua/g… і /ru/g… (групи) → спершу мапа груп ${grpredir:$1}, останнє правило групи → /catalog.html",
    hit("/ua/g155144964-vodnyj.html") == "${grpredir:$1}" and hit("/ru/g1-x") == "${grpredir:$1}"
    and ("^/(?:(?:ua|ru)/)?g[0-9]+-", "/catalog.html") in pats)
chk("/ua/product_list → /catalog.html", hit("/ua/product_list") == "/catalog.html" and hit("/ru/product_list/") == "/catalog.html")
chk("/ua, /ua/, /ru/ → /", hit("/ua/") == "/" and hit("/ru/") == "/" and hit("/ru") == "/")
chk("НЕ чіпає наші адреси", all(hit(p) is None for p in ("/product-1.html", "/catalog.html", "/category-x.html", "/api/np/city", "/ua/other-page.html", "/index.html")))
chk("мапа відповідає лише цифровому prom_id (не плутає /ua/page)", hit("/ua/page12-x") is None)
# 09.10.2026: мовний префікс опційний — Google тримає й старі Prom-URL БЕЗ /ua/ (GMC «Не указана цена»; було 404)
chk("/p…-slug БЕЗ /ua/ → та сама мапа промід", hit("/p3138857147-nabor-dlya-tvorchestva.html") == "${promredir:$1}")
chk("/g… БЕЗ префікса → мапа груп; /product_list БЕЗ префікса → /catalog.html", hit("/g155144964-vodnyj.html") == "${grpredir:$1}" and hit("/product_list") == "/catalog.html")
chk("/site_… (сторінки Prom) з префіксом і без → /", hit("/site_3517399-oplata.html") == "/" and hit("/ua/site_3517399-oplata.html") == "/" and hit("/ru/site_1-x") == "/")
# 10.10.2026 (SEO п.4): /site_contacts.html був 404 (немає числового id) → контакти; будь-який інший /site_* → /
chk("/site_contacts.html з префіксом і без → /contacts.html", hit("/site_contacts.html") == "/contacts.html" and hit("/ua/site_contacts.html") == "/contacts.html" and hit("/ru/site_contacts") == "/contacts.html")
chk("/site_about.html, /ua/site_delivery.html → /", hit("/site_about.html") == "/" and hit("/ua/site_delivery.html") == "/")
chk("favicon/apple-touch-icon: внутрішній rewrite (не редірект) на /assets/…", "RewriteRule ^/favicon\.ico$ /assets/favicon.ico [L]" in sh and "apple-touch-icon" in sh and "/assets/apple-touch-icon.png [L]" in sh)
chk("Cache-Control для /assets/ лише під mod_headers, 1 доба", 'LocationMatch "^/assets/"' in sh and "max-age=86400" in sh and sh.index("mod_headers.c") < sh.index('LocationMatch "^/assets/"'))
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
chk("щоденний ребілд о 00:00:30 за Києвом (партії знижки змінюються щодня; Код 15.10), а не лише в понеділок", "OnCalendar=*-*-* 00:00:30 Europe/Kyiv" in timer and "OnCalendar=Mon" not in timer)
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
