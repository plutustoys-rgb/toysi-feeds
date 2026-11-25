# -*- coding: utf-8 -*-
"""Регрес (SEO 25.11, до Миколая 11 днів): сезонні посилання на головній, у шапці й на хабі подарунків — лише ЗБУДОВАНІ сторінки, звичайні <a href>;
шапка: «Подарунки на Миколая» до 6.12, «Новорічні подарунки» 7.12–31.12; блок 20.11–10.01."""
import datetime, re, sys
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

D = datetime.date
def at(y, m, d):
    bs._today = lambda: D(y, m, d)

# нічого не збудовано → нічого не показуємо (жодних посилань на 404)
bs.GIFT_BUILT[:] = []; bs.GIFT_ARTICLES_BUILT[:] = []; bs.SELECTION_BUILT[:] = []; bs.COLORING_BUILT[:] = []
at(2026, 11, 25)
chk("нічого не збудовано: блок і шапка порожні", bs.season_strip_html() == "" and bs.header_season_html() == "" and bs.season_links() == [])

bs.GIFT_BUILT[:] = [("podarunok-na-mykolaia", "H", "На Миколая"), ("novorichnyi-podarunok-dytyni", "H", "На Новий рік")]
bs.GIFT_ARTICLES_BUILT[:] = [("porady-shcho-podaruvaty-na-mykolaia", "T")]
bs.SELECTION_BUILT[:] = [("advent-kalendari", "H", "__gift")]
bs.COLORING_BUILT[:] = [("rozmalovky", "H"), ("rozmalovky-novorichni", "H")]
s = bs.season_strip_html()
hrefs = re.findall(r'<a href="([^"]+)"', s)
chk("головна 25.11: 5 звичайних <a href> на Миколая, Новий рік, порадник, адвент, новорічні розмальовки",
    hrefs == ["podarunok-na-mykolaia.html", "novorichnyi-podarunok-dytyni.html", "porady-shcho-podaruvaty-na-mykolaia.html", "advent-kalendari.html", "rozmalovky-novorichni.html"])
chk("блок без грошових слів і без кутових дужок у тексті", not re.search(r"грн|₴|безкоштовн|ціна", s, re.I) and "<script" not in s)

bs.COLORING_BUILT[:] = [("rozmalovky", "H")]
bs.GIFT_ARTICLES_BUILT[:] = []
hrefs2 = re.findall(r'<a href="([^"]+)"', bs.season_strip_html())
chk("не збудовані сторінки (порадник, новорічні розмальовки) у блок не потрапляють", hrefs2 == ["podarunok-na-mykolaia.html", "novorichnyi-podarunok-dytyni.html", "advent-kalendari.html"])
bs.GIFT_ARTICLES_BUILT[:] = [("porady-shcho-podaruvaty-na-mykolaia", "T")]; bs.COLORING_BUILT[:] = [("rozmalovky", "H"), ("rozmalovky-novorichni", "H")]

for (y, m, d), want in (((2026, 11, 19), False), ((2026, 11, 20), True), ((2026, 12, 25), True), ((2027, 1, 10), True), ((2027, 1, 11), False), ((2026, 6, 1), False)):
    at(y, m, d)
    chk(f"сезонний блок {d:02d}.{m:02d}: {'є' if want else 'нема'}", (bs.season_strip_html() != "") == want)

for (y, m, d), want in (((2026, 11, 25), "podarunok-na-mykolaia.html"), ((2026, 12, 6), "podarunok-na-mykolaia.html"), ((2026, 12, 7), "novorichnyi-podarunok-dytyni.html"),
                        ((2026, 12, 31), "novorichnyi-podarunok-dytyni.html"), ((2027, 1, 1), None), ((2026, 10, 15), None)):
    at(y, m, d)
    h = bs.header_season_html()
    chk(f"шапка {d:02d}.{m:02d}: {want or 'нема посилання'}", (want is None and h == "") or (want is not None and f'href="{want}"' in h and 'class="hseason"' in h))

at(2026, 12, 7)
bs.GIFT_BUILT[:] = [("podarunok-na-mykolaia", "H", "L")]
chk("7.12, а сторінки «Новорічні подарунки» не збудовано → в шапці нічого (не 404)", bs.header_season_html() == "")
at(2026, 11, 25)
hd = bs.header()
chk("шапка містить посилання перед пошуком, порядок «логотип → сезон → пошук»", hd.index('class="logo"') < hd.index('class="hseason"') < hd.index('class="search"'))
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
chk("CSS: .hseason приховано на вузьких екранах, видно від 1100px; .season-strip є", re.search(r"\.hseason\{display:none", css) and "min-width:1100px){.hseason{display:block}" in css and ".season-strip{" in css)
print("\nALL OK" if not F else f"\nFAIL: {len(F)}")
sys.exit(1 if F else 0)
