#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_chat_bot.py — Telegram-бот «Написати» сайту (site_chat_bot.py, рішення власника 14.10: бот-приймальня + черга, відповідь власник реплаєм).
Інваріанти: (1) /start → привітання з посиланнями на delivery/returns/contacts (факти, без обіцянок термінів); /id → chat_id; (2) повідомлення клієнта: у чергу (номери карток 13–19 цифр маскуються),
підтвердження прийому ≤1 раз/6 год, копія власнику з міткою; (3) відповідь власника РЕПЛАЄМ → клієнту, зберігається; реплай на чуже/без реплая — підказка, клієнту нічого; ВІД ІНШОГО чату «режим власника» не діє;
(4) група/канал ігноруються; не-текст → нотатка; довжина ≤2000; ліміт 20/год → мовчки, одне повідомлення; (5) без SITE_CHAT_OWNER_CHAT_ID копії не шлються, у черзі forwarded=0, retry після появи власника;
(6) збій sendMessage не валить обробку; (7) digest без імен/username/chat_id/телефонів; (8) без токена CLI завершується кодом 0. `python test_site_chat_bot.py` → exit 0/1."""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["SITE_CHAT_DB"] = os.path.join(tempfile.mkdtemp(), "chat.db")
os.environ.pop("SITE_CHAT_BOT_TOKEN", None)

import site_chat_bot as sb  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


class FakeAPI:
    def __init__(self):
        self.sent = []
        self.fail_for = set()
        self.mid = 1000

    def call(self, method, **p):
        if method == "sendMessage":
            if p["chat_id"] in self.fail_for:
                raise RuntimeError("blocked")
            self.mid += 1
            self.sent.append((p["chat_id"], p["text"]))
            return {"message_id": self.mid}
        return []


OWNER, C1, C2 = 111, 501, 502
clock = {"t": datetime(2026, 10, 14, 15, 0, 0)}


def mk(owner=OWNER):
    api = FakeAPI()
    db = sb.ChatDB(os.path.join(tempfile.mkdtemp(), "c.db"))
    return sb.Bot(api, db, owner, now_fn=lambda: clock["t"]), api, db


def upd(chat_id, text=None, uid=1, ctype="private", reply_to=None, name="Олена", username="olena", **extra):
    m = {"chat": {"id": chat_id, "type": ctype}, "from": {"first_name": name, "username": username}}
    if text is not None:
        m["text"] = text
    if reply_to is not None:
        m["reply_to_message"] = {"message_id": reply_to}
    m.update(extra)
    return {"update_id": uid, "message": m}


bot, api, db = mk()
bot.handle_update(upd(C1, "/start"))
chk("/start → привітання з трьома посиланнями, без обіцянок термінів", api.sent[-1][0] == C1 and all(x in api.sent[-1][1] for x in ("/delivery.html", "/returns.html", "/contacts.html")) and "хвилин" not in api.sent[-1][1] and "годин" not in api.sent[-1][1])
chk("/start не потрапляє в чергу й не пересилається власнику", not db.q("SELECT 1 FROM messages") and all(c != OWNER for c, _ in api.sent))
bot.handle_update(upd(C1, "/id"))
chk("/id → chat_id клієнта", api.sent[-1] == (C1, f"Ваш chat_id: {C1}"))

api.sent.clear()
bot.handle_update(upd(C1, "Доброго дня, де моє замовлення PT-123? Телефон 0671234567, картка 4111 1111 1111 1111"))
rows = db.q("SELECT * FROM messages")
chk("повідомлення збережено, номер картки замасковано, телефон лишився (потрібен для відповіді по замовленню)", len(rows) == 1 and "4111" not in rows[0]["text"] and "[номер приховано]" in rows[0]["text"] and "0671234567" in rows[0]["text"])
to_c1 = [t for c, t in api.sent if c == C1]
to_owner = [t for c, t in api.sent if c == OWNER]
chk("клієнту — підтвердження прийому; власнику — копія з міткою клієнта й підказкою «реплаєм»", to_c1 == [sb.ACK] and len(to_owner) == 1 and "чат 501" in to_owner[0] and "@olena" in to_owner[0] and "РЕПЛАЄМ" in to_owner[0] and "4111" not in to_owner[0])
owner_msg_id = db.q("SELECT owner_msg_id FROM messages")[0]["owner_msg_id"]
chk("зв'язок «копія власнику ↔ клієнт» збережено (owner_msg_id)", owner_msg_id is not None)

api.sent.clear()
bot.handle_update(upd(C1, "Ще питання: можна змінити відділення?", uid=2))
chk("друге повідомлення за 6 год: підтвердження прийому НЕ повторюється, копія власнику йде", [c for c, _ in api.sent] == [OWNER])
clock["t"] += timedelta(hours=7)
api.sent.clear()
bot.handle_update(upd(C1, "Є хтось?", uid=3))
chk("через 7 год підтвердження прийому знову", [c for c, t in api.sent if c == C1] == [C1])

# відповідь власника
api.sent.clear()
bot.handle_update(upd(OWNER, "Вітаю, перевіряємо, напишемо номер ТТН.", reply_to=owner_msg_id))
chk("відповідь власника реплаєм → клієнту, збережена як out", api.sent == [(C1, "Вітаю, перевіряємо, напишемо номер ТТН.")] and db.q("SELECT count(*) AS n FROM messages WHERE direction='out'")[0]["n"] == 1)
api.sent.clear()
bot.handle_update(upd(OWNER, "просто текст без реплая"))
chk("текст власника без реплая → підказка власнику, клієнту нічого", [c for c, _ in api.sent] == [OWNER] and "РЕПЛАЄМ" in api.sent[0][1])
api.sent.clear()
bot.handle_update(upd(OWNER, "відповідь", reply_to=999999))
chk("реплай на чуже повідомлення → власнику «не знайшов клієнта», клієнту нічого", [c for c, _ in api.sent] == [OWNER] and "Не знайшов" in api.sent[0][1])
api.sent.clear()
bot.handle_update(upd(C2, "Я теж хочу відповісти як власник", reply_to=owner_msg_id))
chk("інший чат із реплаєм на копію власника НЕ відповідає клієнту (це просто повідомлення клієнта C2)", all(c != C1 for c, _ in api.sent) and db.q("SELECT count(*) AS n FROM messages WHERE chat_id=? AND direction='in'", (C2,))[0]["n"] == 1)

# захисти
api.sent.clear()
bot.handle_update(upd(-100500, "привіт у групі", ctype="supergroup"))
chk("група/канал ігноруються (нічого не надіслано й не збережено)", not api.sent and not db.q("SELECT 1 FROM messages WHERE chat_id=-100500"))
bot.handle_update(upd(C2, None, photo=[{"file_id": "x"}]))
chk("не-текст (фото) → нотатка клієнту, у чергу не пишеться", api.sent[-1] == (C2, sb.NON_TEXT) and db.q("SELECT count(*) AS n FROM messages WHERE chat_id=? AND text LIKE '%photo%'", (C2,))[0]["n"] == 0)
bot.handle_update(upd(C2, "x" * 5000))
chk("довге повідомлення обрізається до 2000", max(len(r["text"]) for r in db.q("SELECT text FROM messages WHERE chat_id=?", (C2,))) <= 2000)

bot2, api2, db2 = mk()
for i in range(sb.RATE_N):
    bot2.handle_update(upd(C1, f"m{i}", uid=i))
api2.sent.clear()
for i in range(5):
    bot2.handle_update(upd(C1, f"спам {i}", uid=100 + i))
chk("після 20/год: повідомлення не зберігаються й не пересилаються, клієнту ОДНЕ повідомлення про ліміт", db2.q("SELECT count(*) AS n FROM messages")[0]["n"] == sb.RATE_N and [t for c, t in api2.sent if c == C1] == [sb.RATE_NOTICE] and not [1 for c, _ in api2.sent if c == OWNER])
clock["t"] += timedelta(hours=2)
api2.sent.clear()
bot2.handle_update(upd(C1, "знову можна", uid=200))
chk("через 2 год ліміт знято: повідомлення приймається", db2.q("SELECT count(*) AS n FROM messages")[0]["n"] == sb.RATE_N + 1)

# без власника
bot3, api3, db3 = mk(owner=None)
bot3.handle_update(upd(C1, "Питання без власника"))
chk("без SITE_CHAT_OWNER_CHAT_ID: збережено в черзі, копії нема, forwarded=0", db3.q("SELECT forwarded FROM messages")[0]["forwarded"] == 0 and all(c == C1 for c, _ in api3.sent))
bot3.owner = OWNER
chk("після появи власника retry_unforwarded пересилає накопичене", bot3.retry_unforwarded() == 1 and db3.q("SELECT forwarded FROM messages")[0]["forwarded"] == 1)

# збій відправки
bot4, api4, db4 = mk()
api4.fail_for = {OWNER}
bot4.handle_update(upd(C1, "Питання, власник недоступний"))
chk("збій відправки власнику: виняток не виходить, повідомлення у черзі forwarded=0", db4.q("SELECT forwarded FROM messages")[0]["forwarded"] == 0)
api4.fail_for = set()
chk("retry після відновлення доставляє", bot4.retry_unforwarded() == 1)

# digest
d = sb.digest(db, 1, now=clock["t"])
chk("digest: кількості є, без імен/username/chat_id, телефон замасковано", "Олена" not in d and "olena" not in d and "501" not in d and "0671234567" not in d and "[телефон]" in d and "Запити клієнтів сайту" in d)

# CLI без токена
rc = sb.main(["site_chat_bot.py", "run"])
chk("без SITE_CHAT_BOT_TOKEN run → код 0 (не крутимо цикл)", rc == 0)

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
