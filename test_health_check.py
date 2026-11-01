#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_health_check.py — health_check.py (десктоп) і vps_health.py (VPS): рішення власника 01.11.2026 «усі збої й контроль VPS — на Код».
ІНВАРІАНТИ: «VPS мовчить» (знімок >3 год) = crit і далі не вірить знімку; failed-сервіс/зупинений таймер/диск/відставання master видно;
алерти групуються (цифри згорнуті), відновлення «✅» і «🛒 Нове замовлення» не шумлять; кожна перевірка в try (збій перевірки = проблема, не тиша);
стан дедуплікує (нова/знята/ескалація), запис у канал угорі, CRLF, один раз; stderr git НЕ потрапляє у звіт (там може бути URL з токеном);
vps_health: парс часу systemd, відсів чужих юнітів, журнал алертів без «Нове замовлення» і старших за вікно, секрети редагуються."""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["AUDIT_NO_TELEGRAM"] = "1"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

import health_check as hc  # noqa: E402
import vps_health as vh  # noqa: E402

F = []


def chk(name, cond):
    print(("OK   " if cond else "FAIL ") + name)
    if not cond:
        F.append(name)


NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def iso(h_ago):
    return (NOW - timedelta(hours=h_ago)).isoformat()


def snap(**kw):
    s = {"generated_at": iso(0.5), "units": {"services": {}, "timers": {}, "failed": []}, "host_stats": {"disk_used_pct": 40.0}, "state_files": {}, "alerts": []}
    s.update(kw)
    return s


def ctx(snapshot=None, err=None, **over):
    d = dict(now=NOW, vps_snapshot=lambda: (snapshot, err), master=lambda: ("", ""), feed_meta=lambda: {}, tasks=lambda: [], local_alerts=lambda: [],
             critical=lambda: {"generated_at": iso(0.2), "events": []})
    d.update(over)
    return hc.Ctx(**d)


def ids(issues):
    return {i["id"] for i in issues}


# --- VPS ---
r = hc.check_vps(ctx(None, "гілка ops-health ще не створена"))
chk("нема знімка → одна проблема warn vps:no_snapshot, не crit", ids(r) == {"vps:no_snapshot"} and r[0]["sev"] == "warn")
r = hc.check_vps(ctx(snap(generated_at=iso(5))))
chk("знімок 5 год → crit «VPS мовчить» + owner, і далі знімку не віримо", ids(r) == {"vps:stale"} and r[0]["sev"] == "crit" and r[0]["owner"])
r = hc.check_vps(ctx(snap(generated_at=iso(2.9))))
chk("знімок 2,9 год — ще свіжий", "vps:stale" not in ids(r))
r = hc.check_vps(ctx(snap(units={"services": {"order-pipeline.service": {"active": "failed", "result": "exit-code", "exit_status": "1"}}, "timers": {}, "failed": ["order-pipeline.service"]})))
chk("failed-юніт: одна проблема (без дубля з services)", [i["id"] for i in r].count("vps:unit_failed:order-pipeline.service") == 1 and r[0]["sev"] == "crit")
r = hc.check_vps(ctx(snap(units={"services": {"x.service": {"active": "inactive", "result": "exit-code", "exit_status": "2"}}, "timers": {}, "failed": []})))
chk("сервіс inactive з result=exit-code теж помічено (failed-список порожній)", "vps:unit_failed:x.service" in ids(r))
r = hc.check_vps(ctx(snap(units={"services": {"ok.service": {"active": "inactive", "result": "success"}, "run.service": {"active": "activating", "result": "success"}}, "timers": {}, "failed": []})))
chk("success і activating — не проблема", ids(r) == set())
r = hc.check_vps(ctx(snap(units={"services": {}, "timers": {"a.timer": {"enabled": "enabled", "active": "inactive"}, "b.timer": {"enabled": "disabled", "active": "inactive"}}, "failed": []})))
chk("увімкнений, але неактивний таймер — warn; вимкнений навмисно — ні", ids(r) == {"vps:timer_down:a.timer"})
r = hc.check_vps(ctx(snap(host_stats={"disk_used_pct": 96.5, "disk_free_gb": 1.2})))
chk("диск ≥95 % → crit", "vps:disk" in ids(r))
r = hc.check_vps(ctx(snap(host_stats={"disk_used_pct": 94.9})))
chk("диск 94,9 % — тихо", "vps:disk" not in ids(r))
r = hc.check_vps(ctx(snap(host_stats={"git_head": "a" * 40, "disk_used_pct": 10}), master=lambda: ("b" * 40, iso(2))))
chk("VPS на іншому sha, master старший за 45 хв → warn vps:code_behind", "vps:code_behind" in ids(r))
r = hc.check_vps(ctx(snap(host_stats={"git_head": "a" * 40, "disk_used_pct": 10}), master=lambda: ("b" * 40, iso(0.2))))
chk("master свіжіший за 45 хв — ще не відставання", "vps:code_behind" not in ids(r))
r = hc.check_vps(ctx(snap(host_stats={"git_head": "b" * 40, "disk_used_pct": 10}), master=lambda: ("b" * 40, iso(9))))
chk("sha збігається — тихо", "vps:code_behind" not in ids(r))
r = hc.check_vps(ctx(snap(state_files={"feed_pipeline_state.json": {"last_status": "failed", "reason": "gate"}})))
chk("feed_pipeline_state failed → crit; degraded → warn", r[0]["id"] == "vps:feed_pipeline:failed" and r[0]["sev"] == "crit"
    and hc.check_vps(ctx(snap(state_files={"feed_pipeline_state.json": {"last_status": "degraded"}})))[0]["sev"] == "warn")
al = [{"at": "2026-11-01T10:00:00", "source": "order_router.py", "category": "error", "text": "🚨 замовлення 123 не передано"},
      {"at": "2026-11-01T11:00:00", "source": "order_router.py", "category": "error", "text": "🚨 замовлення 456 не передано"},
      {"at": "2026-11-01T11:05:00", "source": "service_watchdog.py", "category": "error", "text": "✅ Watchdog: відновлено"},
      {"at": "2026-11-01T11:06:00", "source": "orders_watcher.py", "category": "money", "text": "🛒 Нове замовлення · prom №1 · 100 грн"},
      {"at": "2026-11-01T11:07:00", "source": "daily_report.py", "category": "money", "text": "💰 Дохід за добу 1000 грн"}]
r = hc.check_vps(ctx(snap(alerts=al)))
a = [i for i in r if ":alert:" in i["id"]]
chk("алерти: дві «не передано» з різними id → одна проблема (цифри згорнуті), 2×; ✅ і 🛒 пропущено; money → info", len(a) == 2
    and any(i["sev"] == "warn" and "2×" in i["detail"] for i in a) and any(i["sev"] == "info" for i in a))
chk("ключ алерта стабільний між запусками (ідемпотентність стану)", [i["id"] for i in hc.check_vps(ctx(snap(alerts=al)))] == [i["id"] for i in r])

# --- артефакти ---
meta = {k: {"generated_at": iso(1), "offers": 100} for k in hc.FEED_KEYS + ("eva_feed.xml",)}
chk("свіжі фіди — тихо", hc.check_artifacts(ctx(feed_meta=lambda: meta)) == [])
m2 = json.loads(json.dumps(meta)); m2["meta_feed.xml"]["generated_at"] = iso(13); m2["eva_feed.xml"]["generated_at"] = iso(3.5); m2["bing_feed.xml"]["offers"] = 0
r = hc.check_artifacts(ctx(feed_meta=lambda: m2))
chk("meta >12 год, eva >3 год — warn; 0 позицій — crit", ids(r) == {"art:feed_stale:meta_feed.xml", "art:feed_stale:eva_feed.xml", "art:feed_empty:bing_feed.xml"}
    and [i for i in r if i["id"] == "art:feed_empty:bing_feed.xml"][0]["sev"] == "crit")
m3 = dict(meta); del m3["google_merchant_feed.xml"]
chk("фід зник із feed_meta → warn", ids(hc.check_artifacts(ctx(feed_meta=lambda: m3))) == {"art:feed_missing:google_merchant_feed.xml"})

# --- задачі ---
tasks = [{"n": "PlutusToys_A", "s": "Ready", "r": 0}, {"n": "PlutusToys_B", "s": "Running", "r": 267009}, {"n": "PlutusToys_C", "s": "Ready", "r": 1},
         {"n": "PlutusToys_EvaCabinetKeepalive", "s": "Ready", "r": 1}, {"n": "PlutusToys_D", "s": "Disabled", "r": 267011}, {"n": "PlutusToys_E", "s": "Ready", "r": 267011}]
r = hc.check_tasks(ctx(tasks=lambda: tasks))
chk("задачі: 0/running/ще не запускалась — тихо; помилка — warn; Disabled — info; Keepalive — owner", ids(r) == {"task:fail:PlutusToys_C", "task:fail:PlutusToys_EvaCabinetKeepalive", "task:disabled:PlutusToys_D"}
    and [i for i in r if i["id"].endswith("Keepalive")][0]["owner"] and not [i for i in r if i["id"].endswith("_C")][0]["owner"])

r = hc.check_tasks(ctx(tasks=lambda: [{"n": "PlutusToys_AgentWatch", "s": "Disabled", "r": 0}, {"n": "PlutusToys_Nova", "s": "Disabled", "r": 0}]))
chk("навмисно вимкнені (реєстр SYSTEM_MAP local_tasks_disabled) мовчать, інші вимкнені — info", ids(r) == {"task:disabled:PlutusToys_Nova"}
    and {"PlutusToys_AgentWatch", "PlutusToys_SellerWatchdog", "PlutusToys_BackupCoworkToVps"} <= hc.intentionally_disabled())

# --- критичний календар ---
crit = {"generated_at": iso(0.1), "events": [{"id": "x", "name": "NovaPay", "state": "critical", "detail": "прострочено", "renewal": "перелогінитись", "blocks": "зупиняє звірку"},
                                             {"id": "m", "name": "Веб", "state": "critical", "detail": "прострочено", "blocks": "лише ручний вхід"}, {"id": "y", "state": "ok"}]}
r = hc.check_critical(ctx(critical=lambda: crit))
chk("критичний календар: critical → warn+owner з hint; «лише ручний вхід» → info без owner; ok — ні", ids(r) == {"crit:x", "crit:m"} and [i for i in r if i["id"] == "crit:x"][0]["hint"] == "перелогінитись"
    and [i for i in r if i["id"] == "crit:m"][0]["sev"] == "info" and not [i for i in r if i["id"] == "crit:m"][0]["owner"])
crit["generated_at"] = iso(5)
chk("календар не оновлюється >3 год → crit:stale", "crit:stale" in ids(hc.check_critical(ctx(critical=lambda: crit))))

# --- збій самої перевірки ---
def boom():
    raise OSError("мережа лягла")
r = hc.run_checks(ctx(None, "x", feed_meta=boom))
chk("збій перевірки артефактів не ховається: проблема check:artifacts, решта працює", "check:artifacts" in ids(r) and "vps:no_snapshot" in ids(r))
chk("сортування: crit раніше за warn раніше за info", [hc.SEV_ORDER[i["sev"]] for i in r] == sorted(hc.SEV_ORDER[i["sev"]] for i in r))

# --- стан ---
i1, i2 = hc.issue("a", "warn", "A"), hc.issue("b", "info", "B")
new, res, st = hc.diff_state([i1, i2], {"open": {}}, "t1")
chk("перший запуск: усе нове, нічого знято", len(new) == 2 and res == [] and set(st["open"]) == {"a", "b"})
new, res, st2 = hc.diff_state([i1], st, "t2")
chk("наступний: нічого нового; b зникла → знято", new == [] and [x["id"] for x in res] == ["b"] and st2["open"]["a"]["first"] == "t1" and st2["open"]["a"]["last"] == "t2")
new, res, st3 = hc.diff_state([hc.issue("a", "crit", "A")], st2, "t3")
chk("ескалація warn→crit — знову нова", [x["id"] for x in new] == ["a"])
new, res, _ = hc.diff_state([hc.issue("a", "info", "A")], st3, "t4")
chk("пониження серйозності — не нова", new == [])

# --- канал ---
with tempfile.TemporaryDirectory() as td:
    ch = Path(td) / "EXECUTOR_CHANNEL.md"
    ch.write_bytes("# Канал\r\n\r\n## [Автомат → Код] старе\r\nтекст\r\n".encode("utf-8"))
    ent = hc.channel_entry([hc.issue("a", "warn", "Щось зламалось", "детал", "що робити", owner=True), hc.issue("i", "info", "інфо")], [{"id": "z", "title": "Знято Z"}], NOW)
    hc.post_to_channel(ent, ch)
    b = ch.read_bytes().decode("utf-8")
    chk("запис угорі перед першим «## », CRLF", b.index("Стан системи") < b.index("старе") and b.startswith("# Канал\r\n") and "\n" not in b.replace("\r\n", ""))
    chk("у записі: нових лише warn/crit (info не пишемо), знято, hint, позначка власника", "нових 1, знято 1" in b and "інфо" not in b and "↳ що робити" in b and "👤 потрібен власник" in b and "знято: Знято Z" in b)
    stp = Path(td) / "st.json"
    hc.save_state(st, stp)
    same = hc.load_state(stp)["open"].keys() == st["open"].keys()
    stp.write_text("{", encoding="utf-8")
    chk("стан зберігається/читається; битий файл → порожній стан", same and hc.load_state(stp) == {"open": {}})

# --- звіт ---
chk("brief: підрахунок і «усе чисто»", "усе чисто" in hc.brief([], NOW) and "🔴 1 · 🟠 0" in hc.brief([hc.issue("c", "crit", "К", owner=True)], NOW) and "потрібен власник: 1" in hc.brief([hc.issue("c", "crit", "К", owner=True)], NOW))

# --- безпека fetch ---
_orig = subprocess.run
def fake_run(cmd, **kw):
    return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: unable to access 'https://gho_SECRETTOKEN@github.com/x/y.git/'")
subprocess.run = fake_run
with tempfile.TemporaryDirectory() as td:
    (Path(td) / ".git").mkdir()
    d, err = hc.fetch_vps_snapshot(Path(td))
subprocess.run = _orig
chk("помилка fetch не несе stderr (URL з токеном) у звіт", d is None and "SECRETTOKEN" not in (err or "") and "128" in err)
subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fatal: couldn't find remote ref ops-health")
with tempfile.TemporaryDirectory() as td:
    (Path(td) / ".git").mkdir()
    d, err = hc.fetch_vps_snapshot(Path(td))
subprocess.run = _orig
chk("відсутня гілка → зрозуміле «ще не створена»", d is None and "ще не створена" in err)

# --- vps_health ---
chk("systemd-час → UTC ISO; n/a → None", vh.parse_systemd_ts("Sun 2026-11-01 08:20:31 EET") is not None and vh.parse_systemd_ts("n/a") is None and vh.parse_systemd_ts("") is None)
blocks = vh.parse_show_blocks("Id=a.service\nActiveState=active\n\nId=b.service\nExecStart={ path=/opt/plutustoys/x }\n")
chk("парс блоків systemctl show", len(blocks) == 2 and blocks[1]["Id"] == "b.service")


def fake_systemctl(cmd, timeout=30):
    c = " ".join(cmd)
    if "list-units" in c:
        return "order-pipeline.service loaded inactive dead x\napt-daily.service loaded inactive dead y\n"
    if "--failed" in c:
        return "order-pipeline.service loaded failed failed x\n"
    if "order-pipeline.timer" in c:
        return "Id=order-pipeline.timer\nActiveState=active\nSubState=waiting\nUnitFileState=enabled\nNextElapseUSecRealtime=Sun 2026-11-01 13:09:00 EET\nLastTriggerUSec=Sun 2026-11-01 12:54:00 EET\n"
    if "order-pipeline.service" in c:
        return ("Id=order-pipeline.service\nActiveState=failed\nSubState=failed\nResult=exit-code\nExecMainStatus=1\nExecMainExitTimestamp=Sun 2026-11-01 12:55:00 EET\n"
                "ExecMainStartTimestamp=Sun 2026-11-01 12:54:00 EET\nType=oneshot\nExecStart={ path=/opt/plutustoys/venv/bin/python3 }\n\n"
                "Id=apt-daily.service\nActiveState=inactive\nResult=success\nExecStart={ path=/usr/lib/apt/apt.systemd.daily }\n")
    return ""


u = vh.collect_units(fake_systemctl)
chk("collect_units: лише юніти з /opt/plutustoys (apt-daily відсіяно), таймер і failed підтягнуто",
    list(u["services"]) == ["order-pipeline.service"] and u["services"]["order-pipeline.service"]["result"] == "exit-code"
    and u["timers"]["order-pipeline.timer"]["enabled"] == "enabled" and u["failed"] == ["order-pipeline.service"])
chk("збій systemctl (порожній вихід) → порожній знімок, без винятку", vh.collect_units(lambda c, timeout=30: "") == {"services": {}, "timers": {}, "failed": []})

with tempfile.TemporaryDirectory() as td:
    j = Path(td) / "telegram_alerts.md"
    n = datetime(2026, 11, 1, 12, 0)
    def blk(dt, src, text):
        return f"## {dt:%Y-%m-%d %H:%M:%S} — {src}\n\n{text}\n\n---\n\n"
    j.write_text(blk(n - timedelta(hours=2), "order_router.py", "🚨 збій передачі, Cookie: sessionid=ABCDEF123456") + blk(n - timedelta(hours=1), "orders_watcher.py", "🛒 Нове замовлення · prom №9 · 500 грн")
                 + blk(n - timedelta(hours=40), "old.py", "🚨 старий збій") + blk(n - timedelta(hours=1), "x.py", "інфо без ознак"), encoding="utf-8")
    al = vh.collect_alerts(n.replace(tzinfo=timezone.utc), path=j)
    chk("журнал VPS: лише error/warning/money за вікно, без «🛒 Нове замовлення», без старших; секрет відредаговано; текст ≤300",
        len(al) == 1 and al[0]["source"] == "order_router.py" and "ABCDEF123456" not in al[0]["text"] and len(al[0]["text"]) <= 300)

# --- протокол пробудження (recall.health_section) ---
import recall  # noqa: E402
chk("інша роль — порожньо; роль не визначена — лише нагадування (без запуску)", recall.health_section("seo") == "" and "ПЕРШЕ" in recall.health_section("") and "health_check.py" in recall.health_section(""))
_orig = subprocess.run
subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="🩺 Стан системи X: 🔴 1\n🔴 щось", stderr="")
hs = recall.health_section("kod")
subprocess.run = lambda cmd, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd, 45))
hf = recall.health_section("kod")
subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="traceback")
he = recall.health_section("kod")
subprocess.run = _orig
chk("kod: звіт у картці; таймаут і порожній вихід НЕ ховаються («не перевірено — запусти вручну»); довжина ≤ HEALTH_CAP",
    "🔴 щось" in hs and "ПЕРШЕ ДІЯ ПРОБУДЖЕННЯ" in hs and len(hs) <= recall.HEALTH_CAP and "НЕ перевірено" in hf and "TimeoutExpired" in hf and "НЕ перевірено" in he)
chk("ядро правил містить пробудження, кожен пункт з нового рядка", "Пробудження Коду" in recall.CORE_RULES and recall.CORE_RULES.count("\n•") == 7)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
