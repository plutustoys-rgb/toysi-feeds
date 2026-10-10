#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_hygiene.py — 10.10.2026, аудит живого сайту (Shopify-code): власна 404, головне фото картки, /index.html → «/», стиснення XML.
Інваріанти:
  • page(base_root=True) ставить <base href="/"> ПЕРЕД першим відносним шляхом (інакше браузер візьме stylesheet від адреси 404-сторінки);
    без прапора <base> не з'являється (інакше зламаються звичайні сторінки);
  • write_404() пише 404.html: noindex, без canonical, посилання на каталог, жодних чужих доменів (там був логотип Softaculous);
  • шапка/крихти/«Дякуємо» ведуть на «/», а не на index.html (інакше кожен клік «на головну» ішов би через 301);
  • головне фото картки має fetchpriority="high" (LCP), а фото в сітці «Додайте до замовлення» — ні;
  • шаблон Apache: ErrorDocument 404 /404.html, DEFLATE включає xml, правило /index.html → / захищене умовою THE_REQUEST (без неї —
    нескінченний цикл: DirectoryIndex робить внутрішній перехід «/» → /index.html), і сама умова ловить прямий запит та не ловить «/».
Самодостатній, мережа не потрібна."""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "site"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
for k in ("SITE_LIQPAY_LIVE", "SITE_SELLER_TAXID"):
    os.environ.pop(k, None)

import build_site as b  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


# ── <base> лише на запит
plain = b.page("Тест", "<p>x</p>")
based = b.page("Тест", "<p>x</p>", base_root=True)
chk("звичайна сторінка БЕЗ <base>", "<base" not in plain)
chk("base_root=True: <base href=\"/\"> є", '<base href="/">' in based)
chk("<base> раніше за перший відносний шлях (stylesheet)", based.index('<base href="/">') < based.index('href="assets/styles.css"'))

# ── 404.html
tmp = tempfile.mkdtemp()
b.OUT = tmp
b.write_404()
h404 = open(os.path.join(tmp, "404.html"), encoding="utf-8").read()
chk("404: текст «Такої сторінки немає»", "Такої сторінки немає" in h404)
chk("404: noindex", 'name="robots" content="noindex' in h404)
chk("404: без canonical (сторінка віддається за будь-якої адреси)", 'rel="canonical"' not in h404)
chk("404: є посилання на каталог і категорії", 'href="catalog.html"' in h404 and 'href="categories.html"' in h404)
chk("404: є <base> (віддається за довільним шляхом)", '<base href="/">' in h404)
foreign = [u for u in re.findall(r'(?:src|href)="(https?://[^"]+)"', h404) if "plutustoys.com.ua" not in u]
chk("404: немає слідів хостинг-панелі (softaculous/webuzo) — зовнішні адреси лише наші власні й аналітика/соцмережі, див. друк нижче", not any(x in h404 for x in ("softaculous", "webuzo")))
print("      (зовнішні адреси у 404 лише:", sorted(set(foreign)), ")")

# ── посилання на головну
chk("шапка: логотип веде на «/»", 'class="logo" href="/"' in b.header())
chk("крихти: «Головна» → «/»", 'href="/">Головна<' in b.crumbs_html([("Головна", "index.html"), ("Каталог", "catalog.html"), ("X", None)]))
b.write_thanks()
hth = open(os.path.join(tmp, "thanks.html"), encoding="utf-8").read()
chk("thanks: «На головну» → «/»", 'href="/" style' in hth)
chk("жодне посилання на index.html у шапці/крихтах/thanks", 'href="index.html"' not in b.header() + hth)
ld = b.breadcrumb_ld([("Головна", "index.html"), ("Каталог", None)])
chk("JSON-LD крихт: головна лишається абсолютним «…/» (без index.html)", f'"item": "{b.SITE_URL}/"' in ld)

# ── головне фото картки
p = {"id": "4242", "name": "Конструктор Тест", "price": 349, "category": "Конструктори",
     "photo": "https://example.com/1.jpg", "stock": 3, "desc": "Опис", "contribution": 10.0}
rel = {"id": "9", "name": "Дрібничка", "price": 70, "category": "Інше", "photo": "https://example.com/2.jpg", "stock": 3, "desc": "", "contribution": 1.0}
b.write_product(p, related=[rel], cat_slug={"Конструктори": "konstruktory"})
hp = open(os.path.join(tmp, "product-4242.html"), encoding="utf-8").read()
main_img = re.search(r'<div class="photo">(<img [^>]*>)', hp)
chk("картка: головне фото знайдено", main_img is not None)
chk("картка: головне фото має fetchpriority=\"high\"", main_img is not None and 'fetchpriority="high"' in main_img.group(1))
chk("картка: головне фото НЕ lazy (LCP)", main_img is not None and 'loading="lazy"' not in main_img.group(1))
chk("картка: рівно одне фото з fetchpriority (решта — lazy)", hp.count('fetchpriority="high"') == 1)
chk("картка: фото в «Додайте до замовлення» лишилось lazy", 'src="https://example.com/2.jpg" alt="Дрібничка" loading="lazy"' in hp)

# ── шаблон Apache
sh = open(os.path.join(HERE, "deploy", "activate_site_apache.sh"), encoding="utf-8").read()
tpl = sh[sh.index("body_tpl() {"):sh.index("acme_tpl() {")]
chk("Apache: ErrorDocument 404 /404.html", re.search(r"^\s*ErrorDocument 404 /404\.html\s*$", tpl, re.M) is not None)
chk("Apache: DEFLATE охоплює application/xml (sitemap.xml)", re.search(r"AddOutputFilterByType DEFLATE [^\n]*application/xml", tpl) is not None)
cond = re.search(r"^\s*RewriteCond %\{THE_REQUEST\} (\S+)\s*\n\s*RewriteRule \^/index\\\.html\$ / \[R=301,L\]", tpl, re.M)
chk("Apache: правило /index.html → / стоїть ПІСЛЯ умови THE_REQUEST", cond is not None)
if cond:
    rx = re.compile(cond.group(1))
    chk("THE_REQUEST ловить «GET /index.html HTTP/1.1»", rx.search("GET /index.html HTTP/1.1") is not None)
    chk("THE_REQUEST ловить «GET /index.html?utm=1 HTTP/2.0»", rx.search("GET /index.html?utm=1 HTTP/2.0") is not None)
    chk("THE_REQUEST НЕ ловить «GET / HTTP/1.1» (внутрішній перехід DirectoryIndex не має зациклитись)", rx.search("GET / HTTP/1.1") is None)
    chk("THE_REQUEST НЕ ловить «GET /catalog.html HTTP/1.1» і «GET /myindex.html HTTP/1.1»",
        rx.search("GET /catalog.html HTTP/1.1") is None and rx.search("GET /myindex.html HTTP/1.1") is None)

# ── 404.html не чіпається прибиранням застарілих сторінок і ігнорується git-ом
gi = open(os.path.join(HERE, "site", ".gitignore"), encoding="utf-8").read().split()
chk("site/.gitignore містить 404.html (згенерований вихід)", "404.html" in gi)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
