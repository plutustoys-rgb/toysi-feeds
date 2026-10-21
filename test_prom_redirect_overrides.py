#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_prom_redirect_overrides.py — ручні перекриття для старих Prom-карток без сторінки (21.10.2026, рішення SEO/власника «захист старих адрес»).
Інваріанти: (1) prom_id поза основною мапою + існуюча сторінка категорії → рядок «prom_id /category-<slug>.html» у мапі (nginx і Apache); (2) prom_id, що вже має картку, НЕ перебивається;
(3) відсутня сторінка категорії / поганий id / ціль поза форматом / зіпсований JSON → пропуск без падіння, решта мапи ціла (prom_id лишається на 410);
(4) закомічений prom_redirect_overrides.json структурно валідний; (5) нічого не змінює для prom_id без перекриття."""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def load_gen():
    m = types.ModuleType("gpr_ov")
    m.__file__ = str(HERE / "generate_prom_redirects.py")
    exec(compile((HERE / "generate_prom_redirects.py").read_text(encoding="utf-8"), "generate_prom_redirects.py", "exec"), m.__dict__)
    return m


def setup(overrides_text, cats=("skvishi", "mashynky"), cache=None, products=("111",)):
    td = Path(tempfile.mkdtemp())
    site = td / "site"
    site.mkdir()
    (site / "index.json").write_text(json.dumps([{"id": p} for p in products]), encoding="utf-8")
    for c in cats:
        (site / f"category-{c}.html").write_text("x", encoding="utf-8")
    (td / "cache.json").write_text(json.dumps(cache if cache is not None else {"111": {"prom_id": 1000, "url_text": "a"}}), encoding="utf-8")
    ov = td / "ov.json"
    if overrides_text is not None:
        ov.write_text(overrides_text, encoding="utf-8")
    g = load_gen()
    g.INDEX_FILE = site / "index.json"
    g.CACHE_FILE = td / "cache.json"
    g.OVERRIDES_JSON = ov
    return g


def lines(g):
    g.build_map()
    return sorted(g.build_map.apache_lines)


# 1) базовий: перекриття діє
g = setup(json.dumps({"2000": {"target": "/category-skvishi.html"}, "2001": {"target": "/category-mashynky.html"}}))
L = lines(g)
chk("перекриття: 2000 і 2001 → /category-…; картка 1000 → /product-111.html", L == ["1000 /product-111.html", "2000 /category-skvishi.html", "2001 /category-mashynky.html"])
txt, n, sk = g.build_map()
chk("nginx-мапа містить ті ж рядки з «;»", "2000 /category-skvishi.html;" in txt and "1000 /product-111.html;" in txt and n == 3)
chk("Apache-мапа: формат «prom_id /category-….html» без «;»", "2000 /category-skvishi.html\n" in g.build_map_apache() and ";" not in g.build_map_apache())

# 2) prom_id з карткою не перебивається
g = setup(json.dumps({"1000": {"target": "/category-skvishi.html"}}))
chk("prom_id, що має картку, НЕ перебивається перекриттям", lines(g) == ["1000 /product-111.html"] and g.build_map.override_stats["has_card"] == 1)

# 3) відсутня сторінка / поганий id / ціль поза форматом / сміття
bad = {"2000": {"target": "/category-nema.html"}, "abc": {"target": "/category-skvishi.html"}, "2002": {"target": "https://evil.example/x"}, "2003": {"target": "/category-skvishi.html?x=1"},
       "2004": {"target": "/product-5.html"}, "2005": "рядок", "2006": {"target": "/category-Skvishi.html"}, "2007": {"target": "/category-skvishi.html"}}
g = setup(json.dumps(bad))
L = lines(g)
chk("відсутня сторінка, не-цифровий id, чужий хост, query, не category, не-об'єкт, великі літери → пропущені; валідний 2007 застосований; картка ціла",
    L == ["1000 /product-111.html", "2007 /category-skvishi.html"] and g.build_map.override_stats["no_page"] == 1 and g.build_map.override_stats["invalid"] >= 5)
for bad_txt in ("{не json", "[]", "null", "42"):
    g = setup(bad_txt)
    chk(f"зіпсований/не-об'єкт JSON ({bad_txt!r}): без падіння, мапа карток ціла", lines(g) == ["1000 /product-111.html"])
g = setup(None)
chk("файлу перекриттів нема: поведінка як раніше", lines(g) == ["1000 /product-111.html"])

# 4) закомічений файл
real = json.loads((HERE / "prom_redirect_overrides.json").read_text(encoding="utf-8"))
chk("prom_redirect_overrides.json: непорожній, усі ключі цифрові, усі цілі /category-<slug>.html (a-z0-9-)", bool(real) and all(k.isdigit() and isinstance(v, dict) and __import__("re").fullmatch(r"/category-[a-z0-9-]+\.html", v.get("target", "")) for k, v in real.items()))
gm = json.loads((HERE / "prom_group_to_category.json").read_text(encoding="utf-8"))
chk("prom_group_to_category.json: групи 155145000 (ляльки), 155490013 (плакати), 155412282 (пазли) мають категорію за рішенням SEO 21.10",
    gm["155145000"]["category"] == "lialky-i-pupsy" and gm["155490013"]["category"] == "plakaty-interaktyvni" and gm["155412282"]["category"] == "pazly")
chk("жодна ціль перекриття не вказує на сторінки заборонених тем (мечі/ножі/арбалети/пістолети/азартні/пістони/бомбочки)", not any(w in v["target"] for v in real.values() for w in ("mechi", "nozh", "arbalet", "pistolet", "azart", "pistony", "bombochk")))

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
