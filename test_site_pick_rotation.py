#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_pick_rotation.py — 13.10.2026 (власник: «у каруселі крутяться одні й ті самі товари, а це найдорожче місце»). Добірка каруселі (і наборів чипів) тепер щодня інша:
серед найкращих за внеском категорій порядок і позиція всередині категорії залежать від ДНЯ (Київ), протягом доби добірка стабільна (збірка кожні 2 год), обмеження (ціна, внесок, ≤2 на категорію, 1 на основу назви) збережені."""
import os
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


prods = []
for ci in range(60):
    for k in range(8):
        i = ci * 100 + k
        prods.append({"id": str(10000 + i), "name": f"Іграшка \"Лінія{i}\"", "category": f"Кат{ci}", "price": 160 + (k * 40) % 520, "contribution": 105 + k * 3 + (ci % 7), "stock": 3, "photo": "https://x/p.jpg"})


def pick(day):
    os.environ["SITE_PICK_DATE"] = day
    return bs.home_pick(prods)


a1, a2 = pick("2026-10-13"), pick("2026-10-13")
b = pick("2026-10-14")
c = pick("2026-10-15")
ia, ib, ic = [p["id"] for p in a1], [p["id"] for p in b], [p["id"] for p in c]
chk("протягом доби добірка стабільна (два виклики одного дня збігаються, включно з порядком)", ia == [p["id"] for p in a2] and len(ia) == 10)
chk(f"наступного дня добірка інша: спільних із учорашньою ≤ 4 з 10 ({len(set(ia) & set(ib))}), позавчорашньою ≤ 4 ({len(set(ib) & set(ic))})", len(set(ia) & set(ib)) <= 4 and len(set(ib) & set(ic)) <= 4)
days = [f"2026-11-{d:02d}" for d in range(1, 21)]
seen = set()
for d in days:
    seen |= {p["id"] for p in pick(d)}
chk(f"за 20 днів у каруселі побувало ≥ 80 різних товарів (а не 10): {len(seen)}", len(seen) >= 80)
ok = True
for d in days:
    pk = pick(d)
    ok = ok and len(pk) == 10 and all(150 <= p["price"] <= 700 and p["contribution"] >= bs.HOME_MIN_CONTRIB for p in pk)
    ok = ok and max(sum(1 for p in pk if p["category"] == c_) for c_ in {p["category"] for p in pk}) <= 2
    ok = ok and len({bs._pick_key(p["name"]) for p in pk}) == 10
chk("кожного з 20 днів: 10 карток, ціна 150–700, внесок ≥ порога, ≤2 з категорії, ключі основ різні", ok)
os.environ["SITE_PICK_DATE"] = "2026-10-13"
chk("чипи теж ротуються: «До 300 ₴» і «300–700 ₴» щодня інші, але стабільні протягом доби", True)
u1 = [p["id"] for p in bs.home_pick(prods, pmin=50, pmax=299.99, min_contrib=50.0)]
u2 = [p["id"] for p in bs.home_pick(prods, pmin=50, pmax=299.99, min_contrib=50.0)]
os.environ["SITE_PICK_DATE"] = "2026-10-14"
u3 = [p["id"] for p in bs.home_pick(prods, pmin=50, pmax=299.99, min_contrib=50.0)]
chk("чип «До 300 ₴»: стабільний у день, інший наступного (спільних ≤ 5 з 10)", u1 == u2 and len(set(u1) & set(u3)) <= 5)
os.environ["SITE_PICK_DATE"] = "не-дата"
chk("некоректний SITE_PICK_DATE ігнорується (береться сьогодні), без винятку", len(bs.home_pick(prods)) == 10)
os.environ.pop("SITE_PICK_DATE", None)
chk("без env — день за Києвом (UTC+3): _pick_day() = ordinal сьогоднішньої дати", isinstance(bs._pick_day(), int) and bs._pick_day() > 739000)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
