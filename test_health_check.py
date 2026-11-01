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


def eva_date(h_ago):
    """Дата в голові eva_feed.xml — локальний час хоста без зони, як віддає VPS."""
    return (NOW.astimezone() - timedelta(hours=h_ago)).strftime("%Y-%m-%d %H:%M")


def ctx(snapshot=None, err=None, **over):
    d = dict(now=NOW, vps_snapshot=lambda: (snapshot, err), master=lambda: ("", ""), feed_meta=lambda: {}, eva_date=lambda: eva_date(0.5), tasks=lambda: [], local_alerts=lambda: [],
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
m2 = json.loads(json.dumps(meta)); m2["meta_feed.xml"]["generated_at"] = iso(13); m2["bing_feed.xml"]["offers"] = 0
r = hc.check_artifacts(ctx(feed_meta=lambda: m2, eva_date=lambda: eva_date(3.5)))
chk("meta >12 год, eva (за датою у файлі) >3 год — warn; 0 позицій — crit", ids(r) == {"art:feed_stale:meta_feed.xml", "art:feed_stale:eva_feed.xml", "art:feed_empty:bing_feed.xml"}
    and [i for i in r if i["id"] == "art:feed_empty:bing_feed.xml"][0]["sev"] == "crit")
m4 = json.loads(json.dumps(meta)); m4["eva_feed.xml"]["generated_at"] = iso(5)   # feed_meta для EVA оновлюється раз на 6 год — старий запис НЕ є ознакою збою (аудит #1068 B3)
chk("EVA: старий generated_at у feed_meta при свіжій даті у файлі — тихо", hc.check_artifacts(ctx(feed_meta=lambda: m4, eva_date=lambda: eva_date(1))) == [])
chk("EVA: дату в файлі не прочитано → warn, не тиша", ids(hc.check_artifacts(ctx(feed_meta=lambda: meta, eva_date=lambda: None))) == {"art:feed_stale:eva_feed.xml"})
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
chk("наступний: нічого нового; info-проблема b зникла → у «знято» НЕ пишемо (шум, аудит N1), зі стану вилучено", new == [] and res == [] and "b" not in st2["open"] and st2["open"]["a"]["first"] == "t1" and st2["open"]["a"]["last"] == "t2")
_, res, _ = hc.diff_state([i2], st2, "t3")
chk("warn-проблема a зникла → знято", [x["id"] for x in res] == ["a"])
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
hc.LAST_BRIEF_FILE = Path(tempfile.gettempdir()) / "health_check_test_no_such_last.json"   # тест не залежить від реального кешу звіту
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
    "🔴 щось" in hs and "ПЕРШЕ при пробудженні" in hs and len(hs) <= recall.HEALTH_CAP and "НЕ перевірено" in hf and "TimeoutExpired" in hf and "НЕ перевірено" in he)
chk("ядро правил містить пробудження, кожен пункт з нового рядка", "Пробудження Коду" in recall.CORE_RULES and recall.CORE_RULES.count("\n•") == 7)

# --- аудит #1068: B1 кеш/таймаут хука, B2 бюджет картки, B3 (вище), B4 сліпі перевірки, N2–N4, мутації ---
import health_check as _hcmod  # noqa: E402
with tempfile.TemporaryDirectory() as td:
    _hcmod.LAST_BRIEF_FILE = Path(td) / "last.json"
    now_l = datetime.now().astimezone()
    hc.save_last_brief("🩺 Стан X: 🔴 1\n🔴 кешований рядок", now_l - timedelta(minutes=10), _hcmod.LAST_BRIEF_FILE)
    _orig = subprocess.run
    subprocess.run = lambda cmd, **kw: (_ for _ in ()).throw(AssertionError("свіжий кеш: мережі/підпроцесу бути не повинно"))
    try:
        hs = recall.health_section("kod")
    finally:
        subprocess.run = _orig
    chk("свіжий кеш (<90 хв): хук бере його БЕЗ підпроцесу й мережі (B1), у межах HEALTH_CAP", "кешований рядок" in hs and len(hs) <= recall.HEALTH_CAP)
    hc.save_last_brief("🩺 Стан старий\n🔴 старий рядок", now_l - timedelta(minutes=300), _hcmod.LAST_BRIEF_FILE)
    tmo = []
    subprocess.run = lambda cmd, **kw: (tmo.append(kw.get("timeout")), (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd, kw.get("timeout"))))[1]
    try:
        hs = recall.health_section("kod")
    finally:
        subprocess.run = _orig
    chk("старий кеш + таймаут: жива перевірка з таймаутом хука (11 с < 15 с хука), збій видно + застарілий звіт позначено", tmo == [recall.HEALTH_HOOK_TIMEOUT] and recall.HEALTH_HOOK_TIMEOUT < 15
        and "НЕ перевірено" in hs and "ЗАСТАРІЛИЙ" in hs and "старий рядок" in hs)
    big = "🩺 Стан: 🔴 2\n" + "\n".join(f"🟠 проблема номер {k} з довгим описом, щоб заповнити бюджет картки" for k in range(12))
    hc.save_last_brief(big, now_l - timedelta(minutes=5), _hcmod.LAST_BRIEF_FILE)
    hs = recall.health_section("kod")
    chk("довгий звіт ріжеться ЦІЛИМИ рядками в HEALTH_CAP з лічильником «ще N»", len(hs) <= recall.HEALTH_CAP and "ще " in hs and "--full" in hs and not hs.endswith("з довг"))
    # B2: довгий звіт не витісняє стан PreCompact і картку каналів
    with tempfile.TemporaryDirectory() as td2:
        _rs, _rc = recall.ROLE_STATE_DIR, recall.restore_card
        recall.ROLE_STATE_DIR = Path(td2)
        (Path(td2) / "kod.md").write_text("# Стан\n" + "\n".join(f"рядок стану {k:03d} " + "ж" * 80 for k in range(6)) + "\nКІНЕЦЬ_СТАНУ", encoding="utf-8")
        recall.restore_card = lambda role, cap=1200, **kw: "КАРТКА_КАНАЛІВ " + "к" * 700
        os.environ["PLUTUS_ROLE"] = "kod"
        try:
            ctx_txt = recall.session_context({"cwd": "C:/Users/smach", "source": "compact"})
        finally:
            recall.ROLE_STATE_DIR, recall.restore_card = _rs, _rc
            os.environ.pop("PLUTUS_ROLE", None)
    chk("B2: звіт стану + PreCompact-стан + картка каналів + ядро правил — усе вціліло у cap 3000", "КІНЕЦЬ_СТАНУ" in ctx_txt and "КАРТКА_КАНАЛІВ" in ctx_txt and "проблема номер" in ctx_txt
        and "ЯДРО ПРАВИЛ" in ctx_txt and len(ctx_txt) <= 3000)
_hcmod.LAST_BRIEF_FILE = Path(tempfile.gettempdir()) / "health_check_test_nonexistent_last.json"

# B4: сліпа перевірка не «знімає» проблем
old_state = {"open": {"vps:unit_failed:x.service": {"sev": "crit", "title": "X failed", "first": "t0", "last": "t0"}, "art:feed_stale:meta_feed.xml": {"sev": "warn", "title": "meta", "first": "t0", "last": "t0"},
                      "task:fail:T": {"sev": "warn", "title": "T", "first": "t0", "last": "t0"}}}
stale_issue = hc.issue("vps:stale", "crit", "VPS мовчить")
new, res, st = hc.diff_state([stale_issue], old_state, "t1", hc.blind_prefixes([stale_issue]))
chk("B4: «VPS мовчить» → failed-юніт VPS НЕ знято; чужі префікси (art:, task:) знімаються як звичайно",
    "vps:unit_failed:x.service" in st["open"] and sorted(x["id"] for x in res) == ["art:feed_stale:meta_feed.xml", "task:fail:T"])
broken = hc.issue("check:artifacts", "warn", "Перевірка artifacts не відпрацювала")
new, res, st = hc.diff_state([broken], old_state, "t1", hc.blind_prefixes([broken]))
chk("B4: збій перевірки artifacts → art:* лишається відкритою, не «знято»; vps/task знімаються", "art:feed_stale:meta_feed.xml" in st["open"] and "art:feed_stale:meta_feed.xml" not in [x["id"] for x in res])
new, res, st = hc.diff_state([stale_issue], st, "t2", hc.blind_prefixes([stale_issue]))
chk("B4: після відновлення знімка (без проблем) — тепер справді знято", sorted(x["id"] for x in hc.diff_state([], old_state, "t3", ())[1]) == sorted(old_state["open"]))
r = hc.check_vps(ctx(snap(generated_at=iso(5), units={"services": {}, "timers": {}, "failed": ["x.service"]})))
chk("мутація: після vps:stale знімку далі не віримо (failed-юніт зі старого знімка не з'являється)", ids(r) == {"vps:stale"})

# N3: вік коміту master — на момент знімка
r = hc.check_vps(ctx(snap(generated_at=iso(2), host_stats={"git_head": "a" * 40, "disk_used_pct": 10}), master=lambda: ("b" * 40, iso(1.9))))
chk("N3: коміт з'явився вже ПІСЛЯ знімка — не «відставання»", "vps:code_behind" not in ids(r))
r = hc.check_vps(ctx(snap(generated_at=iso(2), host_stats={"git_head": "a" * 40, "disk_used_pct": 10}), master=lambda: ("b" * 40, iso(2.9))))
chk("N3: на момент знімка коміту було 54 хв, VPS не на ньому → warn", "vps:code_behind" in ids(r))

# N4: файлу каналу нема
with tempfile.TemporaryDirectory() as td:
    chp = Path(td) / "NEW_CHANNEL.md"
    hc.post_to_channel("## [Автомат → Код] перший\nтекст\n", chp)
    chk("N4: файлу каналу нема → створюється, запис на місці", chp.exists() and "перший" in chp.read_text(encoding="utf-8"))

# N2: редакція нових форм секретів
import telegram_notify as tn  # noqa: E402
bad = ["https://api.telegram.org/bot123456789:AAHabcdefghijklmnopqrstuvwxyz0123456/sendMessage", "https://ghp_abcdefghijklmnopqrstuvwxyz0123456789@github.com/x",
       "ключ sk-ant-api03-abcdefghijklmnop", "{'Api-Key': 'SECRETVALUE123'}", "private_key=ABCSECRETDEF"]
red = [tn._redact(t) for t in bad]
chk("N2: токен бота в URL, ghp_, sk-ant-, Api-Key, private_key прибрано; звичайний текст не чіпаємо", not any(w in " ".join(red) for w in ("AAHabc", "ghp_abc", "sk-ant-api03-abc", "SECRETVALUE", "ABCSECRETDEF"))
    and tn._redact("замовлення 12345, сума 500 грн, key не задано") == "замовлення 12345, сума 500 грн, key не задано")
r = hc.group_alerts([{"at": "2026-11-01T10:00:00", "source": "x.py", "category": "error", "text": "🚨 збій https://ghp_abcdefghijklmnopqrstuvwxyz0123456789@github.com/x"}], "vps", NOW)
chk("N2: перший рядок алерта в заголовку проблеми проходить редакцію", "ghp_abc" not in r[0]["title"])

# N6/N7 vps_health: секції в try; вікно журналу — локальний час
_oh = vh.collect_host
vh.collect_host = lambda app_dir=vh.APP_DIR: (_ for _ in ()).throw(IndexError("x"))
try:
    snap_ = vh.collect(NOW, run=lambda c, timeout=30: "", app_dir="/nonexistent")
finally:
    vh.collect_host = _oh
chk("N6: збій однієї секції знімка → errors[], решта секцій є, знімок не втрачено", snap_["errors"] and snap_["errors"][0].startswith("host_stats") and snap_["units"] == {"services": {}, "timers": {}, "failed": []} and "alerts" in snap_)
chk("_age_h: наївний ISO = локальний час (мутація UTC-трактування)", abs(hc._age_h(datetime.now().replace(microsecond=0).isoformat(), datetime.now().astimezone())) < 0.01)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
