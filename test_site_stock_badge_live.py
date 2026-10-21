#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_stock_badge_live.py — 21.10.2026 (тестувальник 24): бейдж «Залишилось N шт» вшитий у статичну сторінку (збірка раз на 2 год), stock.json свіжіший (раз на 15 хв), а app.js переписував лише випадок «нуль».
Застаріле ненульове число = фальшива терміновість. Тепер за СВІЖИМ знімком: те саме число — без змін; інше число 1–3 — переписується; залишок більше межі бейджа (3) — бейдж знімається й повертається «У наявності»;
нуль — «Немає», бейдж знято (як було); id нема в знімку / знімок старий / stock.json недоступний — НЕ чіпаємо. Реальний Chromium."""
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

chk("межа бейджа в app.js (liveBadge: live>3) = STOCK_BADGE_MAX у build_site.py", bs.STOCK_BADGE_MAX == 3 and "if(live>3){" in (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8"))


def prod(i, stock):
    return {"id": str(i), "name": f"Іграшка {i}", "price": 200 + i, "photo": "", "stock": stock, "category": "Іграшки"}


# в збірці (вшито): 1→«2 шт», 2→«1 шт», 3→«3 шт», 4→«2 шт», 5→«2 шт», 6→«2 шт»
BUILT = {1: 2, 2: 1, 3: 3, 4: 2, 5: 2, 6: 2}
out = Path(tempfile.mkdtemp())
(out / "index.html").write_text('<!doctype html><html lang="uk"><head><meta charset="utf-8"></head><body>' + bs.grid([prod(i, s) for i, s in BUILT.items()], badge=True)
                                + '<script src="assets/app.js"></script></body></html>', encoding="utf-8")
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

    # свіжий знімок: 1 — те саме (2), 2 — було 1, тепер 3 (переписати), 3 — було 3, тепер 7 (> межі: бейдж знято), 4 — 0 (Немає), 5 — id нема в знімку (не чіпати), 6 — тепер 1 (переписати вниз)
    rows, errs = load({"1": 2, "2": 3, "3": 7, "4": 0, "6": 1})
    chk(f"фіксовано: те саме число лишається («{rows['1']['badge']}»)", rows["1"]["badge"] == "Залишилось 2 шт")
    chk(f"було «1 шт», свіжий залишок 3 → «{rows['2']['badge']}»", rows["2"]["badge"] == "Залишилось 3 шт")
    chk(f"було «3 шт», свіжий залишок 7 (> межі) → бейдж знято, повернуто «У наявності» ({rows['3']['badge']}, {rows['3']['av']})", rows["3"]["badge"] is None and rows["3"]["av"] == "У наявності" and rows["3"]["avOos"] is False)
    chk(f"свіжий нуль → «Немає», бейдж знято ({rows['4']['badge']}, {rows['4']['av']})", rows["4"]["badge"] is None and rows["4"]["av"] == "Немає" and rows["4"]["avOos"] is True)
    chk(f"id нема в знімку («невідомо») → бейдж не чіпаємо ({rows['5']['badge']})", rows["5"]["badge"] == "Залишилось 2 шт")
    chk(f"було «2 шт», свіжий залишок 1 → «{rows['6']['badge']}» (число лише за свіжими даними, у будь-який бік)", rows["6"]["badge"] == "Залишилось 1 шт")
    chk("без помилок JS", errs == [])
    rows, errs = load({"1": 9, "2": 3, "3": 7, "4": 0, "6": 1}, age_min=120)
    chk("знімок старший за 45 хв («невідомо») — жоден бейдж не змінено, ніщо не знято", all(rows[str(i)]["badge"] == f"Залишилось {s} шт" for i, s in BUILT.items()) and errs == [])
    rows, errs = load({}, status=404)
    chk("stock.json недоступний — бейджі як вшито, без помилок", all(rows[str(i)]["badge"] == f"Залишилось {s} шт" for i, s in BUILT.items()) and errs == [])
    br.close()

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
