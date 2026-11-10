#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dropt_session_keepalive.py — «з протухання виріши щоб його не було» (власник 10.11.2026): кабінет Dropt входить через Google/Telegram (автовходу нема), тож runner (1) раз на 6 год заходить у кабінет і зберігає куки,
які сервер ПРОДОВЖИВ; (2) попереджає заздалегідь, коли до спливу ≤7 діб; (3) алертить, коли сесія мертва. Тести без мережі. `python test_dropt_session_keepalive.py` → exit 0/1."""
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import requests  # noqa: E402
import dropt_cabinet as dc  # noqa: E402
import dropt_order_runner as dr  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
NOW = time.time()
state = tmp / "state.json"


def write_state(exp):
    state.write_text(json.dumps({"cookies": [
        {"name": "DSESSID", "value": "old", "domain": "dropt.in.ua", "path": "/", "expires": exp},
        {"name": "is_logged", "value": "1", "domain": "dropt.in.ua", "path": "/", "expires": exp},
        {"name": "language", "value": "uk", "domain": "dropt.in.ua", "path": "/", "expires": exp + 100},
        {"name": "_ga", "value": "x", "domain": ".google.com", "path": "/", "expires": exp},
    ], "origins": []}), encoding="utf-8")


class FakeHttp:
    def __init__(self, new_exp=None, new_val=None):
        self.s = requests.Session()
        self.s.cookies.set("DSESSID", new_val or "old", domain="dropt.in.ua", path="/", expires=new_exp)
        self.s.cookies.set("is_logged", "1", domain="dropt.in.ua", path="/", expires=new_exp)


write_state(NOW + 3 * 86400)
chk("session_days_left: ≈3 доби (мін. по DSESSID/is_logged; _ga/language не рахуються)", abs(dc.session_days_left(state, NOW) - 3.0) < 0.1)
n = dc.persist_cookies(FakeHttp(new_exp=int(NOW + 30 * 86400), new_val="new"), state)
chk("сервер продовжив куки на 30 діб → persist_cookies оновив 2 (DSESSID: value+expires, is_logged: expires)", n == 2)
chk("після запису днів ≈30, значення збережено, чужий домен/ language не змінено", abs(dc.session_days_left(state, NOW) - 30.0) < 0.1 and any(c["name"] == "DSESSID" and c["value"] == "new" for c in json.loads(state.read_text())["cookies"]) and any(c["name"] == "_ga" and c["value"] == "x" for c in json.loads(state.read_text())["cookies"]))
mt = state.stat().st_mtime_ns
time.sleep(0.05)
n2 = dc.persist_cookies(FakeHttp(new_exp=int(NOW + 30 * 86400), new_val="new"), state)
chk("нічого не змінилось → файл НЕ переписується (mtime той самий)", n2 == 0 and state.stat().st_mtime_ns == mt)
write_state(NOW + 3 * 86400)
n3 = dc.persist_cookies(FakeHttp(new_exp=int(NOW + 1 * 86400), new_val="old"), state)
chk("сервер дав РАНІШИЙ expires → не скорочуємо (expires лише вперед)", n3 == 0 and abs(dc.session_days_left(state, NOW) - 3.0) < 0.1)
chk("нема файлу стану → persist 0, days_left None", dc.persist_cookies(FakeHttp(), tmp / "nope.json") == 0 and dc.session_days_left(tmp / "nope.json") is None)

# keepalive() із підміненим ensure_session
orig_es = dc.ensure_session
dc.ensure_session = lambda http=None: {"logged_in": True, "balance": 367.47}
write_state(NOW + 3 * 86400)
r = dc.keepalive(FakeHttp(new_exp=int(NOW + 30 * 86400), new_val="new"), state)
chk("keepalive: баланс, оновлено куків, днів ≈30", r["balance"] == 367.47 and r["cookies_updated"] == 2 and abs(r["days_left"] - 30.0) < 0.1)
dc.ensure_session = orig_es

# keepalive_if_due
alerts = []
dr._alert = lambda oid, key, text, cooldown=0: alerts.append((oid, key, text))


class Cab:
    STATE_FILE = state

    def __init__(self, days=None, exc=None):
        self.days, self.exc, self.calls = days, exc, 0

    def keepalive(self):
        self.calls += 1
        if self.exc:
            raise self.exc
        return {"balance": 1, "cookies_updated": 0, "days_left": self.days}


mk = tmp / "ka.json"
T0 = datetime(2026, 11, 10, 18, 0, 0)
c = Cab(days=25)
r1 = dr.keepalive_if_due(now=T0, marker=mk, cab=c)
chk("перший виклик: заходить у кабінет, алерту нема (25 діб)", r1["ran"] and c.calls == 1 and alerts == [])
r2 = dr.keepalive_if_due(now=T0 + timedelta(hours=2), marker=mk, cab=c)
chk("через 2 год: не заходить (інтервал 6 год)", r2 == {"ran": False, "why": "not_due"} and c.calls == 1)
r3 = dr.keepalive_if_due(now=T0 + timedelta(hours=7), marker=mk, cab=c)
chk("через 7 год: заходить знову", r3["ran"] and c.calls == 2)
mk.unlink()
alerts.clear()
dr.keepalive_if_due(now=T0, marker=mk, cab=Cab(days=5))
chk("до спливу 5 діб (≤7) → попередження «спливає через 5.0 діб» заздалегідь", len(alerts) == 1 and alerts[0][1] == "session_expiring" and "5" in alerts[0][2])
mk.unlink()
alerts.clear()
dr.keepalive_if_due(now=T0, marker=mk, cab=Cab(exc=dc.OrderError("сесія кабінету Dropt протухла")))
chk("сесія мертва (OrderError) → алерт session_dead, виняток не вилітає", len(alerts) == 1 and alerts[0][1] == "session_dead")
mk.unlink()
alerts.clear()
dr.keepalive_if_due(now=T0, marker=mk, cab=Cab(exc=RuntimeError("мережа")))
chk("збій мережі → теж алерт, не виняток", len(alerts) == 1 and alerts[0][1] == "session_dead")


class NoFile(Cab):
    STATE_FILE = tmp / "absent.json"


alerts.clear()
rn = dr.keepalive_if_due(now=T0, marker=tmp / "ka2.json", cab=NoFile())
chk("нема файлу сесії (Dropt не підключено) → нічого, без алертів", rn == {"ran": False, "why": "no_session_file"} and alerts == [])
chk("збій усередині keepalive_if_due ніколи не кидає (cab=None → імпорт модуля, файл сесії відсутній у тесті)", isinstance(dr.keepalive_if_due(now=T0, marker=tmp / "ka3.json"), dict))
print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
