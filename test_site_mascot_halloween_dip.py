#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_mascot_halloween_dip.py — 15.10.2026 (SMM: сезонний рух «Гелловін», кадр спокою ≠ спільний poster). Chromium: у сезон (SITE_PICK_DATE=2026-10-20) рух Гелловіну грає ЧЕРЕЗ «занурення»: poster → обгортка гасне (.dip, opacity 0) → кліп видно (.clip-on) →
наприкінці знову .dip → кліп знято (src порожній), poster на місці. Ні в який момент не видно обох одночасно (двійник). Ротацію звужено до одного руху через підміну атрибутів у HTML (тест лише про перехід)."""
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
os.environ["SITE_PICK_DATE"] = "2026-10-20"
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
m = re.search(r'data-clips="([^"]+)" data-ms="([^"]+)" data-w="([^"]+)" data-nofirst="([^"]*)" data-dip="([^"]*)"', html)
chk("у сезон розмітка має data-dip", m is not None)
names = [re.search(r"plutus_(\w+)\.webp", c).group(1) for c in m.group(1).split("|")]
hi = names.index("halloween")
only = html.replace(m.group(0), f'data-clips="{m.group(1).split("|")[hi]}" data-ms="{m.group(2).split(",")[hi]}" data-w="2" data-nofirst="" data-dip="0"')

H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None


class _Srv(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


srv = _Srv(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
from playwright.sync_api import sync_playwright  # noqa: E402

INIT = """window.__log=[]; document.addEventListener('DOMContentLoaded',()=>{const w=document.getElementById('hero-mascot').parentNode; const c=document.getElementById('hero-mascot-clip'); const t0=performance.now();
const rec=()=>{const p=document.getElementById('hero-mascot'); window.__log.push([Math.round(performance.now()-t0), w.classList.contains('clip-on'), w.classList.contains('dip'), c.classList.contains('on'), (c.getAttribute('src')||'').indexOf('halloween')>=0, getComputedStyle(p).visibility, getComputedStyle(c).visibility, +getComputedStyle(w).opacity]);};
new MutationObserver(rec).observe(w,{attributes:true}); new MutationObserver(rec).observe(c,{attributes:true}); const loop=()=>{ if(performance.now()-t0<13000){ rec(); setTimeout(loop,40);} }; loop();});"""
with sync_playwright() as pw:
    br = pw.chromium.launch()
    pg = br.new_page(viewport={"width": 1280, "height": 700})
    pg.route("**/index.html", lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=only))
    pg.route("**/googletagmanager.com/**", lambda r: r.abort())
    pg.add_init_script(INIT)
    pg.goto(f"http://127.0.0.1:{srv.server_address[1]}/index.html", wait_until="load")
    pg.wait_for_timeout(12500)
    log = pg.evaluate("window.__log")
    br.close()
# [t, clip-on, dip, clipOn, srcHalloween, posterVis, clipVis, opacity]
ons = [e for e in log if e[1]]
dips = [e for e in log if e[2]]
chk("Гелловін зіграв: wrap отримав .clip-on із src кліпу (3,3 с + пауза)", bool(ons) and any(e[4] for e in ons))
first_dip = next((i for i, e in enumerate(log) if e[2]), None)
first_on = next((i for i, e in enumerate(log) if e[1]), None)
chk("перед показом кліпу спрацювало занурення (.dip раніше за .clip-on)", first_dip is not None and first_on is not None and first_dip < first_on)
# наприкінці: після .clip-on був другий .dip, потім wrap без .clip-on й без src
last_on = max(i for i, e in enumerate(log) if e[1])
end_dip = any(e[2] for e in log[last_on - 12:last_on + 1])
tail = log[-1]
chk("наприкінці — занурення, після нього кліп знято: wrap без .clip-on/.dip, src порожній, poster видно", end_dip and not tail[1] and not tail[2] and not tail[4] and tail[5] == "visible")
both = [e for e in log if e[5] == "visible" and e[6] == "visible"]
chk(f"жодної миті poster і кліп видимі разом (двійник): {len(both)}", not both)
chk(f"під час підміни обгортка справді стає прозорою: мінімум opacity у .dip = {min(e[7] for e in dips) if dips else None} (< 0,5)", bool(dips) and min(e[7] for e in dips) < 0.5)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
