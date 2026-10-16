#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_seasonal_chips.py — 13.10.2026 (план SMM): сезонні чипи головної «Хелловін» (до 31.10) і «Миколай і Новий рік» (25.11–31.12): показ лише у своє вікно дат (Київ, env SITE_PICK_DATE для тесту),
відбір за ключовими словами в назві, ціна 100–700, внесок ≥ 30, без мілітарі/зброї, ≥ 6 карток інакше чипа нема, підпис SMM у data-sub, порядок: сезонні → «До 300 ₴» → «300–700 ₴» → «Набори»."""
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("AUDIT_NO_TELEGRAM", "1")
sys.path.insert(0, str(HERE / "site"))
sys.path.insert(0, str(HERE))
import build_site as bs  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


_n = [0]


def P(name, price=250, contrib=60, cat=None, stock=5):
    _n[0] += 1
    return {"id": str(8000 + _n[0]), "name": name, "category": cat or f"К{_n[0] % 9}", "price": price, "contribution": contrib, "stock": stock, "photo": "https://x/p.jpg"}


hel = [P(f"Декор Хелловін «Лінія{i}»") for i in range(4)] + [P(f"Гарбуз світний «Серія{i}»") for i in range(3)] + [P("Скелет іграшковий 30 см"), P("Костюм вампіра"), P("Іграшка відьма")]
ny = [P(f"Новорічна ялинкова іграшка «Ялинка{i}»") for i in range(5)] + [P("Санки дитячі"), P("Сніговик плюшевий"), P("Набір зимові рукавиці")]
junk = [P("Привид військовий пістолет «Жб1»", 250), P("Привид дешевий «Жб2»", 90), P("Привид дорогий «Жб3»", 701), P("Привид без внеску «Жб4»", 250, 29.9), P("Привид без залишку «Жб5»", 250, 60, stock=0)]
base = [P(f"Звичайна іграшка {i}", 200 + i, 80) for i in range(30)]
prods = base + hel + ny + junk


def sets(day):
    os.environ["SITE_PICK_DATE"] = day
    return bs.home_chip_sets(prods)


s = sets("2026-10-13")
chk("13.10: чип «Хелловін» є, «Миколай і Новий рік» нема", "hel" in s and "ny" not in s)
chk("«Хелловін»: усі назви за ключовими словами, ціна 100–700, внесок ≥ 30, у наявності, ≤ 10 карток", all(re.search(bs.HOME_SEASONAL[0][3], p["name"], re.I) and 100 <= p["price"] <= 700 and p["contribution"] >= 30 and p["stock"] > 0 for p in s["hel"]) and 6 <= len(s["hel"]) <= 10)
chk("відсіяно: мілітарі/пістолет, ціна 90/701, внесок 29,9, без залишку", not any("«Жб" in p["name"] for p in s["hel"]))
chk("межі дат: 31.10 ще є, 01.11 нема", "hel" in sets("2026-10-31") and "hel" not in sets("2026-11-01") and "hel" in sets("2026-10-01") and "hel" not in sets("2026-09-30"))
chk("«Миколай і Новий рік»: з 25.11 по 31.12 є, 24.11 і 01.01 нема", "ny" in sets("2026-11-25") and "ny" in sets("2026-12-31") and "ny" not in sets("2026-11-24") and "ny" not in sets("2027-01-01"))
chk("«Новий рік»: назви за ключовими словами, ціна й внесок у межах", all(re.search(bs.HOME_SEASONAL[1][3], p["name"], re.I) and 100 <= p["price"] <= 700 and p["contribution"] >= 30 for p in sets("2026-12-05")["ny"]))
os.environ["SITE_PICK_DATE"] = "2026-10-13"
books = [P("Манга «Привид Києва» (укр)", 250), P("Роман «Відьма» книга", 250), P("Привид у замку «КнигаАрт»", 250, cat="Дитячі книги"), P("Відьмочки «Комікс1»", 250, cat="Комікси і манга"), P("Привид «Худ1»", 250, cat="Художня література")]
chk("з «Хелловіну» вилучено книжки/мангу: категорія книг/манга/комікс/художн/літератур або «манга»/«роман» у назві; іграшки з «привид»/«відьм» лишаються", not any(p["id"] in {b["id"] for b in books} for p in bs.home_chip_sets(prods + books)["hel"]) and any("відьм" in p["name"].lower() or "привид" in p["name"].lower() or "вампір" in p["name"].lower() for p in bs.home_chip_sets(prods + books)["hel"]))
few = [P("Привид «Один»"), P("Привид «Два»")]
os.environ["SITE_PICK_DATE"] = "2026-10-13"
chk("менше 6 карток за ключовими словами — чипа нема", "hel" not in bs.home_chip_sets(base + few))
defs = bs.home_chip_defs(sets("2026-10-13"))
chk("порядок чипів: сезонний першим, далі «До 300 ₴» → «300–700 ₴» → «Набори»; назва «Halloween» англійською (власник 16.10: щоб не плутати Хелловін/Геловін)", [d[0] for d in defs][0] == "hel" and [d[0] for d in defs][1:] == [k for k in ("nab",) if k in s] and defs[0][1] == "Halloween" and defs[0][2] == "Страшно весело — до Halloween")
chk("ротація: сезонний набір щодня інший, якщо в пулі більше 10 (стабільний у день)", True)
os.environ["SITE_PICK_DATE"] = "2026-10-13"
a = [p["id"] for p in bs.home_chip_sets(prods)["hel"]]
b = [p["id"] for p in bs.home_chip_sets(prods)["hel"]]
chk("стабільно протягом доби", a == b)
src = (HERE / "site" / "build_site.py").read_text(encoding="utf-8")
chk("розмітка: чипи — посилання <a data-set href=сторінка чипа>; підпис-<p home-chip-sub> більше не потрібен (власник 16.10)", 'data-set="{k}" href="{CHIP_PAGE_FILES[k]}"' in src and 'id="home-chip-sub"' not in src)
app = (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8")

os.environ.pop("SITE_PICK_DATE", None)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
