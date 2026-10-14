#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_home_policy.py — 13.10.2026, ретро-аудит головної: (А) ЄДИНА політика «без мілітарі/зброї/книжок» для всіх вітрин (home_pick «Усі» і чипів, «Популярне зараз», /utsinka, /aktsii);
(Б) точніші regex чипів («Для двох гравців» без «Мафії», «До Різдва» без категорії «Прикраси»/«Зірковий патруль», «Активні ігри» без «Обруч для волосся», варіанти апострофа в «мʼяч»);
(Д) час: Київ із зимовим часом (правило ЄС без tzdata), сміттєва SITE_PICK_DATE не валить збірку."""
import os
import re
import sys
from datetime import datetime, timezone
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


def P(name, cat="Кат", price=300, contrib=120, stock=5):
    _n[0] += 1
    return {"id": str(3000 + _n[0]), "name": name, "category": cat, "price": price, "contribution": contrib, "stock": stock, "photo": IMG}


# ── А. єдиний фільтр ──
bad = [P("Фігурки «Піхота»", "Солдатики"), P("Танк Т-34", "Військова техніка"), P("Набір «Танк» з екіпажем", "Набори"), P("Літак-винищувач F-22"), P("Набір «Снайпер» гвинтівка з м'якими кулями"),
       P("Набір Ковбой револьвер і м'які кулі"), P("Уцінка. Набір військового з пістолетом"), P("Лук зі стрілами", "Арбалети і луки"), P("Гра «ППО на захисті країни»"), P("Пазл «Котики ЗСУ»"), P("Кулемет іграшковий")]
good_names = ["Гіппопотам плюшевий", "Танкер-вантажівка іграшкова", "Ракетка для тенісу", "Конструктор «Місто»", "Лялька «Принцеса»", "Паззл «Ферма»"]
good = [P(n, f"Г{i}") for i, n in enumerate(good_names)]
chk("is_home_excluded: усі мілітарі/зброя з аудиту відсіюються (піхота/танк/снайпер/револьвер/ППО/ЗСУ/кулемет/лук)", all(bs.is_home_excluded(p) for p in bad))
chk("is_home_excluded: безпечні («гіппопотам», «танкер», «ракетка») НЕ відсіюються", not any(bs.is_home_excluded(p) for p in good))
os.environ["SITE_PICK_DATE"] = "2026-10-13"
many = [P(f"Конструктор «{w}»", f"Зв{i}", 300 + i) for i, w in enumerate(["Калина", "Мальви", "Барвінок", "Соняшник", "Лелека", "Журавель", "Ромашка", "Волошка", "Тополя", "Берегиня", "Веселка", "Дзвіночок"])]
pool = bad + good + many
picked = {p["id"] for p in bs.home_pick(pool)}
chk("home_pick «Усі» не містить мілітарі/зброї (раніше проходили «Танк Т-34», «Солдатики»)", not (picked & {b["id"] for b in bad}))
# demand_pick: «погані» на ПЕРШИХ позиціях списку попиту (детерміновано), книжки, косметика, дубль основи назви
books = [P("Книжка «Казки»", "Книжки", 200), P("Книжка «Пригоди»", "Дитячі книжки", 200)]
cosm = [P("Скраб для ніг «Green Pharmacy»", "Догляд за тілом", 160)]
dups = [P("Пазл Trefl «Ферма» 500", "Пазли А", 400), P("Пазл Trefl «Ферма» 1000", "Пазли Б", 450)]
ids = [x["id"] for x in bad + books + cosm] + [x["id"] for x in dups] + [x["id"] for x in many]
dp = bs.demand_pick(bad + books + cosm + dups + many, {"ids": ids, "categories": [], "date": datetime(2026, 10, 13).date(), "source": "t"})
dpi = {p["id"] for p in dp}
chk("demand_pick: «погані» на перших позиціях списку (мілітарі, книжки за назвою категорії «Дитячі книжки», косметика) НЕ потрапляють", not (dpi & {x["id"] for x in bad + books + cosm}))
chk("demand_pick: одна «основа назви» — одна картка (два «Trefl Ферма» → один)", sum(1 for p in dp if "Trefl" in p["name"]) <= 1)
ut = {p["id"] for p in bs.utsinka_pick([dict(p, name="Уцінка. " + p["name"]) for p in bad + good])}
chk("utsinka_pick/aktsii: мілітарі в уцінці відсіюються, безпечні лишаються", len(ut) == len(good))

# водні бластери (SMM 13.10): водні іграшки, не зброя; відсіяні скрізь, КРІМ чипа «Активні ігри»
W = ["Калина", "Мальви", "Барвінок", "Соняшник", "Лелека", "Журавель", "Ромашка", "Волошка"]
blasters = [P(f"Водний бластер «{w}»", f"Вода{i}", 250, 40) for i, w in enumerate(W)]
water_auto = P("Водний автомат «Потік»", "Автомати і пістолети", 250, 40)
water_mil = P("Водний бластер військовий «Дельта»", "Вода9", 250, 40)
plain_auto = P("Автомат іграшковий звуковий", "Вода10", 250, 40)
os.environ["SITE_PICK_DATE"] = "2026-03-05"
sets = bs.home_chip_sets(blasters + [water_auto, water_mil, plain_auto])
act_ids = {p["id"] for p in sets.get("active", [])}
chk(f"«Активні ігри»: водні бластери ПОКАЗУЮТЬСЯ ({len(act_ids)} з 8), військовий водний бластер і «Автомат іграшковий» — ні", len(act_ids) >= 6 and not (act_ids & {water_mil["id"], plain_auto["id"], water_auto["id"]}) and act_ids <= {b["id"] for b in blasters})
wa = dict(water_auto, contribution=150)
sets2 = bs.home_chip_sets(blasters + [wa])
chk("«Водний автомат» (слово «автомат» + категорія «Автомати і пістолети») ВІДСІЯНИЙ в «Усі» і в усіх чипах, крім «Активні ігри»", wa["id"] not in {p["id"] for p in bs.home_pick([wa] + many)} and all(wa["id"] not in {p["id"] for p in v} for k, v in sets2.items() if k != "active"))
chk("«Водний бластер» без слів зброї фільтром не чіпається (не зброя), військовий водний бластер відсіяний завжди", not bs.is_home_excluded(blasters[0]) and bs.is_home_excluded(water_mil) and bs.is_home_excluded(water_mil, water_ok=True))
chk("water_ok дозволяє «Водний автомат» (категорія «Автомати і пістолети»), але не «Автомат іграшковий» без «водн»", not bs.is_home_excluded(water_auto, water_ok=True) and bs.is_home_excluded(plain_auto, water_ok=True) and bs.is_home_excluded(water_mil, water_ok=True) and bs.is_home_excluded(water_auto))
os.environ.pop("SITE_PICK_DATE", None)

# ── Б. regex чипів ──
th = {k: re.compile(rx, re.I) for k, _l, _s, rx, _f, _la, _p1, _p2, _mc, _ac in bs.HOME_THEMED}
chk("«Для двох гравців»: «Настільна гра», «Шахи», «Доміно» — так; «Мафія», «Золото», «лотос» — ні", all(th["duo"].search(x) for x in ("Настільна гра «Вежа»", "Шахи дерев'яні", "Доміно «Ферма»", "Лото")) and not any(th["duo"].search(x) for x in ("Гра «Мафія»", "Гірлянда Золото", "Тату Лотоси")))
chk("«До Різдва»: ялинка/янгол/гірлянда/«Зірка новорічна» — так; «Набір прикрас для дівчинки», «Зірковий патруль» — ні; «Ялинкова прикраса» — так", all(th["xmas"].search(x) for x in ("Ялинка штучна", "Янгол", "Гірлянда LED", "Зірка новорічна", "Ялинкова прикраса", "Новорічні прикраси")) and not any(th["xmas"].search(x) for x in ("Набір прикрас для дівчинки", "Зірковий патруль")))
chk("«Активні ігри»: «Обруч гімнастичний», «Обручі», мʼяч з будь-яким апострофом (ʼ ' ’ ‘ ´ ` або без) — так; «Обруч для волосся» — ні", all(th["active"].search(x) for x in ("Обруч гімнастичний", "Обручі", "Мʼяч", "М'яч", "М’яч", "М‘яч", "М´яч", "М`яч", "Мяч")) and not th["active"].search("Обруч для волосся"))
chk("«День народження»: «Мʼяка іграшка» з усіма апострофами", all(th["bday"].search(x) for x in ("Мʼяка іграшка", "М'яка іграшка", "М’яка іграшка", "Мяка іграшка")))
chk("колекційні картки (Pokémon) і романи не потрапляють у тематичні чипи (HOME_SEASONAL_BAD_NAME), книжки за категорією «книж…» — теж", bs.HOME_SEASONAL_BAD_NAME.search("Картки Pokémon колекційні") and bs.HOME_SEASONAL_BAD_NAME.search("Картки покемон") and bs.HOME_SEASONAL_BAD_CAT.search("Дитячі книжки") and not bs.HOME_SEASONAL_BAD_NAME.search("Гіппопотам"))

# ── Д. час ──
chk("Київ без tzdata: літо UTC+3, зима UTC+2; межі 29.03 01:00 UTC і 25.10 01:00 UTC", [bs._eu_dst_hours(datetime(*d, tzinfo=timezone.utc)) for d in ((2026, 10, 24, 22, 30), (2026, 10, 25, 1, 0), (2026, 3, 29, 0, 59), (2026, 3, 29, 1, 0), (2026, 7, 1, 12, 0), (2026, 12, 31, 22, 30))] == [3, 2, 2, 3, 3, 2])
ok = True
for raw in ("2026-02-30", "2026-13-01", "0000-01-01", "не-дата", ""):
    os.environ["SITE_PICK_DATE"] = raw
    try:
        d = bs._today()
        ok = ok and d.year >= 2026
    except Exception:  # noqa: BLE001
        ok = False
chk("сміттєва/неіснуюча SITE_PICK_DATE не валить збірку (береться сьогодні)", ok)
os.environ["SITE_PICK_DATE"] = "2026-03-05"
chk("валідна SITE_PICK_DATE працює, _pick_day = ordinal дати", bs._today().isoformat() == "2026-03-05" and bs._pick_day() == datetime(2026, 3, 5).toordinal())
os.environ.pop("SITE_PICK_DATE", None)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
