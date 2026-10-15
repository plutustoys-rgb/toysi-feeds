#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_group_compact.py — 15.10.2026 (власник: «таку щільну розбивку по підкатегоріям хто зробив? і навіщо? 1 товар виглядає на весь екран не красиво. Ущільніть підкатегорії»).
(1) У сайдбарі групи лише підкатегорії з ≥ GROUP_SUB_MIN товарів (і не більше GROUP_SUB_MAX), решта зведена в одну «Інше» (сума, фільтр на місці; сторінки дрібних категорій лишаються досяжними для краулера через приховані посилання; на телефоні в списку теж «Інше»).
(2) Сітка карток не розтягує одну-дві картки на весь екран (auto-fill, а не auto-fit): картка має ту саму ширину, що й у повному ряду. Chromium."""
import contextlib
import functools
import http.server
import io
import json
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


site_photo_probe.run = lambda idx: None


def item(pid, name, cat, price):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": 5, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/{pid}"}


SIZES = [("Великі А", 30), ("Великі Б", 22), ("Середня", 14), ("Мала", 9), ("Одинак", 1), ("Крихітка", 2)]
catalog, pid = {}, 1
for name, n in SIZES:
    for j in range(n):
        catalog[str(pid)] = item(pid, f"{name} товар {pid}", name, 200 + (pid * 7) % 300)
        pid += 1
for j in range(8):   # власна категорія кореня групи (теж іде в «Інше»)
    catalog[str(pid)] = item(pid, f"Корінь товар {pid}", "Група Один", 150 + j)
    pid += 1
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
gj = Path(tempfile.mkdtemp()) / "category_groups.json"
gj.write_text(json.dumps({"groups": [{"name": "Група Один", "subs": [n for n, _ in SIZES]}]}, ensure_ascii=False), encoding="utf-8")
bs.CATEGORY_GROUPS_FILE = str(gj)
bs.GROUP_SUB_MIN, bs.GROUP_SUB_MAX = 15, 12
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    bs.build()
g = (out / "group-hrupa-odyn.html").read_text(encoding="utf-8") if (out / "group-hrupa-odyn.html").exists() else (out / sorted(f.name for f in out.glob("group-*.html"))[0]).read_text(encoding="utf-8")
side = g.split('<aside class="sb"')[1].split("</aside>")[0]
items = re.findall(r'<a class="sb-i(?: all on)?" href="[^"]+" data-sub="([^"]*)"><span>([^<]+)</span><em>(\d+)</em>', side)
n_other = 14 + 9 + 1 + 2 + 8   # 34
chk(f"сайдбар групи: «Усі товари» (86) + лише підкатегорії ≥ 15 товарів (Великі А 30, Великі Б 22) + одна «Інше» ({n_other}: Середня, Мала, Одинак, Крихітка і корінь): {[(i[1], i[2]) for i in items]}",
    [(i[1], i[2]) for i in items] == [("Усі товари", "86"), ("Великі А", "30"), ("Великі Б", "22"), ("Інше", str(n_other))] and items[-1][0] == "__other")
hid = re.findall(r'<a href="(category-[^"]+\.html)">([^<]+)</a>', side.split('class="sb-hid"')[1])
chk(f"дрібні підкатегорії лишаються досяжними для краулера (приховані посилання на їхні сторінки): {[h[1] for h in hid]}", {h[1] for h in hid} == {"Середня", "Мала", "Одинак", "Крихітка", "Група Один"} and all((out / h[0]).exists() for h in hid))
chk("«Інше» передано в JS (data-others), приховані посилання не показуються (.sb-hid{display:none})", "data-others=" in g and ".sb-hid{display:none}" in (HERE / "site/assets/styles.css").read_text(encoding="utf-8"))

shutil.copytree(HERE / "site" / "assets", out / "assets", dirs_exist_ok=True)
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
H.log_message = lambda *a, **k: None


class _Srv(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass


srv = _Srv(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
gfile = sorted(f.name for f in out.glob("group-*.html"))[0]
from playwright.sync_api import sync_playwright  # noqa: E402

CNT = "document.getElementById('cat-count').textContent"
with sync_playwright() as pw:
    br = pw.chromium.launch()
    for w in (1400, 390):
        ctx = br.new_context(viewport={"width": w, "height": 800})
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("https://toysi.ua/**", lambda r: r.abort())
        pg.goto(f"{base}/{gfile}", wait_until="load")
        pg.wait_for_selector("#cat-js .card")
        pg.wait_for_timeout(300)
        if w >= 1000:
            pg.click('.sb-i[data-sub="__other"]')
            pg.wait_for_timeout(300)
            chk(f"{w}px: клік «Інше» фільтрує на місці всі дрібні підкатегорії разом ({pg.evaluate(CNT)}), активна лише вона", f"Знайдено: {n_other}" in pg.evaluate(CNT) and pg.evaluate("[...document.querySelectorAll('.sb-i.on')].map(a=>a.getAttribute('data-sub'))") == ["__other"]
                and pg.evaluate("[...document.querySelectorAll('#cat-js .card .nm')].every(e=>!/^(Великі А|Великі Б)/.test(e.textContent))"))
            pg.click('.sb-i[data-sub="Великі Б"]')
            pg.wait_for_timeout(300)
            chk(f"{w}px: клік «Великі Б» — лише вона ({pg.evaluate(CNT)})", "Знайдено: 22" in pg.evaluate(CNT))
        else:
            opts = pg.evaluate("[...document.querySelectorAll('#cf-cat option')].map(o=>o.textContent)")
            chk(f"{w}px: список підкатегорій на телефоні теж ущільнено: {opts}", opts == ["Усі підкатегорії", f"Великі А (30)", "Великі Б (22)", f"Інше ({n_other})"] or opts[-1] == f"Інше ({n_other})" and len(opts) == 4)
            pg.select_option("#cf-cat", "__other")
            pg.wait_for_timeout(300)
            chk(f"{w}px: вибір «Інше» зі списку: {pg.evaluate(CNT)}", f"Знайдено: {n_other}" in pg.evaluate(CNT))
        chk(f"{w}px: без помилок JS", errs == [])
        ctx.close()
    # ── (2) одна картка не розтягується на весь екран ──
    for w in (1400, 390):
        ctx = br.new_context(viewport={"width": w, "height": 800})
        pg = ctx.new_page()
        pg.route("**/googletagmanager.com/**", lambda r: r.abort())
        pg.route("https://toysi.ua/**", lambda r: r.abort())
        pg.goto(f"{base}/{gfile}", wait_until="load")
        pg.wait_for_selector("#cat-js .card")
        pg.select_option("#cf-cat", "Великі А") if w < 1000 else pg.click('.sb-i[data-sub="Великі А"]')
        pg.wait_for_timeout(300)
        full = pg.evaluate("document.querySelector('#cat-js .card').getBoundingClientRect().width")
        # лишаємо одну картку: ціна від/до, що пропускає один товар
        pg.evaluate("document.getElementById('cat-js').innerHTML = document.querySelector('#cat-js .card').outerHTML")
        one = pg.evaluate("document.querySelector('#cat-js .card').getBoundingClientRect().width")
        grid_w = pg.evaluate("document.getElementById('cat-js').getBoundingClientRect().width")
        chk(f"{w}px: єдина картка в сітці має ту саму ширину, що й у повному ряду ({one:.0f} px проти {full:.0f}; сітка {grid_w:.0f}), а не розтягується на весь екран", abs(one - full) <= 2 and one < grid_w * 0.6)
        ctx.close()
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
