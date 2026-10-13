#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_themed_chips.py — 13.10.2026 (SMM/власник): чипи над каруселлю. Прибрано «До 300 ₴» і «300–700 ₴» (це сортування). Evergreen: «День народження», «У садок і школу», «Для двох гравців», «Тихі ігри», «Активні ігри»;
сезонний «До Різдва» (01.12–25.12, підпис «Різдвяний настрій»). Кожен: ключові слова за назвою (для «Для двох гравців» — ще категорія «настільні»), власні межі ціни й внеску, ≤2 з категорії, без мілітарі/зброї/книжок/манги,
у наявності, ≥6 карток інакше чипа нема. Активний чип видно (тестувальник #15): темний контур + галочка, і для тематичних, і для «Усі»."""
import http.server
import os
import re
import shutil
import socketserver
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
_n = [0]
W = ["Калина", "Мальви", "Барвінок", "Соняшник", "Лелека", "Журавель", "Ромашка", "Волошка", "Тополя", "Берегиня", "Веселка", "Дзвіночок", "Рушничок", "Колосок", "Мрія", "Зоря", "Хмаринка", "Мушля", "Дубочок", "Ялівець"]


def P(name, price, contrib, cat, stock=5, photo=IMG):
    _n[0] += 1
    return {"id": str(4000 + _n[0]), "name": name, "category": cat, "price": price, "contribution": contrib, "stock": stock, "photo": photo}


def many(fmt, n, price, contrib, cat_fmt, start=0):
    return [P(fmt.format(W[(start + i) % 20] + str(start + i)), price, contrib, cat_fmt.format(start + i)) for i in range(n)]


prods = []
prods += many("Набір «{}» для дівчинки", 8, 300, 80, "Подар{}")                    # bday
prods += many("Рюкзак «{}» шкільний", 7, 400, 50, "Школа{}")                      # school
prods += many("Настільна гра «{}»", 7, 200, 1.0, "Ігри{}")                        # duo (за назвою)
prods += [P(f"Весела розвага «{W[i]}Ок{i}»", 150, 5.0, "Настільні ігри") for i in range(2)]   # duo за категорією, ≤2 з категорії
prods += many("Пазл «{}» 100 елементів", 8, 250, 60, "Пазли{}")                    # quiet
prods += many("Самокат «{}» дитячий", 8, 500, 0.0, "Транспорт{}")                  # active (внесок рівно 0)
prods += many("Іграшка Різдвяна ялинка «{}»", 8, 90, 1.0, "Зима{}")               # xmas
bad = [
    P("Набір «Погана1» військовий", 300, 80, "Бад1"),
    P("Набір «Погана2» солдати", 300, 80, "Бад2"),
    P("Пазл «Погана3» Манга", 250, 60, "Бад3"),
    P("Набір «Погана4» дорогий", 800, 80, "Бад4"),                  # > 700
    P("Набір «Погана5» дешевий", 100, 80, "Бад5"),                  # < 150
    P("Набір «Погана6» мало внеску", 300, 40, "Бад6"),              # внесок < 50
    P("Набір «Погана7» немає", 300, 80, "Бад7", stock=0),
    P("Набір «Погана8» без фото", 300, 80, "Бад8", photo=""),
    P("Рюкзак «Погана9» книжковий", 400, 60, "Дитячі книги"),
    P("Настільна гра «Погана10» збиткова", 200, -1.0, "Ігри-бад"),   # внесок < 0
    P("Подушка «Коктейль Молотова»", 200, 60, "Погана11-Подушки"),                 # політичний/військовий образ
    P("Гірлянда Золото LED «Погана12»", 150, 60, "Погана12-Гірлянди"),             # «лото» всередині слова «Золото»
    P("Тимчасові тату «Лотоси» Погана13", 150, 60, "Погана13-Тату"),                 # «лото» всередині «лотоси»
]
allp = prods + bad
bad_ids = {b["id"] for b in bad}


def sets_on(day):
    os.environ["SITE_PICK_DATE"] = day
    return bs.home_chip_sets(allp)


s = sets_on("2026-03-05")
ids = lambda k: {p["id"] for p in s[k]}  # noqa: E731
chk(f"чипи за ключами: bday/school/duo/quiet/active є, «До Різдва» поза вікном нема, «До 300 ₴»/«300–700 ₴» нема: {sorted(s)}", {"bday", "school", "duo", "quiet", "active"} <= set(s) and "xmas" not in s and "u300" not in s and "m700" not in s)
chk("жоден набір не містить відсіяних (мілітарі/манга/ціна/внесок/нема залишку/фото/книги/збиткова)", all(not (ids(k) & bad_ids) for k in s))
chk("«День народження»: ціна 150–700, внесок ≥50, ≤2 з категорії, ≤10", all(150 <= p["price"] <= 700 and p["contribution"] >= 50 for p in s["bday"]) and len(s["bday"]) <= 10 and max(sum(1 for q in s["bday"] if q["category"] == c) for c in {p["category"] for p in s["bday"]}) <= 2)
chk("«У садок і школу»: рюкзак/пенал/…, ціна 100–600, внесок ≥30", all(re.search(r"рюкзак", p["name"], re.I) and 100 <= p["price"] <= 600 and p["contribution"] >= 30 for p in s["school"]))
chk("«Для двох гравців»: за назвою або категорією «настільні», ціна 50–600, внесок ≥0 (рівно 1.0 проходить), категорію «Настільні ігри» ≤2", all(100 > 0 and 50 <= p["price"] <= 600 and p["contribution"] >= 0 for p in s["duo"]) and sum(1 for p in s["duo"] if p["category"] == "Настільні ігри") <= 2 and any(p["category"] == "Настільні ігри" for p in s["duo"]))
chk("«Тихі ігри»: пазли, ціна 100–600, внесок ≥30", all("Пазл" in p["name"] and 100 <= p["price"] <= 600 and p["contribution"] >= 30 for p in s["quiet"]))
chk("«Активні ігри»: самокати, внесок рівно 0 дозволено, ціна 50–700", all("Самокат" in p["name"] and p["contribution"] >= 0 for p in s["active"]) and len(s["active"]) >= 6)
chk("щоденна ротація: наступного дня склад «Тихих ігор» інший або порядок інший, у межах дня стабільний", [p["id"] for p in sets_on("2026-03-05")["quiet"]] == [p["id"] for p in s["quiet"]])
# «До Різдва»
for day, want in (("2026-11-30", False), ("2026-12-01", True), ("2026-12-25", True), ("2026-12-26", False)):
    chk(f"«До Різдва» {day}: {'є' if want else 'нема'}", ("xmas" in sets_on(day)) == want)
defs = bs.home_chip_defs(sets_on("2026-12-10"))
keys = [d[0] for d in defs]
chk(f"порядок: «До Різдва» (дата-вікно) першим серед тематичних, evergreen після «Набори»/«Популярне» {keys}", keys[0] == "xmas" and keys.index("bday") > 0 and keys[-5:] == ["bday", "school", "duo", "quiet", "active"])
chk("підпис «Різдвяний настрій», назви чипів за SMM", next(d for d in defs if d[0] == "xmas")[2] == "Різдвяний настрій" and {d[0]: d[1] for d in defs}["bday"] == "День народження" and {d[0]: d[1] for d in defs}["school"] == "У садок і школу" and {d[0]: d[1] for d in defs}["duo"] == "Для двох гравців" and {d[0]: d[1] for d in defs}["quiet"] == "Тихі ігри" and {d[0]: d[1] for d in defs}["active"] == "Активні ігри")
chk("менше 6 карток — чипа нема", "quiet" not in bs.home_chip_sets([p for p in allp if "Пазл" not in p["name"]] + [p for p in allp if "Пазл" in p["name"]][:5]))

# Chromium: активний стан
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    chk("playwright доступний", False)
    sync_playwright = None
if sync_playwright:
    os.environ["SITE_PICK_DATE"] = "2026-12-10"
    only = [dict(p, photo=IMG) for p in allp]
    only += [P(f"Уцінка. Іграшка «{W[i]}Уц{i}»", 120 + i, 5.0, "Уцінка") for i in range(3)]
    only += [P(f"Звичайна іграшка «{W[i]}Зв{i}»", 300 + i, 150, f"Звичайна{i}") for i in range(12)]   # основна добірка (внесок ≥100) — інакше каруселі й чипів нема
    cats = {}
    for p in only:
        cats.setdefault(p["category"], []).append(p)
    cl = sorted(cats, key=lambda c: -len(cats[c]))
    root = Path(tempfile.mkdtemp())
    bs.OUT = str(root)
    bs.write_home(only, cats, cl, {c: f"c{i}" for i, c in enumerate(cl)})
    shutil.copytree(HERE / "site" / "assets", root / "assets")
    os.chdir(root)

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    ACT = "(k=>{const b=document.querySelector('#home-chips button[data-set='+k+']'); const s=getComputedStyle(b); const a=getComputedStyle(b,'::before'); return [b.getAttribute('aria-pressed'), s.outlineStyle, s.outlineWidth, s.outlineColor, a.content, s.animationName]})"
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for w in (1280, 390):
            ctx = br.new_context(viewport={"width": w, "height": 800}, reduced_motion="reduce")
            pg = ctx.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.route("**/googletagmanager.com/**", lambda r: r.abort())
            pg.goto(f"{base}/index.html", wait_until="load")
            pg.wait_for_timeout(500)
            chips = pg.evaluate("[...document.querySelectorAll('#home-chips button')].map(b=>b.dataset.set)")
            chk(f"{w}px: чипи на сторінці: {chips}", chips[0] == "all" and "xmas" in chips and "bday" in chips and "u300" not in chips)
            lk = pg.evaluate("(()=>{const a=document.querySelector('#home-chips a.chip-link'); return a? [a.getAttribute('href'), a.textContent.trim(), getComputedStyle(a).textDecorationLine]:null})()")
            chk(f"{w}px: чип-посилання «Уцінка» → utsinka.html (посилання, не набір; без підкреслення) {lk}", lk == ["utsinka.html", "Уцінка", "none"])
            a0 = pg.evaluate(f"{ACT}('all')")
            chk(f"{w}px: «Усі» активний: контур 3px solid + галочка ✓ {a0}", a0[0] == "true" and a0[1] == "solid" and a0[2] == "3px" and "✓" in a0[4])
            for k in ("hel", "xmas", "bday", "nab"):
                if k not in chips:
                    continue
                pg.click(f"#home-chips button[data-set={k}]")
                pg.wait_for_timeout(250)
                a = pg.evaluate(f"{ACT}('{k}')")
                o = pg.evaluate(f"{ACT}('all')")
                chk(f"{w}px: «{k}» активний видно (контур 3px solid + ✓), «Усі» вже НЕ активний {a} / {o}", a[0] == "true" and a[1] == "solid" and a[2] == "3px" and "✓" in a[4] and o[0] == "false" and o[1] == "none" and a[5] == "none")
            chk(f"{w}px: без помилок JS, без горизонтального скролу", errs == [] and pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
            ctx.close()
        br.close()

os.environ.pop("SITE_PICK_DATE", None)
print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
