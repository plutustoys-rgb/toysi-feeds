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
s, e, wk = ss.next_week_window(at(2026, 10, 14))
chk("з середи 14.10 → вікно пн 19.10 00:00 … нд 25.10 23:59:59, тиждень 2026-W43", s.isoformat() == "2026-10-19T00:00:00+03:00" and wk == "2026-W43" and e.date().isoformat() == "2026-10-25" and (e.hour, e.minute, e.second) == (23, 59, 59))
s, e, wk = ss.next_week_window(at(2026, 10, 25, 18))
chk("з неділі 25.10 18:00 → пн 26.10 00:00 з ЗИМОВИМ офсетом +02:00, до нд 01.11 23:59:59 +02:00", s.isoformat() == "2026-10-26T00:00:00+02:00" and e.isoformat() == "2026-11-01T23:59:59+02:00" and wk == "2026-W44")
s, e, wk = ss.next_week_window(at(2026, 10, 19, 0, 30))
chk("у понеділок після 00:00 → наступний понеділок (поточний тиждень уже йде)", s.date().isoformat() == "2026-10-26")

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

# (5) добір
cands = ss.candidates(index, catalog, history, NOW)
demand = {c["id"] for c in cands[:3]}
ch1 = ss.pick(cands, demand, set(), "2026-W44")
chk("добір ≤10, попит першим", len(ch1) == 10 and demand <= {c["id"] for c in ch1[:3]})
chk("детермінований для одного тижня, інший тиждень — інший порядок", [c["id"] for c in ss.pick(cands, demand, set(), "2026-W44")] == [c["id"] for c in ch1] and [c["id"] for c in ss.pick(cands, set(), set(), "2026-W45")] != [c["id"] for c in ss.pick(cands, set(), set(), "2026-W46")])
last = {c["id"] for c in ch1}
ch2 = ss.pick(cands, demand, last, "2026-W45")
chk("товари минулого тижня виключені", not (last & {c["id"] for c in ch2}))

# (6)(7) plan і CLI
sale, rep = ss.plan(at(2026, 10, 25, 18), index, catalog, history, demand, set())
chk("plan: набір 10, вікно пн 26.10 +02:00 … нд 01.11, файл без cost/margin", sale and len(sale["items"]) == 10 and sale["from"] == "2026-10-26T00:00:00+02:00" and sale["until"] == "2026-11-01T23:59:59+02:00"
    and all(set(v) == {"old", "new"} for v in sale["items"].values()) and "margin" not in json.dumps(sale) and "cost" not in json.dumps(sale).lower())
few_hist = {k: v for k, v in list(history.items())[:5]}
sale2, rep2 = ss.plan(at(2026, 10, 25, 18), index, catalog, few_hist, set(), set())
chk("менше 8 придатних → None", sale2 is None and rep2["chosen"] < ss.N_MIN)
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
chk("cmd_plan: файл sale_week.json = {weeks:[1 тиждень]}, picks.json містить id і маржі (приватно), без .tmp", rc == 0 and len(doc["weeks"]) == 1 and len(written["items"]) == 10 and picks[-1]["ids"] and picks[-1]["margins"] and not list(tmpd.glob("*.tmp")))
doc_a = (tmpd / "sale_week.json").read_text(encoding="utf-8")
rc = ss.cmd_plan(now=at(2026, 10, 25, 19), catalog=catalog)
doc_b = json.loads((tmpd / "sale_week.json").read_text(encoding="utf-8"))
picks_b = json.loads((tmpd / "picks.json").read_text(encoding="utf-8"))
chk("повторний запуск того ж тижня: той самий набір (не перетасовано), без дубля в picks, у файлі той самий 1 тиждень", rc == 0 and len(doc_b["weeks"]) == 1 and doc_b["weeks"][0]["items"] == written["items"] and len(picks_b) == 1)
rc = ss.cmd_plan(now=at(2026, 11, 1, 18), catalog=catalog)
doc2 = json.loads((tmpd / "sale_week.json").read_text(encoding="utf-8"))
chk("наступна неділя 18:00: у файлі ДВА тижні — чинний (until 01.11 23:59 ще не минув) + новий; чинну акцію не знято", rc == 0 and [w["week"] for w in doc2["weeks"]] == ["2026-W44", "2026-W45"])
chk("новий тиждень не повторює товари минулого (last_ids із picks.json)", not (set(doc2["weeks"][1]["items"]) & set(doc2["weeks"][0]["items"])))
rc = ss.cmd_plan(now=at(2026, 11, 8, 18), catalog=catalog)
doc3 = json.loads((tmpd / "sale_week.json").read_text(encoding="utf-8"))
chk("через тиждень: прострочений W44 прибраний, лишилось W45 + W46 (≤2 тижні)", rc == 0 and [w["week"] for w in doc3["weeks"]] == ["2026-W45", "2026-W46"])
old_file = (tmpd / "sale_week.json").read_text(encoding="utf-8")
alerts.clear()
young = {k: {"price": v["price"], "since": (at(2026, 11, 8, 18) - timedelta(days=3)).isoformat()} for k, v in history.items()}
Path(os.environ["SITE_PRICE_HISTORY_FILE"]).write_text(json.dumps(young), encoding="utf-8")
rc = ss.cmd_plan(now=at(2026, 11, 8, 18), catalog=catalog)
chk("історія молодша за 7 днів: код 1 БЕЗ алерту й без перезапису файлу (очікувана ситуація, не збій)", rc == 1 and not alerts and (tmpd / "sale_week.json").read_text(encoding="utf-8") == old_file)
alerts.clear()
Path(os.environ["SITE_PRICE_HISTORY_FILE"]).write_text(json.dumps(few_hist), encoding="utf-8")
rc = ss.cmd_plan(now=at(2026, 11, 8, 18), catalog=catalog)
chk("мало кандидатів: код 1, алерт, попередній sale_week.json НЕ перезаписано", rc == 1 and "site_sale_few" in alerts and (tmpd / "sale_week.json").read_text(encoding="utf-8") == old_file)

# зіпсований файл історії → алерт, а не мовчазний перезапуск відліку
alerts.clear()
Path(os.environ["SITE_PRICE_HISTORY_FILE"]).write_text("{не json", encoding="utf-8")
rc = ss.cmd_plan(now=at(2026, 11, 15, 18), catalog=catalog)
chk("файл історії існує, але зіпсований → код 1 і алерт site_sale_history_lost", rc == 1 and "site_sale_history_lost" in alerts)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
