#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_stock_badge_live.py — 21.10.2026 (тестувальник 24; SMM 21.10: поріг 1 і текст «Остання штука»): бейдж вшитий у статичну сторінку (збірка раз на 2 год), stock.json свіжіший (раз на 15 хв).
Застарілий бейдж = фальшива терміновість. За СВІЖИМ знімком (≤45 хв): залишок 1 — «Остання штука» (і стару форму «Залишилось N шт» із попередньої збірки приводимо до неї); залишок більше 1 —
бейдж знімається й повертається «У наявності»; нуль — «Немає», бейдж знято (.av створюється, якщо її не було); id нема в знімку / знімок старий / stock.json недоступний — НЕ чіпаємо. Реальний Chromium."""
import http.server
import json
import os
import shutil
import socketserver
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
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


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    chk("playwright доступний", False)
    sys.exit(1)

chk("межа бейджа в app.js (liveBadge: live>1) = STOCK_BADGE_MAX у build_site.py", bs.STOCK_BADGE_MAX == 1 and "if(live>1){" in (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8"))


def prod(i, stock):
    return {"id": str(i), "name": f"Іграшка {i}", "price": 200 + i, "photo": "", "stock": stock, "category": "Іграшки"}


LAST = "Остання штука"
# вшито при збірці: 1–4 — «Остання штука» (залишок 1); 5, 6 — СТАРА форма попередньої збірки («Залишилось N шт»), її теж мусить привести до правди свіжий знімок
BUILT = {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1}
LEGACY = {5: "Залишилось 2 шт", 6: "Залишилось 3 шт"}
grid = bs.grid([prod(i, s) for i, s in BUILT.items()], badge=True)
parts = grid.split('<span class="stock-badge">' + LAST + '</span>')
assert len(parts) == len(BUILT) + 1
rebuilt = parts[0]
for k, i in enumerate(BUILT, start=1):
    rebuilt += f'<span class="stock-badge">{LEGACY.get(i, LAST)}</span>' + parts[k]
out = Path(tempfile.mkdtemp())
(out / "index.html").write_text('<!doctype html><html lang="uk"><head><meta charset="utf-8"></head><body>' + rebuilt + '<script src="assets/app.js"></script></body></html>', encoding="utf-8")
shutil.copytree(HERE / "site" / "assets", out / "assets")
os.chdir(out)


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"


def stock_route(snapshot, age_min=1, status=200):
    now = datetime.now(timezone.utc)

    def handler(route):
        if status != 200:
            route.fulfill(status=status, body="nope")
            return
        hdr = {"Date": format_datetime(now, usegmt=True), "Last-Modified": format_datetime(now - timedelta(minutes=age_min), usegmt=True), "Cache-Control": "no-cache"}
        route.fulfill(status=200, content_type="application/json", headers=hdr, body=json.dumps({"ts": "2026-10-21T06:00:00", "n": len(snapshot), "s": snapshot}))
    return handler


READ = """Array.from(document.querySelectorAll('a.card')).map(a=>({id:/product-(\\d+)/.exec(a.getAttribute('href'))[1], badge:(a.querySelector('.stock-badge')||{}).textContent||null,
  av:(a.querySelector('.av')||{}).textContent||null, avOos:!!a.querySelector('.av.oos')}))"""
INITIAL = {str(i): (LEGACY.get(i, LAST)) for i in BUILT}

with sync_playwright() as pw:
    br = pw.chromium.launch()

    def load(snapshot, age_min=1, status=200):
        ctx = br.new_context(viewport={"width": 1280, "height": 900})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("**/stock.json*", stock_route(snapshot, age_min, status))
        pg.goto(f"{BASE}/index.html", wait_until="load")
        pg.wait_for_timeout(700)
        rows = {r["id"]: r for r in pg.evaluate(READ)}
        ctx.close()
        return rows, errs

    # свіжий знімок: 1 — залишок 1 (без змін); 2 — тепер 3 (бейдж знято, «У наявності»); 3 — 0 («Немає»); 4 — id нема в знімку (не чіпати);
    # 5 — стара форма «Залишилось 2 шт», свіжий залишок 1 → «Остання штука»; 6 — стара форма «Залишилось 3 шт», свіжий залишок 2 → бейдж знято
    rows, errs = load({"1": 1, "2": 3, "3": 0, "5": 1, "6": 2})
    chk(f"вшито «Остання штука», свіжий залишок 1 — без змін («{rows['1']['badge']}»)", rows["1"]["badge"] == LAST)
    chk(f"вшито «Остання штука», свіжий залишок 3 → бейдж знято, повернуто «У наявності» ({rows['2']['badge']}, {rows['2']['av']})", rows["2"]["badge"] is None and rows["2"]["av"] == "У наявності" and rows["2"]["avOos"] is False)
    chk(f"свіжий нуль → «Немає», бейдж знято, .av створено ({rows['3']['badge']}, {rows['3']['av']})", rows["3"]["badge"] is None and rows["3"]["av"] == "Немає" and rows["3"]["avOos"] is True)
    chk(f"id нема в знімку («невідомо») → бейдж не чіпаємо ({rows['4']['badge']})", rows["4"]["badge"] == LAST)
    chk(f"стара форма «Залишилось 2 шт», свіжий залишок 1 → «{rows['5']['badge']}»", rows["5"]["badge"] == LAST)
    chk(f"стара форма «Залишилось 3 шт», свіжий залишок 2 (> межі) → бейдж знято, «У наявності» ({rows['6']['badge']}, {rows['6']['av']})", rows["6"]["badge"] is None and rows["6"]["av"] == "У наявності")
    chk("без помилок JS", errs == [])
    rows, errs = load({"1": 9, "2": 3, "3": 0, "5": 1, "6": 2}, age_min=120)
    chk("знімок старший за 45 хв («невідомо») — жоден бейдж не змінено, ніщо не знято", all(rows[k]["badge"] == v for k, v in INITIAL.items()) and errs == [])
    rows, errs = load({}, status=404)
    chk("stock.json недоступний — бейджі як вшито, без помилок", all(rows[k]["badge"] == v for k, v in INITIAL.items()) and errs == [])
    br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
