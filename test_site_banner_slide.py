#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_banner_slide.py — 13.10.2026 (план SMM): перший слайд каруселі — плитка-банер теми дня (Хелловін / Набори в подарунок). Перевіряємо в реальному Chromium (1280 і 390):
банер перший, не рахується як товар у view_item_list (10 позицій, не 11), клік по банеру → select_item home_banner_<тема>; «chip:hel» розкриває чип на місці; «nab» веде на nabory.html;
індекс select_item товару не зсувається банером (перший товар = index 0, у т.ч. клікнутий клон); цикл каруселі не ламається (стрілки, без порожніх зон); без активних тем банера нема."""
import http.server
import os
import shutil
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_GA4_ID"] = "G-TEST123456"
os.environ["SITE_META_PIXEL_ID"] = "12345678901"
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


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    chk("playwright доступний", False)
    print("FAILED")
    sys.exit(1)

_n = [0]


def P(price, contrib, cat, name):
    _n[0] += 1
    return {"id": str(9000 + _n[0]), "name": name, "category": cat, "price": price, "contribution": contrib, "stock": 5, "photo": "data:image/gif;base64,R0lGODlhAQABAAAAACw="}


prods = [P(160 + i * 25, 110 + i, f"Кат{i % 12}", f"Іграшка «Лінія{i}»") for i in range(30)]
prods += [P(150 + i * 10, 60, f"Кат{i % 12}", f"Декор Хелловін «Лінія{i}»") for i in range(8)]
prods += [P(220 + i, 60, "Інша творчість", f"Набір для творчості «Серія{i}»") for i in range(8)]
cats = {}
for p in prods:
    cats.setdefault(p["category"], []).append(p)
cat_list = sorted(cats, key=lambda c: -len(cats[c]))
cat_slug = {c: f"c{i}" for i, c in enumerate(cat_list)}
root = Path(tempfile.mkdtemp())
built = {}
for day in ("2026-10-20", "2026-10-21"):
    os.environ["SITE_PICK_DATE"] = day
    d = root / day
    d.mkdir()
    bs.OUT = str(d)
    bs.write_home(prods, cats, cat_list, cat_slug)
    shutil.copytree(HERE / "site" / "assets", d / "assets")
    html = (d / "index.html").read_text(encoding="utf-8")
    import re
    m = re.search(r'<a class="card slide banner[^"]*"[^>]*data-banner="(\w+)"', html)
    built[day] = m.group(1) if m else None
chk(f"два сусідні дні дають різні теми банера (hel і nab): {built}", set(built.values()) == {"hel", "nab"})
chk("без активних тем (немає ні сезонного чипа, ні сторінки наборів) банера нема", bs.home_banner_html({}, False) == "" and "banner" not in bs.home_banner_html({"hel": []}, False).replace("card slide banner", "") or bs.home_banner_html({}, False) == "")
os.environ.pop("SITE_PICK_DATE", None)


def lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
pairs = {  # (текст, фон) із CSS тем: чипи, CTA, банери (SMM 13.10 бриф; WCAG ≥ 4.5:1)
    "chip hel": ("#FFF3E0", "#2A1647"), "chip ny": ("#FFFFFF", "#C62F2F"), "chip nab": ("#3A2A1E", "#FFD9A8"),
    "banner hel текст": ("#FFF3E0", "#3A1D5C"), "banner hel CTA": ("#1B1030", "#FF7A1A"),
    "banner ny текст": ("#FFFFFF", "#1E5A8A"), "banner ny CTA": ("#FFFFFF", "#D93A3A"),
    "banner nab текст": ("#3A2A1E", "#FFD9A8"), "banner nab CTA": ("#FFFFFF", "#C93A26"),
}
chk("кольори пар у CSS присутні: " + ", ".join(sorted({c for p_ in pairs.values() for c in p_})[:3]) + "…", all(c.lower() in css.lower().replace("#fff;", "#ffffff;").replace("#fff ", "#ffffff ") for p_ in pairs.values() for c in p_))
low = {k: round(ratio(*v), 2) for k, v in pairs.items() if ratio(*v) < 4.5}
chk(f"контраст усіх тематичних пар ≥ 4.5:1 (WCAG AA): {low or 'ок'}", not low)
chk("prefers-reduced-motion вимикає анімації тематичних чипів і банера, нахил знято", "@media (prefers-reduced-motion:reduce)" in css and "animation:none!important" in css)
chk("лише системні шрифти (без @font-face/веб-шрифтів у темах), емодзі в банері нема (інлайн-SVG)", "@font-face" not in css and "b-emoji" not in css)

os.chdir(root)


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"
VIS = "(()=>{const t=document.querySelector('#home-car .track'); const tr=t.getBoundingClientRect(); let cov=0, last=tr.left; [...t.children].filter(k=>{const r=k.getBoundingClientRect(); return r.right>tr.left && r.left<tr.right;}).sort((a,b)=>a.getBoundingClientRect().left-b.getBoundingClientRect().left).forEach(k=>{const r=k.getBoundingClientRect(); const l=Math.max(r.left,last), rr=Math.min(r.right,tr.right); if(rr>l){cov+=rr-l; last=rr;} }); return Math.round(100*cov/tr.width)})()"
EV = "(window.dataLayer||[]).map(a=>Array.from(a)).filter(a=>a[1]==='view_item_list'||a[1]==='select_item').map(a=>[a[1],a[2].item_list_id,a[2].item_list_name,a[2].items.length,a[2].items[0].index,a[2].items[0].item_name])"

with sync_playwright() as p:
    br = p.chromium.launch()
    for day, theme in built.items():
        for w in (1280, 390):
            pg = br.new_page(viewport={"width": w, "height": 800})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.route("**/googletagmanager.com/**", lambda r: r.abort())
            pg.route("**/facebook.net/**", lambda r: r.abort())
            pg.goto(f"{BASE}/{day}/index.html", wait_until="load")
            pg.wait_for_timeout(900)
            pg.evaluate("document.addEventListener('click', e => { if (e.target.closest('a.card')) e.preventDefault(); }, true)")
            first = pg.evaluate("(()=>{const t=document.querySelector('#home-car .track'); const f=[...t.children].find(k=>!k.hasAttribute('data-clone')); return [f.className, f.getAttribute('data-banner'), f.querySelector('.nm').textContent, f.querySelector('.b-cta')? f.querySelector('.b-cta').textContent:'', t.children.length, t.querySelectorAll('[data-clone]').length]})()")
            CTA = {"hel": "Обрати страшилки →", "nab": "Відкрити набори →", "ny": "Подарунки під ялинку →"}
            chk(f"{day} {w}px: першим слайдом тематичний банер «{theme}» (клас theme-{theme}, заголовок/підзаголовок/CTA за брифом SMM), 11 слайдів + 20 клонів", first[0].startswith("card slide banner") and f"theme-{theme}" in first[0] and first[1] == theme and first[3] == CTA[theme] and first[4] == 31 and first[5] == 20)
            lst = pg.evaluate(EV)
            chk(f"{day} {w}px: view_item_list рівно один і лише з 10 ТОВАРІВ (банер не рахується)", len(lst) == 1 and lst[0][3] == 10 and lst[0][1] == "home_pick")
            # цикл: 8 кроків стрілкою, вікно завжди заповнене
            vis = []
            for _ in range(8):
                pg.evaluate("document.querySelector('#home-car .arrow.r').click()")
                pg.wait_for_timeout(600)
                vis.append(pg.evaluate(VIS))
            chk(f"{day} {w}px: цикл із банером: 8 кроків, вікно покрите плитками ≥ 90% ширини (порожніх зон нема; банер удвічі ширший, тому рахуємо покриття, а не кількість) {vis}", min(vis) >= 90 and errs == [])
            # повернутись на початок і перевірити індекс першого товару (банер не зсуває)
            pg.evaluate("document.querySelector('#home-car .track').scrollLeft = 0")
            pg.wait_for_timeout(400)
            pg.evaluate("document.querySelector('#home-car .track a.card:not(.banner):not([data-clone])').click()")
            pg.wait_for_timeout(200)
            sel = [e for e in pg.evaluate(EV) if e[0] == "select_item"]
            chk(f"{day} {w}px: select_item першого товару має index 0 (банер індексів не зсуває)", sel and sel[-1][4] == 0 and sel[-1][1] == "home_pick")
            pg.evaluate("document.querySelector('#home-car .track a.card[data-clone]:not(.banner)').click()")
            pg.wait_for_timeout(200)
            sel = [e for e in pg.evaluate(EV) if e[0] == "select_item"]
            chk(f"{day} {w}px: клік по КЛОНУ товару дає індекс оригіналу в списку товарів (0..9)", 0 <= sel[-1][4] <= 9)
            # клік по банеру
            before = len(pg.evaluate(EV))
            pg.evaluate("document.querySelector('#home-car .track a.card.banner:not([data-clone])').click()")
            pg.wait_for_timeout(300)
            ev = pg.evaluate(EV)[before:]
            chk(f"{day} {w}px: клік по банеру → select_item home_banner_{theme} (item_name = заголовок)", ev and ev[0][0] == "select_item" and ev[0][1] == f"home_banner_{theme}" and ev[0][2] == f"home_banner_{theme}" and ev[0][5])
            if theme == "hel":
                st = pg.evaluate("[document.querySelector('#home-chips button[data-set=hel]').getAttribute('aria-pressed'), document.querySelectorAll('#home-car .track a.card:not([data-clone])').length, (document.getElementById('home-chip-sub')||{}).textContent]")
                chk(f"{day} {w}px: банер «Хелловін» розкриває чип «Хелловін» на місці (aria-pressed, набір з 8 товарів, підпис)", st[0] == "true" and st[1] == 8 and "Гелловіну" in st[2])
            else:
                href = pg.evaluate("document.querySelector('#home-car .track a.card.banner:not([data-clone])').getAttribute('href')")
                chk(f"{day} {w}px: банер «Набори в подарунок» веде на nabory.html", href == "nabory.html")
            pg.close()
    br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
