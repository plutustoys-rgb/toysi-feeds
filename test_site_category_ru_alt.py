#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_category_ru_alt.py — 19.10.2026 (SEO): видимий рядок «Також шукають: …» (російські синоніми запиту) під H1 категорії.
Слот: site/category_ru_alt.json {slug: "фраза, фраза, фраза"}; рядок у .cat-intro, екранується, не прихований; без тексту категорії теж показується; нема запису — нічого не змінюється."""
import json
import sys
from pathlib import Path

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


bs._CATEGORY_TEXTS = {"a": "Абзац перший.\n\nАбзац другий.", "c": "Текст без синонімів."}
bs._CATEGORY_RU_ALT = {"a": "самокат четырёхколёсный, самокат 4 колеса", "b": "ночник <b>детский</b>"}
h = bs._category_intro_html("category-a.html")
chk("текст + синоніми: рядок «Також шукають: …» ПЕРЕД абзацами, в одному .cat-intro", h.startswith('<div class="cat-intro"><p class="cat-alt">Також шукають: самокат четырёхколёсный, самокат 4 колеса</p><p>Абзац перший.</p><p>Абзац другий.</p></div>'))
chk("рядок не прихований (ні hidden, ні display:none, ні aria-hidden)", "hidden" not in h and "display:none" not in h)
h = bs._category_intro_html("category-b.html")
chk("синоніми без тексту категорії: показується лише рядок, HTML екранується", h == '<div class="cat-intro"><p class="cat-alt">Також шукають: ночник &lt;b&gt;детский&lt;/b&gt;</p></div>\n')
chk("є текст, нема синонімів: як раніше, без рядка", "cat-alt" not in bs._category_intro_html("category-c.html") and "<p>Текст без синонімів.</p>" in bs._category_intro_html("category-c.html"))
chk("нема ні того, ні іншого: порожньо", bs._category_intro_html("category-z.html") == "" and bs._category_intro_html("catalog.html") == "")
d = json.loads((HERE / "site" / "category_ru_alt.json").read_text(encoding="utf-8-sig"))
chk("site/category_ru_alt.json: 19 slug партії 2, кожен 3–5 фраз", len(d) == 19 and all(isinstance(v, str) and 3 <= len([x for x in v.split(",") if x.strip()]) <= 5 for v in d.values()))
t = json.loads((HERE / "site" / "category_texts.json").read_text(encoding="utf-8-sig"))
chk("кожен slug синонімів має текст категорії (партія 2)", all(k in t for k in d))
bs._load_category_ru_alt()
chk("_load_category_ru_alt читає файл і прибирає зайві пробіли", len(bs._CATEGORY_RU_ALT) == 19 and all("  " not in v for v in bs._CATEGORY_RU_ALT.values()))
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
chk("стиль .cat-alt є, без animation/@keyframes", ".cat-alt{" in css)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
