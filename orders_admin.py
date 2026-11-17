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
import time
from datetime import datetime, timedelta

SSH_HOST = os.environ.get("ORDERS_ADMIN_SSH_HOST", "root@45.94.157.4")
SSH_KEY = os.environ.get("ORDERS_ADMIN_SSH_KEY", os.path.join(os.path.expanduser("~"), ".ssh", "plutustoys_vps"))
SSH_TIMEOUT_SEC = 60
ROW_LIMIT = 3000
STALE_UNPAID_MIN = 30        # передоплата без оплати довше цього = «зависла»
STALLED_MIN = 60             # оплачене/накладене, ще не передане ні Toysi, ні постачальнику довше цього = «стоїть»
STALLED_MAX_DAYS = 3         # старіше — це історія, не тривога (не підсвічуємо)
_RT_LABELS = {"ttn_creating": "створюється ТТН (результат невідомий, якщо довго)", "ttn_created": "ТТН створено", "marking_ready": "наліпка готова",
              "rt_ordering": "оформлюється у постачальника (результат невідомий, якщо довго)", "rt_ordered": "замовлено у постачальника", "manual": "виконано вручну"}

# Лише ASCII: кирилиця через stdin ssh ламається. Читає orders.db у режимі mode=ro (запис неможливий). Аргумент: кількість днів.
REMOTE_SCRIPT = r'''
import sqlite3, json, sys, datetime
days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
days = max(1, min(days, 365))
want_np = (sys.argv[2] != "0") if len(sys.argv) > 2 else True
c = sqlite3.connect("file:orders.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
cols = {r[1] for r in c.execute("pragma table_info(orders)")}
want = ["internal_order_id", "platform", "order_id", "public_number", "created_at", "status", "payment_method", "payment_confirmed", "site_charged_total",
        "customer_name", "phone", "np_branch", "items", "forwarded_to_toysi_at", "toysi_order_id", "toysi_ttn", "delivery_status", "delivered_at", "cancelled_at", "cancel_reason", "carrier", "rt_state", "rt_error", "rt_confirmed_at"]
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
# carry-over: orders OLDER than the chosen period that are still on the way (have a TTN, not final): the parcel may be waiting at the post office
# (owner 17.11.2026: "the panel no longer shows orders that are still at the post office, not picked up"). Final = delivered/cancelled/returned/expired.
CARRY_DAYS = 120
carry_since = (datetime.datetime.now() - datetime.timedelta(days=CARRY_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
if "toysi_ttn" in cols and "delivery_status" in cols:
    cancel_since = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    q = ("select " + ",".join(sel) + " from orders where created_at < ? and toysi_ttn is not null and toysi_ttn != '' and (status is null or status != 'sandbox_test') and "
         "((created_at >= ? and cancelled_at is null and (delivery_status is null or delivery_status not in ('delivered','cancelled','returned','expired')) and (status is null or status not like '%cancel%')) "
         "or (created_at >= ? and (cancelled_at is not null or delivery_status = 'cancelled' or status like '%cancel%') and (delivery_status is null or delivery_status not in ('delivered','returned','expired')))) "
         "order by created_at desc limit 300")
    for r in c.execute(q, (since, carry_since, cancel_since)):
        d = {k: r[k] for k in sel}
        try:
            its = json.loads(d.pop("items") or "[]")
        except Exception:
            its = []
        d["items"] = [{"code": str(i.get("toysi_code", ""))[:30], "name": str(i.get("name", ""))[:100], "qty": i.get("qty"), "price": i.get("price")} for i in its if isinstance(i, dict)][:30]
        d["carry"] = True
        rows.append(d)
# Nova Poshta status for parcels on the way (read-only batch call, public tracking). The result carries np_meta: a failure must NOT look like "all fine" (audit #1699).
np_meta = {"asked": 0, "got": 0, "ok": True, "error": "", "skipped": not want_np}
if want_np:
    try:
        import nova_poshta
        cancel_since_np = (datetime.datetime.now() - datetime.timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%S")
        cand = [d for d in rows if str(d.get("toysi_ttn") or "").strip() and (d.get("delivery_status") or "") not in ("delivered", "returned", "expired") and str(d.get("created_at") or "") >= cancel_since_np]
        cand.sort(key=lambda d: (0 if d.get("carry") else 1))        # carry (older, easy to forget) FIRST: the limit of 150 must not eat exactly them
        ttns = list(dict.fromkeys(str(d.get("toysi_ttn")).strip() for d in cand))[:150]
        np_meta["asked"] = len(ttns)
        info = {}
        for i in range(0, len(ttns), 50):
            try:
                got = nova_poshta._call("TrackingDocument", "getStatusDocuments", {"Documents": [{"DocumentNumber": t, "Phone": ""} for t in ttns[i:i + 50]]}) or []
            except Exception as e:
                np_meta["ok"] = False
                np_meta["error"] = type(e).__name__ + ": " + str(e)[:80].encode("ascii", "replace").decode()
                break          # the chunks already received are kept (partial data is better than none, but np_meta says it is partial)
            for x in got:
                info[str(x.get("Number") or "")] = {"np_status": str(x.get("Status") or "")[:80], "np_code": str(x.get("StatusCode") or ""), "np_arrived": str(x.get("ActualDeliveryDate") or "")[:19], "np_scheduled": str(x.get("ScheduledDeliveryDate") or "")[:19]}
        np_meta["got"] = len(info)
        for d in rows:
            d.update(info.get(str(d.get("toysi_ttn") or "").strip(), {}))
    except Exception as e:
        np_meta["ok"] = False
        np_meta["error"] = type(e).__name__
print(json.dumps({"generated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "days": days, "np_meta": np_meta, "rows": rows}, ensure_ascii=True))
'''.replace("ROWLIMIT", str(ROW_LIMIT))


def fetch(days: int = 30, runner=None, with_np: bool = True) -> dict:
    """→ {"ok": True, "generated", "days", "rows": [...]} або {"ok": False, "error": "..."}. runner — для тестів (замінює subprocess.run)."""
    try:
        days = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        days = 30
    cmd = ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15", SSH_HOST,
           f"cd /opt/plutustoys && venv/bin/python3 - {days} {1 if with_np else 0}"]
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


_SUPPLIER_BY_PREFIX = (("rt-", "RoyalToys"), ("kvu-", "KIDDISVIT"), ("kv-", "KIDDISVIT"), ("zb-", "ZooBaza"))


def supplier_of(row: dict) -> str:
    """Постачальник замовлення за кодами позицій (так само, як розводить конвеєр: rt- → RoyalToys, kv-/kvu- → KIDDISVIT, zb- → ZooBaza, решта — Toysi).
    Змішане (кілька постачальників в одному замовленні) — перелік через « + »."""
    found = []
    for i in row.get("items") or []:
        code = str((i or {}).get("code") or "").strip().lower()
        name = next((n for p, n in _SUPPLIER_BY_PREFIX if code.startswith(p)), "Toysi")
        if name not in found:
            found.append(name)
    return " + ".join(found) if found else "?"


def _stage_base(row: dict, now: datetime) -> tuple:
    """→ (код, підпис українською, прапорець «потребує уваги»). Лише за службовими полями orders.db.
    ЗМІШАНИЙ кошик сайту (Toysi+RT) у базі — ДВА окремі замовлення (site_order_api.split_mixed_children) з однією оплатою: неоплачений змішаний кошик у лічильнику «чекають оплати» дає 2 (одиниця = рядок orders.db)."""
    st = (row.get("status") or "")
    ds = (row.get("delivery_status") or "")
    if st == "sandbox_test":
        return "test", "ТЕСТ (пісочниця LiqPay)", False
    if st == "toysi_error" or "error" in st or "failed" in st:      # найважливіший збій конвеєра (order_router.py:1164) — не ховати під «прийнято» (аудит #1165)
        return "error", f"ПОМИЛКА ({st})", True
    if row.get("cancelled_at") or ds == "cancelled" or st == "cancelled" or st.endswith("_cancelled_before_forward"):      # скасовані до форварду маркетплейси (order_router.py:643/693/737; cancellation_audit.py:29-30)
        return "cancelled", "скасовано" + (f" ({row['cancel_reason']})" if row.get("cancel_reason") else ""), False
    if ds == "returned":
        return "returned", "повернення", True
    if ds == "delivered":
        return "delivered", "доставлено", False
    if ds == "expired":
        return "expired", "Toysi більше не віддає статус (минуло 40 днів) — звірити вручну", True
    if ds.startswith("unknown"):
        return "unknown", f"невідомий статус Toysi ({ds})", True
    rt_err = str(row.get("rt_error") or "").strip()
    if rt_err and not row.get("rt_confirmed_at"):      # після підтвердження оплати старий rt_error (напр. «статус невідомий») — застарілий: раннер його не очищає (12.11, eva_8-084262173); конвеєр постачальника (RoyalToys) зупинився на цьому замовленні й записав причину — рівно те, що власник мусить бачити (12.11: eva_8-084262173 «City not found»)
        return "stuck", f"ЗУПИНЕНО конвеєром: {rt_err[:160]}", True
    rs = str(row.get("rt_state") or "")
    if rs == "rt_ordered" and not row.get("rt_confirmed_at") and ds not in _DELIVERY and supplier_of(row) == "RoyalToys":      # резерв є, оплату з балансу ще не зроблено: чекаємо перехід у «Очікує оплати» на боці постачальника або поповнення балансу
        created = _parse(row.get("created_at"))
        age = (now - created).total_seconds() / 60 if created else 0
        return "rt_wait_pay", "резерв у постачальника, ЧЕКАЄ ОПЛАТИ З БАЛАНСУ" + (f" ({age / 60:.1f} год)" if age >= 60 else "") + (f" · ТТН {row['toysi_ttn']}" if row.get("toysi_ttn") else ""), age > 60
    if rs in _RT_LABELS and ds not in _DELIVERY:
        return "rt_" + rs, _RT_LABELS[rs] + (f" · ТТН {row['toysi_ttn']}" if row.get("toysi_ttn") else ""), rs in ("ttn_creating", "rt_ordering")
    if ds in _DELIVERY:
        return ds, _DELIVERY[ds] + (f" · ТТН {row['toysi_ttn']}" if row.get("toysi_ttn") else ""), False
    if row.get("forwarded_to_toysi_at"):
        return "forwarded", "передано в Toysi" + (f" #{row['toysi_order_id']}" if row.get("toysi_order_id") else "") + (f" · ТТН {row['toysi_ttn']}" if row.get("toysi_ttn") else ""), False
    if (row.get("payment_method") == "prepaid") and not row.get("payment_confirmed"):
        created = _parse(row.get("created_at"))
        age = (now - created).total_seconds() / 60 if created else 0
        return "unpaid", f"ЧЕКАЄ ОПЛАТИ ({age:.0f} хв)" + (" — зависло" if age > STALE_UNPAID_MIN else ""), age > STALE_UNPAID_MIN
    if (row.get("payment_method") == "cod" or row.get("payment_confirmed")) and not row.get("forwarded_to_toysi_at") and not rs:
        created = _parse(row.get("created_at"))
        age = (now - created).total_seconds() / 60 if created else 0
        if age > STALLED_MIN:
            return "stalled", f"ОПЛАЧЕНО/ПРИЙНЯТО, але нікуди не передано ({age / 60:.1f} год)", age <= STALLED_MAX_DAYS * 1440
    return "accepted", "прийнято" + (f" ({st})" if st else ""), False


NP_HOLD_WARN_DAYS = 4        # безкоштовне зберігання на НП — 5 діб (налаштоване власником автоповернення): з 4-ї доби на відділенні підсвічуємо
_FINAL_STAGES = ("delivered", "cancelled", "returned", "expired", "test", "error")
ACK_FILE = os.environ.get("ORDERS_ADMIN_ACK_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel_ack.json"))      # {"<internal_order_id>": "нотатка власника"} — підтверджені винятки (не тривожимо); локальний файл, поза git


def load_ack() -> dict:
    try:
        d = json.load(open(ACK_FILE, encoding="utf-8"))
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


_NP_NO_PARCEL = ("1", "2", "3", "105", "106", "107")      # «очікує надходження від відправника», «видалено», «номер не знайдено», … — посилки в НП фактично нема: не тривога


def _np_arrived_days(row: dict, now: datetime):
    t = _parse(row.get("np_arrived"))
    return max(0.0, (now - t).total_seconds() / 86400.0) if t else None


def stage(row: dict, now: datetime) -> tuple:
    """→ (код, підпис, прапорець «потребує уваги»). Базовий стан (_stage_base) + статус НП за ТТН (read-only трекінг, якщо є): «НП: Прибув у відділення (3,2 дн.)»; з NP_HOLD_WARN_DAYS на відділенні — прапорець уваги
    (власник 17.11: «є замовлення ще на НП не забрані», панель мусить їх показувати навіть якщо вони старші за вибраний період — див. carry у REMOTE_SCRIPT)."""
    code, label, attn = _stage_base(row, now)
    np_st = str(row.get("np_status") or "").strip()
    if np_st and code == "cancelled":
        low = np_st.lower()
        got = str(row.get("np_code") or "") in ("9", "10", "11") or low.startswith("відправлення отримано")
        back = any(w in low for w in ("повертається", "відмов", "повернен")) or str(row.get("np_code") or "") in _NP_NO_PARCEL
        if got:
            return code, label + f" · ⚠ ПОСИЛКУ ОТРИМАНО (НП) при скасованому замовленні — перевір гроші", True
        if not back:
            days = _np_arrived_days(row, now)
            return code, label + f" · ⚠ посилка ще в НП: {np_st}" + (f" ({days:.1f} дн.)" if days is not None and "прибув" in low else ""), True
        return code, label + f" · НП: {np_st}", attn
    if np_st and code not in _FINAL_STAGES and (str(row.get("np_code") or "") in ("9", "10", "11") or np_st.lower().startswith("відправлення отримано")):
        return "delivered", "доставлено (за статусом НП)" + (f" · ТТН {row['toysi_ttn']}" if row.get("toysi_ttn") else ""), False      # наш delivery_status може відставати: НП уже каже «отримано»
    if np_st and code not in _FINAL_STAGES:
        days = _np_arrived_days(row, now)
        waiting = "прибув" in np_st.lower() and days is not None
        label += f" · НП: {np_st}" + (f" ({days:.1f} дн.)" if waiting else "")
        if waiting and days >= NP_HOLD_WARN_DAYS:
            attn = True
            label += " — НЕ ЗАБРАНО, скоро повернення"
    return code, label, attn


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
    attn = []
    for r in rows:
        code, label, flag = stage(r, now)
        if "attention" in r:                  # payload уже врахував підтвердження власника (panel_ack.json) — тривогу не перераховуємо
            flag, label = bool(r["attention"]), r.get("stage_label") or label
        if flag and code not in ("unpaid",):
            attn.append({"platform": r.get("platform"), "id": r.get("public_number") or r.get("order_id"), "stage": code, "label": label})
    return {"total": len(rows), "by_platform": by_platform, "by_stage": by_stage, "today": today_n, "site_unpaid": site_unpaid, "site_unpaid_stale": site_unpaid_stale,
            "attention": len(attn), "attention_rows": attn[:12]}


NP_CACHE_TTL_SEC = 1800          # панель автооновлюється кожні 5 хв, а НП-трекінг — публічний API з лімітом: свіжі дані НП беремо раз на 30 хв або за явним кліком (force)
_np_cache = {"ts": 0.0, "info": {}}


def payload(days: int = 30, runner=None, now: datetime | None = None, force_np: bool = False) -> dict:
    """Те, що віддає /api/orders: рядки з обчисленими полями (стан, сума) + лічильники."""
    use_cache = (not force_np) and bool(_np_cache["info"]) and (time.time() - _np_cache["ts"] < NP_CACHE_TTL_SEC)
    d = fetch(days, runner=runner, with_np=not use_cache)
    if not d.get("ok"):
        return d
    meta = d.get("np_meta") or {}
    if not use_cache and meta.get("ok") and meta.get("got"):
        _np_cache["info"] = {str(r.get("toysi_ttn")): {k: r[k] for k in ("np_status", "np_code", "np_arrived", "np_scheduled") if k in r} for r in d.get("rows") or [] if r.get("np_status")}
        _np_cache["ts"] = time.time()
    if use_cache:
        for r in d.get("rows") or []:
            r.update(_np_cache["info"].get(str(r.get("toysi_ttn")), {}))
        meta = {"ok": True, "cached": True, "asked": 0, "got": len(_np_cache["info"])}
    np_warning = ""
    if not meta.get("ok", True):
        np_warning = f"дані НП недоступні ({meta.get('error') or '?'}): статуси «не забрано/отримано» не показано — не вважати, що все гаразд"
    elif meta.get("asked") and meta.get("got", 0) < meta["asked"]:
        np_warning = f"НП віддала статус лише для {meta.get('got', 0)} із {meta['asked']} посилок"
    now = now or datetime.now()
    ack = load_ack()
    out = []
    for r in d.get("rows") or []:
        r = dict(r)
        note = ack.get(str(r.get("internal_order_id") or ""))
        if note is not None:
            r["ack"] = note
        code, label, attn = stage(r, now)
        if note is not None:
            label += f" · ✔ підтверджено власником: {note}"
            attn = False
        if r.get("carry") and code in _FINAL_STAGES and not (code == "cancelled" and attn):
            continue          # старіше періоду й уже отримано/скасовано/повернено — для огляду не цікаве (carry показує лише ТЕ, ЩО ЩЕ В ДОРОЗІ)
        r = dict(r)
        r.update({"stage": code, "stage_label": label, "attention": attn, "total": round(total(r), 2), "supplier": supplier_of(r)})
        out.append(r)
    summ = summarize(out, now)
    summ["np_warning"] = np_warning
    return {"ok": True, "generated": d.get("generated"), "days": d.get("days"), "rows": out, "summary": summ}


if __name__ == "__main__":
    res = payload(int(sys.argv[1]) if len(sys.argv) > 1 else 7)
    print(json.dumps(res.get("summary") or res, ensure_ascii=False))
