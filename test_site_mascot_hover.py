#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_mascot_hover.py — 17.10.2026 (власник: «чи можна зробити щоб Плутус реагував на курсор мишки?» → «давай зробимо 1»).
Наведення мишею / клік / тап по Плутусу (.mascot-wrap) одразу запускає наступний рух із ротації, не чекаючи звичайних 12–20 с. Не під час іншого руху, не частіше ніж раз на 3 с після кінця попереднього;
не діє при prefers-reduced-motion. Chromium (реальний час; у розмітці лишено один короткий кліп «gift» 1,66 с)."""
import contextlib
import functools
import http.server
import io
import os
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["SITE_PICK_DATE"] = "2026-10-10"
HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402
import site_photo_probe  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


def item(pid, name, price):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": 5, "category_name": "Картини за номерами", "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/{pid}"}


site_photo_probe.run = lambda idx: None
cat = {str(i): item(i, f"Товар {i}", 250 + i) for i in range(1, 30)}
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.tp.fetch_toysi_catalog = lambda: dict(cat)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()
shutil.copytree(HERE / "site" / "assets", out / "assets", dirs_exist_ok=True)
html = (out / "index.html").read_text(encoding="utf-8")
m = re.search(r'data-clips="([^"]+)" data-ms="([^"]+)" data-w="([^"]+)" data-nofirst="([^"]*)"(?: data-dip="([^"]*)")?', html)
names = [re.search(r"plutus_(\w+)\.webp", c).group(1) for c in m.group(1).split("|")]
gi = names.index("gift")
only = html.replace(m.group(0), f'data-clips="{m.group(1).split("|")[gi]}" data-ms="{m.group(2).split(",")[gi]}" data-w="2" data-nofirst=""')
chk("розмітка: .mascot-wrap є, курсор-вказівник у CSS", 'class="mascot-wrap"' in html and ".hero2 .mascot-wrap{cursor:pointer;-webkit-tap-highlight-color:transparent" in (HERE / "site/assets/styles.css").read_text(encoding="utf-8"))
css = (HERE / "site/assets/styles.css").read_text(encoding="utf-8")
chk("CSS: без «квадрата» при тапі (tap-highlight прозорий, user-select none, touch-action manipulation), відгук .poke через transition (без @keyframes)", all(x in css for x in ("-webkit-tap-highlight-color:transparent", "user-select:none", "touch-action:manipulation", ".hero2 .mascot-wrap.poke{transform:scale(.9)}", "transform .15s ease")) and "@keyframes pt-poke" not in css)
js = (HERE / "site/assets/app.js").read_text(encoding="utf-8")
chk("JS: react() не під час руху/підвантаження, пауза після кінця, скасовує відкладений автопоказ; pointerenter (миша), pointerdown (дотик), click", all(x in js for x in ("function react(touch)", "function poke()", "function prefetchNext()", "REACT_COOL=3000", 'wrap.addEventListener("pointerenter"', 'wrap.addEventListener("pointerdown"', 'wrap.addEventListener("click"', "clearTimeout(timer);   // скасувати відкладений автопоказ")))

H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None


class _Srv(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


srv = _Srv(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
from playwright.sync_api import sync_playwright  # noqa: E402

INIT = """window.__plays=0; window.__pf=[]; const _f=window.fetch; window.fetch=function(u){ if(String(u).indexOf('plutus_gift')>=0) window.__pf.push(String(u)); return _f.apply(this,arguments); }; document.addEventListener('DOMContentLoaded',()=>{const c=document.getElementById('hero-mascot-clip'); let was=false;
new MutationObserver(()=>{const has=!!c.getAttribute('src'); if(has && !was) window.__plays++; was=has;}).observe(c,{attributes:true,attributeFilter:['src']});});"""
URL = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
STATE = "(()=>{const w=document.querySelector('.mascot-wrap'); return [window.__plays, w.classList.contains('clip-on')]})()"


def new_page(br, **kw):
    ctx = br.new_context(viewport={"width": 1280, "height": 700}, **kw)
    pg = ctx.new_page()
    pg.route("**/index.html", lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=only))
    pg.route("**/googletagmanager.com/**", lambda r: r.abort())
    pg.add_init_script(INIT)
    pg.goto(URL, wait_until="load")
    return ctx, pg


with sync_playwright() as pw:
    br = pw.chromium.launch()
    ctx, pg = new_page(br)
    pg.wait_for_timeout(500)          # раніше, ніж перший автопоказ (≥ 3 с після load)
    chk(f"наступний рух ПЕРЕДЗАВАНТАЖЕНО наперед (fetch у кеш), поки Плутус спокійно сидить: {pg.evaluate('window.__pf.length')} запит(и)", pg.evaluate("window.__pf.length") >= 1)
    pg.hover(".mascot-wrap")
    pg.wait_for_timeout(900)
    st = pg.evaluate(STATE)
    chk(f"наведення миші ЗАПУСКАЄ рух одразу (≈1 с, а не через 3+ с автопоказу): plays={st[0]}, clip-on={st[1]}", st[0] == 1 and st[1] is True)
    pg.mouse.move(2, 2)
    pg.hover(".mascot-wrap")
    pg.wait_for_timeout(120)
    chk(f"повторне наведення ПІД ЧАС руху не запускає другий: plays={pg.evaluate(STATE)[0]}", pg.evaluate(STATE)[0] == 1)
    chk("…але дає миттєвий візуальний відгук (клас .poke на 0,3 с): людина бачить, що зараховано", pg.evaluate("document.querySelector('.mascot-wrap').classList.contains('poke')") is True)
    pg.wait_for_timeout(1800)           # рух (1,66 с + 0,4 запас) завершився
    st = pg.evaluate(STATE)
    chk(f"рух закінчився, wrap без clip-on: plays={st[0]}, clip-on={st[1]}", st[0] == 1 and st[1] is False)
    pg.mouse.move(2, 2)
    pg.hover(".mascot-wrap")
    pg.wait_for_timeout(900)
    chk(f"одразу після кінця — пауза 3 с: нове наведення НЕ запускає рух (plays={pg.evaluate(STATE)[0]})", pg.evaluate(STATE)[0] == 1)
    pg.wait_for_timeout(2600)           # пауза спливла
    pg.mouse.move(2, 2)
    pg.hover(".mascot-wrap")
    pg.wait_for_timeout(900)
    st = pg.evaluate(STATE)
    chk(f"після паузи наведення знову запускає рух: plays={st[0]}, clip-on={st[1]}", st[0] == 2 and st[1] is True)
    pg.wait_for_timeout(1500)           # кінець руху
    pg.wait_for_timeout(1500)           # пауза для дотику 1,2 с (для миші 3 с)
    pg.dispatch_event(".mascot-wrap", "pointerdown", {"pointerType": "touch", "isPrimary": True, "bubbles": True})
    pg.wait_for_timeout(900)
    st = pg.evaluate(STATE)
    chk(f"тап (pointerdown touch) запускає рух: plays={st[0]}", st[0] == 3 and st[1] is True)
    ctx.close()

    ctx, pg = new_page(br, reduced_motion="reduce")
    pg.wait_for_timeout(400)
    pg.hover(".mascot-wrap")
    pg.wait_for_timeout(1200)
    chk(f"prefers-reduced-motion: наведення нічого не запускає (plays={pg.evaluate(STATE)[0]})", pg.evaluate(STATE)[0] == 0)
    ctx.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
