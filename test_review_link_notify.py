#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_review_link_notify.py — прохання про відгук КЛІЄНТУ в боті сайту (слово власника 15.11.2026: «відправляйте клієнту напряму, якщо він підписався на нашого бота для отримання статуса»).
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
_td = tempfile.mkdtemp()
os.environ["ORDERS_DB_PATH"] = os.path.join(_td, "orders.db")
os.environ.pop("SITE_CHAT_BOT_TOKEN", None)
import orders_db  # noqa: E402
import reviews_db  # noqa: E402
import review_link_notify as rl  # noqa: E402

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


def reviews_count(iid):
    with orders_db.get_connection() as c:
        reviews_db.init(c)
        return c.execute("SELECT COUNT(*) FROM reviews WHERE order_id = ?", (iid,)).fetchone()[0]


sent = []


def send(chat_id, text):
    sent.append((chat_id, text))
    return True


def run(iid, snd=send, chats=None, now=NOW):
    with orders_db.get_connection() as c:
        return rl.notify_for_internal_id(c, iid, snd, now, chats)


items = [{"toysi_code": "218158", "name": "Іграшка-ліхтарик", "qty": 1, "price": 100}]

# --- клієнт ПІДПИСАНИЙ -------------------------------------------------------
a = mk("PT-1", items, "2026-11-14T10:00:00")
n = run(a, chats=[111])
chk("підписаний клієнт: 1 посилання, 1 повідомлення саме цьому чату", n == 1 and len(sent) == 1 and sent[0][0] == 111)
txt = sent[0][1]
chk("текст: номер замовлення, назва товару, посилання review.html#t=", "PT-1" in txt and "Іграшка-ліхтарик" in txt and "https://plutustoys.com.ua/review.html#t=" in txt)
chk("БЕЗ особистих даних і БЕЗ знижок/бонусів у тексті", not any(p in txt for p in ("Іван", "Петренко", "380671112233", "b@e.com", "Київ", "Відділення")) and not any(w in txt.lower() for w in ("знижк", "бонус", "промокод", "подарун", "купон")))
raw = txt.split("#t=")[1].split()[0]
with orders_db.get_connection() as c:
    info = reviews_db.form_info(c, raw, now=NOW)
chk("токен робочий: reviews_db.form_info віддає товар (форма відгуку відкриється)", info and str(info.get("product_id")) == "218158")
sent.clear()
chk("повторний прохід трекера → друге посилання на ту саму пару (замовлення, товар) НЕ видається", run(a, chats=[111], now=NOW + timedelta(hours=2)) == 0 and sent == [])

# --- клієнт НЕ підписаний: нічого ---------------------------------------------
b = mk("PT-2", items, "2026-11-14T10:00:00")
sent.clear()
chk("нема підписаних чатів → 0, жодного повідомлення, токен НЕ видано", run(b, chats=[]) == 0 and sent == [] and reviews_count(b) == 0)
chk("send=None (нема SITE_CHAT_BOT_TOKEN) → 0, токен НЕ видано", run(b, snd=None, chats=[222]) == 0 and reviews_count(b) == 0)

# --- виключення ---------------------------------------------------------------
shipped = mk("PT-3", items, "2026-11-14T10:00:00", ds="shipped")
sandbox = mk("PT-4", items, "2026-11-14T10:00:00", status="sandbox_test")
cancelled = mk("PT-5", items, "2026-11-14T10:00:00", cancelled="2026-11-14T11:00:00")
prom = mk("PT-6", items, "2026-11-14T10:00:00", platform="prom")
old = mk("PT-7", items, "2026-11-10T10:00:00")                       # доставлено 5 діб тому: > MAX_AGE_DAYS (3)
rtd = mk("PT-8", [{"toysi_code": "rt-68654", "name": "RT", "qty": 1, "price": 50}, {"toysi_code": "dropt-57114", "name": "D", "qty": 1, "price": 50}], "2026-11-14T10:00:00")
sent.clear()
r = [run(x, chats=[333]) for x in (shipped, sandbox, cancelled, prom, old, rtd)]
chk("не доставлене / sandbox / скасоване / не сайт / доставлене >3 діб тому / лише RT+Dropt-товари → жодного повідомлення", r == [0] * 6 and sent == [])
fresh = mk("PT-9", items, "2026-11-14T10:00:00")
chk("межа віку: доставлено 2 доби тому — пишемо", run(fresh, chats=[444]) == 1)

# --- кілька товарів, кілька чатів ---------------------------------------------
many = [{"toysi_code": str(100 + i), "name": f"Т{i}", "qty": 1, "price": 10} for i in range(5)] + [{"toysi_code": "100", "name": "дубль", "qty": 1, "price": 10}]
m1 = mk("PT-10", many, "2026-11-14T10:00:00")
sent.clear()
nm = run(m1, chats=[501, 502, 501])
chk("5 товарів → не більше 3 посилань, у ОДНОМУ повідомленні кожному чату (дубль чату — одне повідомлення); дубль коду не дає другого", nm == 3 and [c for c, _ in sent] == [501, 502] and sent[0][1].count("review.html#t=") == 3)
sent.clear()
chk("наступний цикл довидає решту (2 товари) і знову пише клієнтам", run(m1, chats=[501, 502, 501], now=NOW + timedelta(hours=1)) == 2 and sent[0][1].count("review.html#t=") == 2)

# --- збої не кидають, токен не губиться ---------------------------------------
def boom(chat_id, text):
    raise RuntimeError("tg down")


h1 = mk("PT-11", items, "2026-11-14T10:00:00")
chk("виняток у відправці → не кидає, 0, запис посилання ПРИБРАНО (інакше «видано, але ніхто не отримав»)", run(h1, snd=boom, chats=[601]) == 0 and reviews_count(h1) == 0)
sent.clear()
chk("наступний цикл трекера сам повторює і надсилає", run(h1, chats=[601], now=NOW + timedelta(hours=1)) == 1 and len(sent) == 1)
h2 = mk("PT-12", items, "2026-11-14T10:00:00")
chk("send повернув False (клієнт заблокував бота) → 0, запис прибрано", run(h2, snd=lambda c, t: False, chats=[602]) == 0 and reviews_count(h2) == 0)
h3 = mk("PT-13", items, "2026-11-14T10:00:00")
got = []
chk("два чати: перший не прийняв, другий прийняв → токен ЗАФІКСОВАНО (1 посилання), повтору немає",
    run(h3, snd=lambda c, t: (got.append(c) or c == 702), chats=[701, 702]) == 1 and got == [701, 702] and reviews_count(h3) == 1
    and run(h3, snd=lambda c, t: True, chats=[701, 702], now=NOW + timedelta(hours=1)) == 0)
chk("неіснуюче замовлення → 0 без винятку", run("site_NONE", chats=[1]) == 0)

# --- підписки з site_chat.db --------------------------------------------------
chat_db = Path(_td) / "site_chat.db"
cc = sqlite3.connect(chat_db)
cc.execute("CREATE TABLE subscriptions (order_id TEXT, chat_id INTEGER, created_at TEXT, last_text TEXT, fails INTEGER DEFAULT 0, done INTEGER DEFAULT 0, PRIMARY KEY(order_id, chat_id))")
cc.executemany("INSERT INTO subscriptions(order_id, chat_id, fails, done) VALUES(?,?,?,?)", [("PT-20", 801, 0, 1), ("PT-20", 802, 5, 0), ("PT-20", 803, 0, 0), ("PT-OTHER", 804, 0, 0), ("GRP-1", 805, 0, 0)])
cc.commit()
cc.close()
chk("subscribed_chats: підписки цього замовлення (done=1 теж), без мертвих (fails≥5) і без чужих",
    sorted(rl.subscribed_chats({"internal_order_id": "site_PT-20"}, chat_db)) == [801, 803])
chk("subscribed_chats: змішаний кошик — і за батьківським номером платежу (site_group_id)", sorted(rl.subscribed_chats({"internal_order_id": "site_PT-21", "site_group_id": "GRP-1"}, chat_db)) == [805])
chk("subscribed_chats: нема бази / не site_ / збій → [] без винятку", rl.subscribed_chats({"internal_order_id": "site_PT-20"}, Path(_td) / "nope.db") == [] and rl.subscribed_chats({"internal_order_id": "prom_1"}, chat_db) == [] and rl.subscribed_chats({}, chat_db) == [])
bad = Path(_td) / "bad.db"
bad.write_bytes(b"not a database")
chk("subscribed_chats: зіпсована база → [] без винятку", rl.subscribed_chats({"internal_order_id": "site_PT-20"}, bad) == [])
chk("customer_sender: без SITE_CHAT_BOT_TOKEN → None (нічого не шлемо)", rl.customer_sender() is None)
os.environ["SITE_CHAT_BOT_TOKEN"] = "123:TEST"
calls = []
import site_chat_bot  # noqa: E402
_orig = site_chat_bot.TelegramAPI.call
site_chat_bot.TelegramAPI.call = lambda self, method, **p: calls.append((method, p)) or {}
try:
    snd = rl.customer_sender()
    ok1 = snd(77, "привіт")
    site_chat_bot.TelegramAPI.call = lambda self, method, **p: (_ for _ in ()).throw(RuntimeError("telegram sendMessage: Forbidden: bot was blocked"))
    ok2 = snd(77, "привіт")
finally:
    site_chat_bot.TelegramAPI.call = _orig
    os.environ.pop("SITE_CHAT_BOT_TOKEN", None)
chk("customer_sender: sendMessage(chat_id, text) без прев'ю; помилка Telegram → False, не виняток", ok1 is True and calls and calls[0][0] == "sendMessage" and calls[0][1].get("chat_id") == 77 and calls[0][1].get("disable_web_page_preview") is True and ok2 is False)

# повний шлях: підписка в site_chat.db → відправка саме підписаному чату
os.environ["SITE_CHAT_DB"] = str(chat_db)
e1 = mk("PT-20", items, "2026-11-14T10:00:00")
sent.clear()
chk("повний шлях: підписки з site_chat.db → повідомлення рівно підписаним чатам (801, 803)", run(e1) == 1 and sorted(c for c, _ in sent) == [801, 803])

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
