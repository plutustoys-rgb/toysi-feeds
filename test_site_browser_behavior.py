#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_browser_behavior.py — 13.10.2026. Поведінка в РЕАЛЬНОМУ Chromium (python Playwright), а не grep по джерелу (ретро-аудит 12.10: 43 з 95 мутацій тестів виживали, а тестове радіо LiqPay було видно всім,
бо перевірявся лише рядок CSS). Перевіряє на збірці синтетичного каталогу:
  • кошик: тестового радіо LiqPay НЕМАЄ в DOM без ?liqpay_test=1; з параметром є й видиме (computed display ≠ none); нова вкладка без параметра — знову нема;
  • чипи головної: перемикання, рівно один view_item_list на перемикання з item_list_id/name, повторний клік по активному — без події, 60 перемикань → 20 клонів і 10 оригіналів, select_item
    несе id чипа; ціни наборів у межах;
  • Плутус: ротація першого мішка = точно вагам (по 2 «звичайних», по 1 «рідких»), stretch_yawn не першим (8 завантажень), повторів підряд ≤ 1, жодного кадру «обидва/жоден» не видно.
Без playwright тест падає ЯВНО."""
import http.server
import json
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
os.environ.pop("SITE_LIQPAY_LIVE", None)
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
    chk("playwright доступний (без нього поведінку в браузері не перевірити)", False)
    print("FAILED")
    sys.exit(1)

# ── синтетична збірка: головна з чипами + кошик ──
_n = [0]


def P(price, contrib, cat, name=None, stock=5):
    _n[0] += 1
    return {"id": str(7000 + _n[0]), "name": name or f"Іграшка \"Лінія{_n[0]}\"", "category": cat, "price": price, "contribution": contrib, "stock": stock, "photo": "data:image/gif;base64,R0lGODlhAQABAAAAACw="}


prods = [P(60 + i * 18, 55 + i, f"Кат{i % 12}") for i in range(14)]
prods += [P(320 + i * 25, 105 + i, f"Кат{i % 12}") for i in range(14)]
prods += [P(220 + i, 60, "Інша творчість", name=f"Набір для творчості \"Серія{i}\"") for i in range(12)]
cats = {}
for p in prods:
    cats.setdefault(p["category"], []).append(p)
cat_list = sorted(cats, key=lambda c: -len(cats[c]))
cat_slug = {c: f"c{i}" for i, c in enumerate(cat_list)}
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.LIQPAY_LIVE = False
bs.write_home(prods, cats, cat_list, cat_slug)
bs.write_cart([])
shutil.copytree(HERE / "site" / "assets", out / "assets")
# poster/клипи: справжні файли з assets; Image() вантажить їх з локального сервера миттєво

os.chdir(out)


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"
CART = "localStorage.setItem('pt_cart_v1', JSON.stringify({'1':{id:'1',name:'T',price:200,photo:'',qty:1}}))"

with sync_playwright() as p:
    br = p.chromium.launch()

    def page(ctx=None, w=1280, h=800, init=None):
        ctx = ctx or br.new_context(viewport={"width": w, "height": h})
        pg = ctx.new_page()
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/facebook.net/**", lambda r: r.abort())
        if init:
            pg.add_init_script(init)
        return ctx, pg

    # ── кошик: тестове радіо LiqPay ──
    ctx, pg = page(init=CART)
    st = lambda url: (pg.goto(url, wait_until="load"), pg.wait_for_timeout(500), pg.evaluate("(()=>{const e=document.getElementById('pay-test'); return [!!e, e? getComputedStyle(e).display:null, document.querySelectorAll('input[name=payment]').length]})()"))[2]  # noqa: E731
    a = st(f"{BASE}/cart.html")
    chk("кошик без параметра: тестового радіо НЕМАЄ в DOM, радіо оплати одне (cod)", a == [False, None, 1])
    b = st(f"{BASE}/cart.html?liqpay_test=1")
    chk("?liqpay_test=1: радіо з'являється й ВИДИМЕ (computed display ≠ none), два радіо", b[0] is True and b[1] not in (None, "none") and b[2] == 2)
    pg2 = ctx.new_page()
    pg2.add_init_script(CART)
    pg2.goto(f"{BASE}/cart.html", wait_until="load")
    pg2.wait_for_timeout(400)
    chk("прапорець у sessionStorage лише цієї вкладки: нова вкладка без параметра — радіо нема", pg2.evaluate("!document.getElementById('pay-test')") is True)
    pg2.close()
    ctx.close()
    ctx, pg = page(init=CART)
    c = st(f"{BASE}/cart.html?liqpay_test=0")
    chk("?liqpay_test=0 не розкриває", c[0] is False)
    ctx.close()

    # ── чипи головної ──
    for w in (1280, 390):
        ctx, pg = page(w=w, h=800)
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"{BASE}/index.html", wait_until="load")
        pg.wait_for_timeout(800)
        pg.evaluate("document.addEventListener('click', e => { if (e.target.closest('a.card')) e.preventDefault(); }, true)")
        ev = lambda: pg.evaluate("(window.dataLayer||[]).map(a=>Array.from(a)).filter(a=>a[1]==='view_item_list'||a[1]==='select_item').map(a=>[a[1],a[2].item_list_id,a[2].item_list_name,a[2].items.length])")  # noqa: E731
        state = lambda: pg.evaluate("(()=>{const t=document.querySelector('#home-car .track'); return [t.children.length, t.querySelectorAll('[data-clone]').length, [...t.querySelectorAll('a.card:not([data-clone]) .pr')].map(e=>parseFloat(e.textContent))]})()")  # noqa: E731
        chips = pg.evaluate("[...document.querySelectorAll('#home-chips button')].map(b=>b.dataset.set)")
        chk(f"{w}px: чипи Усі, u300, m700, nab", chips == ["all", "u300", "m700", "nab"])
        base_state = state()
        chk(f"{w}px: початково 10 оригіналів + 20 клонів", base_state[0] == 30 and base_state[1] == 20)
        seen = {}
        for key in ("u300", "m700", "nab"):
            before = len(ev())
            pg.click(f"#home-chips button[data-set={key}]")
            pg.wait_for_timeout(250)
            new = ev()[before:]
            stt = state()
            seen[key] = (new, stt)
        chk(f"{w}px: кожне перемикання → РІВНО один view_item_list з id=name=home_chip_<ключ>, 10 позицій", all(len(seen[k][0]) == 1 and seen[k][0][0] == ["view_item_list", f"home_chip_{k}", f"home_chip_{k}", 10] for k in seen))
        chk(f"{w}px: «До 300 ₴» усі ціни < 300; «300–700 ₴» у 300–700; цикл (30 дітей, 20 клонів) після кожного перемикання", all(x < 300 for x in seen["u300"][1][2]) and all(300 <= x <= 700 for x in seen["m700"][1][2]) and all(seen[k][1][:2] == [30, 20] for k in seen))
        before = len(ev())
        pg.click("#home-chips button[data-set=nab]")
        pg.wait_for_timeout(200)
        chk(f"{w}px: повторний клік по активному чипу — без події й без зміни", len(ev()) == before)
        pg.evaluate("document.querySelector('#home-car .track a.card:not([data-clone])').click()")
        pg.wait_for_timeout(200)
        sel = [e for e in ev() if e[0] == "select_item"]
        chk(f"{w}px: select_item у наборі «Набори» несе item_list_id/name home_chip_nab", sel and sel[-1][1] == "home_chip_nab" and sel[-1][2] == "home_chip_nab")
        pg.click("#home-chips button[data-set=all]")
        pg.wait_for_timeout(250)
        pg.evaluate("document.querySelector('#home-car .track a.card:not([data-clone])').click()")
        pg.wait_for_timeout(200)
        sel = [e for e in ev() if e[0] == "select_item"]
        chk(f"{w}px: після повернення на «Усі» select_item знову home_pick / «Добірка Плутуса»", sel[-1][1] == "home_pick" and sel[-1][2] == "Добірка Плутуса")
        for i in range(60):
            pg.evaluate(f"document.querySelector('#home-chips button[data-set={['u300','m700','nab','all'][i % 4]}]').click()")
        pg.wait_for_timeout(300)
        chk(f"{w}px: після 60 перемикань клонів рівно 20, помилок консолі 0", state()[:2] == [30, 20] and errs == [])
        ctx.close()

    # ── Плутус: ротація ──
    NAMES = ["tailtrip", "ear_scratch", "grooming", "sniff", "shakeoff", "stretch_yawn", "sneeze", "tailchase", "sleep", "rolljump"]
    OBS = """window.__srcs=[]; new MutationObserver(()=>{const c=document.getElementById('hero-mascot-clip'); if(c && c.getAttribute('src')) { const n=c.getAttribute('src').split('/').pop().split('?')[0].replace('plutus_','').replace('.webp',''); const l=window.__srcs; if(l[l.length-1]!==n+'#'+window.__t) l.push(n+'#'+window.__t);} }).observe(document, {subtree:true, attributes:true, attributeFilter:['src']}); window.__t=0;"""

    def run_plays(n_plays, max_steps=4000):
        ctx = br.new_context(viewport={"width": 1280, "height": 800})
        pg = ctx.new_page()
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/facebook.net/**", lambda r: r.abort())
        pg.add_init_script(OBS)
        pg.clock.install()
        pg.goto(f"{BASE}/index.html", wait_until="load")
        both = neither = 0
        for step in range(max_steps):
            pg.evaluate("window.__t = (window.__t||0) + 1")
            pg.clock.run_for(600)
            pg.wait_for_timeout(8)
            if step % 6 == 0:
                v = pg.evaluate("[getComputedStyle(document.getElementById('hero-mascot')).visibility, getComputedStyle(document.getElementById('hero-mascot-clip')).visibility]")
                both += v == ["visible", "visible"]
                neither += v == ["hidden", "hidden"]
            seq = pg.evaluate("window.__srcs")
            if len(seq) >= n_plays:
                break
        seq = [x.split("#")[0] for x in pg.evaluate("window.__srcs")]
        ctx.close()
        return seq, both, neither

    seq, both, neither = run_plays(16)
    first14 = seq[:16]
    from collections import Counter
    cnt = Counter(first14)
    expected = {n: (1 if n in ("sneeze", "tailchase", "sleep", "rolljump") else 2) for n in NAMES}
    chk(f"ротація: перший мішок із 16 показів (6×2 + 4×1) = точно вагам (по 2 «звичайних», по 1 «рідких»): {dict(cnt)}", len(first14) == 16 and dict(cnt) == expected)
    chk("ротація: повторів підряд ≤ 1 (вимушений повтор у мішку можливий, ≈4%)", sum(1 for a, b in zip(first14, first14[1:]) if a == b) <= 1)
    chk(f"Плутус: жодного відліку «обидва видимі»/«жоден» (both={both}, neither={neither})", both == 0 and neither == 0)
    firsts = []
    for _ in range(8):
        s, _b, _n2 = run_plays(1, max_steps=60)
        firsts.append(s[0] if s else None)
    chk(f"stretch_yawn НЕ першим показом (8 завантажень: {firsts})", None not in firsts and "stretch_yawn" not in firsts)
    br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
