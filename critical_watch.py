"""critical_watch.py — монітор критичних подій PlutusToys (терміни ключів, баланси,
абонплати) зі світлофором і проактивним попередженням «за N днів».

НАВІЩО (2026-08-25, пряме рішення власника): Telegram-алерти постфактум стали шумом і
не ловляться завчасно. Треба: (1) попереджати ЗА 3 ДНІ до критичних подій, (2) внутрішній
монітор-стан для гейта, що блокує ризиковані дії, поки критику не знято. Замість шуму —
візуальне ЛОКАЛЬНЕ вікно (critical_calendar.html), що само лізе в очі лише коли треба.

Переглядає давнє рішення «порогів не ставимо, лише тенденція» (01.08): weekly_balance_digest.py
лишається трендом БЕЗ порогів, а цей модуль ДОДАЄ пороги/дати/гейт-стан поверх тих самих даних.

ДЖЕРЕЛА значень:
- balance_threshold з balance_platform/balance_field → ЖИВЕ з balance_history.jsonl (де є скрейпер).
- manual_value / manual date → поки джерела нема (TODO: авточитання Checkbox тощо).
- ручна дата, що минула без підтвердження → 'звірити' (🔴), НЕ тихо зеленіє.

ВИХІД:
- reports/critical_calendar.html — самодостатнє локальне вікно (світлофор, авто-refresh).
- reports/critical_status.json — машинний стан для майбутнього гейта order-pipeline.
- stdout — однорядкове зведення.

Запуск (локально, напр. Task Scheduler щогодини):
    python critical_watch.py
"""
import html
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

# Реальне середовище власника — cp1251-консоль без PYTHONUTF8: print() з ₴/кирилицею
# інакше падає UnicodeEncodeError (той самий запобіжник, що в orders_watcher.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
OUT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (BASE_DIR / "reports"))
EVENTS_FILE = BASE_DIR / "critical_events.json"
HISTORY_FILE = OUT_DIR / "balance_history.jsonl"
HTML_FILE = OUT_DIR / "critical_calendar.html"
STATUS_FILE = OUT_DIR / "critical_status.json"

DEFAULT_LEAD_DAYS = 3

# Стан у порядку тяжкості (для зведення й гейта).
CRITICAL, WARN, OK = "critical", "warn", "ok"
_SEVERITY = {CRITICAL: 2, WARN: 1, OK: 0}


def _load_events() -> list:
    try:
        data = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[critical_watch] не читається {EVENTS_FILE}: {e}")
        return []
    return data.get("events", []) if isinstance(data, dict) else []


def _latest_balance(platform: str, field: str):
    """Останнє значення field для platform з balance_history.jsonl. (value, ts) або (None, None)."""
    if not HISTORY_FILE.exists():
        return None, None
    best_ts, best_val = None, None
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if str(row.get("platform")) != platform or field not in row:
            continue
        ts = str(row.get("ts") or "")
        if best_ts is None or ts > best_ts:
            best_ts, best_val = ts, row.get(field)
    return best_val, best_ts


def _next_monthly(day_of_month: int, today: date) -> date:
    """Найближча майбутня (або сьогоднішня) дата з заданим числом місяця."""
    y, m = today.year, today.month
    for _ in range(2):
        try:
            cand = date(y, m, day_of_month)
        except ValueError:
            # число більше за к-сть днів у місяці (напр. 31) — беремо останній день
            nm_y, nm_m = (y + 1, 1) if m == 12 else (y, m + 1)
            cand = date(nm_y, nm_m, 1)
        if cand >= today:
            return cand
        m, y = (1, y + 1) if m == 12 else (m + 1, y)
    return today


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _eval_event(ev: dict, today: date) -> dict:
    """Обчислює стан однієї події. Повертає {id,name,icon,state,detail,blocks,source,note}."""
    etype = ev.get("type")
    lead = int(ev.get("lead_days", DEFAULT_LEAD_DAYS))
    out = {"id": ev.get("id"), "name": ev.get("name", "?"), "icon": ev.get("icon", "dot"),
           "blocks": ev.get("blocks", ""), "source": ev.get("source", ""),
           "used_in": ev.get("used_in", ""), "renewal": ev.get("renewal", ""),
           "note": ev.get("note", ""), "state": OK, "detail": ""}

    if etype in ("balance_threshold", "count_threshold"):
        threshold = float(ev.get("threshold", 0))
        warn_ratio = float(ev.get("warn_ratio", 1.5))
        unit = ev.get("unit", "")
        live = False
        value, ts = None, None
        if ev.get("balance_platform") and ev.get("balance_field"):
            value, ts = _latest_balance(ev["balance_platform"], ev["balance_field"])
            live = value is not None
        if value is None:
            value = ev.get("manual_value")
        if value is None:
            out["state"] = WARN
            out["detail"] = f"немає даних (поріг {threshold:g} {unit}) — скрейпер не наповнив"
            return out
        value = float(value)
        if value < threshold:
            out["state"] = CRITICAL
        elif value < threshold * warn_ratio:
            out["state"] = WARN
        origin = f"живе, {str(ts)[:10]}" if live else f"вручну, {ev.get('manual_as_of', '?')}"
        vfmt = f"{int(value)}" if etype == "count_threshold" else f"{value:g}"
        out["detail"] = f"{vfmt} {unit} / поріг {threshold:g} {unit} ({origin})"
        return out

    if etype == "expiry_date":
        d = _parse_date(ev.get("date"))
        if d is None:
            out["state"] = WARN
            out["detail"] = "дата не задана — звірити"
            return out
        days = (d - today).days
        if days < 0:
            out["state"] = CRITICAL
            out["detail"] = f"ПРОТЕРМІНОВАНО {-days} дн тому (до {d.isoformat()})"
        elif days <= lead:
            out["state"] = WARN
            out["detail"] = f"до {d.isoformat()} · {days} дн"
        else:
            out["detail"] = f"до {d.isoformat()} · {days} дн"
        return out

    if etype == "recurring_monthly":
        nxt = _next_monthly(int(ev.get("day_of_month", 1)), today)
        days = (nxt - today).days
        out["state"] = WARN if days <= lead else OK
        out["detail"] = f"наступне ~{nxt.isoformat()} · {days} дн"
        return out

    if etype == "heartbeat":
        # Самовідновний health-check: читає «пульс» (останній успішний прогін) з файлу.
        # 🔴 якщо пульсу нема / застарів; 🟢 сам щойно прогін оновить файл — без ручних дат.
        hb_file = OUT_DIR / str(ev.get("heartbeat_file", ""))
        field = ev.get("heartbeat_field", "last_ok")
        max_stale = int(ev.get("max_stale_days", 2))
        warn_stale = int(ev.get("warn_stale_days", 1))
        if not ev.get("heartbeat_file") or not hb_file.exists():
            out["state"] = CRITICAL
            out["detail"] = f"пульсу нема ({ev.get('heartbeat_file') or '?'}) — прогін не підтверджено"
            return out
        try:
            data = json.loads(hb_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out["state"] = WARN
            out["detail"] = "пульс нечитабельний"
            return out
        last = _parse_date(str(data.get(field)))
        if last is None:
            out["state"] = WARN
            out["detail"] = "пульс без дати"
            return out
        age = (today - last).days
        if age > max_stale:
            out["state"] = CRITICAL
            out["detail"] = f"останній успіх {age} дн тому (поріг {max_stale})"
        elif age > warn_stale:
            out["state"] = WARN
            out["detail"] = f"останній успіх {age} дн тому"
        else:
            out["detail"] = f"звірка жива — останній успіх {age} дн тому"
        return out

    if etype == "manual_date":
        d = _parse_date(ev.get("date"))
        if d is None:
            out["state"] = WARN
            out["detail"] = "дата невідома — звірити"
            return out
        days = (d - today).days
        if days < 0:
            out["state"] = CRITICAL
            out["detail"] = f"ЗВІРИТИ — минуло {-days} дн (до {d.isoformat()})"
        elif days <= lead:
            out["state"] = WARN
            out["detail"] = f"до {d.isoformat()} · {days} дн"
        else:
            out["detail"] = f"до {d.isoformat()} · {days} дн"
        return out

    out["state"] = WARN
    out["detail"] = f"невідомий тип '{etype}'"
    return out


def evaluate(today: date = None) -> list:
    today = today or date.today()
    results = [_eval_event(ev, today) for ev in _load_events()]
    results.sort(key=lambda r: -_SEVERITY.get(r["state"], 0))
    return results


_STATE_UA = {CRITICAL: "критично", WARN: "увага", OK: "у нормі"}
_STATE_COLOR = {CRITICAL: "#d03b3b", WARN: "#e08a00", OK: "#0ca30c"}


def render_html(results: list, now: datetime) -> str:
    n_crit = sum(1 for r in results if r["state"] == CRITICAL)
    n_warn = sum(1 for r in results if r["state"] == WARN)
    n_ok = sum(1 for r in results if r["state"] == OK)

    rows = []
    for r in results:
        color = _STATE_COLOR.get(r["state"], "#888")
        blocks = f"блокує: {html.escape(r['blocks'])}" if r["blocks"] else ""
        src = html.escape(r["source"])
        sub = " · ".join(p for p in (blocks, src) if p)
        note = f"<div class='note'>{html.escape(r['note'])}</div>" if r.get("note") else ""
        used = (f"<div class='meta'><span class='lbl'>Задіяно:</span> {html.escape(r['used_in'])}</div>"
                if r.get("used_in") else "")
        renew = (f"<div class='meta'><span class='lbl'>Відновити:</span> {html.escape(r['renewal'])}</div>"
                 if r.get("renewal") else "")
        rows.append(
            "<div class='row'>"
            f"<span class='dot' style='background:{color}'></span>"
            "<div class='main'>"
            f"<div class='name'>{html.escape(r['name'])}</div>"
            f"<div class='sub'>{sub}</div>{note}{used}{renew}</div>"
            "<div class='right'>"
            f"<div class='detail'>{html.escape(r['detail'])}</div>"
            f"<span class='pill' style='color:{color};border-color:{color}'>{_STATE_UA.get(r['state'], '?')}</span>"
            "</div></div>"
        )

    return f"""<!doctype html>
<html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="900">
<title>Критичний календар PlutusToys</title>
<style>
:root{{--bg:#f7f7f5;--card:#fff;--line:#e6e5e0;--txt:#1a1a19;--mut:#6b6a66;}}
@media(prefers-color-scheme:dark){{:root{{--bg:#1a1a19;--card:#232322;--line:#33332f;--txt:#f0efec;--mut:#a3a29a;}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;padding:20px}}
.wrap{{max-width:760px;margin:0 auto}}
h1{{font-size:20px;font-weight:600;margin:0 0 2px}}
.ts{{color:var(--mut);font-size:13px;margin-bottom:18px}}
.kpis{{display:flex;gap:10px;margin-bottom:18px}}
.kpi{{flex:1;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.kpi .lbl{{font-size:13px;color:var(--mut)}}
.kpi .num{{font-size:26px;font-weight:600;margin-top:2px}}
.list{{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}}
.row{{display:flex;align-items:flex-start;gap:12px;padding:14px 16px;border-bottom:1px solid var(--line)}}
.row:last-child{{border-bottom:none}}
.dot{{width:12px;height:12px;border-radius:50%;flex:none;margin-top:5px}}
.main{{flex:1;min-width:0}}
.name{{font-weight:600}}
.sub{{font-size:13px;color:var(--mut);margin-top:2px}}
.note{{font-size:12px;color:var(--mut);margin-top:2px;font-style:italic}}
.meta{{font-size:12px;color:var(--mut);margin-top:4px;line-height:1.45}}
.lbl{{font-weight:600;color:var(--txt)}}
.right{{text-align:right;white-space:nowrap;flex:none;padding-top:2px}}
.detail{{font-size:14px}}
.pill{{display:inline-block;margin-top:4px;font-size:12px;padding:2px 10px;border:1px solid;border-radius:999px}}
</style></head><body><div class="wrap">
<h1>Критичний календар PlutusToys</h1>
<div class="ts">оновлено {now.strftime('%Y-%m-%d %H:%M')} · само-оновлення кожні 15 хв</div>
<div class="kpis">
<div class="kpi"><div class="lbl">Критично</div><div class="num" style="color:{_STATE_COLOR[CRITICAL]}">{n_crit}</div></div>
<div class="kpi"><div class="lbl">Увага</div><div class="num" style="color:{_STATE_COLOR[WARN]}">{n_warn}</div></div>
<div class="kpi"><div class="lbl">У нормі</div><div class="num" style="color:{_STATE_COLOR[OK]}">{n_ok}</div></div>
</div>
<div class="list">{''.join(rows)}</div>
</div></body></html>"""


def run() -> dict:
    now = datetime.now()
    results = evaluate(now.date())
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    HTML_FILE.write_text(render_html(results, now), encoding="utf-8")

    n_crit = sum(1 for r in results if r["state"] == CRITICAL)
    n_warn = sum(1 for r in results if r["state"] == WARN)
    status = {
        "generated_at": now.isoformat(timespec="seconds"),
        "n_critical": n_crit, "n_warn": n_warn,
        "blocking": [r["id"] for r in results if r["state"] == CRITICAL and r["blocks"]],
        "events": results,
    }
    STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[critical_watch] критично={n_crit} увага={n_warn} у-нормі="
          f"{sum(1 for r in results if r['state'] == OK)} -> {HTML_FILE}")
    for r in results:
        if r["state"] != OK:
            print(f"  [{_STATE_UA[r['state']]}] {r['name']}: {r['detail']}")
    return status


if __name__ == "__main__":
    run()
