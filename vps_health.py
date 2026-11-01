# -*- coding: utf-8 -*-
"""vps_health.py — знімок стану VPS для сесії «Код» (рішення власника 01.11.2026: «усі збої — на тебе, контроль на VPS теж на тебе»).

ПРОБЛЕМА. З 12.10 у Telegram іде лише «🛒 Нове замовлення» (telegram_triage); решта алертів VPS пишеться лише у /opt/plutustoys/reports/telegram_alerts.md НА VPS.
Сесія «Код» VPS не бачить (SSH їй закритий), тож збої VPS не бачив ніхто, крім власника. Цей скрипт — VPS-половина: раз на годину збирає знімок і
публікує його в ПРИВАТНУ гілку `ops-health` репо plutustoys-private-ledger (publish_vps_health_private.sh). Десктопна половина — health_check.py.

ЩО В ЗНІМКУ (лише читання, нічого на VPS не змінює):
  • systemd: стан КОЖНОГО сервісу з ExecStart у /opt/plutustoys (Result, код виходу, час останнього завершення), стан їхніх таймерів, `systemctl --failed`;
  • хост: диск `/`, пам'ять, навантаження, uptime; git HEAD у /opt/plutustoys (чи доїхав master);
  • файли стану, що вже є на VPS (feed_pipeline_state.json, vps_code_sync_state.json, watchdog_state.json);
  • алерти за останню добу з telegram_alerts.md (не «нове замовлення»), текст — після telegram_notify._redact і обрізаний до 300 символів.
Репо ПРИВАТНЕ (як plutustoys-private-ledger): текст алертів може містити гривні/id замовлень — у публічний feed-data його класти НЕ можна (витік 10.08).

ЗАПУСК: python vps_health.py --out /opt/plutustoys/reports/vps_health.json
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
APP_DIR = os.environ.get("VPS_HEALTH_APP_DIR") or "/opt/plutustoys"
ALERT_WINDOW_HOURS = 26          # добу + запас на інтервал публікації
ALERT_TEXT_MAX = 300
ALERT_MAX_ENTRIES = 60
SHOW_PROPS = ("Id", "ActiveState", "SubState", "Result", "ExecMainStatus", "ExecMainExitTimestamp", "ExecMainStartTimestamp",
              "Type", "UnitFileState", "NextElapseUSecRealtime", "LastTriggerUSec", "ExecStart")
STATE_FILES = ("feed_pipeline_state.json", "vps_code_sync_state.json", "watchdog_state.json")
_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _run(cmd, timeout=30) -> str:
    """Виводить stdout команди; будь-який збій → '' (знімок best-effort: відсутність даних видно на десктопі як «не змогли зібрати»)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
        return p.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_systemd_ts(s: str):
    """'Sun 2026-11-01 08:20:31 EET' → ISO UTC. Час systemd — локальний час хоста, на якому ми й працюємо; порожнє/'n/a' → None."""
    m = _TS_RE.search(s or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def parse_show_blocks(text: str) -> list:
    """Вивід `systemctl show a b c -p …`: блоки Key=Value, розділені порожнім рядком → список dict."""
    blocks, cur = [], {}
    for line in (text or "").splitlines():
        if not line.strip():
            if cur:
                blocks.append(cur)
                cur = {}
            continue
        k, sep, v = line.partition("=")
        if sep:
            cur[k] = v
    if cur:
        blocks.append(cur)
    return blocks


def collect_units(run=_run, app_dir: str = APP_DIR) -> dict:
    """Сервіси/таймери проєкту: ті, чий ExecStart містить app_dir (системні таймери apt/fstrim/logrotate відсіюються самі)."""
    names = [ln.split()[0] for ln in run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain", "--no-pager"]).splitlines() if ln.split()]
    names = [n for n in names if n.endswith(".service")]
    props = ",".join(SHOW_PROPS)
    services = {}
    if names:
        for b in parse_show_blocks(run(["systemctl", "show", "--no-pager", f"--property={props}", *names], timeout=60)):
            if app_dir not in b.get("ExecStart", ""):
                continue
            sid = b.get("Id", "")
            services[sid] = {"active": b.get("ActiveState"), "sub": b.get("SubState"), "result": b.get("Result"), "exit_status": b.get("ExecMainStatus"),
                             "type": b.get("Type"), "last_exit_utc": parse_systemd_ts(b.get("ExecMainExitTimestamp")),
                             "last_start_utc": parse_systemd_ts(b.get("ExecMainStartTimestamp"))}
    timers = {}
    tnames = [f"{sid[:-len('.service')]}.timer" for sid in services]
    if tnames:
        for b in parse_show_blocks(run(["systemctl", "show", "--no-pager", "--property=Id,ActiveState,SubState,UnitFileState,NextElapseUSecRealtime,LastTriggerUSec", *tnames], timeout=60)):
            if b.get("ActiveState") == "inactive" and b.get("UnitFileState") in ("", "not-found", None) and not b.get("NextElapseUSecRealtime"):
                continue   # такого таймера нема (сервіс запускається інакше)
            timers[b.get("Id", "")] = {"active": b.get("ActiveState"), "sub": b.get("SubState"), "enabled": b.get("UnitFileState"),
                                       "next": parse_systemd_ts(b.get("NextElapseUSecRealtime")), "last_trigger_utc": parse_systemd_ts(b.get("LastTriggerUSec"))}
    failed = [ln.split()[0] for ln in run(["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"]).splitlines() if ln.split()]
    return {"services": services, "timers": timers, "failed": failed}


def collect_host(app_dir: str = APP_DIR) -> dict:
    out = {}
    try:
        du = shutil.disk_usage("/")
        out["disk_used_pct"] = round(100 * du.used / du.total, 1)
        out["disk_free_gb"] = round(du.free / 1e9, 1)
    except OSError:
        pass
    try:
        mem = {}
        for ln in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = ln.partition(":")
            mem[k] = int(v.split()[0])
        out["mem_available_mb"] = mem["MemAvailable"] // 1024
        out["mem_total_mb"] = mem["MemTotal"] // 1024
        out["uptime_h"] = round(float(Path("/proc/uptime").read_text().split()[0]) / 3600, 1)
        out["load1"] = os.getloadavg()[0]
    except (OSError, KeyError, ValueError, AttributeError):
        pass
    head = _run(["git", "-C", app_dir, "rev-parse", "HEAD"]).strip()
    if head:
        out["git_head"] = head
        out["git_head_time"] = _run(["git", "-C", app_dir, "log", "-1", "--format=%cI"]).strip()
    return out


def collect_state_files(app_dir: str = APP_DIR) -> dict:
    out = {}
    for n in STATE_FILES:
        p = Path(app_dir) / n
        try:
            if p.stat().st_size <= 20000:
                out[n] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return out


def collect_alerts(now: datetime, app_dir: str = APP_DIR, path=None) -> list:
    """Алерти за ALERT_WINDOW_HOURS з журналу VPS (категорії error/warning/money, крім «🛒 Нове замовлення»), найновіші першими."""
    sys.path.insert(0, str(BASE))
    try:
        from telegram_digest import parse_log
    except Exception:  # noqa: BLE001
        return []
    try:
        from telegram_notify import _redact
    except Exception:  # noqa: BLE001
        _redact = None
    cut = now.astimezone().replace(tzinfo=None) - timedelta(hours=ALERT_WINDOW_HOURS)   # журнал пишеться ЛОКАЛЬНИМ часом хоста (не UTC)
    entries = parse_log(Path(path or Path(app_dir) / "reports" / "telegram_alerts.md"))
    keep = []
    for e in entries:
        if e["dt"] is None or e["dt"] < cut or e["category"] not in ("error", "warning", "money"):
            continue
        t = e["text"].lstrip()
        if t.startswith("🛒 Нове замовлення"):
            continue
        t = _redact(t) if _redact else t
        keep.append({"at": e["dt"].isoformat(timespec="seconds"), "source": e["source"], "category": e["category"],
                     "muted_in_telegram": "[не надіслано в Telegram" in e["text"], "text": t[:ALERT_TEXT_MAX]})
    keep.sort(key=lambda x: x["at"], reverse=True)
    return keep[:ALERT_MAX_ENTRIES]


def collect(now: datetime = None, run=_run, app_dir: str = APP_DIR) -> dict:
    """Кожна секція в своєму try: несподівана помилка в одній не губить решту знімка (інакше десктоп побачить «VPS мовчить» замість часткових даних)."""
    now = now or datetime.now(timezone.utc)
    out = {"schema": 1, "generated_at": now.isoformat(timespec="seconds"), "host": os.uname().nodename if hasattr(os, "uname") else "?", "errors": []}
    for key, fn, default in (("units", lambda: collect_units(run, app_dir), {"services": {}, "timers": {}, "failed": []}), ("host_stats", lambda: collect_host(app_dir), {}),
                             ("state_files", lambda: collect_state_files(app_dir), {}), ("alerts", lambda: collect_alerts(now, app_dir), [])):
        try:
            out[key] = fn()
        except Exception as e:  # noqa: BLE001
            out[key] = default
            out["errors"].append(f"{key}: {type(e).__name__}: {str(e)[:120]}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(APP_DIR) / "reports" / "vps_health.json"))
    a = ap.parse_args()
    data = collect()
    tmp = a.out + ".tmp"
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, a.out)
    u = data["units"]
    print(f"[vps_health] сервісів {len(u['services'])}, таймерів {len(u['timers'])}, failed {len(u['failed'])}, алертів {len(data['alerts'])} → {a.out}")


if __name__ == "__main__":
    main()
