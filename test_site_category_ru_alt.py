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
# партія 2 (19.10, 19 slug) і партія 3 (31.10, +13 slug з виміряних запитів GSC; SEO відбирала очима проти H1 категорії)
BATCH2 = {"bloknoty", "dytiachi-hadzhety", "ihrashky-dlia-vannoi", "koliasky-dlia-lialok", "konstruktory-typu-leho", "kubyky", "mashynky-na-batarey-kakh", "miaki-ihrashky",
          "nezvychay-ni-konstruktory", "nichnyky", "pazly-i-vkladyshi", "pidhotovka-do-shkoly", "pupsy", "riukzaky", "roboty", "samokaty-chotyrykolisni", "soldatyky",
          "sumochky-ta-hamantsi", "vodna-zbroia"}
BATCH3 = {"briazkaltsia", "dereviani-konstruktory", "ihrashky-antystres", "inshi-pazly", "kukhni-i-posud", "nastilni-ihry", "pazly-castorland", "pazly-trefl",
          "samokaty-trykolisni", "sosky-i-prorizuvachi", "spetstekhnika", "tisto-dlia-liplennia-i-plastylin", "zavodni-ihrashky"}


# партія 4 (31.10, v4): рядок має обіцяти лише те, що є на ЦІЙ сторінці (Wader — у «Пластикових машинках», Bubble Pops — у «Тварини і птахи»), тому два slug перенесено
BATCH4 = {"plastykovi-mashynky", "tvaryny-i-ptakhy"}


def n_phrases(v):
    return len([x for x in v.split(",") if x.strip()])


chk("site/category_ru_alt.json: рівно партії 2 (19) + 3 (13) + 4 (2) = 34 slug; рядки; партія 2 — 3–5 фраз, партії 3–4 — 2–5 (дві чесні форми краще за третю вигадану)",
    set(d) == BATCH2 | BATCH3 | BATCH4 and len(d) == 34 and all(isinstance(v, str) for v in d.values())
    and all(3 <= n_phrases(d[k]) <= 5 for k in BATCH2) and all(2 <= n_phrases(d[k]) <= 5 for k in BATCH3 | BATCH4))
chk("рядок не обіцяє товару, якого на сторінці нема: Wader/Вадер/Bubble Pops не стоять на dereviani-konstruktory та ihrashky-antystres (аудит 31.10)",
    not any(w in d["dereviani-konstruktory"].lower() for w in ("wader", "вадер")) and "bubble pops" not in d["ihrashky-antystres"].lower())
t = json.loads((HERE / "site" / "category_texts.json").read_text(encoding="utf-8-sig"))
chk("кожен slug партії 2 має текст категорії (партія 3 може бути без тексту: рядок показується й так — перевірено вище)", all(k in t for k in BATCH2))
bs._load_category_ru_alt()
chk("_load_category_ru_alt читає файл і прибирає зайві пробіли", len(bs._CATEGORY_RU_ALT) == 34 and all("  " not in v for v in bs._CATEGORY_RU_ALT.values()))
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
chk("стиль .cat-alt є, без animation/@keyframes", ".cat-alt{" in css)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
