# -*- coding: utf-8 -*-
"""review_link_notify.py — посилання на відгук власнику в Telegram, коли замовлення САЙТУ доставлено (слово власника 15.11.2026: «у телеграм посилання… поки запускаю я»).

ЯК ПРАЦЮЄ: order_status_tracker (VPS), щойно виставив замовленню сайту delivery_status='delivered', кличе maybe_notify(): для кожного Toysi-товару замовлення (reviews_db вимагає канонічний цифровий id Toysi) видає ОДНОРАЗОВЕ посилання
https://plutustoys.com.ua/review.html#t=<токен> (reviews_db.issue_token: лише доставлене замовлення сайту, не скасоване/не sandbox/не повернене; пара (замовлення, товар) — раз) і шле ОДНЕ повідомлення власнику.
Власник САМ пересилає його покупцеві (перший реальний лист — лише власник; контакти покупця — у панелі). Нічого покупцеві автоматично не йде.
ПРИВАТНІСТЬ: у повідомленні лише номер замовлення, дата доставки, назва товару й посилання — без ПІБ/телефону/адреси. Сирий токен у журнал telegram_alerts.md НЕ пишеться (log_text із замаскованим посиланням).
ОБМЕЖЕННЯ: лише замовлення, доставлені за останні MAX_AGE_DAYS діб (старі не «підтягуємо» самі); на одне замовлення — не більше MAX_LINKS_PER_ORDER посилань за раз (решту видасть наступний цикл).
Ніколи не кидає: збій відгуків не має зачіпати трекер доставок."""
import sys
from datetime import datetime, timedelta

import orders_db
import reviews_db

LINK = "https://plutustoys.com.ua/review.html#t={t}"
MAX_AGE_DAYS = 14
MAX_LINKS_PER_ORDER = 3


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def build_message(order: dict, name: str, link: str) -> str:
    when = str(order.get("delivered_at") or "")[:10] or "?"
    return (f"⭐ Посилання на відгук · замовлення {order.get('public_number') or order.get('order_id') or order.get('internal_order_id')} (сайт) доставлено {when} · товар «{(name or '?')[:80]}»\n{link}\n"
            "Одноразове, діє 60 діб. Перешліть покупцю (контакти — в панелі, блок «Замовлення»). Автоматично покупцю нічого не надсилається.")


def maybe_notify(conn, order: dict, send, now: datetime | None = None) -> int:
    """→ скільки посилань видано й надіслано. send(text, log_text=…) — telegram_notify.send_telegram_message. Не кидає."""
    try:
        now = now or datetime.now()
        if not reviews_db.eligible_order(order):
            return 0
        delivered = _parse(order.get("delivered_at"))
        if delivered is not None and now - delivered > timedelta(days=MAX_AGE_DAYS):
            return 0
        reviews_db.init(conn)
        sent = 0
        seen = set()
        for it in order.get("items") or []:
            if sent >= MAX_LINKS_PER_ORDER:
                break
            pid = str((it or {}).get("toysi_code") or "").strip()
            if pid in seen:
                continue
            seen.add(pid)
            token = reviews_db.issue_token(conn, order, pid, now=now)
            if not token:
                continue
            link = LINK.format(t=token)
            msg = build_message(order, (it or {}).get("name") or "", link)
            try:
                ok = send(msg, log_text=msg.replace(link, LINK.format(t="…приховано…"))) is not False      # send_telegram_message повертає False при збої Telegram (не кидає)
            except Exception as e:  # noqa: BLE001
                print(f"[review_link_notify] {order.get('internal_order_id')}: відправка збій ({type(e).__name__})", file=sys.stderr)
                ok = False
            if not ok:
                # НЕ ЗАЛИШАЄМО «видано, але власник не отримав» (посилання нічим не відновити): прибираємо щойно вставлений запис — наступний цикл трекера (~15 хв) повторить спробу
                try:
                    conn.execute("DELETE FROM reviews WHERE order_id = ? AND product_id = ? AND token_hash = ?", (order["internal_order_id"], pid, reviews_db._hash(token)))
                    conn.commit()
                except Exception as e:  # noqa: BLE001
                    print(f"[review_link_notify] відкат запису посилання не вдався ({type(e).__name__})", file=sys.stderr)
                continue
            conn.commit()      # токен фіксуємо ЛИШЕ після успішної відправки (дубль із новим токеном можливий, втрата — ні)
            sent += 1
        return sent
    except Exception as e:  # noqa: BLE001
        print(f"[review_link_notify] збій ({type(e).__name__}) — трекер не зачеплено", file=sys.stderr)
        return 0


def notify_for_internal_id(conn, internal_id: str, send, now: datetime | None = None) -> int:
    """Перечитує замовлення з БД (після update_delivery_status) і викликає maybe_notify."""
    try:
        row = conn.execute("SELECT * FROM orders WHERE internal_order_id = ?", (internal_id,)).fetchone()
        if not row:
            return 0
        return maybe_notify(conn, orders_db._row_to_dict(row), send, now)
    except Exception as e:  # noqa: BLE001
        print(f"[review_link_notify] {internal_id}: збій ({type(e).__name__})", file=sys.stderr)
        return 0
