#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_ukr_chip.py — 13.10.2026 (SMM): чип «Українське для дітей» (evergreen, без дат, підпис «Своє, рідне»). Власник 13.10: банера в каруселі нема (вибірка — у чипах), на кнопках чипів нема анімацій.
Ключові слова: українськ|вишиван|патріот|тризуб|абетка; ціна 100–700, внесок ≥ 30, у наявності; відсіюються: воєнне/політичне/обсценне («воєн|руск|російськ|іди на»), англійська абетка,
«фрази|гасл|слава» (невідомий зміст; id 191377 «Тимчасові тату "Українські фрази"»), військові образи (захисник|воїн|козак|зсу|герої), зброя/мілітарі (NABORY_BAD_WORDS), книжки/манга."""
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


_n = [0]


def P(name, price=250, contrib=60, stock=5, cat=None, photo="https://x/p.jpg", pid=None):
    _n[0] += 1
    return {"id": pid or str(8000 + _n[0]), "name": name, "category": cat or f"К{_n[0] % 9}", "price": price, "contribution": contrib, "stock": stock, "photo": photo}


W = ["Калина", "Мальви", "Барвінок", "Соняшник", "Лелека", "Журавель", "Ромашка", "Волошка", "Тополя", "Берегиня", "Веселка", "Дзвіночок", "Рушничок", "Колосок", "Мрія"]
good = [P(f"Абетка українська «{W[i]}»", cat=f"Кат{i}") for i in range(4)] + [P(f"Сорочка-вишиванка для ляльки «{W[4 + i]}»", cat=f"Кат{4 + i}") for i in range(4)] + [P(f"Пазл Україна патріотичний «{W[8 + i]}»", cat=f"Кат{8 + i}") for i in range(3)] + [P(f"Тризуб магнітний «{W[11 + i]}»", cat=f"Кат{11 + i}") for i in range(2)]
bad = {
    "id191377": P('Тимчасові тату "Українські фрази"', pid="191377"),
    "gaslo": P("Наліпки українські гасла «Г1»"),
    "slava": P("Футболка українська «Слава Україні»"),
    "war": P("Пазл українська воєнна техніка «W1»"),
    "rus": P("Розмальовка русский алфавіт та українська «R1»"),
    "idy": P("Українська іграшка іди на «I1»"),
    "eng": P("Абетка англійська та українська «E1»"),
    "def": P("Фігурка захисник українська «D1»"),
    "kozak": P("Козак українська лялька «K1»"),
    "zsu": P("Набір українська ЗСУ «Z1»"),
    "heroes": P("Українські герої набір «H1»"),
    "weapon": P("Український пістолет «P1»"),
    "mil": P("Українська військова техніка «M1»"),
    "book": P("Українська абетка «Букварик»", cat="Дитячі книги"),
    "manga": P("Українська манга «Мк1»"),
    "cheap": P("Українська абетка дешева «C1»", price=50),
    "dear": P("Українська абетка дорога «C2»", price=900),
    "lowc": P("Українська абетка низький внесок «C3»", contrib=10),
    "oos": P("Українська абетка немає «C4»", stock=0),
}
prods = good + list(bad.values())
for day in ("2026-03-05", "2026-07-20", "2026-10-13", "2026-12-24"):
    os.environ["SITE_PICK_DATE"] = day
    cs = bs.home_chip_sets(prods)
    chk(f"{day}: чип «ukr» є в будь-яку пору року (evergreen)", "ukr" in cs)
os.environ["SITE_PICK_DATE"] = "2026-03-05"
cs = bs.home_chip_sets(prods)
ids = {p["id"] for p in cs["ukr"]}
bad_ids = {b["id"] for b in bad.values()}
chk(f"у чипі лише придатні картки: жодного з {len(bad_ids)} відсіяних ({sorted(ids & bad_ids) or 'ок'})", not (ids & bad_ids))
chk("id 191377 «Тимчасові тату \"Українські фрази\"» відсіяно", "191377" not in ids)
chk("кожна картка містить ключове слово, ціна 100–700, внесок ≥ 30, у наявності", all(re.search(r"українськ|вишиван|патріот|тризуб|абетка", p["name"], re.I) and 100 <= p["price"] <= 700 and p["contribution"] >= 30 and p["stock"] > 0 for p in cs["ukr"]))
chk("до 10 карток, без повторів", 6 <= len(cs["ukr"]) <= 10 and len(ids) == len(cs["ukr"]))
defs = bs.home_chip_defs(cs)
chk("підпис чипа «Своє, рідне», назва «Українське для дітей»", any(d[0] == "ukr" and d[1] == "Українське для дітей" and d[2] == "Своє, рідне" for d in defs))
chk("сезонні чипи («ny»/«hel») поза вікном не з’являються, «ukr» не заважає", "hel" not in cs and "ny" not in cs)
# менше 6 придатних — чипа нема
few = good[:5] + list(bad.values())
chk("менше 6 придатних карток — чипа нема (не вигадуємо)", "ukr" not in bs.home_chip_sets(few))
# HTML: чип із іконкою-соняшником
chk("іконка чипа ukr — інлайн-SVG (aria-hidden), без емодзі", "ukr" in bs.CHIP_ICONS and bs.CHIP_ICONS["ukr"].startswith("<svg") and 'aria-hidden="true"' in bs.CHIP_ICONS["ukr"])

# Chromium: чип «ukr» (інших активних тем нема): першим слайдом — товар, не банер; кнопки чипів без анімацій; клік по чипу розкриває набір
import http.server  # noqa: E402
import shutil  # noqa: E402
import socketserver  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
chk("playwright доступний", sync_playwright is not None)
if sync_playwright:
    os.environ["SITE_GA4_ID"] = "G-TEST123456"
    only = [dict(p, photo="data:image/gif;base64,R0lGODlhAQABAAAAACw=") for p in good] + [P(f"Іграшка «Лінія{W[i]}»", price=300 + i * 11, contrib=100, cat=f"Загал{i}", photo="data:image/gif;base64,R0lGODlhAQABAAAAACw=") for i in range(12)]
    cats = {}
    for p in only:
        cats.setdefault(p["category"], []).append(p)
    cat_list = sorted(cats, key=lambda c: -len(cats[c]))
    cat_slug = {c: f"c{i}" for i, c in enumerate(cat_list)}
    root = Path(tempfile.mkdtemp())
    os.environ["SITE_PICK_DATE"] = "2026-03-05"
    bs.OUT = str(root)
    bs.write_home(only, cats, cat_list, cat_slug)
    shutil.copytree(HERE / "site" / "assets", root / "assets")
    os.chdir(root)

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for w in (1280, 390):
            ctx = br.new_context(viewport={"width": w, "height": 800}, reduced_motion="reduce")
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.route("**/googletagmanager.com/**", lambda r: r.abort())
            pg.route("**/facebook.net/**", lambda r: r.abort())
            pg.goto(f"{base}/index.html", wait_until="load")
            pg.wait_for_timeout(700)
            info = pg.evaluate("(()=>{const t=document.querySelector('#home-car .track'); const f=[...t.children].find(k=>!k.hasAttribute('data-clone')); const b=document.querySelector('#home-chips a.chip[data-set=ukr]'); return [f.className, !!document.querySelector('.banner'), b? b.textContent.trim():'', b? !!b.querySelector('svg.chip-ico'):false, b? getComputedStyle(b).backgroundColor:'', [...document.querySelectorAll('#home-chips .chip, #home-chips .chip *')].map(e=>getComputedStyle(e).animationName).filter(n=>n!=='none').length]})()")
            chk(f"{w}px: банера в каруселі нема, перший слайд — товар; чип «Українське для дітей» з іконкою-SVG і синьо-жовтою темою; жодної анімації на чипах {info}", "banner" not in info[0] and info[1] is False and info[2] == "Українське для дітей" and info[3] and info[4] == "rgb(234, 242, 255)" and info[5] == 0)
            pg.evaluate("document.addEventListener('click', e => { if (e.target.closest('a.card')) e.preventDefault(); }, true)")
            st = pg.evaluate("(()=>{const a=document.querySelector('#home-chips a.chip[data-set=ukr]'); return [a.tagName, a.getAttribute('href'), document.querySelectorAll('#home-chips button').length]})()")
            chk(f"{w}px: чип «Українське для дітей» — посилання на окрему сторінку (власник 16.10), не перемикач каруселі {st}", st == ["A", bs.CHIP_PAGE_FILES["ukr"], 0])
            chk(f"{w}px: без помилок JS", errs == [])
            ctx.close()
        br.close()
    os.environ.pop("SITE_PICK_DATE", None)

def _lum(h):
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
pairs = {"chip hel": ("#FFF3E0", "#2A1647"), "chip ny": ("#FFFFFF", "#C62F2F"), "chip nab": ("#3A2A1E", "#FFD9A8"), "chip ukr": ("#0B2447", "#EAF2FF")}
low = {k: round(_ratio(*v), 2) for k, v in pairs.items() if _ratio(*v) < 4.5}
chk(f"контраст тексту на тематичних чипах ≥ 4.5:1 (WCAG AA): {low or 'ок'}", not low and all(c.lower() in css.lower() for v in pairs.values() for c in v if c != "#FFFFFF"))
chk("у CSS нема анімацій і @keyframes (власник 13.10: чипи не вантажать сайт), нема стилів банера", "@keyframes" not in css and "animation" not in css and "card.banner" not in css and "home_banner" not in (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8") and not hasattr(bs, "home_banner_html"))
os.environ.pop("SITE_PICK_DATE", None)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
