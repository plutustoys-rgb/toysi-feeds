#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_apache_neutral_public_urls.py — Apache-правила нейтральних публічних адрес RoyalToys (власник 10.11.2026: «щоб покупець не бачив» постачальника; контракт Shopify-code, SITE_CHANNEL 07:45, PR #1142).
Перевіряє deploy/activate_site_apache.sh (шаблон vhost body_tpl): правила на місці, умови -f, регулярки на прикладах (PCRE ≈ Python re для цих шаблонів), порядок відносно сусідніх правил.
`python test_apache_neutral_public_urls.py` → exit 0/1."""
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SRC = (Path(__file__).resolve().parent / "deploy" / "activate_site_apache.sh").read_text(encoding="utf-8")
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


m = re.search(r"body_tpl\(\) \{\ncat <<'EOF'\n(.*?)\nEOF\n", SRC, re.S)
chk("шаблон body_tpl знайдено", bool(m))
body = m.group(1) if m else ""
lines = body.split("\n")


def rules():
    """[(cond_lines, pattern, target, flags)] для RewriteRule із попередніми RewriteCond."""
    out, conds = [], []
    for ln in lines:
        s = ln.strip()
        if s.startswith("RewriteCond"):
            conds.append(s)
        elif s.startswith("RewriteRule"):
            p = s.split(None, 3)
            out.append((conds, p[1], p[2], p[3] if len(p) > 3 else ""))
            conds = []
        elif s and not s.startswith("#"):
            conds = []
    return out


R = rules()


def find(pat_sub):
    return [r for r in R if pat_sub in r[1]]


r301 = find("product-rt-")
rimg = find("/img/p/y")
chk("є 301 RT і фото RT", all(len(x) == 1 for x in (r301, rimg)))
chk("правил постачальника, якого прибрано (16.11.2026), у шаблоні Apache немає: ні 301, ні фото, ні sitemap", not find("product-dropt-") and not find("/img/p/x") and not find("sitemap-dropt") and not re.search(r"dropt", body, re.I))
if r301:
    chk("301 RT: прапор R=301 і умова -f (нова сторінка існує)", "R=301" in r301[0][3] and any(c.endswith("-f") for c in r301[0][0]))
if rimg:
    chk("фото RT: внутрішній rewrite (без R=), умова -f на фізичний файл", "R=" not in rimg[0][3] and any(c.endswith("-f") for c in rimg[0][0]) and "/img/" in rimg[0][2])


def match(rule, path):
    return re.match(rule[1], path)


if r301 and rimg:
    r, ri = r301[0], rimg[0]
    chk("RT: /product-rt-69616.html і буквено-цифровий id 18636s22164", match(r, "/product-rt-69616.html") and match(r, "/product-rt-18636s22164.html").group(1) == "18636s22164")
    chk("RT: нова /product-y69616.html не збігається (нема циклу)", not match(r, "/product-y69616.html"))
    chk("Toysi /product-218158.html не чіпається жодним новим правилом", not match(r, "/product-218158.html"))
    mm = match(ri, "/img/p/y18636s22164-3.jpg")
    chk("фото RT: /img/p/y18636s22164-3.jpg → rt-18636s22164-3.jpg (групи 1,2)", mm and mm.groups() == ("18636s22164", "3") and ri[2] == "/img/rt/rt-$1-$2.jpg")
    chk("фото: умова -f перевіряє ТОЙ САМИЙ файл, що й ціль rewrite", ("%{DOCUMENT_ROOT}" + ri[2]) in " ".join(ri[0]))
    chk("старі шляхи /img/rt/… не збігаються з новими правилами (лишаються як були)", not match(ri, "/img/rt/rt-69616-1.jpg"))
    prom = [x for x in R if x[1].startswith("^/(?:(?:ua|ru)/)?p(")]
    chk("правило старих Prom-URL не перехоплює /img/p/… (шлях починається з /img/)", prom and not any(re.match(x[1], "/img/p/y69616-1.jpg") for x in prom))
    idx = {id(x): i for i, x in enumerate(R)}
    gone = [x for x in R if "(?:product|category)-" in x[1]]
    chk("новий блок іде ПІСЛЯ правила 410 для знятих сторінок і ПЕРЕД загальними 301/410 нижче", gone and idx[id(gone[0])] < idx[id(r)] < idx[id(find("helovin")[0])])

# --- KIDDISVIT (17.11.2026): фото kv-карток і захист supplier_kv.json
_txt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy", "activate_site_apache.sh"), encoding="utf-8").read()
chk("Apache: /img/p/k<id>-N.jpg → /img/kv/kv-<id>-N.jpg (лише цифри, з умовою -f)", "RewriteRule ^/img/p/k([0-9]+)-([0-9]{1,2})\\.jpg$ /img/kv/kv-$1-$2.jpg [L]" in _txt and "RewriteCond %{DOCUMENT_ROOT}/img/kv/kv-$1-$2.jpg -f" in _txt)
chk("Apache: supplier_kv.json заборонений (FilesMatch, як supplier_rt.json)", '<FilesMatch "^supplier_kv\\.json$">' in _txt)
_m = re.search(r"RewriteRule (\^/img/p/k\(\[0-9\]\+\)-\(\[0-9\]\{1,2\}\)\\\.jpg\$) ", _txt)
chk("регекс фото kv: збігається з /img/p/k5-1.jpg і /img/p/k123456-12.jpg, не збігається з /img/p/y5-1.jpg, /img/p/k5a-1.jpg, /img/p/k5-123.jpg, /img/rt/…", _m and re.match(_m.group(1).replace("^/", "^/"), "/img/p/k5-1.jpg") and re.match(_m.group(1), "/img/p/k123456-12.jpg") and not any(re.match(_m.group(1), x) for x in ("/img/p/y5-1.jpg", "/img/p/k5a-1.jpg", "/img/p/k5-123.jpg", "/img/rt/rt-5-1.jpg")))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
