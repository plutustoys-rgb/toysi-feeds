#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_catalog_rows.py — 14.10.2026 (власник: «чому не повне заповнення строки? і це після кожного "Показати ще"»): у каталозі порція карток (PER=24) не ділилась на число колонок сітки (5 на широкому екрані) →
неповний останній рядок після кожного натискання. Тепер, поки кнопка «Показати ще» видима, кількість карток кратна числу колонок; неповним може бути лише ПОСЛІДНІЙ рядок кінця списку. Chromium, 4 ширини."""
import functools
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
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


IMG = "data:image/gif;base64,R0lGODlhAQABAAAAACw="
N = 203
prods = [{"id": str(3000 + i), "name": f"Іграшка «Р{i}»", "category": f"Категорія{i % 3}", "price": 200 + i, "contribution": 100, "stock": 5, "photo": IMG, "desc": ""} for i in range(N)]
cats = {}
for p in prods:
    cats.setdefault(p["category"], []).append(p)
cl = sorted(cats, key=lambda c: -len(cats[c]))
cs = {c: f"c{i}" for i, c in enumerate(cl)}
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
bs.write_catalog("Каталог іграшок", prods, cl, cs, "catalog.html", active=None)
(out / "index.json").write_text(json.dumps(bs.index_entries(prods), ensure_ascii=False), encoding="utf-8")
(out / "index-t.json").write_text(json.dumps(bs.index_entries(prods), ensure_ascii=False), encoding="utf-8")
(out / "index-rt.json").write_text("[]", encoding="utf-8")
shutil.copytree(HERE / "site" / "assets", out / "assets")

H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"

from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1741, 1280, 900, 390):
        ctx = br.new_context(viewport={"width": w, "height": 900})
        pg = ctx.new_page()
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/stock.json*", lambda r: r.abort())
        pg.goto(f"{base}/catalog.html", wait_until="load")
        pg.wait_for_selector("#cat-js .card")
        cols = pg.evaluate("getComputedStyle(document.getElementById('cat-js')).gridTemplateColumns.trim().split(/\s+/).length")
        counts, bad = [], []
        for _ in range(40):
            n = pg.evaluate("document.querySelectorAll('#cat-js .card').length")
            more = pg.evaluate("!document.getElementById('cat-more-wrap').hidden")
            counts.append(n)
            if more and n % cols:
                bad.append(n)
            if not more:
                break
            pg.click("#cat-more")
        chk(f"{w}px ({cols} колонок): кожна порція кратна числу колонок, поки є «Показати ще» {counts[:6]}…; некратні: {bad}", not bad and counts[-1] == N and cols >= 1)
        chk(f"{w}px: кожне натискання додає картки (без зависання) і список доходить до кінця ({N})", all(b > a for a, b in zip(counts, counts[1:])) and counts[-1] == N)
        ctx.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
