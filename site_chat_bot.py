# -*- coding: utf-8 -*-
"""site_chat_bot.py — Telegram-бот «Написати» для сайту plutustoys.com.ua (рішення власника 14.10.2026: варіант «бот-приймальня» + черга; кнопка в меню месенджерів сайту веде сюди).

ЩО РОБИТЬ (без LLM, без обіцянок):
  • /start → привітання з фактами (посилання на сторінки «Доставка», «Повернення», «Контакти»); /id → клієнт бачить свій chat_id (потрібно лише власнику, щоб прописати SITE_CHAT_OWNER_CHAT_ID).
  • Кожне текстове повідомлення клієнта: зберігається у ПРИВАТНУ чергу site_chat.db (номери карток маскуються), клієнту один раз за 6 год — підтвердження прийому, власнику (SITE_CHAT_OWNER_CHAT_ID) — копія з міткою клієнта.
  • Власник відповідає РЕПЛАЄМ на копію → бот пересилає текст клієнту (відповідь зберігається). Відповіді від інших чатів на «режим власника» не діють.
  • АВТОВІДПОВІДЬ ПРО СТАТУС (16.10, рішення власника «статус треба віддавати клієнту»): повідомлення з номером замовлення сайту PT-YYYYMMDD-HHMMSS-xxxxxx І останніми 4 цифрами телефону замовлення → бот читає orders.db (mode=ro, platform='site') і відповідає станом (передано на комплектацію / укомплектовано / відправлено / доставлено / скасовано / очікуємо оплату) + ТТН НП із посиланням відстеження; без ПІБ, адреси, телефону, сум. Хибні/відсутні 4 цифри і неіснуюче замовлення — однакова відповідь; ≥5 невдалих звірок на замовлення за добу → автовідповідь по ньому вимкнена; невідомий стан (expired, sandbox) → відповідь дає людина. Копія власнику з міткою «🤖».
  • ПІДПИСКА НА СТАТУС (16.10, варіант «2» власника): кнопка «Статус у Telegram» на сторінці «Дякуємо» веде на https://t.me/<бот>?start=PT-… ; клієнт натискає «Старт» → бот прив'язує чат до замовлення (≤3 чатів на замовлення, ≤10 активних підписок на чат) і щохвилини (notify_changes у poll_once) пише клієнту про кожну ЗМІНУ стану: комплектація / ТТН / відправлено / доставлено / скасовано. Джерело — orders.db (mode=ro, platform='site'); невідомий стан нічого не шле; доставлено/скасовано закриває підписку; 5 невдалих відправок підряд (клієнт заблокував бота) закривають її. Без ПІБ/адреси/сум.
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
ORDERS_DB = Path(os.environ.get("SITE_CHAT_ORDERS_DB", str(HERE / "orders.db")))   # лише читання (mode=ro): статус замовлення для автовідповіді
BASE_URL = "https://plutustoys.com.ua"
STATUS_FAIL_LIMIT = 5            # невдалих звірок (номер + 4 цифри телефону) на одне замовлення за добу; далі автовідповідь про статус вимкнено (захист від добору 4 цифр)
MAX_LEN = 2000
RATE_N = 20                      # повідомлень від одного клієнта за RATE_WINDOW
RATE_WINDOW = timedelta(hours=1)
ACK_EVERY = timedelta(hours=6)   # підтвердження прийому клієнту не частіше

GREETING = ("Вітаємо в PlutusToys! Напишіть своє питання, ми прочитаємо його й відповімо тут. "
            "Будь ласка, не надсилайте дані банківської картки, паролі та коди з SMS.\n\n"
            f"Доставка: {BASE_URL}/delivery.html\n"
            f"Повернення і обмін: {BASE_URL}/returns.html\n"
            f"Контакти: {BASE_URL}/contacts.html" + chr(10) + chr(10) +
            "Щоб дізнатися статус замовлення, надішліть його номер (PT-…) і останні 4 цифри телефону, на який оформлено замовлення." + chr(10) + chr(10) +
            "Ваші повідомлення зберігаються лише для обробки звернення.")
SUB_PER_ORDER = 3                # чатів на одне замовлення (покупець + родина), далі «підписку» не додаємо
SUB_PER_CHAT = 10                # активних підписок на один чат
SUB_FAIL_LIMIT = 5               # невдалих відправок підряд (клієнт заблокував бота) → підписку закрито; тимчасові помилки Telegram (429/мережа) не рахуються
START_FAIL_LIMIT = 10            # хибних «/start PT-…» з одного чату за годину; далі мовчимо
NOTIFY_EVERY = 60                # секунд між перевірками змін статусів підписаних замовлень
SUB_WELCOME = "Дякуємо! Тут ми повідомлятимемо вас про зміни статусу замовлення {oid}."
STATUS_NEED_PHONE = "Щоб показати статус замовлення, надішліть в ОДНОМУ повідомленні номер замовлення (PT-…) і рівно одне число з останніх 4 цифр телефону, на який воно оформлено."
STATUS_NOT_FOUND = "Не вдалося знайти замовлення за цими даними. Перевірте номер (PT-…) і останні 4 цифри телефону; ми також прочитаємо ваше повідомлення й відповімо тут."
ACK = "Дякуємо, повідомлення отримано. Ми прочитаємо його й відповімо тут."
NON_TEXT = "Поки що ми приймаємо лише текстові повідомлення. Напишіть, будь ласка, питання словами."
RATE_NOTICE = "Забагато повідомлень за короткий час. Ми їх бачимо, зачекайте, будь ласка, на відповідь."
OWNER_HELP = "Режим власника: відповідайте РЕПЛАЄМ на повідомлення клієнта, і я перешлю відповідь клієнту."

# український номер телефону (0XXXXXXXXX, +380…); решту довгих чисел (ТТН 14 цифр, номери замовлень) НЕ чіпаємо
_PHONE_RE = re.compile(r"(?<![\w-])(?:\+?38[\s().-]*)?0[\s().-]*\d{2}[\s().-]*\d{3}[\s().-]*\d{2}[\s().-]*\d{2}(?![\w-])")
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
            CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS status_fails (order_id TEXT, ts TEXT);
            CREATE TABLE IF NOT EXISTS subscriptions (order_id TEXT, chat_id INTEGER, created_at TEXT, last_text TEXT, fails INTEGER DEFAULT 0, done INTEGER DEFAULT 0, PRIMARY KEY(order_id, chat_id));""")
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


_ORDER_RE = re.compile(r"(?<![\w-])PT-\d{8}-\d{6}-[0-9a-f]{6}(?![\w-])", re.I)
_LAST4_RE = re.compile(r"(?<![\w+-])(\d{4})(?![\w-])")
_TTN_RE = re.compile(r"^\d{10,20}$")


def order_status_request(text: str):
    """(номер замовлення, набір кандидатів «останні 4 цифри») із тексту клієнта, або None, якщо номера PT-… нема. Кандидати: окремі 4-значні числа поза номером замовлення і останні 4 цифри повного українського номера."""
    m = _ORDER_RE.search(text or "")
    if not m:
        return None
    rest = text[:m.start()] + " " + text[m.end():]
    cands = set(_LAST4_RE.findall(rest))
    for pm in _PHONE_RE.finditer(rest):
        d = re.sub(r"\D", "", pm.group(0))
        if len(d) >= 4:
            cands.add(d[-4:])
    return "PT-" + m.group(0)[3:].lower(), cands          # у БД номер у форматі PT-YYYYMMDD-HHMMSS-<hex малими>


def order_status_text(row) -> str | None:
    """Текст статусу замовлення сайту для клієнта (лише стан і ТТН, без ПІБ/адреси/телефону/сум); None — стан невідомий, автовідповіді не даємо (відповість людина)."""
    st = (row["status"] or "").lower()
    ds = (row["delivery_status"] or "").lower()
    if st == "sandbox_test":
        return None
    if "cancel" in st or ds == "cancelled" or row["cancelled_at"]:
        return "Замовлення скасовано. Якщо це сталося не за вашим бажанням, напишіть нам тут."
    if (row["payment_method"] or "") == "prepaid" and not row["payment_confirmed"]:
        return "Замовлення прийнято, очікуємо підтвердження оплати."
    if st == "new":
        return "Замовлення прийнято й обробляється."
    if st != "forwarded_to_supplier":
        return None             # toysi_error / escalated / awaiting_manual_confirmation / deleted…: відповідає людина, не «обробляється»
    ttn = str(row["toysi_ttn"] or "").strip()
    link = (chr(10) + f"ТТН Нової Пошти: {ttn}" + chr(10) + f"Відстежити: https://novaposhta.ua/tracking/?cargo_number={ttn}") if _TTN_RE.match(ttn) else ""
    if ds == "delivered":
        return "Замовлення доставлено." + link
    if ds == "shipped":
        return "Замовлення відправлено Новою Поштою." + link
    if ds == "packed":
        return "Замовлення укомплектовано, чекає відправлення." + link
    if ds in ("processing", "assembling", ""):
        return "Замовлення передано на комплектацію."
    return None


def fetch_order_row(oid: str):
    """Рядок замовлення сайту з orders.db (лише читання) або None; виняток — якщо база недоступна."""
    import sqlite3 as _sq
    oc = _sq.connect(Path(ORDERS_DB).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    oc.row_factory = _sq.Row
    try:
        return oc.execute("SELECT status, delivery_status, payment_method, payment_confirmed, toysi_ttn, cancelled_at, phone FROM orders WHERE internal_order_id=? AND platform='site'", ("site_" + oid,)).fetchone()
    finally:
        oc.close()


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
            self._last_send_error = str(e)
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
            parts = text.strip().split(None, 1)
            payload = parts[1].strip() if len(parts) > 1 else ""
            if payload and _ORDER_RE.fullmatch(payload):
                self._subscribe(chat_id, "PT-" + payload[3:].lower(), now)       # кнопка «Статус у Telegram» на сторінці «Дякуємо»: t.me/<бот>?start=PT-…
            else:
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
        try:
            note, replied = self._auto_status(chat_id, clean, now)
        except Exception as e:  # noqa: BLE001 — збій автовідповіді не має забрати ACK і копію власнику
            print(f"[site_chat_bot] автовідповідь про статус: {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
            note, replied = "🤖 автовідповідь про статус не спрацювала: відповісти вручну", False
        cust = self.db.q("SELECT last_ack FROM customers WHERE chat_id=?", (chat_id,))[0]
        if not replied and (not cust["last_ack"] or cust["last_ack"] < (now - ACK_EVERY).replace(microsecond=0).isoformat()):
            if self._send(chat_id, ACK):
                self.db.run("UPDATE customers SET last_ack=? WHERE chat_id=?", (iso, chat_id))
        self._forward(mid, note)

    def _subscribe(self, chat_id, oid: str, now) -> None:
        """Прив'язує чат до замовлення (клієнт сам натиснув «Старт» за посиланням зі сторінки «Дякуємо»): далі notify_changes() пише про кожну зміну стану."""
        since = (now - timedelta(hours=1)).replace(microsecond=0).isoformat()
        if self.db.q("SELECT count(*) AS n FROM status_fails WHERE order_id=? AND ts>=?", (f"start:{chat_id}", since))[0]["n"] >= START_FAIL_LIMIT:
            return                  # забагато хибних /start PT-… з цього чату за годину: мовчимо (захист від добору номерів і флуду)
        try:
            row = fetch_order_row(oid)
        except Exception as e:  # noqa: BLE001
            print(f"[site_chat_bot] orders.db не прочитано (підписка): {type(e).__name__}", file=sys.stderr)
            self._send(chat_id, GREETING)
            return
        if row is None or (row["status"] or "").lower() == "sandbox_test":
            self.db.run("INSERT INTO status_fails(order_id, ts) VALUES(?,?)", (f"start:{chat_id}", now.replace(microsecond=0).isoformat()))
            self._send(chat_id, STATUS_NOT_FOUND)
            return
        iso = now.replace(microsecond=0).isoformat()
        have = self.db.q("SELECT 1 FROM subscriptions WHERE order_id=? AND chat_id=?", (oid, chat_id))
        if not have:
            per_order = self.db.q("SELECT count(*) AS n FROM subscriptions WHERE order_id=?", (oid,))[0]["n"]
            per_chat = self.db.q("SELECT count(*) AS n FROM subscriptions WHERE chat_id=? AND done=0", (chat_id,))[0]["n"]
            if per_order >= SUB_PER_ORDER or per_chat >= SUB_PER_CHAT:
                self._send(chat_id, "На це замовлення вже підключено максимум чатів. Напишіть нам тут, і ми допоможемо.")
                return
            self.db.run("INSERT INTO subscriptions(order_id, chat_id, created_at, last_text) VALUES(?,?,?,?)", (oid, chat_id, iso, ""))
        txt = order_status_text(row)
        msg = SUB_WELCOME.format(oid=oid) + (chr(10) + chr(10) + "Зараз: " + txt if txt else "")
        if self._send(chat_id, msg) and txt:
            final = (row["delivery_status"] or "").lower() in ("delivered", "cancelled") or "cancel" in (row["status"] or "").lower() or bool(row["cancelled_at"])
            self.db.run("UPDATE subscriptions SET last_text=?, done=?, fails=0 WHERE order_id=? AND chat_id=?", (txt, 1 if final else 0, oid, chat_id))

    def notify_changes(self) -> int:
        """Для кожної активної підписки: стан замовлення змінився (текст статусу інший, ніж востаннє надісланий) → пишемо клієнту. Невідомий стан (None) нічого не шле. Доставлено/скасовано → підписку закрито. Повертає кількість надісланих."""
        subs = self.db.q("SELECT order_id, chat_id, last_text FROM subscriptions WHERE done=0 AND fails<?", (SUB_FAIL_LIMIT,))
        sent = 0
        for s in subs:
            try:
                row = fetch_order_row(s["order_id"])
            except Exception as e:  # noqa: BLE001
                print(f"[site_chat_bot] orders.db не прочитано (сповіщення): {type(e).__name__}", file=sys.stderr)
                return sent
            if row is None:
                continue
            txt = order_status_text(row)
            if not txt or txt == (s["last_text"] or ""):
                continue
            if self._send(s["chat_id"], f"Замовлення {s['order_id']}: {txt}"):
                final = (row["delivery_status"] or "").lower() == "delivered" or "cancel" in (row["status"] or "").lower() or (row["delivery_status"] or "").lower() == "cancelled" or bool(row["cancelled_at"])
                self.db.run("UPDATE subscriptions SET last_text=?, fails=0, done=? WHERE order_id=? AND chat_id=?", (txt, 1 if final else 0, s["order_id"], s["chat_id"]))
                sent += 1
            elif any(k in str(getattr(self, "_last_send_error", "")).lower() for k in ("forbidden", "blocked", "chat not found", "deactivated")):
                self.db.run("UPDATE subscriptions SET fails=fails+1 WHERE order_id=? AND chat_id=?", (s["order_id"], s["chat_id"]))      # клієнт заблокував бота / чат зник: остаточно
            # інакше (429, мережа, 5xx) — тимчасово: без лічильника, повтор через хвилину
        return sent

    def _auto_status(self, chat_id, text: str, now) -> tuple:
        """Автовідповідь про статус замовлення сайту → (мітка для копії власнику або '', чи надіслано відповідь клієнту)."""
        req = order_status_request(text)
        if not req:
            return "", False
        oid, cands = req
        if len(cands) != 1:          # 0 — нема цифр; >1 — це добір (кожне 4-значне число було б окремою спробою): звіряємо лише ОДНЕ число
            self.reply_to(chat_id, STATUS_NEED_PHONE, kind="auto")
            return "🤖 просила номер + рівно одне число з 4 цифр", True
        since = (now - timedelta(days=1)).replace(microsecond=0).isoformat()
        fails = self.db.q("SELECT count(*) AS n FROM status_fails WHERE order_id=? AND ts>=?", (oid, since))[0]["n"]
        if fails >= STATUS_FAIL_LIMIT:
            return "🤖 автовідповідь вимкнена (забагато невдалих звірок по цьому замовленню)", False
        row = None
        try:
            import sqlite3 as _sq
            oc = _sq.connect(Path(ORDERS_DB).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            oc.row_factory = _sq.Row
            try:
                row = oc.execute("SELECT status, delivery_status, payment_method, payment_confirmed, toysi_ttn, cancelled_at, phone FROM orders WHERE internal_order_id=? AND platform='site'", ("site_" + oid,)).fetchone()
            finally:
                oc.close()
        except Exception as e:  # noqa: BLE001
            print(f"[site_chat_bot] orders.db не прочитано: {type(e).__name__}", file=sys.stderr)
            return "🤖 статус не перевірено (orders.db недоступна): відповісти вручну", False
        last4 = re.sub(r"\D", "", (row["phone"] if row else "") or "")[-4:]
        if row is None or len(last4) < 4 or last4 not in cands:
            self.db.run("DELETE FROM status_fails WHERE ts<?", ((now - timedelta(days=2)).replace(microsecond=0).isoformat(),))
            self.db.run("INSERT INTO status_fails(order_id, ts) VALUES(?,?)", (oid, now.replace(microsecond=0).isoformat()))
            self.reply_to(chat_id, STATUS_NOT_FOUND, kind="auto")
            return "🤖 замовлення не знайдено / 4 цифри не збіглися", True
        msg = order_status_text(row)
        if not msg:
            return "🤖 стан замовлення невідомий для автовідповіді: відповісти вручну", False
        self.reply_to(chat_id, f"Замовлення {oid}: {msg}", kind="auto")
        return "🤖 надіслала клієнту статус замовлення", True

    def _forward(self, mid: int, note: str = "") -> bool:
        """Копія клієнтського повідомлення власнику (з міткою клієнта). Без SITE_CHAT_OWNER_CHAT_ID — лишається в черзі (forwarded=0)."""
        if self.owner is None:
            return False
        r = self.db.q("SELECT m.chat_id, m.text, c.first_name, c.username FROM messages m JOIN customers c ON c.chat_id=m.chat_id WHERE m.id=?", (mid,))
        if not r:
            return False
        r = r[0]
        who = (r["first_name"] or "клієнт") + (f" @{r['username']}" if r["username"] else "")
        res = self._send(self.owner, f"💬 {who} (чат {r['chat_id']}):\n{r['text']}\n\n" + (note + "\n" if note else "") + "↩️ Відповідайте РЕПЛАЄМ на це повідомлення.")
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

    def reply_to(self, chat_id: int, text: str, kind: str = "out") -> bool:
        """kind='out' — відповідь людини (рахується в digest/list-new як «відповіли»); kind='auto' — автовідповідь бота (не рахується: чат лишається в черзі, поки не відповість людина)."""
        ok = self._send(chat_id, text)
        if ok:
            self.db.run("INSERT INTO messages(chat_id, direction, text, ts, forwarded) VALUES(?,?,?,?,1)", (chat_id, kind, text, self.now().replace(microsecond=0).isoformat()))
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
        mono = time.monotonic()
        if mono - getattr(self, "_last_notify", 0.0) >= NOTIFY_EVERY:
            self._last_notify = mono
            try:
                self.notify_changes()
            except Exception as e:  # noqa: BLE001
                print(f"[site_chat_bot] notify_changes: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
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
        txt = r["text"]
        if _ORDER_RE.search(txt):            # пара «номер замовлення + 4 цифри телефону» = ключ до статусу: у збірці лишаємо номер, 4-значні числа ховаємо
            txt = _LAST4_RE.sub("[4 цифри]", txt)
        masked = _EMAIL_RE.sub("[e-mail]", _PHONE_RE.sub("[телефон]", txt))[:300]
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
