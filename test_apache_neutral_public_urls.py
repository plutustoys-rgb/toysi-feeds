#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_apache_neutral_public_urls.py — Apache-правила нейтральних публічних адрес Dropt/RoyalToys (власник 10.11.2026: «щоб покупець не бачив» постачальника; контракт Shopify-code, SITE_CHANNEL 07:45, PR #1142).
Перевіряє deploy/activate_site_apache.sh (шаблон vhost body_tpl): правила на місці, умови -f, регулярки на прикладах (PCRE ≈ Python re для цих шаблонів), порядок відносно сусідніх правил.
`python test_apache_neutral_public_urls.py` → exit 0/1."""
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


d301 = find("product-dropt-")
r301 = find("product-rt-")
dimg = find("/img/p/x")
rimg = find("/img/p/y")
smap = find("sitemap-dropt")
chk("є 301 Dropt, RT, фото Dropt, фото RT, sitemap", all(len(x) == 1 for x in (d301, r301, dimg, rimg, smap)))
for name, x, flag in (("301 Dropt", d301, "R=301"), ("301 RT", r301, "R=301"), ("sitemap", smap, "R=301")):
    if x:
        chk(f"{name}: прапор {flag} і умова -f (нова сторінка існує)", flag in x[0][3] and any(c.endswith("-f") for c in x[0][0]))
for name, x in (("фото Dropt", dimg), ("фото RT", rimg)):
    if x:
        chk(f"{name}: внутрішній rewrite (без R=), умова -f на фізичний файл", "R=" not in x[0][3] and any(c.endswith("-f") for c in x[0][0]) and "/img/" in x[0][2])


def match(rule, path):
    return re.match(rule[1], path)


if d301 and r301 and dimg and rimg:
    d, r, di, ri = d301[0], r301[0], dimg[0], rimg[0]
    chk("Dropt: /product-dropt-57114.html → захоплює 57114", match(d, "/product-dropt-57114.html") and match(d, "/product-dropt-57114.html").group(1) == "57114")
    chk("Dropt: нульовий/нечисловий id не збігається", not match(d, "/product-dropt-0123.html") and not match(d, "/product-dropt-abc.html"))
    chk("Dropt: нова адреса /product-x57114.html НЕ збігається зі старим правилом (нема циклу)", not match(d, "/product-x57114.html"))
    chk("RT: /product-rt-69616.html і буквено-цифровий id 18636s22164", match(r, "/product-rt-69616.html") and match(r, "/product-rt-18636s22164.html").group(1) == "18636s22164")
    chk("RT: нова /product-y69616.html не збігається (нема циклу)", not match(r, "/product-y69616.html"))
    chk("Toysi /product-218158.html не чіпається жодним новим правилом", not any(match(x, "/product-218158.html") for x in (d, r)))
    mm = match(di, "/img/p/x57114-1.jpg")
    chk("фото Dropt: /img/p/x57114-1.jpg → dropt-57114-1.jpg (групи 1,2)", mm and mm.groups() == ("57114", "1") and di[2] == "/img/dropt/dropt-$1-$2.jpg")
    mm = match(ri, "/img/p/y18636s22164-3.jpg")
    chk("фото RT: /img/p/y18636s22164-3.jpg → rt-18636s22164-3.jpg (групи 1,2)", mm and mm.groups() == ("18636s22164", "3") and ri[2] == "/img/rt/rt-$1-$2.jpg")
    chk("фото: умова -f перевіряє ТОЙ САМИЙ файл, що й ціль rewrite", all(("%{DOCUMENT_ROOT}" + x[2]).replace("$1", "$1") in " ".join(x[0]) for x in (di, ri)))
    chk("старі шляхи /img/dropt/… і /img/rt/… не збігаються з новими правилами (лишаються як були)", not any(match(x, "/img/dropt/dropt-57114-1.jpg") or match(x, "/img/rt/rt-69616-1.jpg") for x in (di, ri)))
    prom = [x for x in R if x[1].startswith("^/(?:(?:ua|ru)/)?p(")]
    chk("правило старих Prom-URL не перехоплює /img/p/… (шлях починається з /img/)", prom and not any(re.match(x[1], "/img/p/x57114-1.jpg") for x in prom))
    idx = {id(x): i for i, x in enumerate(R)}
    gone = [x for x in R if "(?:product|category)-" in x[1]]
    chk("новий блок іде ПІСЛЯ правила 410 для знятих сторінок і ПЕРЕД загальними 301/410 нижче", gone and idx[id(gone[0])] < idx[id(d)] < idx[id(find("helovin")[0])])
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
