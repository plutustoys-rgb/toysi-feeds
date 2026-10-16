#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_sale.py — «Знижка тижня −15%» (site_sale.py, рішення власника 14.10: ціна не мінялась ≥7 днів, запас 25 ₴, автоматичний щотижневий набір).
Інваріанти: (1) ціна після знижки = ціла гривня вгору (100→85, 101→86, 199→170); (2) вікно акції = понеділок 00:00 … неділя 23:59:59 за Києвом з ПРАВИЛЬНИМ офсетом (літній +03:00 / зимовий +02:00, перехід 25.10.2026);
(3) історія цін: зміна ціни скидає since, незмінна лишається, урізаний індекс історію не чіпає; (4) кандидат: ціна стабільна ≥7 днів, залишок ≥3, маржа після знижки ≥25 ₴ з еквайрингом і 17,81, є фото, не заборонений/уцінка/UNO;
(5) добір: попит першим, потім залишок, детермінований за тижнем, минулотижневі виключені, ≤10; (6) <8 придатних → набору нема (None), файл не пишеться; (7) файл акції без собівартості/маржі. `python test_site_sale.py` → exit 0/1."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
tmpd = Path(tempfile.mkdtemp())
os.environ["SITE_DIR"] = str(tmpd)
os.environ["SITE_PRICE_HISTORY_FILE"] = str(tmpd / "hist.json")
os.environ["SITE_SALE_PICKS_FILE"] = str(tmpd / "picks.json")

import site_sale as ss  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


alerts = []
ss._alert = lambda k, t: alerts.append(k)

# (1) округлення
chk("ціна після знижки вгору до гривні: 100→85, 101→86, 199→170, 20→17", (ss.sale_ceil(100), ss.sale_ceil(101), ss.sale_ceil(199), ss.sale_ceil(20)) == (85, 86, 170, 17))

# (2) Київ
def at(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ss.KYIV)

chk("офсет Києва: 19.10.2026 +03:00 (літній), 26.10.2026 +02:00 (зимовий), 25.03.2026 о 12:00 +02:00, 29.03.2026 о 12:00 +03:00",
    (at(2026, 10, 19).utcoffset(), at(2026, 10, 26).utcoffset(), at(2026, 3, 25).utcoffset(), at(2026, 3, 29).utcoffset()) == (timedelta(hours=3), timedelta(hours=2), timedelta(hours=2), timedelta(hours=3)))
utc = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
chk("astimezone(KYIV): 14.10 10:00 UTC → 13:00 Київ", utc.astimezone(ss.KYIV).hour == 13)
s, e, wk = ss.next_batch_window(at(2026, 10, 15, 18))
chk("з четверга 15.10 18:00 → партія з пт 16.10 00:00 до 23.10 23:59:59 (8 діб, +03:00), мітка = дата старту", s.isoformat() == "2026-10-16T00:00:00+03:00" and e.isoformat() == "2026-10-23T23:59:59+03:00" and wk == "2026-10-16" and (e - s) < timedelta(days=8))
s, e, wk = ss.next_batch_window(at(2026, 10, 24, 18))
chk("з суботи 24.10 18:00 → партія з неділі 25.10 (літній +03:00) до 01.11 23:59:59 вже з ЗИМОВИМ +02:00", s.isoformat() == "2026-10-25T00:00:00+03:00" and e.isoformat() == "2026-11-01T23:59:59+02:00" and wk == "2026-10-25")
s, e, wk = ss.next_batch_window(at(2026, 10, 19, 0, 30))
chk("о 00:30 понеділка → партія завтрашнього дня (сьогоднішня вже йде)", s.date().isoformat() == "2026-10-20")

# (3) історія
now = at(2026, 10, 14, 14)
idx = [{"id": str(i), "pr": 100 + i, "n": f"т{i}", "p": "x"} for i in range(20)]
hp = tmpd / "h.json"
chk("перший запис історії: since = now для всіх", ss.update_price_history(idx, now, hp) and all(r["since"].startswith("2026-10-14T14:00") for r in ss.load_history(hp).values()))
later = now + timedelta(days=3)
idx2 = [dict(x) for x in idx]
idx2[0]["pr"] = 999
ss.update_price_history(idx2, later, hp)
h = ss.load_history(hp)
chk("ціна змінилась → since оновлено (товар 0); не змінилась → since лишився (товар 1)", h["0"]["since"].startswith("2026-10-17") and h["1"]["since"].startswith("2026-10-14") and h["0"]["price"] == 999)
before = hp.read_text(encoding="utf-8")
chk("порожній індекс не змінює історію", ss.update_price_history([], later, hp) is False and hp.read_text(encoding="utf-8") == before)
chk("урізаний індекс (<50% історії) не змінює історію", ss.update_price_history(idx[:5], later, hp) is False and hp.read_text(encoding="utf-8") == before)
chk("is_stable: рівно 7 днів так, 6 днів 23 год ні, інша ціна ні", ss.is_stable({"price": 100, "since": now.isoformat()}, 100, now + timedelta(days=7)) and not ss.is_stable({"price": 100, "since": now.isoformat()}, 100, now + timedelta(days=7) - timedelta(hours=1))
    and not ss.is_stable({"price": 100, "since": now.isoformat()}, 101, now + timedelta(days=9)))

# (4) кандидати: фейкові каталожні товари
import competitor_pricing as cp

def cat_item(price, stock=10, cost_kind="ok"):
    return {"price": price, "stock": stock, "name": "Іграшка", "category_name": "Конструктори", "category_id": "1", "vendor": "Brand", "id": "x"}

NOW = at(2026, 10, 26, 12)
STABLE = (NOW - timedelta(days=8)).isoformat()
RECENT = (NOW - timedelta(days=2)).isoformat()
index = []
catalog = {}
history = {}
for i in range(1, 31):
    pid = str(1000 + i)
    index.append({"id": pid, "pr": 400, "n": f"т{i}", "p": f"u{i}"})
    catalog[pid] = cat_item(220)       # знижена ціна Toysi ≈187 → real_cost ≈202; ціна сайту 400
    history[pid] = {"price": 400, "since": STABLE}
c0 = ss.candidates(index, catalog, history, NOW)
chk("30 нормальних товарів → усі кандидати, нова ціна 340, маржа ≥25", len(c0) == 30 and all(c["new"] == 340 and c["margin"] >= 25 for c in c0))
cheap = {"id": "2001", "pr": 60, "n": "дешевий", "p": "u"}
catalog["2001"] = cat_item(45); history["2001"] = {"price": 60, "since": STABLE}
chk("збитковий після знижки (ціна 60 при собівартості ≈54) не кандидат", all(c["id"] != "2001" for c in ss.candidates(index + [cheap], catalog, history, NOW)))
history["1001"] = {"price": 400, "since": RECENT}
chk("ціна мінялась 2 дні тому → не кандидат", all(c["id"] != "1001" for c in ss.candidates(index, catalog, history, NOW)))
history["1002"] = {"price": 399, "since": STABLE}
chk("ціна в історії інша, ніж зараз → не кандидат", all(c["id"] != "1002" for c in ss.candidates(index, catalog, history, NOW)))
del history["1003"]
chk("нема в історії → не кандидат", all(c["id"] != "1003" for c in ss.candidates(index, catalog, history, NOW)))
catalog["1004"]["stock"] = 2
chk("залишок 2 (<3) → не кандидат", all(c["id"] != "1004" for c in ss.candidates(index, catalog, history, NOW)))
index[4 + 0]["p"] = ""   # 1005 без фото
chk("без фото → не кандидат", all(c["id"] != "1005" for c in ss.candidates(index, catalog, history, NOW)))
catalog["1006"]["category_name"] = "Мечі, ножі та шаблі"
catalog["1006"]["name"] = "Меч сувенірний"
chk("заборонена політикою (зброя) → не кандидат", all(c["id"] != "1006" for c in ss.candidates(index, catalog, history, NOW)))
catalog["1007"]["name"] = "Уцінка. Іграшка"
chk("уцінка → не кандидат", all(c["id"] != "1007" for c in ss.candidates(index, catalog, history, NOW)))

# (4б) випадкова знижка товару 5–15%
pcts = [ss.item_pct("2026-10-26", str(i)) for i in range(1, 2001)]
chk("item_pct: усі в межах 5…15, усі 11 значень трапляються, детермінований", min(pcts) == 5 and max(pcts) == 15 and len(set(pcts)) == 11 and ss.item_pct("2026-10-26", "7") == ss.item_pct("2026-10-26", "7") and any(ss.item_pct("2026-10-27", str(i)) != ss.item_pct("2026-10-26", str(i)) for i in range(1, 50)))
cl = ss.candidates(index, catalog, history, NOW, label="2026-10-26")
chk("candidates з датою партії: pct на кожному, new = ceil(old×(100−pct)/100), маржа ≥25, різні відсотки", cl and all(5 <= c["pct"] <= 15 and c["new"] == ss.sale_ceil(c["old"], c["pct"]) and c["margin"] >= 25 for c in cl) and len({c["pct"] for c in cl}) > 1)
_it = {"id": "9001", "pr": 300, "n": "тонка маржа", "p": "u"}
catalog["9001"] = cat_item(220)
_h = dict(history); _h["9001"] = {"price": 300, "since": STABLE}
import competitor_pricing as _cp
_cost = _cp.real_toysi_cost(catalog["9001"])
_fit = [p for p in range(5, 16) if ss.margin_after(300, _cost, p) >= 25]
_c9 = [c for c in ss.candidates(index + [_it], catalog, _h, NOW, label="2026-10-26") if c["id"] == "9001"]
chk(f"тонка маржа: випадковий % знижується до дозволеного маржею (дозволені {_fit}), якщо не проходить жоден — не кандидат", (not _fit and not _c9) or (_fit and _c9 and _c9[0]["pct"] <= max(_fit)))
del catalog["9001"]

# (5) добір
cands = ss.candidates(index, catalog, history, NOW)
demand = {c["id"] for c in cands[:3]}
ch1 = ss.pick(cands, demand, set(), "2026-10-26")
chk("добір рівно N_ITEMS=9 (9×8 діб = 72 = 3 сторінки), попит першим", len(ch1) == ss.N_ITEMS == 9 and demand <= {c["id"] for c in ch1[:3]})
chk("детермінований для однієї партії, інша дата — інший порядок", [c["id"] for c in ss.pick(cands, demand, set(), "2026-10-26")] == [c["id"] for c in ch1] and [c["id"] for c in ss.pick(cands, set(), set(), "2026-10-27")] != [c["id"] for c in ss.pick(cands, set(), set(), "2026-10-28")])
last = {c["id"] for c in ch1}
ch2 = ss.pick(cands, demand, last, "2026-10-27")
chk("товари діючих партій виключені", not (last & {c["id"] for c in ch2}))

# (6)(7) plan і CLI
sale, rep = ss.plan(at(2026, 10, 25, 18), index, catalog, history, demand, set())
chk("plan: партія 9, вікно пн 26.10 +02:00 … 02.11 23:59:59 +02:00 (8 діб), файл без cost/margin", sale and len(sale["items"]) == 9 and sale["week"] == "2026-10-26" and sale["from"] == "2026-10-26T00:00:00+02:00" and sale["until"] == "2026-11-02T23:59:59+02:00"
    and all(set(v) == {"old", "new", "pct"} and 5 <= v["pct"] <= 15 and v["new"] == ss.sale_ceil(v["old"], v["pct"]) for v in sale["items"].values()) and "margin" not in json.dumps(sale) and "cost" not in json.dumps(sale).lower())
few_hist = {c["id"]: history[c["id"]] for c in cands[:5]}
sale2, rep2 = ss.plan(at(2026, 10, 25, 18), index, catalog, few_hist, set(), set())
chk("мало придатних (5) → партія неповна (5 < 6), але публікується", sale2 is not None and rep2["chosen"] == 5 < rep2["want"])
sale3, rep3 = ss.plan(at(2026, 10, 25, 18), index, catalog, {}, set(), set())
chk("жодного придатного → None", sale3 is None and rep3["chosen"] == 0)
# плоский (старий) формат файлу читається як один тиждень
flat = tmpd / "flat.json"
flat.write_text(json.dumps({"week": "2026-W44", "from": "2026-10-26T00:00:00+02:00", "until": "2026-11-01T23:59:59+02:00", "items": {"1": {"old": 100, "new": 85}}}), encoding="utf-8")
chk("_read_weeks: плоский формат → 1 тиждень; прострочений → []", len(ss._read_weeks(flat, at(2026, 10, 28))) == 1 and ss._read_weeks(flat, at(2026, 11, 3)) == [])

(tmpd / "index.json").write_text(json.dumps(index), encoding="utf-8")
Path(os.environ["SITE_PRICE_HISTORY_FILE"]).write_text(json.dumps(history), encoding="utf-8")
(tmpd / "demand_featured.json").write_text(json.dumps({"ids": list(demand)}), encoding="utf-8")
rc = ss.cmd_plan(now=at(2026, 10, 25, 18), catalog=catalog)
doc = json.loads((tmpd / "sale_week.json").read_text(encoding="utf-8"))
picks = json.loads((tmpd / "picks.json").read_text(encoding="utf-8"))
written = doc["weeks"][-1]
chk("cmd_plan: файл sale_week.json = {weeks:[1 партія]}, picks.json містить id і маржі (приватно), без .tmp", rc == 0 and len(doc["weeks"]) == 1 and len(written["items"]) == 9 and picks[-1]["ids"] and picks[-1]["margins"] and not list(tmpd.glob("*.tmp")))
rc = ss.cmd_plan(now=at(2026, 10, 25, 19), catalog=catalog)
doc_b = json.loads((tmpd / "sale_week.json").read_text(encoding="utf-8"))
picks_b = json.loads((tmpd / "picks.json").read_text(encoding="utf-8"))
chk("повторний запуск тієї ж дати: той самий набір (не перетасовано, власна партія не виключає себе), без дубля в picks, у файлі та сама 1 партія", rc == 0 and len(doc_b["weeks"]) == 1 and doc_b["weeks"][0]["items"] == written["items"] and len(picks_b) == 1)
rc = ss.cmd_plan(now=at(2026, 10, 26, 18), catalog=catalog)
rc2 = ss.cmd_plan(now=at(2026, 10, 27, 18), catalog=catalog)
doc2 = json.loads((tmpd / "sale_week.json").read_text(encoding="utf-8"))
ids_all = [set(w["items"]) for w in doc2["weeks"]]
chk("щодня по партії: 3 партії одночасно (26.10, 27.10, 28.10), діючі не знято", rc == 0 and rc2 == 0 and [w["week"] for w in doc2["weeks"]] == ["2026-10-26", "2026-10-27", "2026-10-28"])
chk("партії не перетинаються (жоден товар у двох партіях одночасно) і перші дві по 9 (останню обмежує пул фікстури)", sum(len(x) for x in ids_all) == len(set().union(*ids_all)) and [len(x) for x in ids_all][:2] == [9, 9])
rc = ss.cmd_plan(now=at(2026, 11, 4, 18), catalog=catalog)
doc3 = json.loads((tmpd / "sale_week.json").read_text(encoding="utf-8"))
chk("04.11 18:00: прострочені партії 26.10 і 27.10 прибрані; 28.10 (до 04.11 23:59:59) ще діє + нова 05.11", rc == 0 and [w["week"] for w in doc3["weeks"]] == ["2026-10-28", "2026-11-05"])
old_file = (tmpd / "sale_week.json").read_text(encoding="utf-8")
alerts.clear()
young = {k: {"price": v["price"], "since": (at(2026, 11, 8, 18) - timedelta(days=3)).isoformat()} for k, v in history.items()}
Path(os.environ["SITE_PRICE_HISTORY_FILE"]).write_text(json.dumps(young), encoding="utf-8")
rc = ss.cmd_plan(now=at(2026, 11, 8, 18), catalog=catalog)
chk("історія молодша за 7 днів: код 0 (очікуваний стан, служба не «failed») БЕЗ алерту й без перезапису файлу", rc == 0 and not alerts and (tmpd / "sale_week.json").read_text(encoding="utf-8") == old_file)
alerts.clear()
Path(os.environ["SITE_PRICE_HISTORY_FILE"]).write_text(json.dumps(few_hist), encoding="utf-8")
rc = ss.cmd_plan(now=at(2026, 11, 8, 18), catalog={})
chk("каталог порожній: код 1, алерт, попередній sale_week.json НЕ перезаписано", rc == 1 and "site_sale_no_catalog" in alerts and (tmpd / "sale_week.json").read_text(encoding="utf-8") == old_file)

# зіпсований файл історії → алерт, а не мовчазний перезапуск відліку
alerts.clear()
Path(os.environ["SITE_PRICE_HISTORY_FILE"]).write_text("{не json", encoding="utf-8")
rc = ss.cmd_plan(now=at(2026, 11, 15, 18), catalog=catalog)
chk("файл історії існує, але зіпсований → код 1 і алерт site_sale_history_lost", rc == 1 and "site_sale_history_lost" in alerts)

# check_applied: чинний тиждень, а на сайті акція не застосована → алерт; застосована → тиша; нема чинного тижня → тиша
sw = tmpd / "sw_check.json"
sw.write_text(json.dumps({"weeks": [{"week": "2026-W44", "from": "2026-10-26T00:00:00+02:00", "until": "2026-11-01T23:59:59+02:00", "items": {str(i): {"old": 100, "new": 85} for i in range(10)}},
                                     {"week": "2026-W45", "from": "2026-11-02T00:00:00+02:00", "until": "2026-11-08T23:59:59+02:00", "items": {"99": {"old": 100, "new": 85}}}]}), encoding="utf-8")
idx_ok = [{"id": str(i), "pr": 85, "op": 100} for i in range(10)]
idx_bad = [{"id": str(i), "pr": 100} for i in range(10)]
alerts.clear()
chk("перевірка: чинний W44, на сайті застосовано 10 з 10 → без алерту", ss.check_applied(at(2026, 10, 28, 12), idx_ok, sw) == (10, 10) and not alerts)
chk("перевірка: чинний W44, index без op (збій понеділкової збірки) → алерт site_sale_not_applied", ss.check_applied(at(2026, 10, 27, 12), idx_bad, sw) == (10, 0) and "site_sale_not_applied" in alerts)
alerts.clear()
chk("перевірка: до старту (неділя) чинного тижня нема → (0,0) без алерту", ss.check_applied(at(2026, 10, 25, 12), idx_bad, sw) == (0, 0) and not alerts)
chk("перевірка: файл відсутній → (0,0) без алерту", ss.check_applied(at(2026, 10, 28, 12), idx_bad, tmpd / "nofile.json") == (0, 0) and not alerts)
chk("перевірка: застосовано 4 із 10 (<половини) → алерт; 5 з 10 → ні", (ss.check_applied(at(2026, 10, 28, 12), idx_ok[:4] + idx_bad[4:], sw)[1] == 4 and "site_sale_not_applied" in alerts) and (alerts.clear() or ss.check_applied(at(2026, 10, 28, 12), idx_ok[:5] + idx_bad[5:], sw)[1] == 5 and not alerts))

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
