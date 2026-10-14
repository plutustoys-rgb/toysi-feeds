# -*- coding: utf-8 -*-
"""site_chat_bot.py — Telegram-бот «Написати» для сайту plutustoys.com.ua (рішення власника 14.10.2026: варіант «бот-приймальня» + черга; кнопка в меню месенджерів сайту веде сюди).

ЩО РОБИТЬ (без LLM, без обіцянок):
  • /start → привітання з фактами (посилання на сторінки «Доставка», «Повернення», «Контакти»); /id → клієнт бачить свій chat_id (потрібно лише власнику, щоб прописати SITE_CHAT_OWNER_CHAT_ID).
  • Кожне текстове повідомлення клієнта: зберігається у ПРИВАТНУ чергу site_chat.db (номери карток маскуються), клієнту один раз за 6 год — підтвердження прийому, власнику (SITE_CHAT_OWNER_CHAT_ID) — копія з міткою клієнта.
  • Власник відповідає РЕПЛАЄМ на копію → бот пересилає текст клієнту (відповідь зберігається). Відповіді від інших чатів на «режим власника» не діють.
  • Захист: лише приватні чати, текст ≤2000 символів, ≤20 повідомлень/год на клієнта (далі мовчки ігноруємо, раз повідомляємо), не-текст (фото/файли) не зберігаємо, номери карток 13–19 цифр маскуємо перед записом.
  • Аудитор (plutus-executor) не має каналу до VPS: щоденну збірку запитів (без особистих даних) передає Код за вимогою — `python site_chat_bot.py digest`.
ЗАПУСК: systemd site-chat-bot.service (Type=simple, long-polling getUpdates). Без SITE_CHAT_BOT_TOKEN у .env сервіс просто завершується (код 0) — токен створює власник у @BotFather і кладе ТІЛЬКИ в .env на VPS.
CLI: run | digest | list-new | reply <chat_id> <текст>.
"""
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
try:                                  # .env на VPS (SITE_CHAT_BOT_TOKEN, SITE_CHAT_OWNER_CHAT_ID), як у telegram_notify/liqpay_client
    from dotenv import load_dotenv
    load_dotenv(HERE / ".env")
except Exception:  # noqa: BLE001
    pass
DB_PATH = Path(os.environ.get("SITE_CHAT_DB", str(HERE / "site_chat.db")))
BASE_URL = "https://plutustoys.com.ua"
MAX_LEN = 2000
RATE_N = 20                      # повідомлень від одного клієнта за RATE_WINDOW
RATE_WINDOW = timedelta(hours=1)
ACK_EVERY = timedelta(hours=6)   # підтвердження прийому клієнту не частіше

GREETING = ("Вітаємо в PlutusToys! Напишіть своє питання, ми прочитаємо його й відповімо тут. "
            "Будь ласка, не надсилайте дані банківської картки, паролі та коди з SMS.\n\n"
            f"Доставка: {BASE_URL}/delivery.html\n"
            f"Повернення і обмін: {BASE_URL}/returns.html\n"
            f"Контакти: {BASE_URL}/contacts.html" + chr(10) + chr(10) +
            "Ваші повідомлення зберігаються лише для обробки звернення.")
ACK = "Дякуємо, повідомлення отримано. Ми прочитаємо його й відповімо тут."
NON_TEXT = "Поки що ми приймаємо лише текстові повідомлення. Напишіть, будь ласка, питання словами."
RATE_NOTICE = "Забагато повідомлень за короткий час. Ми їх бачимо, зачекайте, будь ласка, на відповідь."
OWNER_HELP = "Режим власника: відповідайте РЕПЛАЄМ на повідомлення клієнта, і я перешлю відповідь клієнту."

# український номер телефону (0XXXXXXXXX, +380…); решту довгих чисел (ТТН 14 цифр, номери замовлень) НЕ чіпаємо
_PHONE_RE = re.compile(r"(?<![\w-])(?:\+?38)?0\d{9}(?![\w-])")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# кандидат у номер картки: 13–19 цифр, дозволені роздільники пробіл/дефіс/крапка між цифрами; не частина слова/ідентифікатора (PT-20261010-142322-…)
_CARD_RE = re.compile(r"(?<![\w-])\d(?:[ .-]?\d){12,18}(?![\w-])")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def mask_cards(text: str) -> str:
    """Номери банківських карток → [номер приховано]: кандидат 13–19 цифр, що проходить Luhn. 14-значне число БЕЗ роздільників (ТТН Нової Пошти) і довгі ідентифікатори (PT-…-…) не чіпаємо:
    власнику потрібен ТТН/номер замовлення клієнта, а маскувати хибно означало б сліпе «де моя посилка»."""
    def repl(m):
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        if not 13 <= len(digits) <= 19 or not _luhn(digits):
            return raw
        if len(digits) == 14 and digits == raw:           # ТТН НП: 14 цифр підряд
            return raw
        return "[номер приховано]"
    return _CARD_RE.sub(repl, text or "")


class ChatDB:
    def __init__(self, path=None):
        self.path = str(path or DB_PATH)
        with self._c() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS customers (chat_id INTEGER PRIMARY KEY, first_name TEXT, username TEXT, first_seen TEXT, last_seen TEXT, last_ack TEXT, rate_notice_at TEXT);
            CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, direction TEXT, text TEXT, ts TEXT, forwarded INTEGER DEFAULT 0, owner_msg_id INTEGER);
            CREATE INDEX IF NOT EXISTS idx_msg_chat ON messages(chat_id, ts);
            CREATE INDEX IF NOT EXISTS idx_msg_owner ON messages(owner_msg_id);
            CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT);""")
        try:
            os.chmod(self.path, 0o600)       # у черзі чужі ПІБ/телефони: лише власник процесу
        except OSError:
            pass

    def _c(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def run(self, sql, args=()):
        c = self._c()
        try:
            cur = c.execute(sql, args)
            c.commit()
            return cur
        finally:
            c.close()

    def q(self, sql, args=()):
        c = self._c()
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()

    def get_state(self, k, default=None):
        r = self.q("SELECT v FROM state WHERE k=?", (k,))
        return r[0]["v"] if r else default

    def set_state(self, k, v):
        self.run("INSERT INTO state(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


class TelegramAPI:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"

    def call(self, method: str, **params):
        import requests
        try:
            r = requests.post(f"{self.base}/{method}", json=params, timeout=40)
            d = r.json()
        except Exception as e:  # noqa: BLE001 — повідомлення requests містить URL з токеном: у лог/виняток його НЕ пускаємо
            raise RuntimeError(f"{type(e).__name__} (telegram {method})") from None
        if not d.get("ok"):
            raise RuntimeError(f"telegram {method}: {str(d.get('description'))[:100]}")
        return d["result"]


class Bot:
    def __init__(self, api, db: ChatDB, owner_chat_id=None, now_fn=None):
        self.api, self.db = api, db
        try:
            self.owner = int(owner_chat_id) if owner_chat_id not in (None, "") else None
        except (TypeError, ValueError):
            print("[site_chat_bot] SITE_CHAT_OWNER_CHAT_ID не число — режим власника вимкнено", file=sys.stderr)
            self.owner = None
        self.now = now_fn or datetime.now

    def _send(self, chat_id, text, **kw):
        try:
            return self.api.call("sendMessage", chat_id=chat_id, text=text, **kw)
        except Exception as e:  # noqa: BLE001
            print(f"[site_chat_bot] sendMessage → {chat_id} не вдалось: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
            return None

    def handle_update(self, upd: dict) -> None:
        msg = upd.get("message")
        if not isinstance(msg, dict):
            return
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            return
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return
        text = msg.get("text")
        if isinstance(text, str) and text.strip().split("@")[0] == "/id":
            self._send(chat_id, f"Ваш chat_id: {chat_id}")
            return
        if self.owner is not None and chat_id == self.owner:
            self._owner_message(msg)
        else:
            self._customer_message(msg, chat_id)

    # ── клієнт
    def _customer_message(self, msg, chat_id):
        now = self.now()
        iso = now.replace(microsecond=0).isoformat()
        frm = msg.get("from") or {}
        self.db.run("INSERT INTO customers(chat_id, first_name, username, first_seen, last_seen) VALUES(?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET first_name=excluded.first_name, username=excluded.username, last_seen=excluded.last_seen",
                    (chat_id, str(frm.get("first_name") or "")[:60], str(frm.get("username") or "")[:60], iso, iso))
        text = msg.get("text")
        if isinstance(text, str) and text.strip().startswith("/start"):
            self._send(chat_id, GREETING)
            return
        if not isinstance(text, str) or not text.strip():
            self._send(chat_id, NON_TEXT)
            return
        since = (now - RATE_WINDOW).replace(microsecond=0).isoformat()
        n = self.db.q("SELECT count(*) AS n FROM messages WHERE chat_id=? AND direction='in' AND ts>=?", (chat_id, since))[0]["n"]
        if n >= RATE_N:
            cust = self.db.q("SELECT rate_notice_at FROM customers WHERE chat_id=?", (chat_id,))[0]
            if not cust["rate_notice_at"] or cust["rate_notice_at"] < since:
                self._send(chat_id, RATE_NOTICE)
                self.db.run("UPDATE customers SET rate_notice_at=? WHERE chat_id=?", (iso, chat_id))
            return
        clean = mask_cards(text.strip())[:MAX_LEN]
        cur = self.db.run("INSERT INTO messages(chat_id, direction, text, ts) VALUES(?,?,?,?)", (chat_id, "in", clean, iso))
        mid = cur.lastrowid
        cust = self.db.q("SELECT last_ack FROM customers WHERE chat_id=?", (chat_id,))[0]
        if not cust["last_ack"] or cust["last_ack"] < (now - ACK_EVERY).replace(microsecond=0).isoformat():
            if self._send(chat_id, ACK):
                self.db.run("UPDATE customers SET last_ack=? WHERE chat_id=?", (iso, chat_id))
        self._forward(mid)

    def _forward(self, mid: int) -> bool:
        """Копія клієнтського повідомлення власнику (з міткою клієнта). Без SITE_CHAT_OWNER_CHAT_ID — лишається в черзі (forwarded=0)."""
        if self.owner is None:
            return False
        r = self.db.q("SELECT m.chat_id, m.text, c.first_name, c.username FROM messages m JOIN customers c ON c.chat_id=m.chat_id WHERE m.id=?", (mid,))
        if not r:
            return False
        r = r[0]
        who = (r["first_name"] or "клієнт") + (f" @{r['username']}" if r["username"] else "")
        res = self._send(self.owner, f"💬 {who} (чат {r['chat_id']}):\n{r['text']}\n\n↩️ Відповідайте РЕПЛАЄМ на це повідомлення.")
        if not res:
            return False
        self.db.run("UPDATE messages SET forwarded=1, owner_msg_id=? WHERE id=?", (res.get("message_id"), mid))
        return True

    def retry_unforwarded(self) -> int:
        n = 0
        for r in self.db.q("SELECT id FROM messages WHERE direction='in' AND forwarded=0 ORDER BY id LIMIT 50"):
            if self._forward(r["id"]):
                n += 1
        return n

    # ── власник
    def _owner_message(self, msg):
        text = msg.get("text")
        reply = msg.get("reply_to_message")
        if isinstance(text, str) and text.strip().startswith("/"):
            self._send(self.owner, OWNER_HELP)
            return
        if not isinstance(text, str) or not text.strip():
            return
        if not isinstance(reply, dict) or not reply.get("message_id"):
            self._send(self.owner, OWNER_HELP)
            return
        r = self.db.q("SELECT chat_id FROM messages WHERE owner_msg_id=? ORDER BY id DESC LIMIT 1", (reply["message_id"],))
        if not r:
            self._send(self.owner, "Не знайшов клієнта для цього повідомлення (відповідайте реплаєм саме на копію від бота).")
            return
        ok = self.reply_to(r[0]["chat_id"], text.strip()[:MAX_LEN])
        self._send(self.owner, "✅ Надіслано клієнту." if ok else "⚠️ Не вдалося надіслати клієнту (він міг заблокувати бота).")

    def reply_to(self, chat_id: int, text: str) -> bool:
        ok = self._send(chat_id, text)
        if ok:
            self.db.run("INSERT INTO messages(chat_id, direction, text, ts, forwarded) VALUES(?,?,?,?,1)", (chat_id, "out", text, self.now().replace(microsecond=0).isoformat()))
        return bool(ok)

    # ── цикл
    def poll_once(self, timeout: int = 25) -> int:
        offset = int(self.db.get_state("offset", "0"))
        updates = self.api.call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message"])
        for u in updates:
            try:
                self.handle_update(u)
            except Exception as e:  # noqa: BLE001
                print(f"[site_chat_bot] update {u.get('update_id')}: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
            self.db.set_state("offset", u["update_id"] + 1)
        self.retry_unforwarded()
        return len(updates)


def digest(db: ChatDB, days: int = 1, now: datetime | None = None) -> str:
    """Збірка для Аудитора: кількість клієнтів/повідомлень і тексти БЕЗ імен, username, chat_id, телефонів і e-mail (вільний текст може містити інші дані, напр. адресу; ТТН і номери замовлень лишаються — вони потрібні для відповіді)."""
    now = now or datetime.now()
    since = (now - timedelta(days=days)).replace(microsecond=0).isoformat()
    rows = db.q("SELECT chat_id, text FROM messages WHERE direction='in' AND ts>=? ORDER BY id", (since,))
    answered = db.q("SELECT count(DISTINCT chat_id) AS n FROM messages WHERE direction='out' AND ts>=?", (since,))[0]["n"]
    ids = {}
    out = [f"Запити клієнтів сайту за {days} добу: повідомлень {len(rows)}, клієнтів {len({r['chat_id'] for r in rows})}, клієнтам відповіли {answered}."]
    for r in rows:
        k = ids.setdefault(r["chat_id"], len(ids) + 1)
        masked = _EMAIL_RE.sub("[e-mail]", _PHONE_RE.sub("[телефон]", r["text"]))[:300]
        out.append(f"  К{k}: {masked}")
    return "\n".join(out)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    token = os.environ.get("SITE_CHAT_BOT_TOKEN", "").strip()
    db = ChatDB()
    if cmd == "digest":
        print(digest(db, int(argv[2]) if len(argv) > 2 else 1))
        return 0
    if cmd == "list-new":
        for r in db.q("SELECT m.id, m.ts, m.chat_id, m.text FROM messages m WHERE m.direction='in' AND m.chat_id NOT IN (SELECT chat_id FROM messages WHERE direction='out') ORDER BY m.id"):
            print(f"#{r['id']} {r['ts']} чат {r['chat_id']}: {r['text'][:300]}")
        return 0
    if not token:
        print("[site_chat_bot] SITE_CHAT_BOT_TOKEN не задано в .env — бот не запущено (створіть бота в @BotFather і додайте токен у .env на VPS).")
        return 0
    bot = Bot(TelegramAPI(token), db, os.environ.get("SITE_CHAT_OWNER_CHAT_ID", ""))
    if cmd == "reply" and len(argv) >= 4:
        return 0 if bot.reply_to(int(argv[2]), " ".join(argv[3:])) else 1
    if cmd == "run":
        print("[site_chat_bot] запущено" + ("" if bot.owner is not None else " (SITE_CHAT_OWNER_CHAT_ID не задано: копії власнику не надсилаються, повідомлення лишаються в черзі)"))
        errors = 0
        while True:
            try:
                bot.poll_once()
                errors = 0
            except Exception as e:  # noqa: BLE001
                errors += 1
                print(f"[site_chat_bot] poll збій #{errors}: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
                time.sleep(min(60, 2 ** min(errors, 6)))
    print("використання: python site_chat_bot.py run | digest [днів] | list-new | reply <chat_id> <текст>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
