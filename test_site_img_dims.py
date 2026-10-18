#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_img_dims.py — 18.10.2026 (SEO-аудит п.3): усі <img> у статичному HTML мають width/height (≥95%; було 4 з 15 на картці, 5 з 25 на головній); атрибути лише резервують пропорцію — кадр лишається квадратним
(Chromium: головне фото картки й фото в списках — квадрат на 390 і 1280 px, без спотворення від height=800/400, і без зсуву макета при завантаженні)."""
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
os.environ["SITE_PICK_DATE"] = "2026-10-18"
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


site_photo_probe.run = lambda idx: None


def item(pid, name, cat):
    return {"id": str(pid), "name": name, "vendor": "T", "price": 250 + pid % 90, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": [], "url": f"https://toysi.ua/{pid}", "barcode": ""}


catalog = {str(i): item(i, f"Іграшка номер {i}", f"Кат{i % 4}") for i in range(1, 41)}
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
bs.CATEGORY_GROUPS_FILE = str(Path(tempfile.mkdtemp()) / "none.json")
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()
tot = withd = 0
bad = []
for f in ("index.html", "catalog.html", "product-5.html", "product-6.html", "cart.html", "contacts.html"):
    h = (out / f).read_text(encoding="utf-8")
    for tag in re.findall(r"<img\b[^>]*>", h):
        tot += 1
        if re.search(r"\bwidth=", tag) and re.search(r"\bheight=", tag):
            withd += 1
        else:
            bad.append((f, tag[:90]))
chk(f"≥ 95% статичних <img> мають width і height: {withd}/{tot} (без: {bad[:3]})", tot > 0 and withd / tot >= 0.95)
p = (out / "product-5.html").read_text(encoding="utf-8")
chk("головне фото картки: width=800 height=800 + fetchpriority=high (LCP) лишилось", 'width="800" height="800" fetchpriority="high"' in p)
_prods = [{"id": str(9000 + i), "name": f"Карусельна іграшка {i}", "category": f"К{i % 6}", "price": 300 + i, "old_price": None, "contribution": 150.0, "stock": 5, "photo": f"https://toysi.ua/p/c{i}.jpg", "desc": "", "vendor": "", "barcode": ""} for i in range(14)]
_cats = {}
for _p in _prods:
    _cats.setdefault(_p["category"], []).append(_p)
_cl = sorted(_cats)
bs.write_home(_prods, _cats, _cl, {c: bs.slugify(c) for c in _cats})
hm = (out / "index.html").read_text(encoding="utf-8")
first_card = re.search(r'<a class="card slide"[^>]*><div class="ph"><img src="([^"]+)"', hm)
chk("головна: <link rel=preload as=image fetchpriority=high> на перше фото каруселі (LCP, SEO-аудит п.6)", first_card is not None and f'<link rel="preload" as="image" href="{first_card.group(1)}" fetchpriority="high">' in hm)
pre = re.search(r'<link rel="preload" as="image" href="([^"]+)" fetchpriority="high">', p)
chk("картка: preload головного фото (LCP) збігається з src зображення", pre is not None and f'<img src="{pre.group(1)}"' in p)
css = (HERE / "site/assets/styles.css").read_text(encoding="utf-8")
chk("CSS: .photo img має height:auto (атрибут height не перебиває квадрат aspect-ratio)", ".photo img{width:100%;height:auto;aspect-ratio:1/1" in css)

shutil.copytree(HERE / "site" / "assets", out / "assets", dirs_exist_ok=True)
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None


class _Srv(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


srv = _Srv(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1280, 390):
        pg = br.new_page(viewport={"width": w, "height": 800})
        pg.route("https://toysi.ua/**", lambda r: r.abort())      # фото не вантажаться: перевіряємо ЗАРЕЗЕРВОВАНЕ місце
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.goto(f"{base}/product-5.html", wait_until="load")
        r = pg.evaluate("(()=>{const i=document.querySelector('.photo img'); const b=i.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)]})()")
        chk(f"{w}px: головне фото картки — квадратний кадр зарезервовано до завантаження ({r})", abs(r[0] - r[1]) <= 2 and r[0] > 100)
        pg.goto(f"{base}/catalog.html", wait_until="load")
        pg.wait_for_selector("#cat-js .card, #cat-static .card")
        c = pg.evaluate("(()=>{const i=document.querySelector('.card .ph img'); const b=i.getBoundingClientRect(); const p=i.parentElement.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height), Math.round(p.width), Math.round(p.height)]})()")
        chk(f"{w}px: картка в списку — фото заповнює квадратний .ph ({c})", abs(c[0] - c[2]) <= 2 and abs(c[1] - c[3]) <= 2 and abs(c[2] - c[3]) <= 2)
        pg.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
