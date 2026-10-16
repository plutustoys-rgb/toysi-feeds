#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_helovin.py — 15.10.2026 (власник: «це вкладка в Toysi /ua/halloween — чому у нас немає цих товарів?»). Виявлено: усі 52 товари ВЖЕ в нашому каталозі й в наявності, але не зібрані: чип «Хелловін» шукав «гелловін» (Toysi пише «Геловін»), не брав маски/грим/помаду
і відсікав ціни < 100 ₴. Тепер: (1) сторінка /helovin.html — id з вкладки Toysi (site/seasonal_halloween.json) + збіги за назвою (HELOVIN_RE), у вікні 1.10–2.11, лише в наявності/з фото/не уцінка/не зброя; без цінового порога; (2) чип головної ловить «Геловін»;
(3) біля чипа «Хелловін» посилання «Усі товари для Геловіна →» на /helovin.html (Chromium); (4) поза вікном сторінки нема і стара прибирається; (5) сторінка в sitemap."""
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


def item(pid, name, cat="Карнавальні костюми", price=300.0, stock=5):
    return {"id": str(pid), "name": name, "vendor": "T", "price": price, "stock": stock, "category_name": cat, "category_id": "10", "pictures": [f"https://toysi.ua/p/enl_{pid}.jpg"],
            "description": "Опис товару достатньої довжини для картки сайту, понад сорок символів.", "params": {}, "url": f"https://toysi.ua/{pid}"}


NAMES = ["Набір гриму для Геловіна", "Світильник у формі привида", "Декор гарбузовий для дому", "Маска вампіра карнавальна", "Костюм відьми дитячий", "Іграшка павук-скакунчик", "Скелет надувний для вечірки", "Плюшевий привид з очима"]
catalog = {}
for i in range(1, 13):                                  # 12 «курованих» (як у вкладці Toysi): без слова «геловін» у назві, дешеві
    catalog[str(i)] = item(i, f"Помада для гриму кольорова {i}", "Грим", price=46 + i)
catalog["40"] = item(40, "Світильник для Геловіна «Скелет» 13 см", price=120)         # збіг за назвою (Геловіна)
catalog["41"] = item(41, "Гарбуз декоративний великий", price=250)                      # збіг за назвою (гарбуз)
catalog["42"] = item(42, "Конструктор «Місто» 500 дет.", "Конструктори", price=400)     # не Геловін
catalog["43"] = item(43, "Маска карнавальна Вампір", price=90, stock=0)                 # немає в наявності
catalog["44"] = item(44, "Автомат іграшковий «Скелет»", "Автомати і пістолети", price=300)   # зброя → не показувати
catalog["46"] = item(46, "Рюкзак «Людина Павук» 33 см", "Рюкзаки", price=300)                # хибний збіг: Spider-Man
catalog["47"] = item(47, "Пазл «Привид Києва» 271 дет.", "Пазли", price=200)                  # хибний збіг: патріотичне
catalog["48"] = item(48, "Фігурка супергероя «Людина-павук: Додому шляху нема»", "Фігурки", price=250)
catalog["45"] = item(45, "Павук скакунчик на аркуші, 6 см", price=50)                    # збіг за назвою (павук), дешевий
for i in range(100, 112):                               # щоб чип головної мав ≥6 товарів: ціна ≥100 і внесок ≥30
    catalog[str(i)] = item(i, NAMES[i % 8], f"Святкові набори {i % 4}", price=240 + i)
bs.tp.fetch_toysi_catalog = lambda: dict(catalog)
bs.tp.assert_catalog_size_sane = lambda c, *a, **k: None
tmpj = Path(tempfile.mkdtemp()) / "seasonal_halloween.json"
tmpj.write_text(json.dumps({"ids": [str(i) for i in range(1, 13)] + ["43", "9999"]}), encoding="utf-8")   # 43 — без залишку, 9999 — нема в каталозі
bs.HELOVIN_JSON = str(tmpj)
out = Path(tempfile.mkdtemp())
bs.OUT = str(out)


def build(day):
    os.environ["SITE_PICK_DATE"] = day
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        bs.build()


build("2026-10-20")
hp = out / "helovin.html"
chk("у вікні 1.10–2.11 сторінка /helovin.html є", hp.exists())
h = hp.read_text(encoding="utf-8") if hp.exists() else ""
ids = json.loads(re.search(r'<script type="application/json" id="cat-ids">(.*?)</script>', h).group(1)) if 'id="cat-ids"' in h else []   # усі товари сторінки (панель сортування, власник 16.10)
chk(f"усі 12 «курованих» (ціни 47–58 ₴, нижче порога вітрини) на сторінці, у порядку вкладки Toysi: {ids[:5]}…", ids[:12] == [str(i) for i in range(1, 13)])
chk("збіги за назвою додані після куратора: світильник «Геловіна» (40), гарбуз (41), дешевий павук (45) і набори «Геловіна»", {"40", "41", "45", "100", "101"} <= set(ids) and ids.index("40") > 11)
chk("не потрапили: «Конструктор» (42), без залишку (43), зброя «Автомат… Скелет» (44), «Людина-павук»/«Привид Києва» (46–48: хибні збіги за назвою), id якого нема в каталозі", not ({"42", "43", "44", "46", "47", "48"} & set(ids)))
chk("сторінка: H1 «Геловін», крихти, title, canonical, список для аналітики data-list=helovin, посилання в каталог", '<h1 class="page cat-h1">Геловін</h1>' in h and 'id="cat-controls"' in h and 'class="crumbs"' in h and 'rel="canonical" href="https://plutustoys.com.ua/helovin.html"' in h and 'data-list="helovin"' in h and 'href="catalog.html"' in h)
sm = "\n".join(f.read_text(encoding="utf-8") for f in sorted(out.glob("sitemap-*.xml")))
chk("/helovin.html є в sitemap (сегмент categories)", "/helovin.html</loc>" in (out / "sitemap-categories.xml").read_text(encoding="utf-8"))
home = (out / "index.html").read_text(encoding="utf-8")
chk("головна: чип «Хелловін» — посилання на /helovin.html (власник 16.10: чип веде на окрему сторінку)",
    re.search(r'<a class="chip chip-theme" data-set="hel" href="helovin\.html">', home) is not None and 'data-all=' not in home)
# чип ловить «Геловін» (одна л): набори «Геловіна» мають потрапити в сет hel
chk("чип «Хелловін» бачить написання «Геловін» і «павук/грим/карнавальн» у регулярці", bool(re.search(bs.HOME_SEASONAL[0][3], "Набір для Геловіна", re.I)) and bool(re.search(bs.HOME_SEASONAL[0][3], "Грим для обличчя", re.I)) and bool(re.search(bs.HOME_SEASONAL[0][3], "Павук скакунчик", re.I)))

# ── поза вікном ──
build("2026-11-10")
chk("поза вікном (10.11) сторінки нема, стара прибрана, у sitemap її нема, чипа «Хелловін» на головній нема", not hp.exists() and "/helovin.html</loc>" not in (out / "sitemap-categories.xml").read_text(encoding="utf-8") and 'data-set="hel"' not in (out / "index.html").read_text(encoding="utf-8"))
# мало товарів → сторінки нема
tmpj.write_text(json.dumps({"ids": ["1", "2"]}), encoding="utf-8")
for k in [k for k in list(catalog) if k in {"40", "41", "45"} or k.startswith("10") and len(k) == 3 or k in {"43", "44"}]:
    catalog.pop(k, None)
build("2026-10-20")
chk("менше 6 товарів у вікні → сторінки нема (порожня сторінка не публікується)", not hp.exists())

# ── Chromium: посилання «Усі товари для Геловіна →» біля чипа ──
for i in range(100, 112):
    catalog[str(i)] = item(i, NAMES[i % 8], f"Святкові набори {i % 4}", price=240 + i)
for i in range(1, 13):
    catalog[str(i)] = item(i, f"Помада для гриму кольорова {i}", "Грим", price=46 + i)
tmpj.write_text(json.dumps({"ids": [str(i) for i in range(1, 13)]}), encoding="utf-8")
build("2026-10-20")
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
    pg = br.new_page(viewport={"width": 1280, "height": 800})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.route("**/googletagmanager.com/**", lambda r: r.abort())
    pg.route("https://toysi.ua/**", lambda r: r.abort())
    pg.goto(f"{base}/index.html", wait_until="load")
    pg.wait_for_selector('#home-chips [data-set="hel"]')
    a1 = pg.evaluate("(()=>{const a=document.querySelector('#home-chips a.chip[data-set=hel]'); return [a.tagName, a.getAttribute('href')]})()")
    chk(f"Chromium: чип «Хелловін» — посилання на helovin.html {a1}", a1 == ["A", "helovin.html"] and errs == [])
    pg.goto(f"{base}/helovin.html", wait_until="load")
    pg.wait_for_selector("#cat-js .card")
    chk("Chromium: сторінка /helovin.html відкривається, є картки, без помилок JS", pg.evaluate("document.querySelectorAll('#cat-js .card').length") >= 6 and errs == [])
    br.close()
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
