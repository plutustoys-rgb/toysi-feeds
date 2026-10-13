# -*- coding: utf-8 -*-
"""site_refund_receipts.py — автоматичний фіскальний чек ПОВЕРНЕННЯ (RETURN) для онлайн-оплат сайту (рішення власника 13.10.2026).

СИГНАЛ — повернення грошей у кабінеті LiqPay (його робить людина: гроші автоматика не рухає). Платіж стає `reversed`; цей модуль раз у цикл order-pipeline
(~15 хв) питає LiqPay API `status` (наші ключі; звірено живо 13.10: `status`, `refund_amount`) для оплачених передоплатних замовлень сайту й, коли бачить ПОВНЕ повернення,
видає чек RETURN до чека продажу тими самими перевіреними функціями, що й ручний `kodv_return_receipt.py` (validate/build_body: одна позиція, одна оплата, без податків/знижок,
сума збігається, дубль відхиляється).

Що НЕ робить (віддає людині, з поясненням у журналі й Telegram-журналі): часткове повернення (сума повернення ≠ сумі замовлення), багатопозиційний чек, невідповідність суми.
Ідемпотентність: атомарний claim у БД (refund_receipt_claim_at) ПЕРЕД відправкою; якщо POST пішов, а результат невідомий (збій після відправки) — claim ЛИШАЄТЬСЯ, повторів нема, алерт «перевір касу».
Якщо RETURN до цього чека вже існує (бухгалтер зробив вручну) — просто позначає замовлення виконаним, нічого не створюючи.
Перевірка статусу в LiqPay — не частіше разу на годину на замовлення (refund_checked_at).
"""
import sys
from datetime import datetime, timedelta

import requests

import checkbox_client as cb
import checkbox_registry_sync as cs
import kodv_return_receipt as krr
import liqpay_client as lp
import orders_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LIQPAY_API_URL = "https://www.liqpay.ua/api/request"
LOOKBACK_DAYS = 90          # LiqPay дозволяє повернення пізніше, але чек RETURN до продажу старше 89-денного вікна пошуку Checkbox звіряється ланцюжком вікон
RECHECK_AFTER = timedelta(hours=1)


def liqpay_status(order_id: str) -> dict:
    """LiqPay API status (читання): {'status', 'amount', 'refund_amount', ...}. Кидає виняток при збої мережі/підпису."""
    data = lp._encode_data({"version": 3, "public_key": lp.LIQPAY_PUBLIC_KEY, "action": "status", "order_id": str(order_id)})
    r = requests.post(LIQPAY_API_URL, data={"data": data, "signature": lp._signature(data)}, timeout=30)
    r.raise_for_status()
    return r.json()


def _alert(key: str, text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=24 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[refund_receipts] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


def run_once(conn=None, **kw) -> dict:
    """Один прохід. Повертає лічильники {'checked','created','already','human','failed'}. Не кидає винятків назовні.
    conn=None → власне з'єднання з бойовою БД (orders_db.get_connection — context manager); у тестах передається готове."""
    if conn is not None:
        return _run(conn, **kw)
    try:
        with orders_db.get_connection() as c:
            return _run(c, **kw)
    except Exception as e:  # noqa: BLE001
        print(f"[refund_receipts] збій проходу (БД): {type(e).__name__}: {e}", file=sys.stderr)
        return {"checked": 0, "created": 0, "already": 0, "human": 0, "failed": 1}


def _run(conn, *, status_fn=None, auth=None, get_receipt=None, fetch_window=None, ensure_shift=None, post=None, wait=None, now=None) -> dict:
    out = {"checked": 0, "created": 0, "already": 0, "human": 0, "failed": 0}
    try:
        if not lp.is_configured() or (lp.LIQPAY_SANDBOX and status_fn is None):
            return out         # без бойових ключів чеки повернення не потрібні (тестові платежі не фіскалізуються)
        status_fn = status_fn or liqpay_status
        auth = auth or cb._authenticate_cashier
        get_receipt = get_receipt or krr._get_receipt
        fetch_window = fetch_window or cs._fetch_window
        ensure_shift = ensure_shift or cb._ensure_shift_open
        post = post or krr._post_receipt
        wait = wait or krr.wait_done
        now_k = now or datetime.now(krr.KYIV)
        cands = orders_db.get_refund_receipt_candidates(
            conn, (datetime.now() - timedelta(days=LOOKBACK_DAYS)).isoformat(timespec="seconds"), (datetime.now() - RECHECK_AFTER).isoformat(timespec="seconds"))
        token = None
        for o in cands:
            oid = o["internal_order_id"]
            out["checked"] += 1
            try:
                liq = status_fn(o["order_id"])
            except Exception as e:  # noqa: BLE001
                print(f"[refund_receipts] {oid}: статус LiqPay не отримано ({type(e).__name__}: {e})", file=sys.stderr)
                continue                                  # refund_checked_at не ставимо: наступний цикл спробує знову
            orders_db.mark_refund_checked(conn, oid)
            conn.commit()
            if (liq or {}).get("status") != "reversed":
                continue
            total = o.get("site_charged_total")
            refunded = liq.get("refund_amount")
            try:
                full = total is not None and refunded is not None and abs(float(refunded) - float(total)) <= 0.01
            except (TypeError, ValueError):
                full = False
            if not full:
                out["human"] += 1
                _alert(f"refund_partial:{oid}", f"🔴 LiqPay: повернення по {oid} не повне (повернено {refunded}, оплачено {total}) — чек повернення автоматично НЕ створено, потрібен ручний (kodv_return_receipt.py або бухгалтер).")
                continue
            try:
                token = token or auth()
                headers = {"X-License-Key": cb.CHECKBOX_API_KEY, "Authorization": f"Bearer {token}"}
                sell = get_receipt(token, o["checkbox_receipt_id"])
                fresh = fetch_window(headers, now_k.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=89), now_k) or []
                existing = krr.find_duplicate(sell.get("id"), fresh)
                if existing:
                    orders_db.mark_refund_receipt_done(conn, oid, existing.get("id"))
                    conn.commit()
                    out["already"] += 1
                    print(f"[refund_receipts] {oid}: чек повернення вже існує (серіал {existing.get('serial')}) — позначено виконаним")
                    continue
                krr.validate(sell, fresh, expect_fiscal=None, expect_sum=total)
                body = krr.build_body(sell)
            except krr.Refusal as e:
                out["human"] += 1
                _alert(f"refund_refusal:{oid}", f"🔴 LiqPay повернув платіж {oid}, але чек повернення автоматично НЕ створено: {e}. Потрібен ручний чек (kodv_return_receipt.py або бухгалтер).")
                continue
            except Exception as e:  # noqa: BLE001
                out["failed"] += 1
                print(f"[refund_receipts] {oid}: підготовка чека повернення не вдалась ({type(e).__name__}: {e}); повтор у наступному циклі", file=sys.stderr)
                continue
            if not orders_db.claim_refund_receipt(conn, oid):
                continue                                  # інший процес виграв claim
            conn.commit()                                 # claim має бути видимий іншим ДО звернення до Checkbox
            sent = False
            try:
                ensure_shift(token)
                sent = True                               # з цього моменту стан чека у Checkbox може бути невідомим
                resp = post(token, body)
                rid = (resp or {}).get("id") or body["id"]
                done = wait(token, rid)
                orders_db.mark_refund_receipt_done(conn, oid, done.get("id") or rid)
                conn.commit()
                out["created"] += 1
                print(f"[refund_receipts] ✅ {oid}: чек повернення створено (серіал {done.get('serial')}, фіскальний {done.get('fiscal_code')}, {(done.get('total_sum') or 0) / 100:.2f} грн)")
                _alert(f"refund_done:{oid}", f"🧾 Чек повернення видано для {oid}: серіал {done.get('serial')}, {(done.get('total_sum') or 0) / 100:.2f} грн (LiqPay повернув платіж).")
            except Exception as e:  # noqa: BLE001
                out["failed"] += 1
                if sent:
                    _alert(f"refund_unknown:{oid}", f"🔴 Чек повернення для {oid}: запит у Checkbox ВІДПРАВЛЕНО, результат невідомий ({type(e).__name__}: {e}). Автоповтору нема. Перевір касу Checkbox, чи існує RETURN до цього чека, і створи/позначи вручну.")
                else:
                    orders_db.release_refund_receipt_claim(conn, oid)
                    conn.commit()
                    print(f"[refund_receipts] {oid}: чек повернення НЕ створено до відправки ({type(e).__name__}: {e}); повтор у наступному циклі", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[refund_receipts] збій проходу: {type(e).__name__}: {e}", file=sys.stderr)
    return out


if __name__ == "__main__":
    print(run_once())
