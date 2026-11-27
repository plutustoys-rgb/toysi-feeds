# -*- coding: utf-8 -*-
"""Регрес (SMM 27.11, згода власника): угорі /ig.html (посилання з профілю Instagram) — блок «Подарунок на Миколая (до 6 грудня)» з кнопкою на podarunok-na-mykolaia.html та під ним «Безкоштовні розмальовки»
→ rozmalovky-novorichni.html; обидва з UTM instagram/social/profile_link, без цін. З 7.12 — «Новорічний подарунок» → novorichnyi-podarunok-dytyni.html. Нема збудованої сторінки — нема блоку (без 404);
нема нічого — звична смужка розмальовок."""
import contextlib, datetime, io, re, sys, tempfile
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

D = datetime.date
UTM = "utm_source=instagram&utm_medium=social&utm_campaign=profile_link"
bs.GIFT_BUILT[:] = [("podarunok-na-mykolaia", "H", "На Миколая"), ("novorichnyi-podarunok-dytyni", "H", "На Новий рік")]
bs.COLORING_BUILT[:] = [("rozmalovky", "H"), ("rozmalovky-novorichni", "H")]

bs._today = lambda: D(2026, 11, 27)
h = bs._ig_top_blocks_html()
chk("27.11: блок «Подарунок на Миколая» з UTM-посиланням на podarunok-na-mykolaia.html", f'href="podarunok-na-mykolaia.html?{UTM}"' in h and "Подарунок на Миколая" in h and "до 6 грудня" in h)
chk("27.11: під ним «Безкоштовні розмальовки» з UTM на rozmalovky-novorichni.html; подарунок ПЕРШИЙ", f'href="rozmalovky-novorichni.html?{UTM}"' in h and "Безкоштовні розмальовки" in h and h.index("Подарунок на Миколая") < h.index("Безкоштовні розмальовки"))
chk("без цін і знижок у блоці", not re.search(r"₴|грн|знижк|ціна", h))
chk("подарунок — акцентна кнопка (клас is-gift), розмальовки — без", h.count("is-gift") == 1)

bs._today = lambda: D(2026, 12, 6)
chk("6.12: ще «Подарунок на Миколая»", "podarunok-na-mykolaia.html?" in bs._ig_top_blocks_html())
bs._today = lambda: D(2026, 12, 7)
h = bs._ig_top_blocks_html()
chk("7.12: «Новорічний подарунок» → novorichnyi-podarunok-dytyni.html з UTM, Миколая вже нема", f'href="novorichnyi-podarunok-dytyni.html?{UTM}"' in h and "podarunok-na-mykolaia" not in h and "Новорічний подарунок" in h)
bs._today = lambda: D(2026, 12, 31)
chk("31.12: ще «Новорічний подарунок»", "novorichnyi-podarunok-dytyni.html?" in bs._ig_top_blocks_html())
bs._today = lambda: D(2027, 1, 5)
h = bs._ig_top_blocks_html()
chk("5.01: подарункового блоку нема, лишились розмальовки", "podarunok" not in h and "rozmalovky-novorichni.html?" in h)

bs._today = lambda: D(2026, 11, 27)
bs.GIFT_BUILT[:] = [("novorichnyi-podarunok-dytyni", "H", "На Новий рік")]
h = bs._ig_top_blocks_html()
chk("сторінка Миколая не збудована: кнопки на неї нема (без 404), розмальовки є", "podarunok-na-mykolaia" not in h and "rozmalovky-novorichni.html?" in h)
bs.GIFT_BUILT[:] = []
bs.COLORING_BUILT[:] = [("rozmalovky", "H")]
h = bs._ig_top_blocks_html()
chk("нема сезонних сторінок: звична смужка розмальовок (хаб), без UTM-блоків", 'class="coloring-strip"' in h and "ig-season" not in h)
bs.COLORING_BUILT[:] = []
chk("нічого не збудовано: порожньо", bs._ig_top_blocks_html() == "")

# повна сторінка ig.html: блок угорі, до списку товарів, після H1
bs.GIFT_BUILT[:] = [("podarunok-na-mykolaia", "H", "На Миколая")]
bs.COLORING_BUILT[:] = [("rozmalovky-novorichni", "H")]
out = Path(tempfile.mkdtemp()); bs.OUT = str(out)
prods = [{"id": "12", "name": "Іграшка", "price": 100, "stock": 3, "photos": [], "category_name": "К", "category_slug": "k", "desc": "", "brand": "", "url": "product-12.html"}]
try:
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        bs.write_ig(prods)
    ig = (out / "ig.html").read_text(encoding="utf-8")
    chk("ig.html: блок угорі — після H1, до сітки/кнопки каталогу", ig.index('<h1 class="page">') < ig.index('class="ig-season"') < ig.index("catalog.html"))
    chk("ig.html лишається noindex і з canonical без параметрів", "noindex" in ig and 'rel="canonical" href="https://plutustoys.com.ua/ig.html"' in ig.replace("'", '"'))
except Exception as e:
    chk(f"write_ig не впав на мінімальному каталозі: {e}", False)

print("\nALL OK" if not F else f"\nFAIL: {len(F)}")
sys.exit(1 if F else 0)
