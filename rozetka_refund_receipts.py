# -*- coding: utf-8 -*-
"""rozetka_refund_receipts.py — автоматичний фіскальний чек ПОВЕРНЕННЯ (RETURN) для Rozetka-замовлень з оплатою карткою (запит бухгалтера 27.11.2026, слово власника).

СИГНАЛ — власник повертає кошти кнопкою «Повернути кошти» в кабінеті Rozetka; платіж стає `returned` («Повернено», status_payment_id 8):
`rozetka_client.get_payment_status(order_id)` -> name. Цей модуль раз у цикл order-pipeline (~15 хв) питає статус для оплачених передоплатних Rozetka-замовлень з чеком продажу й, коли бачить `returned`,
видає чек RETURN до чека продажу тими самими функціями, що й site_refund_receipts / ручний kodv_return_receipt (validate/build_body: одна позиція, одна оплата, без податків/знижок, дубль відхиляється).

Що НЕ робить (віддає людині з алертом): будь-який статус, окрім чіткого `returned` (часткове повернення чи невідомий статус не йдуть у чек); багатопозиційний чек або невідповідність (відмова validate).
Гроші автоматика не рухає — лише фіскалізує повернення, яке людина вже зробила. Ідемпотентність: атомарний claim у БД (refund_receipt_claim_at) ПЕРЕД відправкою; POST пішов, результат невідомий —
claim ЛИШАЄТЬСЯ, повторів нема, алерт «перевір касу». Якщо RETURN до чека вже є (бухгалтер зробив вручну) — позначає виконаним, нічого не створюючи. Статус — не частіше разу на годину на замовлення.
"""
import sys
from datetime import datetime, timedelta

import checkbox_client as cb
import checkbox_registry_sync as cs
import kodv_return_receipt as krr
import orders_db
import rozetka_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOOKBACK_DAYS = 90
RECHECK_AFTER = timedelta(hours=1)
RETURNED = "returned"          # Rozetka status-payment name для «Повернено»
# ЗАПОБІЖНИК ВІД ЧАСТКОВОГО ПОВЕРНЕННЯ (бухгалтер 27.11: у Orders API ознаки часткового нема — `returned` лише name): RETURN на суму чека продажу видаємо автоматично ТІЛЬКИ коли замовлення
# повернуто/скасовано ЦІЛИМ (наш delivery_status): returned (посилка повернулась), cancelled, expired (Rozetka скасувала). Якщо Rozetka каже «returned», а замовлення «доставлене/в дорозі» —
# це може бути часткова компенсація: чек НЕ видаємо, алерт людині (бухгалтер звіряє суму з реєстром RozetkaPay). Краще чек пізніше, ніж фіскальний чек на неправильну суму.
FULL_RETURN_DELIVERY = ("returned", "cancelled", "expired")


def _alert(key: str, text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=24 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[rozetka_refund_receipts] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


def run_once(conn=None, **kw) -> dict:
    """Один прохід. Повертає лічильники {'checked','created','already','human','failed'}. Не кидає винятків назовні."""
    if conn is not None:
        return _run(conn, **kw)
    try:
        with orders_db.get_connection() as c:
            return _run(c, **kw)
    except Exception as e:  # noqa: BLE001
        print(f"[rozetka_refund_receipts] збій проходу (БД): {type(e).__name__}: {e}", file=sys.stderr)
        return {"checked": 0, "created": 0, "already": 0, "human": 0, "failed": 1}


def _run(conn, *, status_fn=None, auth=None, get_receipt=None, fetch_window=None, ensure_shift=None, post=None, wait=None, now=None) -> dict:
    out = {"checked": 0, "created": 0, "already": 0, "human": 0, "failed": 0}
    try:
        status_fn = status_fn or rozetka_client.get_payment_status
        auth = auth or cb._authenticate_cashier
        get_receipt = get_receipt or krr._get_receipt
        fetch_window = fetch_window or cs._fetch_window
        ensure_shift = ensure_shift or cb._ensure_shift_open
        post = post or krr._post_receipt
        wait = wait or krr.wait_done
        now_k = now or datetime.now(krr.KYIV)
        cands = orders_db.get_rozetka_refund_candidates(
            conn, (datetime.now() - timedelta(days=LOOKBACK_DAYS)).isoformat(timespec="seconds"), (datetime.now() - RECHECK_AFTER).isoformat(timespec="seconds"))
        for st in orders_db.get_stuck_refund_claims(conn, (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")):
            if str(st["internal_order_id"]).startswith("rozetka_"):
                _alert(f"rz_refund_stuck:{st['internal_order_id']}", f"🔴 Чек повернення Rozetka {st['internal_order_id']}: claim від {st['refund_receipt_claim_at']} без результату. Перевір касу Checkbox: якщо RETURN до чека продажу є, познач refund_receipt_at у БД; якщо нема, зніми refund_receipt_claim_at (наступний цикл створить) або створи вручну.")
        token = None
        api_fails = 0
        for o in cands:
            oid = o["internal_order_id"]
            out["checked"] += 1
            try:
                st = status_fn(o["order_id"])
            except Exception as e:  # noqa: BLE001
                print(f"[rozetka_refund_receipts] {oid}: статус Rozetka не отримано ({type(e).__name__}: {e})", file=sys.stderr)
                api_fails += 1
                if api_fails >= 3:
                    print("[rozetka_refund_receipts] Rozetka API недоступний (3 збої підряд) — прохід зупинено", file=sys.stderr)
                    break
                continue                                  # refund_checked_at не ставимо: наступний цикл повторить
            api_fails = 0
            orders_db.mark_refund_checked(conn, oid)
            conn.commit()
            if (st or {}).get("name") != RETURNED:
                continue                                  # paid / інше — нічого (часткове чи невідоме не фіскалізуємо)
            if str(o.get("delivery_status") or "") not in FULL_RETURN_DELIVERY:
                out["human"] += 1
                serial = "?"
                try:                                      # серіал чека продажу для бухгалтера (рідкісна гілка; збій не заважає алерту)
                    token = token or auth()
                    serial = (get_receipt(token, o["checkbox_receipt_id"]) or {}).get("serial") or "?"
                except Exception:  # noqa: BLE001
                    pass
                _alert(f"rz_refund_notfull:{oid}", f"🔴 Rozetka показує «Повернено» по {oid} (Rozetka № {o.get('order_id')}), чек продажу: id {o.get('checkbox_receipt_id')}, серіал {serial}, але замовлення не повернуто/не скасовано цілим (наш delivery_status={o.get('delivery_status')!r}): це може бути ЧАСТКОВЕ повернення/компенсація — чек повернення автоматично НЕ створено. "
                       f"Звір суму повернення в реєстрі RozetkaPay (має дорівнювати сумі чека продажу) і оформи чек вручну (kodv_return_receipt.py), або зміни стан замовлення на returned/cancelled, і наступний цикл створить сам.")
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
                    print(f"[rozetka_refund_receipts] {oid}: чек повернення вже існує (серіал {existing.get('serial')}) — позначено виконаним")
                    continue
                krr.validate(sell, fresh, expect_fiscal=None, expect_sum=None)
                body = krr.build_body(sell)
            except krr.Refusal as e:
                out["human"] += 1
                _alert(f"rz_refund_refusal:{oid}", f"🔴 Rozetka повернула кошти по {oid}, але чек повернення автоматично НЕ створено: {e}. Потрібен ручний чек (kodv_return_receipt.py або бухгалтер).")
                continue
            except Exception as e:  # noqa: BLE001
                out["failed"] += 1
                print(f"[rozetka_refund_receipts] {oid}: підготовка чека повернення не вдалась ({type(e).__name__}: {e}); повтор у наступному циклі", file=sys.stderr)
                _alert(f"rz_refund_prepare:{oid}", f"🔴 Rozetka повернула кошти по {oid}, але чек повернення не вдалось підготувати ({type(e).__name__}: {str(e)[:200]}); повтор щогодини. Якщо повторюється, створи вручну.")
                continue
            if not orders_db.claim_refund_receipt(conn, oid):
                continue                                  # інший процес виграв claim
            conn.commit()                                 # claim видимий іншим ДО звернення до Checkbox
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
                print(f"[rozetka_refund_receipts] ✅ {oid}: чек повернення створено (серіал {done.get('serial')}, фіскальний {done.get('fiscal_code')}, {(done.get('total_sum') or 0) / 100:.2f} грн)")
                _alert(f"rz_refund_done:{oid}", f"🧾 Чек повернення видано для {oid}: серіал {done.get('serial')}, {(done.get('total_sum') or 0) / 100:.2f} грн (Rozetka: «Повернено»).")
            except Exception as e:  # noqa: BLE001
                out["failed"] += 1
                if sent:
                    _alert(f"rz_refund_unknown:{oid}", f"🔴 Чек повернення Rozetka {oid}: запит у Checkbox ВІДПРАВЛЕНО, результат невідомий ({type(e).__name__}: {e}). Автоповтору нема. Перевір касу Checkbox: якщо RETURN є, познач refund_receipt_at у БД; якщо нема, зніми refund_receipt_claim_at або створи вручну.")
                else:
                    orders_db.release_refund_receipt_claim(conn, oid)
                    conn.commit()
                    print(f"[rozetka_refund_receipts] {oid}: чек повернення НЕ створено до відправки ({type(e).__name__}: {e}); повтор у наступному циклі", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[rozetka_refund_receipts] збій проходу: {type(e).__name__}: {e}", file=sys.stderr)
    return out


if __name__ == "__main__":
    print(run_once())
