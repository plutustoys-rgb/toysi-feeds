#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_review_link_notify.py — посилання на відгук власнику в Telegram (слово власника 15.11.2026: «у телеграм посилання… поки запускаю я»).
Без мережі. `python test_review_link_notify.py` → exit 0/1."""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
os.environ["ORDERS_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "orders.db")
import orders_db  # noqa: E402
import reviews_db  # noqa: E402
import review_link_notify as rl  # noqa: E402
import telegram_notify as tn  # noqa: E402
import telegram_triage  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


orders_db.init_db()
NOW = datetime(2026, 11, 15, 12, 0, 0)


def mk(oid, items, delivered_at, status="forwarded_to_supplier", ds="delivered", platform="site", cancelled=None):
    with orders_db.get_connection() as c:
        orders_db.insert_order(c, {"order_id": oid, "platform": platform, "payment_method": "cod", "payment_confirmed": 0, "customer_name": "Іван Петренко", "phone": "380671112233",
                                   "email": "b@e.com", "np_branch": "Київ, Відділення №5", "carrier": "nova_poshta", "items": items})
        iid = f"{platform}_{oid}"
        c.execute("UPDATE orders SET status=?, delivery_status=?, delivered_at=?, cancelled_at=? WHERE internal_order_id=?", (status, ds, delivered_at, cancelled, iid))
        c.commit()
    return iid


def get(iid):
    with orders_db.get_connection() as c:
        return orders_db._row_to_dict(c.execute("SELECT * FROM orders WHERE internal_order_id=?", (iid,)).fetchone())


sent = []


def send(text, log_text=None):
    sent.append((text, log_text))
    return True


items = [{"toysi_code": "218158", "name": "Іграшка-ліхтарик", "qty": 1, "price": 100}]
a = mk("PT-1", items, "2026-11-14T10:00:00")
with orders_db.get_connection() as c:
    n = rl.notify_for_internal_id(c, a, send, NOW)
chk("доставлене замовлення сайту, Toysi-товар → 1 посилання, 1 повідомлення", n == 1 and len(sent) == 1)
txt, logt = sent[0]
chk("текст: починається з «⭐ Посилання на відгук», номер замовлення, дата доставки, назва товару, посилання review.html#t=", txt.startswith("⭐ Посилання на відгук") and "PT-1" in txt and "2026-11-14" in txt and "Іграшка-ліхтарик" in txt and "https://plutustoys.com.ua/review.html#t=" in txt)
chk("БЕЗ особистих даних: ні ПІБ, ні телефону, ні email, ні адреси", not any(p in txt for p in ("Іван", "Петренко", "380671112233", "b@e.com", "Київ", "Відділення")))
raw = txt.split("#t=")[1].split()[0]
chk("у ЖУРНАЛ іде версія без сирого токена (log_text), у Telegram — з токеном", raw not in logt and "приховано" in logt and raw in txt)
with orders_db.get_connection() as c:
    info = reviews_db.form_info(c, raw, now=NOW)
chk("токен робочий: reviews_db.form_info за ним віддає товар (форма відгуку відкриється)", info and str(info.get("product_id")) == "218158")
sent.clear()
with orders_db.get_connection() as c:
    n2 = rl.notify_for_internal_id(c, a, send, NOW + timedelta(hours=2))
chk("повторний прохід (наступний цикл трекера) → НЕ видає друге посилання на ту саму пару (замовлення, товар)", n2 == 0 and sent == [])

# виключення
b = mk("PT-2", items, "2026-11-14T10:00:00", ds="shipped")
c1 = mk("PT-3", items, "2026-11-14T10:00:00", status="sandbox_test")
d1 = mk("PT-4", items, "2026-11-14T10:00:00", cancelled="2026-11-14T11:00:00")
e1 = mk("PT-5", items, "2026-11-14T10:00:00", platform="prom")
f1 = mk("PT-6", items, "2026-09-01T10:00:00")
g1 = mk("PT-7", [{"toysi_code": "rt-68654", "name": "RT", "qty": 1, "price": 50}, {"toysi_code": "dropt-57114", "name": "D", "qty": 1, "price": 50}], "2026-11-14T10:00:00")
sent.clear()
with orders_db.get_connection() as c:
    r = [rl.notify_for_internal_id(c, x, send, NOW) for x in (b, c1, d1, e1, f1, g1)]
chk("не доставлене / sandbox / скасоване / не сайт / доставлене давно (>14 діб) / лише RT+Dropt-товари → жодного посилання", r == [0, 0, 0, 0, 0, 0] and sent == [])

# кілька товарів: до 3 на замовлення, дублі кодів — раз
many = [{"toysi_code": str(100 + i), "name": f"Т{i}", "qty": 1, "price": 10} for i in range(5)] + [{"toysi_code": "100", "name": "дубль", "qty": 1, "price": 10}]
m1 = mk("PT-8", many, "2026-11-14T10:00:00")
sent.clear()
with orders_db.get_connection() as c:
    nm = rl.notify_for_internal_id(c, m1, send, NOW)
chk("5 товарів → не більше 3 посилань за раз (MAX_LINKS_PER_ORDER); дубль коду не дає другого", nm == 3 and len(sent) == 3)
sent.clear()
with orders_db.get_connection() as c:
    nm2 = rl.notify_for_internal_id(c, m1, send, NOW + timedelta(hours=1))
chk("наступний цикл довидає решту (2 товари), не повторюючи видані", nm2 == 2 and len(sent) == 2)

# збої не кидають
def boom(text, log_text=None):
    raise RuntimeError("tg down")


h1 = mk("PT-9", items, "2026-11-14T10:00:00")
with orders_db.get_connection() as c:
    nb = rl.notify_for_internal_id(c, h1, boom, NOW)
chk("виняток у Telegram → не кидає, 0 надіслано", nb == 0)
with orders_db.get_connection() as c:
    left = c.execute("SELECT COUNT(*) FROM reviews WHERE order_id = ?", (h1,)).fetchone()[0]
chk("після збою запис посилання ПРИБРАНО (аудит #1501: «видано, але власник не отримав» нічим не відновити)", left == 0)
sent.clear()
with orders_db.get_connection() as c:
    nb2 = rl.notify_for_internal_id(c, h1, send, NOW + timedelta(hours=1))
chk("наступний цикл трекера сам повторює і надсилає (1 повідомлення)", nb2 == 1 and len(sent) == 1)
h2 = mk("PT-10", items, "2026-11-14T10:00:00")
with orders_db.get_connection() as c:
    nf = rl.notify_for_internal_id(c, h2, lambda text, log_text=None: False, NOW)
    left2 = c.execute("SELECT COUNT(*) FROM reviews WHERE order_id = ?", (h2,)).fetchone()[0]
chk("send_telegram_message повернув False (мережа/нема токена бота; НЕ виняток) → 0 надіслано, запис прибрано", nf == 0 and left2 == 0)
sent.clear()
with orders_db.get_connection() as c:
    nf2 = rl.notify_for_internal_id(c, h2, send, NOW + timedelta(hours=1))
chk("після False наступний цикл надсилає", nf2 == 1 and len(sent) == 1)
with orders_db.get_connection() as c:
    chk("неіснуюче замовлення → 0 без винятку", rl.notify_for_internal_id(c, "site_NONE", send, NOW) == 0)

# Telegram: режим «лише замовлення» пропускає саме ці повідомлення; токен не потрапляє в журнал
chk("telegram_triage.is_review_link: «⭐ Посилання на відгук…» так; інші повідомлення — ні", telegram_triage.is_review_link("⭐ Посилання на відгук · замовлення PT-1") and not telegram_triage.is_review_link("⭐ Щось інше") and not telegram_triage.is_review_link("🛒 Нове замовлення · site №1"))
posts = []
tn.TELEGRAM_BOT_TOKEN, tn.TELEGRAM_CHAT_ID = "T", "C"
tn.ALERTS_LOG_FILE = Path(tempfile.mkdtemp()) / "telegram_alerts.md"
tn.ALERT_THROTTLE_FILE = tn.ALERTS_LOG_FILE.parent / ".t.json"


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True}


tn.requests.post = lambda url, json=None, timeout=0: (posts.append(json["text"]) or _Resp())
os.environ.pop("AUDIT_NO_TELEGRAM", None)
os.environ.pop("TELEGRAM_ORDERS_ONLY", None)
secret = "AbCdEfGhIjKlMnOpQrStUvWxYz012345"
link = f"https://plutustoys.com.ua/review.html#t={secret}"
msg = f"⭐ Посилання на відгук · замовлення PT-1 (сайт) доставлено 2026-11-14 · товар «X»\n{link}"
ok = tn.send_telegram_message(msg, log_text=msg.replace(link, "https://plutustoys.com.ua/review.html#t=…приховано…"))
logged = tn.ALERTS_LOG_FILE.read_text(encoding="utf-8") if tn.ALERTS_LOG_FILE.exists() else ""
chk("режим «лише замовлення»: повідомлення із посиланням ПРОХОДИТЬ у Telegram (з токеном)", ok and len(posts) == 1 and secret in posts[0])
chk("журнал telegram_alerts.md: токена НЕМАЄ, є «приховано»", secret not in logged and "приховано" in logged)
posts.clear()
tn.send_telegram_message("ℹ️ звичайне службове повідомлення")
chk("режим «лише замовлення» все ще глушить решту повідомлень", posts == [])

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
