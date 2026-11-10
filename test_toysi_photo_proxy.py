#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_toysi_photo_proxy.py — фото Toysi під нашою адресою (власник 10.11.2026: «роби проксі»). Apache: /img/t/<шлях> → https://toysi.ua/p/<шлях> ЛИШЕ за суворим шаблоном (не відкритий проксі);
фіди Google/Meta/Bing: _public_toysi_image за брамою FEEDS_TOYSI_PHOTO_PROXY=1. `python test_toysi_photo_proxy.py` → exit 0/1."""
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.pop("FEEDS_TOYSI_PHOTO_PROXY", None)
import generate_google_feed as gf  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


src = (Path(__file__).resolve().parent / "deploy" / "activate_site_apache.sh").read_text(encoding="utf-8")
m = re.search(r"body_tpl\(\) \{\ncat <<'EOF'\n(.*?)\nEOF\n", src.replace("\r\n", "\n"), re.S)
body = m.group(1) if m else ""
rule = [l.strip() for l in body.split("\n") if l.strip().startswith("RewriteRule ^/img/t/")]
chk("є рівно одне правило проксі /img/t/ у шаблоні vhost, прапори [P,L], ціль https://toysi.ua/p/$1", len(rule) == 1 and rule[0].endswith("https://toysi.ua/p/$1 [P,L]"))
chk("у шаблоні є SSLProxyEngine On, ProxyTimeout і Cache-Control для /img/t/", "SSLProxyEngine On" in body and "ProxyTimeout" in body and '<LocationMatch "^/img/t/">' in body)
blk = re.search(r'<LocationMatch "\^/img/t/">\s*ProxyPreserveHost Off\s*</LocationMatch>', body)
chk("для /img/t/ ProxyPreserveHost Off (інакше Toysi отримує Host нашого сайту і редіректить на toysi.com.ua) + ProxyPassReverse на нашу адресу", bool(blk) and "ProxyPassReverse /img/t/ https://toysi.ua/p/" in body)
chk("vhost-широкий ProxyPreserveHost On для /api/ лишився (не ламаємо API)", (lambda ls: "ProxyPreserveHost On" in ls and "ProxyPreserveHost Off" in ls and ls.index("ProxyPreserveHost On") > ls.index("ProxyPreserveHost Off"))([l.strip() for l in body.split("
")]))
chk("у скрипті активації є НЕфатальна проба /img/t/ (попередження, не відкат)", "/img/t/enl_218158.jpg" in src and "НЕ віддає 200 image/jpeg" in src)
pat = re.compile(rule[0].split()[1]) if rule else None
ok = ["/img/t/enl_218158.jpg", "/img/t/218158.jpg", "/img/t/3/enl_265377.jpg", "/img/t/2/255663.jpg", "/img/t/12/enl_1.png", "/img/t/enl_5.webp"]
bad = ["/img/t/../../etc/passwd", "/img/t/%2e%2e/x.jpg", "/img/t/a/b/enl_1.jpg", "/img/t/123/456/7.jpg", "/img/t/enl_1.php", "/img/t/enl_1.jpg/x", "/img/t/https://evil.com/x.jpg",
       "/img/t/@evil.com/enl_1.jpg", "/img/t/enl_.jpg", "/img/t/x.jpg", "/img/t/enl_1.jpg?x=1", "/img/t/enl_1234567890123.jpg", "/img/t/", "/img/t", "/img/t/ENL_1.JPG"]
chk("Apache-шаблон приймає всі справжні форми файлів Toysi", pat and all(pat.fullmatch(p) for p in ok))
chk("Apache-шаблон відхиляє обходи шляху, чужі хости, розширення, довгі числа, query (не відкритий проксі)", pat and not any(pat.fullmatch(p) for p in bad))
# фіди
chk("брама FEEDS_TOYSI_PHOTO_PROXY за замовчуванням ВИМКНЕНО → адреса без змін", gf.toysi_photo_proxy_on() is False and gf._public_toysi_image("https://toysi.ua/p/enl_1.jpg") == "https://toysi.ua/p/enl_1.jpg")
os.environ["FEEDS_TOYSI_PHOTO_PROXY"] = "1"
conv = {"https://toysi.ua/p/enl_1.jpg": "https://plutustoys.com.ua/img/t/enl_1.jpg", "https://toysi.ua/p/218158.jpg": "https://plutustoys.com.ua/img/t/218158.jpg",
        "https://toysi.ua/p/3/enl_265377.jpg": "https://plutustoys.com.ua/img/t/3/enl_265377.jpg"}
chk("брама =1: фото Toysi → /img/t/… нашого домену (три форми)", all(gf._public_toysi_image(k) == v for k, v in conv.items()))
keep = ["https://images.prom.ua/x_w200_h200_1.jpg", "https://plutustoys.com.ua/img/p/y1-1.jpg", "https://toysi.ua/p/../x.jpg", "http://toysi.ua/p/enl_1.jpg", "https://toysi.ua.evil.com/p/enl_1.jpg", "", None]
chk("чужі хости, наші адреси, підроблені адреси, порожнє — не чіпаємо", all(gf._public_toysi_image(u) == u for u in keep))
chk("результат проходить через Apache-шаблон (фід і сервер узгоджені)", pat and all(pat.fullmatch(v.replace("https://plutustoys.com.ua", "")) for v in conv.values()))
os.environ.pop("FEEDS_TOYSI_PHOTO_PROXY", None)
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
