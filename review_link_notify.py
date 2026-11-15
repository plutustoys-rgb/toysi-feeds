# -*- coding: utf-8 -*-
"""review_link_notify.py — прохання про відгук КЛІЄНТУ в Telegram-боті сайту, коли замовлення САЙТУ доставлено.

ІСТОРІЯ. 15.11.2026 (ранок) власник просив посилання на відгук собі в Telegram, щоб пересилати самому. Того ж дня передумав (слово власника):
«мені на телеграм прийшли посилання на відгуки, я не пересильний; відправляйте клієнту напряму, якщо він підписався на нашого бота для отримання статуса».
Тому тепер власнику НІЧОГО не надсилається; повідомлення йде лише клієнтам, які самі підписалися на статус замовлення (кнопка «Статус у Telegram» на сторінці «Дякуємо» → site_chat_bot, таблиця subscriptions у site_chat.db).

ЯК ПРАЦЮЄ: order_status_tracker (VPS), щойно виставив замовленню сайту delivery_status='delivered', кличе maybe_notify(): знаходить підписані чати цього замовлення; якщо їх нема — НІЧОГО не робить
(токен не видається, відгук не просимо). Інакше для кожного Toysi-товару (reviews_db вимагає канонічний цифровий id Toysi; до MAX_LINKS_PER_ORDER) видає ОДНОРАЗОВЕ посилання
https://plutustoys.com.ua/review.html#t=<токен> (reviews_db.issue_token: лише доставлене замовлення сайту, не скасоване/не sandbox/не повернене; пара (замовлення, товар) — раз)
і шле клієнту ОДНЕ повідомлення (посилання на всі видані товари) від імені бота сайту (SITE_CHAT_BOT_TOKEN).
ЖОДНИХ знижок/бонусів/обіцянок у тексті (рішення власника 13.10: знижки без його слова не будуємо).
ПРИВАТНІСТЬ: у повідомленні лише номер замовлення, назва товару й посилання. Токен у журнали не пишеться (повідомлення йде в обхід telegram_notify).
ОБМЕЖЕННЯ: лише замовлення, доставлені за останні MAX_AGE_DAYS діб (старі клієнтам «підтягуванням» не пишемо).
Ніколи не кидає: збій відгуків не має зачіпати трекер доставок."""
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import orders_db
import reviews_db

LINK = "https://plutustoys.com.ua/review.html#t={t}"
MAX_AGE_DAYS = 3             # клієнту пишемо «по свіжому»: старіші доставки не чіпаємо (на відміну від першої версії для власника — 14)
MAX_LINKS_PER_ORDER = 3
SUB_FAIL_LIMIT = 5           # як у site_chat_bot: підписка з ≥5 невдалих відправок (клієнт заблокував бота) мертва
HERE = Path(__file__).resolve().parent


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def subscribed_chats(order: dict, db_path=None) -> list:
    """chat_id клієнтів, що підписалися на статус ЦЬОГО замовлення (site_chat.db → subscriptions; order_id там — внутрішній id без «site_» або батьківський номер платежу змішаного кошика).
    Лише читання; будь-який збій/відсутність бази → []. Підписки зі статусом done=1 (замовлення завершене) теж враховуємо: «доставлено» і є момент прохання."""
    try:
        ids = []
        iid = str(order.get("internal_order_id") or "")
        if iid.startswith("site_"):
            ids.append(iid[len("site_"):])
        if order.get("site_group_id"):
            ids.append(str(order["site_group_id"]))
        if not ids:
            return []
        p = Path(db_path or os.environ.get("SITE_CHAT_DB") or HERE / "site_chat.db")
        if not p.exists():
            return []
        c = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            rows = c.execute(f"SELECT DISTINCT chat_id FROM subscriptions WHERE order_id IN ({','.join('?' * len(ids))}) AND fails < ?", (*ids, SUB_FAIL_LIMIT)).fetchall()
        finally:
            c.close()
        return [int(r[0]) for r in rows if r and r[0] is not None]
    except Exception as e:  # noqa: BLE001
        print(f"[review_link_notify] підписки не прочитано ({type(e).__name__})", file=sys.stderr)
        return []


def customer_sender():
    """→ send(chat_id, text) -> bool від імені бота сайту, або None, якщо SITE_CHAT_BOT_TOKEN не задано (тоді нічого не шлемо). Помилку Telegram НЕ кидає: False."""
    try:
        import site_chat_bot
        token = os.environ.get("SITE_CHAT_BOT_TOKEN", "").strip()
        if not token:
            return None
        api = site_chat_bot.TelegramAPI(token)
    except Exception as e:  # noqa: BLE001
        print(f"[review_link_notify] бот сайту недоступний ({type(e).__name__})", file=sys.stderr)
        return None

    def send(chat_id, text):
        try:
            api.call("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[review_link_notify] клієнту {chat_id} не надіслано ({type(e).__name__})", file=sys.stderr)
            return False
    return send


def build_message(order: dict, items: list) -> str:
    """items = [(назва, посилання)]. Без знижок/бонусів/обіцянок."""
    num = order.get("public_number") or order.get("order_id") or order.get("internal_order_id")
    body = "\n".join(f"«{(n or '?')[:80]}»: {l}" for n, l in items)
    return (f"Дякуємо за замовлення {num}! Воно доставлено. Нам дуже важлива ваша думка — залиште, будь ласка, відгук:\n{body}\n"
            "Посилання одноразове, діє 60 діб. Якщо щось пішло не так — напишіть нам тут, і ми допоможемо.")


def maybe_notify(conn, order: dict, send, now: datetime | None = None, chats: list | None = None) -> int:
    """→ скільки посилань видано й надіслано клієнту(ам). send(chat_id, text) -> bool. chats — для тестів (інакше з site_chat.db). Не кидає."""
    try:
        now = now or datetime.now()
        if send is None or not reviews_db.eligible_order(order):
            return 0
        delivered = _parse(order.get("delivered_at"))
        if delivered is not None and now - delivered > timedelta(days=MAX_AGE_DAYS):
            return 0
        chats = subscribed_chats(order) if chats is None else chats
        chats = list(dict.fromkeys(chats or []))      # той самий чат двічі (підписка на внутрішній і на батьківський id) — одне повідомлення
        if not chats:
            return 0                # клієнт не підписаний на бота → відгук не просимо (токен навіть не видаємо)
        reviews_db.init(conn)
        issued, seen = [], set()      # [(pid, token, name, link)]
        for it in order.get("items") or []:
            if len(issued) >= MAX_LINKS_PER_ORDER:
                break
            pid = str((it or {}).get("toysi_code") or "").strip()
            if pid in seen:
                continue
            seen.add(pid)
            token = reviews_db.issue_token(conn, order, pid, now=now)
            if token:
                issued.append((pid, token, (it or {}).get("name") or "", LINK.format(t=token)))
        if not issued:
            return 0
        msg = build_message(order, [(n, l) for _p, _t, n, l in issued])
        ok = False
        for ch in chats:
            try:
                ok = bool(send(ch, msg)) or ok
            except Exception as e:  # noqa: BLE001
                print(f"[review_link_notify] {order.get('internal_order_id')}: відправка збій ({type(e).__name__})", file=sys.stderr)
        if not ok:
            # НІХТО не отримав: прибираємо щойно видані токени — наступний цикл трекера (~15 хв) повторить спробу (інакше «видано, але ніхто не отримав» нічим не відновити)
            try:
                for pid, token, _n, _l in issued:
                    conn.execute("DELETE FROM reviews WHERE order_id = ? AND product_id = ? AND token_hash = ?", (order["internal_order_id"], pid, reviews_db._hash(token)))
                conn.commit()
            except Exception as e:  # noqa: BLE001
                print(f"[review_link_notify] відкат запису посилання не вдався ({type(e).__name__})", file=sys.stderr)
            return 0
        conn.commit()      # токени фіксуємо ЛИШЕ після успішної відправки хоча б одному чату
        return len(issued)
    except Exception as e:  # noqa: BLE001
        print(f"[review_link_notify] збій ({type(e).__name__}) — трекер не зачеплено", file=sys.stderr)
        return 0


def notify_for_internal_id(conn, internal_id: str, send, now: datetime | None = None, chats: list | None = None) -> int:
    """Перечитує замовлення з БД (після update_delivery_status) і викликає maybe_notify."""
    try:
        row = conn.execute("SELECT * FROM orders WHERE internal_order_id = ?", (internal_id,)).fetchone()
        if not row:
            return 0
        return maybe_notify(conn, orders_db._row_to_dict(row), send, now, chats)
    except Exception as e:  # noqa: BLE001
        print(f"[review_link_notify] {internal_id}: збій ({type(e).__name__})", file=sys.stderr)
        return 0
