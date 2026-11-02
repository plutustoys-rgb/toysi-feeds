#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_critical_cookie.py — critical_watch: тип події cookie_expiry (термін сесії кабінету читається з файлу сесії Playwright).
ІНВАРІАНТИ: дата береться з куки sessionid (а не з вручну вписаної дати, що застаріває після кожного входу); ≤lead_days → warn, минуло → critical;
нема файлу / нема куки / сесійна кука без дати → НЕ «усе гаразд» (critical/warn), мовчання неприпустиме; подія EVA зареєстрована в critical_events.json."""
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

import critical_watch as cw  # noqa: E402

F = []


def chk(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        F.append(name)


TODAY = date(2026, 11, 2)


def ts(days):
    return datetime(2026, 11, 2, 12, 0).timestamp() + days * 86400


with tempfile.TemporaryDirectory() as td:
    cw.BASE_DIR = Path(td)
    (Path(td) / "st.json").write_text(json.dumps({"cookies": [{"name": "csrftoken", "expires": ts(300)}, {"name": "sessionid", "expires": ts(10)}]}), encoding="utf-8")
    ev = {"id": "x", "name": "X", "type": "cookie_expiry", "state_file": "st.json", "cookie": "sessionid", "lead_days": 3}
    r = cw._eval_event(ev, TODAY)
    chk("дата береться з sessionid, а не з іншої куки (10 діб → ok)", r["state"] == cw.OK and "2026-11-12" in r["detail"])
    for days, want in ((3, cw.WARN), (1, cw.WARN), (-1, cw.CRITICAL)):
        (Path(td) / "st.json").write_text(json.dumps({"cookies": [{"name": "sessionid", "expires": ts(days)}]}), encoding="utf-8")
        chk(f"{days} дн до спливання → {want}", cw._eval_event(ev, TODAY)["state"] == want)
    (Path(td) / "st.json").write_text(json.dumps({"cookies": [{"name": "sessionid", "expires": -1}]}), encoding="utf-8")
    chk("сесійна кука без дати (-1) → warn «звірити», не ok", cw._eval_event(ev, TODAY)["state"] == cw.WARN)
    (Path(td) / "st.json").write_text(json.dumps({"cookies": [{"name": "other", "expires": ts(30)}]}), encoding="utf-8")
    chk("нема куки sessionid → warn, не ok", cw._eval_event(ev, TODAY)["state"] == cw.WARN)
    chk("нема файлу сесії → critical", cw._eval_event({**ev, "state_file": "нема.json"}, TODAY)["state"] == cw.CRITICAL)
    (Path(td) / "st.json").write_text("не json", encoding="utf-8")
    chk("зіпсований файл сесії → critical", cw._eval_event(ev, TODAY)["state"] == cw.CRITICAL)

events = json.loads((Path(__file__).parent / "critical_events.json").read_text(encoding="utf-8"))["events"]
eva = [e for e in events if e.get("id") == "eva_session"]
chk("подія eva_session зареєстрована (cookie_expiry, sessionid, шлях до файлу сесії EVA)",
    len(eva) == 1 and eva[0]["type"] == "cookie_expiry" and eva[0]["cookie"] == "sessionid" and eva[0]["state_file"].endswith("eva_cabinet_state.json"))
chk("blocks НЕ «лише ручний…» (протухла сесія зупиняє автоматику → health_check покаже «потрібен власник»)", not str(eva[0].get("blocks", "")).startswith("лише ручний"))
chk("renewal містить обидві команди: --login і deploy\\eva_session_to_vps.ps1", "--login" in eva[0]["renewal"] and "eva_session_to_vps.ps1" in eva[0]["renewal"])

# --- аудит #1070: стійкість до структурно хибного файлу, вибір валідної куки, ескалація warn ---
with tempfile.TemporaryDirectory() as td:
    cw.BASE_DIR = Path(td)
    ev = {"id": "x", "name": "X", "type": "cookie_expiry", "state_file": "st.json", "cookie": "sessionid", "lead_days": 3}
    bad = {"cookies": None}, [1, 2], {"cookies": [1, "a", None]}, {"cookies": [{"name": "sessionid", "expires": 1e18}]}, {"cookies": [{"name": "sessionid", "expires": float("nan")}]}, \
          {"cookies": [{"name": "sessionid", "expires": float("inf")}]}, {"cookies": [{"name": "sessionid", "expires": True}]}, {"cookies": [{"name": "sessionid", "expires": "17"}]}
    for i, doc in enumerate(bad):
        (Path(td) / "st.json").write_text(json.dumps(doc), encoding="utf-8")
        try:
            st = cw._eval_event(ev, TODAY)["state"]
        except Exception as e:  # noqa: BLE001
            st = f"ВИНЯТОК {type(e).__name__}"
        chk(f"структурно хибний файл №{i} не валить календар і НЕ дає ok (отримали {st})", st in (cw.WARN, cw.CRITICAL))
    (Path(td) / "st.json").write_text(json.dumps({"cookies": [{"name": "sessionid", "expires": -1}, {"name": "sessionid", "expires": ts(10)}]}), encoding="utf-8")
    r = cw._eval_event(ev, TODAY)
    chk("дубль куки: перша без дати, друга валідна → береться валідна (ok, 10 дн)", r["state"] == cw.OK and "2026-11-12" in r["detail"])
    chk("прапор escalate за замовчуванням False, True лише з escalate_warn", cw._eval_event(ev, TODAY)["escalate"] is False and cw._eval_event({**ev, "escalate_warn": True}, TODAY)["escalate"] is True)
chk("eva_session має escalate_warn і renewal ≤200 символів (health_check обрізає до 200 — команда не губиться)",
    eva[0].get("escalate_warn") is True and len(eva[0]["renewal"]) <= 200 and "eva_session_to_vps.ps1" in eva[0]["renewal"])

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
