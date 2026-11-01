# -*- coding: utf-8 -*-
"""health_check.py — перевірка стану всієї системи сесією «Код» (рішення власника 01.11.2026).

ВЛАСНИК: «у Telegram нічого окрім замовлень — і це добре; мені не треба контролювати збої: усі збої на себе, контроль VPS теж на себе;
при пробудженні перше — перевірка стану; якщо без мене не виправити — доклад і що робити».

ЩО РОБИТЬ (лише читає; не змінює ні VPS, ні кабінети, ні фіди):
  1. VPS — знімок vps_health.json із приватної гілки ops-health (його публікує VPS щогодини, vps_health.py / publish_vps_health_private.sh):
     failed-сервіси, зупинені таймери, диск, чи доїхав master, збої/попередження з журналу алертів VPS. Знімок застарілий >3 год = «VPS мовчить».
  2. Опубліковані артефакти (публічний feed-data/feeds/feed_meta.json): фіди не старіші за прогін + запас; кількість позицій не нуль.
  3. Локальні Windows-задачі PlutusToys_*: помилка останнього запуску (вимкнені — окремо, інформаційно).
  4. Локальний журнал алертів reports/telegram_alerts.md (скрейпери, десктопні автоматики): збої за останню добу.
  5. Критичний календар (critical_status.json у спільній теці): прострочені/критичні пункти.

ВИХІД: стислий звіт (`--brief`), нові проблеми дописуються у EXECUTOR_CHANNEL.md угорі (`--post`, `## [Автомат → Код] Стан системи …`) один раз:
стан (.local_secrets/health_check_state.json) пам'ятає відкриті проблеми, повторно не пише; зникла проблема → «знято».
Код виходу: 0 — чисто, 1 — є попередження, 2 — є критичне.

ПРОТОКОЛ ПРОБУДЖЕННЯ (CLAUDE.md): перше — `python health_check.py --brief --post`. Що можна виправити самій (код, PR з аудитом, локальні задачі) — виправити.
Що без власника не виправити (вхід у кабінет, запис на VPS) — один доклад власнику: що зламано, чому, яка саме дія.
Сам скрипт НЕ мовчить про власні збої: кожна перевірка в try — збій перевірки стає проблемою `check:<ім'я>`.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent
SHARED = Path(os.environ.get("PLUTUS_SHARED_DIR") or r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya")
EXEC_CHANNEL = Path(os.environ.get("HEALTH_CHANNEL_FILE") or SHARED / "EXECUTOR_CHANNEL.md")
STATE_FILE = Path(os.environ.get("HEALTH_STATE_FILE") or REPO / ".local_secrets" / "health_check_state.json")
LAST_BRIEF_FILE = Path(os.environ.get("HEALTH_LAST_BRIEF_FILE") or REPO / ".local_secrets" / "health_check_last.json")
# Хук SessionStart має бюджет 15 с (session-start.sh: `timeout 15`), тож у хуку: спершу кеш звіту (його щогодини оновлює задача), потім «швидка» перевірка з короткими таймаутами.
FAST = os.environ.get("HEALTH_FAST") == "1"
T_NET = 5 if FAST else 45          # мережеві виклики (git fetch, HTTP)
T_PS = 8 if FAST else 60           # PowerShell Get-ScheduledTask
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}   # не зависати на запиті пароля, якщо токен у ремоуті не спрацював
PRIVATE_CLONE = Path(os.environ.get("HEALTH_PRIVATE_CLONE") or REPO / ".kodv_ledger_private")
FEED_META_URL = "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/feed_meta.json"
EVA_FEED_URL = "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/eva_feed.xml"
ALERT_WINDOW_HOURS = 26
# Пороги — з ВИМІРЯНИХ каденцій (SYSTEM_MAP.md): знімок VPS — щогодини (+запас на дві пропущені публікації); feed-pipeline стартує кожні 6 год
# і триває 3 год 11 хв – 4 год 30 хв (запас +1,5 год); eva_feed — щогодини (:30).
VPS_SNAPSHOT_MAX_AGE_H = 3
FEED_MAX_AGE_H = 12
EVA_FEED_MAX_AGE_H = 3
VPS_CODE_BEHIND_MIN = 45         # vps-code-sync підтягує master ~15 хв → три цикли
DISK_FULL_PCT = 95
OK_TASK_RESULTS = {0, 267008, 267009, 267011}   # 0 успіх; 0x41300 готова; 0x41301 виконується; 0x41303 ще не запускалась
SEV_ORDER = {"crit": 0, "warn": 1, "info": 2}
SEV_ICON = {"crit": "🔴", "warn": "🟠", "info": "ℹ️"}
FEED_KEYS = ("google_merchant_feed.xml", "meta_feed.xml", "bing_feed.xml", "prom_feed_top.xml")

KEEPALIVE_HINT = ("сесія кабінету протухла: потрібен ручний вхід власника в кабінет (скрейпер сам не логіниться); "
                  "після входу задача сама відновиться")


def issue(iid, sev, title, detail="", hint="", owner=False):
    return {"id": iid, "sev": sev, "title": title, "detail": detail, "hint": hint, "owner": owner}


def _hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]


def _age_h(iso: str, now: datetime):
    """Вік у годинах для ISO-часу; без таймзони — локальний час цієї машини (VPS і десктоп обидва в Києві). None, якщо не розібрати."""
    try:
        t = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.astimezone()
    return (now - t).total_seconds() / 3600


def _to_dt(iso, default):
    try:
        t = datetime.fromisoformat(iso)
        return t if t.tzinfo else t.astimezone()
    except (TypeError, ValueError):
        return default


def headline(text: str) -> str:
    """Перший змістовний рядок алерта, цифри згорнуті — щоб «так само, але з іншим числом» був одним ключем."""
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if ln:
            return re.sub(r"\d+", "N", ln)[:110]
    return ""


def group_alerts(alerts, prefix, now, source_label=""):
    """alerts: [{at, source, category, text}] → проблеми за ключем (джерело, заголовок). error → warn, решта → info; «✅ …» (відновлення) пропускаємо."""
    groups = {}
    for a in alerts:
        t = (a.get("text") or "").lstrip()
        if not t or t.startswith("✅") or t.startswith("🛒"):
            continue
        key = (a.get("source", "?"), headline(t))
        g = groups.setdefault(key, {"n": 0, "last": "", "cat": a.get("category"), "text": t})
        g["n"] += 1
        if a.get("at", "") >= g["last"]:
            g["last"], g["text"] = a.get("at", ""), t
        if a.get("category") == "error":
            g["cat"] = "error"
    out = []
    try:
        from telegram_notify import _redact   # у канал/звіт іде перший рядок алерта — теж після редакції секретів
    except Exception:  # noqa: BLE001
        def _redact(x):
            return "[текст приховано: редакція секретів недоступна]"
    for (src, head), g in groups.items():
        first = _redact(g["text"].splitlines()[0])[:160] if g["text"] else head
        out.append(issue(f"{prefix}:alert:{_hash(src + '|' + head)}", "warn" if g["cat"] == "error" else "info",
                         f"{source_label}{src}: {first}", f"{g['n']}× за добу, остання {g['last']}",
                         "розібрати причину за текстом алерта; якщо це відома тимчасова ситуація — вона зникне з вікна доби сама"))
    return out


# ---------- джерела даних (підміняються в тестах) ----------

def fetch_vps_snapshot(clone: Path = PRIVATE_CLONE):
    """→ (dict|None, помилка|None). УВАГА: stderr git НЕ виводимо — у ньому може бути URL ремоуту з токеном."""
    if not (clone / ".git").exists():
        return None, "нема локального клону приватного репо (.kodv_ledger_private)"
    try:
        r = subprocess.run(["git", "-C", str(clone), "fetch", "-q", "origin", "ops-health"], capture_output=True, text=True, timeout=T_NET, errors="replace", env=_GIT_ENV)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"fetch не виконано ({type(e).__name__})"
    if r.returncode != 0:
        if "find remote ref" in (r.stderr or "").lower():
            return None, "гілка ops-health ще не створена: VPS не опублікував жодного знімка"
        return None, f"fetch не вдався (код {r.returncode})"
    try:
        s = subprocess.run(["git", "-C", str(clone), "show", "origin/ops-health:vps_health.json"], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        return json.loads(s.stdout), None
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return None, f"знімок не прочитано ({type(e).__name__})"


def fetch_master():
    """→ (sha, iso-час коміту) tip origin/master цього репо."""
    subprocess.run(["git", "-C", str(REPO), "fetch", "-q", "origin", "master"], capture_output=True, timeout=T_NET, env=_GIT_ENV)
    sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "origin/master"], capture_output=True, text=True, timeout=15).stdout.strip()
    ts = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%cI", "origin/master"], capture_output=True, text=True, timeout=15).stdout.strip()
    return sha, ts


def fetch_feed_meta():
    req = urllib.request.Request(FEED_META_URL, headers={"User-Agent": "plutustoys-health-check"})
    return json.loads(urllib.request.urlopen(req, timeout=T_NET).read().decode("utf-8"))


def fetch_eva_feed_date():
    """Свіжість погодинного EVA-фіда — з `<yml_catalog date="…">` на початку самого файлу (час VPS = Київ). feed_meta.json оновлюється лише
    раз на 6 год (feed-pipeline), погодинний publish_eva_feed_vps.sh його не чіпає — по ньому «EVA застарів» було б хибним (аудит #1068 B3)."""
    req = urllib.request.Request(EVA_FEED_URL, headers={"User-Agent": "plutustoys-health-check"})
    head = urllib.request.urlopen(req, timeout=T_NET).read(3000).decode("utf-8", errors="replace")
    m = re.search(r'yml_catalog date="([^"]+)"', head)
    return m.group(1) if m else None


def fetch_tasks():
    ps = ("Get-ScheduledTask -TaskName 'PlutusToys_*' | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
          "[pscustomobject]@{n=$_.TaskName; s=[string]$_.State; r=[int64]$i.LastTaskResult} } | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True, timeout=T_PS, errors="replace").stdout.strip()
    data = json.loads(out) if out else []
    return [data] if isinstance(data, dict) else data


def read_local_alerts(now: datetime, path: Path = None):
    sys.path.insert(0, str(REPO))
    from telegram_digest import parse_log
    cut = now.replace(tzinfo=None) - timedelta(hours=ALERT_WINDOW_HOURS)
    res = []
    for e in parse_log(path or REPO / "reports" / "telegram_alerts.md"):
        if e["dt"] is not None and e["dt"] >= cut and e["category"] in ("error", "warning", "money"):
            res.append({"at": e["dt"].isoformat(timespec="seconds"), "source": e["source"], "category": e["category"], "text": e["text"]})
    return res


def read_critical(path: Path = None):
    return json.loads((path or SHARED / "reports" / "critical_status.json").read_text(encoding="utf-8"))


class Ctx:
    """Пакет джерел; тести підставляють свої функції."""
    def __init__(self, now=None, **over):
        self.now = now or datetime.now(timezone.utc).astimezone()
        self.vps_snapshot, self.master, self.feed_meta, self.eva_date = fetch_vps_snapshot, fetch_master, fetch_feed_meta, fetch_eva_feed_date
        self.tasks, self.local_alerts, self.critical = fetch_tasks, (lambda: read_local_alerts(self.now)), read_critical
        for k, v in over.items():
            setattr(self, k, v)


# ---------- перевірки ----------

def check_vps(ctx):
    snap, err = ctx.vps_snapshot()
    if snap is None:
        return [issue("vps:no_snapshot", "warn", f"Знімок VPS недоступний: {err}", "",
                      "знімок з'являється після мержу PR health-монітора (VPS підтягне код ~15 хв) і першого погодинного EVA-циклу (:30); "
                      "якщо давно — перевірити publish_vps_health_private.sh і deploy-ключ приватного репо")]
    out = []
    age = _age_h(snap.get("generated_at"), ctx.now)
    if age is None or age > VPS_SNAPSHOT_MAX_AGE_H:
        out.append(issue("vps:stale", "crit", f"VPS мовчить: знімок застарів ({'невідомо' if age is None else f'{age:.1f} год'})",
                         f"останній знімок {snap.get('generated_at')}",
                         "публікує його eva-feed щогодини: або зупинився eva-feed/таймер, або не працює публікація (ключ/мережа), або VPS недоступний; "
                         "на VPS мені писати не можна — потрібен власник (read-only: journalctl -u eva-feed)", owner=True))
        return out    # решта знімка вже не «зараз»
    if snap.get("errors"):
        out.append(issue("vps:snapshot_errors", "warn", "Знімок VPS зібрано з помилками: " + "; ".join(str(x) for x in snap["errors"])[:200], "", "порожні секції знімка НЕ означають «усе гаразд»: виправити vps_health.py/доступ до systemctl"))
    u = snap.get("units") or {}
    if not u.get("services"):
        out.append(issue("vps:no_units", "warn", "Знімок VPS без жодного сервісу проєкту (systemctl недоступний або змінився ExecStart)", "", "перевірити vps_health.collect_units на VPS"))
    for name in u.get("failed") or []:
        out.append(issue(f"vps:unit_failed:{name}", "crit", f"VPS: {name} у стані failed", "systemctl --failed", "розібрати журнал юніта (потрібен read-only витяг від власника, якщо причини нема в алертах знімка)", owner=True))
    seen = {i["id"] for i in out}
    for name, s in (u.get("services") or {}).items():
        bad = s.get("active") == "failed" or (s.get("result") not in (None, "", "success") and s.get("active") != "activating")
        iid = f"vps:unit_failed:{name}"
        if bad and iid not in seen:
            out.append(issue(iid, "crit", f"VPS: {name}: {s.get('result')} (код {s.get('exit_status')})", f"останнє завершення {s.get('last_exit_utc')}", "розібрати журнал юніта", owner=True))
    for name, t in (u.get("timers") or {}).items():
        if t.get("enabled") == "enabled" and t.get("active") != "active":
            out.append(issue(f"vps:timer_down:{name}", "warn", f"VPS: таймер {name} увімкнений, але не активний ({t.get('active')}/{t.get('sub')})", "", "перезапуск таймера — запис на VPS, робить власник", owner=True))
    h = snap.get("host_stats") or {}
    if (h.get("disk_used_pct") or 0) >= DISK_FULL_PCT:
        out.append(issue("vps:disk", "crit", f"VPS: диск заповнений на {h['disk_used_pct']}% (вільно {h.get('disk_free_gb')} ГБ)", "", "потрібне очищення на VPS — запис, робить власник; назву великих файлів дам, коли знімок їх покаже", owner=True))
    try:
        sha, ts = ctx.master()
        if sha and h.get("git_head") and h["git_head"] != sha:
            # вік коміту — на момент ЗНІМКА, а не «зараз»: знімок може бути старшим на годину, а VPS уже синхронізований (аудит #1068 N3)
            mage = _age_h(ts, _to_dt(snap.get("generated_at"), ctx.now))
            if mage is not None and mage * 60 > VPS_CODE_BEHIND_MIN:
                out.append(issue("vps:code_behind", "warn", f"VPS не підтягнув master: на VPS {h['git_head'][:7]}, у master {sha[:7]} (на момент знімка коміту було {mage * 60:.0f} хв)", "", "перевірити vps-code-sync (vps_code_sync_state.json у знімку); можливий локальний розбіг гілки на VPS"))
    except Exception:  # noqa: BLE001
        pass   # master недоступний — це не збій VPS
    fp = (snap.get("state_files") or {}).get("feed_pipeline_state.json") or {}
    if fp.get("last_status") in ("failed", "degraded"):
        out.append(issue(f"vps:feed_pipeline:{fp['last_status']}", "crit" if fp["last_status"] == "failed" else "warn",
                         f"feed-pipeline: статус {fp['last_status']}", str(fp.get("reason") or "")[:200], "причина — у полі reason; репрайсер/топ-фід провалились, фолбек публікує попередню версію"))
    out += group_alerts(snap.get("alerts") or [], "vps", ctx.now, "VPS ")
    return out


def check_artifacts(ctx):
    meta = ctx.feed_meta()
    out = []
    for k in FEED_KEYS + ("eva_feed.xml",):
        m = meta.get(k)
        if not m:
            out.append(issue(f"art:feed_missing:{k}", "warn", f"У feed_meta нема {k}", "", "перевірити publish-скрипти"))
            continue
        if k != "eva_feed.xml":    # EVA — нижче, за датою у самому файлі (feed_meta для нього оновлюється раз на 6 год)
            age = _age_h(m.get("generated_at"), ctx.now)
            if age is None or age > FEED_MAX_AGE_H:
                out.append(issue(f"art:feed_stale:{k}", "warn", f"Фід {k} застарів: {'невідомо' if age is None else f'{age:.1f} год'} (ліміт {FEED_MAX_AGE_H} год)", f"generated_at {m.get('generated_at')}", "feed-pipeline на VPS не публікує вчасно; деталі — у знімку VPS", owner=True))
        if not m.get("offers"):
            out.append(issue(f"art:feed_empty:{k}", "crit", f"Фід {k}: 0 позицій", "", "порожній фід не має публікуватись (feed_publish_gate) — перевірити", owner=True))
    try:
        eva = ctx.eva_date()
    except Exception as e:  # noqa: BLE001
        out.append(issue("art:eva_unreadable", "warn", f"Дату EVA-фіда не прочитано: {type(e).__name__}", "", "мережа до raw.githubusercontent.com або формат файлу"))
        return out
    # дата в файлі — час VPS без зони (Київ); якщо пояс VPS зміниться на UTC, вік стане хибним — тоді змінити джерело
    age = _age_h(eva.replace(" ", "T") if eva else None, ctx.now)
    if age is None or age > EVA_FEED_MAX_AGE_H:
        out.append(issue("art:feed_stale:eva_feed.xml", "warn", f"EVA-фід застарів: {'дату в файлі не прочитано' if age is None else f'{age:.1f} год'} (ліміт {EVA_FEED_MAX_AGE_H} год)", f"yml_catalog date {eva}", "погодинний eva-feed на VPS не публікує; деталі — у знімку VPS", owner=True))
    return out


def intentionally_disabled(path: Path = None) -> set:
    """Задачі, які власник/Код ВИМКНУЛИ навмисно (реєстр `local_tasks_disabled` у SYSTEM_MAP.md §6) — про них не нагадуємо щоразу."""
    try:
        txt = (path or REPO / "SYSTEM_MAP.md").read_text(encoding="utf-8")
        m = re.search(r'"local_tasks_disabled"\s*:\s*\[(.*?)\]', txt, re.S)
        return set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()
    except OSError:
        return set()


def check_tasks(ctx):
    out = []
    skip = intentionally_disabled()
    for t in ctx.tasks():
        n, st, r = t.get("n"), t.get("s"), t.get("r")
        if st == "Disabled":
            if n in skip:
                continue
            out.append(issue(f"task:disabled:{n}", "info", f"Локальна задача {n} вимкнена", "", "чи вимкнена навмисно — якщо так, ігнорувати; якщо ні — увімкнути"))
        elif r not in OK_TASK_RESULTS:
            keep = n.endswith("Keepalive")
            out.append(issue(f"task:fail:{n}", "warn", f"Локальна задача {n}: помилка останнього запуску (код {r})", "",
                             KEEPALIVE_HINT if keep else "дивитись вихід задачі / reports/*_failure_*", owner=keep))
    return out


def check_local_journal(ctx):
    return group_alerts(ctx.local_alerts(), "loc", ctx.now, "Десктоп ")


def check_critical(ctx):
    d = ctx.critical()
    out = []
    age = _age_h(d.get("generated_at"), ctx.now)
    if age is None or age > 3:
        out.append(issue("crit:stale", "info", "Критичний календар не оновлюється", f"generated_at {d.get('generated_at')}", "перевірити задачу PlutusToys_CriticalCalendar"))
    for e in d.get("events") or []:
        if e.get("state") == "critical":
            # пункт, що блокує лише ручний вхід власника (поле blocks починається «лише ручний…»), автоматику не зупиняє → info, не «потрібен власник» щоразу
            manual = str(e.get("blocks") or "").startswith("лише ручний")
            out.append(issue(f"crit:{e.get('id')}", "info" if manual else "warn", f"Критичний календар: {e.get('name')}: {e.get('detail')}", "", (e.get("renewal") or "")[:200], owner=not manual))
    return out


CHECKS = (("vps", check_vps), ("artifacts", check_artifacts), ("tasks", check_tasks), ("local_journal", check_local_journal), ("critical", check_critical))


def _run_one(ctx, name, fn):
    try:
        return fn(ctx)
    except Exception as e:  # noqa: BLE001
        if FAST:   # хук SessionStart: короткі таймаути під навантаженням дають хибні збої — не пишемо їх у канал/кеш, лише «не перевірено» (аудит #1068 B6)
            return [issue(f"check:{name}", "info", f"«{name}»: не перевірено у швидкому режимі ({type(e).__name__})", "", "повна перевірка — `python health_check.py --full`")]
        # текст винятку підпроцесу (TimeoutExpired тощо) несе повну команду — у звіт лише тип; для решти — короткий текст
        detail = "" if isinstance(e, subprocess.SubprocessError) else str(e)[:160].replace("\n", " ")
        return [issue(f"check:{name}", "warn", f"Перевірка «{name}» не відпрацювала: {type(e).__name__}", detail, "спостерігач не має мовчати про власний збій — виправити перевірку")]


def run_checks(ctx):
    """П'ять перевірок паралельно (мережа/PowerShell/git незалежні): загальний час = найповільніша, а не сума — хук SessionStart має бюджет 15 с."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(CHECKS)) as ex:
        futs = [ex.submit(_run_one, ctx, name, fn) for name, fn in CHECKS]
        issues = [i for f in futs for i in f.result()]
    return sorted(issues, key=lambda i: (SEV_ORDER[i["sev"]], i["id"]))


# ---------- стан, канал, звіт ----------

def load_state(path: Path = None) -> dict:
    try:
        d = json.loads((path or STATE_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("open"), dict) else {"open": {}}
    except (OSError, ValueError):
        return {"open": {}}


def save_state(state: dict, path: Path = None) -> None:
    p = path or STATE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


BLIND_PREFIX = {"vps": ("vps:",), "artifacts": ("art:",), "tasks": ("task:",), "local_journal": ("loc:",), "critical": ("crit:",)}
BLIND_IDS = {"vps:stale": ("vps:",), "vps:no_snapshot": ("vps:",)}   # знімок застарів/нема → стан VPS невідомий, «знято» писати не можна


def blind_prefixes(issues) -> tuple:
    """Префікси проблем, про які ЗАРАЗ нічого не відомо: перевірка впала (`check:<ім'я>`) або знімок VPS недоступний. Їхні відкриті проблеми НЕ «знято» (аудит #1068 B4)."""
    pref = []
    for i in issues:
        if i["id"].startswith("check:"):
            pref += BLIND_PREFIX.get(i["id"][6:], ())
        pref += BLIND_IDS.get(i["id"], ())
    return tuple(set(pref))


def diff_state(issues, state, now_iso, blind=()):
    """→ (нові, знято, новий_стан). Нова = id не був відкритий, або серйозність зросла. Відкриті проблеми з префіксом із `blind` лишаються відкритими."""
    old = state.get("open", {})
    new_open, new, resolved = {}, [], []
    for i in issues:
        prev = old.get(i["id"])
        if prev is None or SEV_ORDER[i["sev"]] < SEV_ORDER.get(prev.get("sev"), 9):
            new.append(i)
            new_open[i["id"]] = {"sev": i["sev"], "title": i["title"], "first": now_iso, "last": now_iso}
        else:
            new_open[i["id"]] = {**prev, "sev": i["sev"], "title": i["title"], "last": now_iso}
    for iid, prev in old.items():
        if iid not in new_open:
            if blind and iid.startswith(blind):
                new_open[iid] = prev     # перевірка сліпа — стан не знаємо, лишаємо як було
            elif prev.get("sev") != "info":
                resolved.append({"id": iid, "title": prev.get("title", iid)})
    return new, resolved, {"open": new_open, "last_run": now_iso}


def format_issue(i, full=False) -> str:
    own = " · 👤 потрібен власник" if i["owner"] else ""
    s = f"{SEV_ICON[i['sev']]} {i['title']}{own}"
    if full:
        if i["detail"]:
            s += f" — {i['detail']}"
        if i["hint"]:
            s += f"\n   ↳ {i['hint']}"
    return s


def channel_entry(new, resolved, now: datetime) -> str:
    lines = [f"## [Автомат → Код] Стан системи {now:%Y-%m-%d %H:%M}: нових {len([i for i in new if i['sev'] != 'info'])}, знято {len(resolved)}", ""]
    for i in new:
        if i["sev"] != "info":
            lines.append("- " + format_issue(i, full=True))
    for r in resolved:
        lines.append(f"- ✅ знято: {r['title']}")
    lines += ["", "Джерело: health_check.py (знімок VPS у приватній гілці ops-health, feed_meta, задачі PlutusToys_*, журнал алертів, критичний календар). Дія — Коду: за протоколом пробудження виправити або доповісти власнику.", "", ""]
    return "\n".join(lines)


def post_to_channel(text: str, path: Path = None) -> None:
    p = path or EXEC_CHANNEL
    b = p.read_bytes() if p.exists() else b""
    e = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    m = re.search(rb"(?m)^## ", b)
    new = (b[:m.start()] + e + b[m.start():]) if m else (b + b"\r\n" + e)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(new)
    tmp.replace(p)


def save_last_brief(text: str, now: datetime, path: Path = None) -> None:
    """Кеш останнього звіту: хук SessionStart (бюджет 15 с) показує його без мережі; щогодини оновлює задача PlutusToys_HealthCheck."""
    p = path or LAST_BRIEF_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps({"at": now.isoformat(timespec="seconds"), "brief": text}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def load_last_brief(path: Path = None):
    try:
        d = json.loads((path or LAST_BRIEF_FILE).read_text(encoding="utf-8"))
        return d["at"], d["brief"]
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def brief(issues, now: datetime, limit=12) -> str:
    c = {s: sum(1 for i in issues if i["sev"] == s) for s in SEV_ORDER}
    own = sum(1 for i in issues if i["owner"] and i["sev"] != "info")
    head = f"🩺 Стан системи {now:%Y-%m-%d %H:%M}: 🔴 {c['crit']} · 🟠 {c['warn']} · ℹ️ {c['info']}" + (f" (потрібен власник: {own})" if own else "") + (" — усе чисто" if not issues else "")
    rows = [format_issue(i) for i in issues if i["sev"] != "info"][:limit]
    unchecked = [i["id"][6:] for i in issues if i["id"].startswith("check:") and i["sev"] == "info"]
    if unchecked:
        rows.append("ℹ️ не перевірено (швидкий режим): " + ", ".join(unchecked) + " — `python health_check.py --full`")
    return "\n".join([head] + rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--brief", action="store_true", help="стислий звіт у stdout")
    ap.add_argument("--full", action="store_true", help="повний звіт зі підказками «що робити»")
    ap.add_argument("--post", action="store_true", help="дописати НОВІ проблеми у EXECUTOR_CHANNEL.md і оновити стан")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    ctx = Ctx()
    issues = run_checks(ctx)
    now_iso = ctx.now.isoformat(timespec="seconds")
    new, resolved, state = diff_state(issues, load_state(), now_iso, blind_prefixes(issues))
    if a.post:
        try:
            if any(i["sev"] != "info" for i in new) or resolved:
                post_to_channel(channel_entry(new, resolved, ctx.now))
            save_state(state)       # стан зберігаємо ЛИШЕ після вдалого запису в канал: інакше нова проблема «загубиться» без сліду
        except OSError as e:
            print(f"⚠️ запис у канал не вдався ({type(e).__name__}) — стан не оновлено, наступний запуск спробує знову", file=sys.stderr)
    save_last_brief(brief(issues, ctx.now), ctx.now)
    if a.json:
        print(json.dumps(issues, ensure_ascii=False, indent=1))
    elif a.full:
        print(brief(issues, ctx.now, limit=0))
        for i in issues:
            if i["sev"] != "info":
                print(format_issue(i, full=True))
    else:
        print(brief(issues, ctx.now))
    return 2 if any(i["sev"] == "crit" for i in issues) else 1 if any(i["sev"] == "warn" for i in issues) else 0


if __name__ == "__main__":
    sys.exit(main())
