# -*- coding: utf-8 -*-
"""Регрес (SEO 23.11): /llms.txt — короткий опис для ШІ-пошуку. Без цін/обіцянок/грн, лише збудовані сторінки, абсолютні https-посилання."""
import re, sys, tempfile
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

with tempfile.TemporaryDirectory() as d:
    out = Path(d)
    for f in ("catalog.html", "brendy.html", "podarunky.html", "rozmalovky.html", "category-pisochni-nabory.html", "about.html"):
        (out / f).write_text("x", encoding="utf-8")
    old = bs.OUT
    bs.OUT = str(out)
    try:
        bs.write_llms()
    finally:
        bs.OUT = old
    t = (out / "llms.txt").read_text(encoding="utf-8")

chk("заголовок і опис: PlutusToys, інтернет-магазин дитячих іграшок, Україна", t.startswith("# PlutusToys") and "Інтернет-магазин дитячих іграшок в Україні" in t)
links = re.findall(r"\]\((https://[^)]+)\)", t)
chk("лише збудовані сторінки (6 з 6 створених; решту пропущено)", len(links) == 6)
chk("посилання абсолютні на https://plutustoys.com.ua/", all(u.startswith("https://plutustoys.com.ua/") for u in links))
chk("хаби: brendy, podarunky, rozmalovky", all(f"https://plutustoys.com.ua/{h}.html" in links for h in ("brendy", "podarunky", "rozmalovky")))
chk("без цін і обіцянок (грн/₴/безкоштовн/гарант/знижк)", not re.search(r"грн|₴|безкоштовн|гарант|знижк|найкращ|дешев", t, re.I))
chk("без вкладених < > і CRLF", "<" not in t and ">" not in t.replace("> Інтернет", "") and "\r" not in t)
chk("sameAs головної містить Pinterest", bs.PINTEREST_URL == "https://www.pinterest.com/plutustoys/" and bs.PINTEREST_URL in bs.home_jsonld())
print("\nALL OK" if not F else f"\nFAIL: {len(F)}")
sys.exit(1 if F else 0)
