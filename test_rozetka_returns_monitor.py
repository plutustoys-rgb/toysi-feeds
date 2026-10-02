# -*- coding: utf-8 -*-
"""Регрес rozetka_returns_monitor.py: стадії RZ-Delivery за id статусу (звірено живо 2026-10-02 на 5 посилках),
дедуп переходів, базова лінія, текст сповіщення. Мережа не потрібна."""
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import rozetka_returns_monitor as m

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

# id → стадія (реальні останні статуси п'яти посилок: 60040, 50015, 50012; і словник track-status)
for sid, want in ((60040, "returned"), (50015, "returning"), (50012, "returning"), (50020, "collect"),
                  (40070, "expired"), (40050, "refused"), (40040, "waiting"), (10080, "lost"),
                  (99999, "other"), (None, "other"), ("x", "other")):
    chk(f"stage_of({sid}) = {want}", m.stage_of(sid) == want)

def case(oid, stage): return {"order_id": oid, "ttn": f"RMP-{oid}", "amount": "100.00", "stage": stage}

# перехід стадії → сповіщення; та сама стадія → ні
st = {"1": "returning"}
r = m.decide([case("1", "collect"), case("2", "returning")], st, baseline=False)
chk("returning→collect сповіщає", [(c["order_id"], s) for c, s in r] == [("1", "collect")])
chk("нова returning без попереднього НЕ сповіщає (не notify-стадія)", all(c["order_id"] != "2" for c, _ in r))
chk("та сама стадія → тиша", m.decide([case("1", "collect")], {"1": "collect"}, baseline=False) == [])
chk("unknown (збій трекінгу) → тиша й state не затираємо", m.decide([case("1", "unknown")], {"1": "collect"}, baseline=False) == [])
chk("collect→returned сповіщає закриття", [s for _, s in m.decide([case("1", "returned")], {"1": "collect"}, baseline=False)] == ["returned"])

# базова лінія: лише collect
rb = m.decide([case("1", "returned"), case("2", "expired"), case("3", "collect")], {}, baseline=True)
chk("baseline: лише 'collect' (дія), старі returned/expired мовчки", [c["order_id"] for c, _ in rb] == ["3"])

# текст
a = case("5", "collect"); a["prepaid"] = True
txt = m.build_message([(a, "collect")])
chk("текст: номер, ТТН, адреса, передоплата, 'Як забрати'",
    all(x in txt for x in ("№5", "RMP-5", "Алматинська, 4", "ПЕРЕДОПЛАТА", "Реєстр повернення")))
b = case("6", "returned")
chk("returned без collect: без блоку 'Як забрати'", "Як забрати" not in m.build_message([(b, "returned")]))

# ── виправлення за аудитом PR #605 ──
from datetime import datetime, timedelta
import json, tempfile
from pathlib import Path
chk("40060 = seller_refused, текст НЕ про відмову покупця",
    m.stage_of(40060) == "seller_refused" and "покупець" not in m._TEXT["seller_refused"])
now = datetime(2026, 10, 3, 12, 0)
old = (now - timedelta(hours=30)).isoformat(); fresh = (now - timedelta(hours=2)).isoformat()
chk("collect: нагадування через >24 год", len(m.decide([case("1", "collect")], {"1": {"stage": "collect", "notified_at": old}}, False, now)) == 1)
chk("collect: свіже сповіщення → тиша", m.decide([case("1", "collect")], {"1": {"stage": "collect", "notified_at": fresh}}, False, now) == [])
chk("старий формат state (рядок) читається", m.decide([case("1", "collect")], {"1": "returning"}, False, now)[0][1] == "collect")
chk("'other' не потрапляє в сповіщення", m.decide([case("1", "other")], {}, False, now) == [])
chk("вперше побачений 'returned' → мовчки", m.decide([case("9", "returned")], {}, False, now) == [])
chk("вперше побачений 'expired' → сповіщає", [s2 for _, s2 in m.decide([case("9", "expired")], {}, False, now)] == ["expired"])

# run(): збій Telegram НЕ губить сповіщення; 'other' не затирає state; успіх — оновлює
tmp = Path(tempfile.mkdtemp()) / "state.json"
m.STATE_FILE = tmp
tmp.write_text(json.dumps({"1": "returning", "2": {"stage": "collect", "notified_at": fresh}}), encoding="utf-8")
m.collect_cases = lambda: [dict(case("1", "collect"), rz_status=11, status_name="x"),
                           dict(case("2", "other"), rz_status=11, status_name="y")]
m._prepaid = lambda oid: None
m._NO_TELEGRAM = False
m.send_telegram_message = lambda t: False
m.run()
st = json.loads(tmp.read_text(encoding="utf-8"))
chk("Telegram впав → стадія 'collect' НЕ записана (повтор наступного циклу)", st["1"] == "returning")
chk("'other' не затер попередній state", st["2"]["stage"] == "collect")
sent_msgs = []
m.send_telegram_message = lambda t: sent_msgs.append(t) or True
m.run()
st = json.loads(tmp.read_text(encoding="utf-8"))
chk("повтор: надіслано і стадію записано", st["1"]["stage"] == "collect" and st["1"]["notified_at"] and len(sent_msgs) == 1)
chk("текст: оплату не вдалось перевірити (prepaid=None)", "не вдалось перевірити" in sent_msgs[0])
m.run()
chk("наступний цикл — тиша (дедуп)", len(sent_msgs) == 1)

print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ rozetka_returns_monitor — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
