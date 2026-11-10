# -*- coding: utf-8 -*-
"""orders_admin.py — огляд ВСІХ замовлень (сайт, Prom, Rozetka, EVA) для власника в ЛОКАЛЬНІЙ панелі керування (рішення власника 10.11.2026: «усе проходить всліпу», «телеграм не засмічуємо — шукай інший варіант адмінки»).

ЯК ПРАЦЮЄ: панель (localhost, на ПК власника) за кліком запускає ЧИТАЛЬНИЙ запит до orders.db на VPS через ssh (ключ власника на цьому ПК; режим SQLite mode=ro; скрипт фіксований, без введення користувача) і показує таблицю:
час, площадка, номер, сума, оплата, СТАН (чекає оплати / оплачено / передано в Toysi / відправлено / доставлено / скасовано …), покупець. Telegram не задіяний; нічого не пишеться ні в базу, ні в GitHub.
ПРИВАТНІСТЬ: ПІБ/телефон/місто — лише в відповіді локальній сторінці панелі (127.0.0.1), не логуються, не потрапляють у знімок ops-health/GitHub.
Чисті функції (stage, total, summarize) тестуються без мережі: test_orders_admin.py."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

SSH_HOST = os.environ.get("ORDERS_ADMIN_SSH_HOST", "root@45.94.157.4")
SSH_KEY = os.environ.get("ORDERS_ADMIN_SSH_KEY", os.path.join(os.path.expanduser("~"), ".ssh", "plutustoys_vps"))
SSH_TIMEOUT_SEC = 60
ROW_LIMIT = 3000
STALE_UNPAID_MIN = 30        # передоплата без оплати довше цього = «зависла»

# Лише ASCII: кирилиця через stdin ssh ламається. Читає orders.db у режимі mode=ro (запис неможливий). Аргумент: кількість днів.
REMOTE_SCRIPT = r'''
import sqlite3, json, sys, datetime
days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
days = max(1, min(days, 365))
c = sqlite3.connect("file:orders.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
cols = {r[1] for r in c.execute("pragma table_info(orders)")}
want = ["internal_order_id", "platform", "order_id", "public_number", "created_at", "status", "payment_method", "payment_confirmed", "site_charged_total",
        "customer_name", "phone", "np_branch", "items", "forwarded_to_toysi_at", "toysi_order_id", "toysi_ttn", "delivery_status", "delivered_at", "cancelled_at", "cancel_reason", "carrier"]
sel = [w for w in want if w in cols]
since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
rows = []
for r in c.execute("select " + ",".join(sel) + " from orders where created_at >= ? order by created_at desc limit %d" % ROWLIMIT, (since,)):
    d = {k: r[k] for k in sel}
    try:
        its = json.loads(d.pop("items") or "[]")
    except Exception:
        its = []
    d["items"] = [{"code": str(i.get("toysi_code", ""))[:30], "name": str(i.get("name", ""))[:100], "qty": i.get("qty"), "price": i.get("price")} for i in its if isinstance(i, dict)][:30]
    rows.append(d)
print(json.dumps({"generated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "days": days, "rows": rows}, ensure_ascii=True))
'''.replace("ROWLIMIT", str(ROW_LIMIT))


def fetch(days: int = 30, runner=None) -> dict:
    """→ {"ok": True, "generated", "days", "rows": [...]} або {"ok": False, "error": "..."}. runner — для тестів (замінює subprocess.run)."""
    try:
        days = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        days = 30
    cmd = ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15", SSH_HOST,
           f"cd /opt/plutustoys && venv/bin/python3 - {days}"]
    try:
        r = (runner or subprocess.run)(cmd, input=REMOTE_SCRIPT.encode("ascii"), capture_output=True, timeout=SSH_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"VPS не відповів за {SSH_TIMEOUT_SEC} с"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"ssh не запустився: {type(e).__name__}"}
    out = (r.stdout or b"").decode("utf-8", errors="replace").strip()
    if r.returncode != 0 or not out:
        err = (r.stderr or b"").decode("utf-8", errors="replace").strip().splitlines()[-1:] or ["?"]
        return {"ok": False, "error": f"ssh код {r.returncode}: {err[0][:200]}"}
    try:
        d = json.loads(out.splitlines()[-1])
    except ValueError:
        return {"ok": False, "error": "VPS віддав не JSON"}
    d["ok"] = True
    return d


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def total(row: dict) -> float:
    """Сума замовлення: для сайту — зафіксована при створенні (site_charged_total), інакше сума позицій."""
    t = row.get("site_charged_total")
    if t not in (None, ""):
        try:
            return float(t)
        except (TypeError, ValueError):
            pass
    s = 0.0
    for i in row.get("items") or []:
        try:
            s += float(i.get("price") or 0) * float(i.get("qty") or 0)
        except (TypeError, ValueError):
            continue
    return s


_DELIVERY = {"processing": "передано, в обробці", "assembling": "збирається", "packed": "запаковано", "shipped": "відправлено", "delivered": "доставлено",
             "cancelled": "скасовано", "returned": "повернення"}


def stage(row: dict, now: datetime) -> tuple:
    """→ (код, підпис українською, прапорець «потребує уваги»). Лише за службовими полями orders.db."""
    st = (row.get("status") or "")
    ds = (row.get("delivery_status") or "")
    if st == "sandbox_test":
        return "test", "ТЕСТ (пісочниця LiqPay)", False
    if row.get("cancelled_at") or ds == "cancelled":
        return "cancelled", "скасовано" + (f" ({row['cancel_reason']})" if row.get("cancel_reason") else ""), False
    if ds == "returned":
        return "returned", "повернення", True
    if ds == "delivered":
        return "delivered", "доставлено", False
    if ds in _DELIVERY:
        return ds, _DELIVERY[ds] + (f" · ТТН {row['toysi_ttn']}" if row.get("toysi_ttn") else ""), False
    if row.get("forwarded_to_toysi_at"):
        return "forwarded", "передано в Toysi" + (f" #{row['toysi_order_id']}" if row.get("toysi_order_id") else "") + (f" · ТТН {row['toysi_ttn']}" if row.get("toysi_ttn") else ""), False
    if (row.get("payment_method") == "prepaid") and not row.get("payment_confirmed"):
        created = _parse(row.get("created_at"))
        age = (now - created).total_seconds() / 60 if created else 0
        return "unpaid", f"ЧЕКАЄ ОПЛАТИ ({age:.0f} хв)" + (" — зависло" if age > STALE_UNPAID_MIN else ""), age > STALE_UNPAID_MIN
    return "accepted", "прийнято" + (f" ({st})" if st else ""), False


def summarize(rows: list, now: datetime) -> dict:
    """Лічильники: усього / по площадках / по станах / сьогодні; окремо «сайт: неоплачені». Одиниця — ЗАМОВЛЕННЯ (рядок orders.db)."""
    today = now.strftime("%Y-%m-%d")
    by_platform, by_stage, today_n = {}, {}, {}
    site_unpaid = site_unpaid_stale = 0
    for r in rows:
        p = r.get("platform") or "?"
        by_platform[p] = by_platform.get(p, 0) + 1
        code = stage(r, now)[0]
        by_stage[code] = by_stage.get(code, 0) + 1
        if str(r.get("created_at") or "").startswith(today):
            today_n[p] = today_n.get(p, 0) + 1
        if p == "site" and code == "unpaid":
            site_unpaid += 1
            if stage(r, now)[2]:
                site_unpaid_stale += 1
    return {"total": len(rows), "by_platform": by_platform, "by_stage": by_stage, "today": today_n, "site_unpaid": site_unpaid, "site_unpaid_stale": site_unpaid_stale}


def payload(days: int = 30, runner=None, now: datetime | None = None) -> dict:
    """Те, що віддає /api/orders: рядки з обчисленими полями (стан, сума) + лічильники."""
    d = fetch(days, runner=runner)
    if not d.get("ok"):
        return d
    now = now or datetime.now()
    out = []
    for r in d.get("rows") or []:
        code, label, attn = stage(r, now)
        r = dict(r)
        r.update({"stage": code, "stage_label": label, "attention": attn, "total": round(total(r), 2)})
        out.append(r)
    return {"ok": True, "generated": d.get("generated"), "days": d.get("days"), "rows": out, "summary": summarize(out, now)}


if __name__ == "__main__":
    res = payload(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
    print(json.dumps(res.get("summary") or res, ensure_ascii=False))
