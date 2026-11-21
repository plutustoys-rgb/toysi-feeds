#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_chat_relay.py — вузький канал до чату підтримки (site_chat_relay.py + support_relay.py + ps1), 21.11.2026; перероблено за аудитом #1949 (НЕ ЧИСТО → 13 знахідок).
ІНВАРІАНТИ: (1) дозволено лише list | digest [1..14 ASCII] | reply <digits> <base64>; решта → ВІДХИЛЕНО до виклику бота; (2) reply лише чату з вхідним повідомленням НОВІШИМ за останню відповідь людини;
(3) текст: 1..1000, без керівних; будь-яке посилання/домен/@/t.me/схема, крім https://plutustoys.com.ua/…, відхиляється (у т.ч. обходи: склейка, Cf, fullwidth, чужий URL у токені з дозволеним префіксом);
(4) ≥13 цифр навіть через розділювачі й IBAN відхиляються, рівно 14 цифр підряд (ТТН) — ні; (5) ліміти 12/год і 3/год на чат; резервація в журналі ДО відправки, АТОМАРНА (паралельні виклики не перевищують ліміт);
журнал недоступний → відправки НЕМАЄ; (6) «відправлено» лише за НОВИМ 'out' (id), не за збігом тексту; таймаут бота → «результат невідомий»; (7) відхилення в журналі обмежені (200 символів + sha1, ≤50/год); (8) installer/remove: base64, захист від склеєного рядка, резервна копія, точкове видалення.
Без мережі. `python test_site_chat_relay.py` → exit 0/1."""
import base64
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))
import site_chat_relay as r  # noqa: E402
import support_relay as w  # noqa: E402

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


TMP = Path(tempfile.mkdtemp(prefix="relay_"))
DB = TMP / "site_chat.db"
LOG = TMP / "relay.log"
NOW = datetime(2026, 11, 21, 21, 0, 0)


def b64(t):
    return base64.b64encode(t.encode("utf-8")).decode("ascii")


def mkdb(extra_chats=0):
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, chat_id INTEGER, direction TEXT, text TEXT, ts TEXT, forwarded INTEGER)")
    rows = [(111, "in", "привіт"), (222, "in", "питання"), (333, "in", "старе питання"), (333, "out", "Дякуємо!"), (333, "in", "нове питання"), (444, "in", "питання 444"), (444, "out", "відповідь 444")]
    rows += [(1000 + i, "in", f"питання {i}") for i in range(extra_chats)]
    for cid, d, t in rows:
        con.execute("INSERT INTO messages(chat_id,direction,text,ts,forwarded) VALUES(?,?,?,?,1)", (cid, d, t, "2026-11-21T12:00:00"))
    con.commit()
    con.close()


class Bot:
    """Підміна site_chat_bot.py: як справжній бот, записує 'out' у БД; фіксує списки аргументів; delay — імітація повільного бота."""
    def __init__(self, record=True, rc=0, delay=0.0, boom=None):
        self.calls, self.record, self.rc, self.delay, self.boom = [], record, rc, delay, boom
        self.log_at_call = None

    def __call__(self, args, timeout=90):
        self.calls.append(list(args))
        if self.boom:
            raise self.boom
        if args[0] == "reply":
            self.log_at_call = LOG.read_text(encoding="utf-8") if LOG.is_file() else ""
            if self.delay:
                time.sleep(self.delay)
            if self.record and self.rc == 0:
                c = sqlite3.connect(DB, timeout=30)
                c.execute("INSERT INTO messages(chat_id,direction,text,ts,forwarded) VALUES(?,?,?,?,1)", (int(args[1]), "out", args[2], "2026-11-21T21:00:00"))
                c.commit()
                c.close()
        return self.rc, ""


def H(cmd, bot=None, now=NOW):
    return r.handle(cmd, runner=bot or Bot(), db_path=DB, log_path=LOG, now=now)


def fresh(extra=0):
    mkdb(extra)
    LOG.unlink(missing_ok=True)
    Path(str(LOG) + ".lock").unlink(missing_ok=True)


# ── 1. парсинг
fresh()
for bad in ("", "id", "cat /opt/plutustoys/.env", "list; id", "list && id", "list $(id)", "digest 0", "digest 15", "digest x", "digest ²", "digest ٣", "digest 1 2", "reply", "reply 111", "reply abc " + b64("x"), "reply 111 не-base64!",
            "reply 111 " + b64("x") + " extra", "reply 1234567890123456 " + b64("x"), "run", "reply 111 `id`", "list\nid", "LIST", "reply ١١١ " + b64("x")):
    b = Bot()
    code, out = H(bad, b)
    chk(f"відхилено без виклику бота: {bad[:40]!r}", code == 2 and not b.calls and "ВІДХИЛЕНО" in out)
code, out = H("digest 3", Bot())
chk("digest 3 → бот digest 3, код 0", code == 0)
code, out = H("list", Bot())
lines = out.splitlines()
chk("list: показує ЛИШЕ повідомлення, новіші за останню відповідь людини (111, 222, нове 333); 444 (відповіли) і старе 333 — ні, бот не викликається",
    code == 0 and len(lines) == 3 and "чат 111" in lines[0] and "чат 222" in lines[1] and "чат 333" in lines[2] and "нове питання" in lines[2] and "444" not in out and "старе" not in out)

# ── 2. reply: успіх, резервація ДО відправки
fresh()
txt = "Добрий день! Каталог: https://plutustoys.com.ua/catalog.html. Дякуємо!"
b = Bot()
code, out = H("reply 111 " + b64(txt), b)
chk("reply відомому чату → код 0, бот викликаний СПИСКОМ [reply, chat_id, текст] (без shell)", code == 0 and b.calls == [["reply", "111", txt]] and "відправлено" in out)
chk("резервація записана в журнал ДО відправки (у момент виклику бота рядок reserve уже є)", b.log_at_call and '"phase": "reserve"' in b.log_at_call and '"chat_id": 111' in b.log_at_call)
rows = [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines()]
chk("журнал: reserve (повний текст) + result sent", [x["phase"] for x in rows] == ["reserve", "result"] and rows[0]["text"] == txt and rows[1]["result"] == "sent" and rows[0]["id"] == rows[1]["id"])

# ── 3. стан чату
fresh()
for chat, why in ((999, "невідомий"), (444, "уже відповіли")):
    b = Bot()
    code, out = H(f"reply {chat} " + b64("Добрий день"), b)
    chk(f"reply чату {chat} ({why}) → відхилено, бот не викликаний", code == 2 and not b.calls)
b = Bot()
code, out = H("reply 333 " + b64("Нова відповідь"), b)
chk("чат 333 має НОВЕ вхідне після останньої відповіді → дозволено", code == 0 and len(b.calls) == 1)
b = Bot()
code, out = H("reply 333 " + b64("Ще одна"), b)
chk("після відповіді 333 знову «уже відповіли» → відхилено (нема нового вхідного)", code == 2 and not b.calls)

# ── 4. текст: посилання
OK_TEXTS = ("Каталог: https://plutustoys.com.ua/catalog.html.", "Дивіться https://plutustoys.com.ua", "(https://plutustoys.com.ua/podarunky.html) і ще", "сайт plutustoys.com.ua працює", "Ціна 1250 грн, артикул 123456789", "1000, 2000, 3000, 4000, 5000 грн",
            "ТТН 20451234567890 вже в дорозі", "https://plutustoys.com.ua/category-skvishi.html?page=2&x=1#a",
            "Замовлення PT-20261110-142322-abcdef відправлено", "0671234567 / 0501234567", "Дивіться https://plutustoys.com.ua 24/7", "UA 25 шт", "напр. т.д. вул. Шевченка, 21.11.2026 14:30, 30x20.5 см, Wi-Fi 5.0",
            "21.11.2026 14:30 22.11.2026 15:45", "ТТН 20451234567890 і 20451234567891", "+380501112233",
            "Адреса: м.Київ, вул.Шевченка 5, кв.12", "смт.Гостомель, пров.Лісовий 3, мкр.Північний, оф.5, пл.Перемоги", "PT-123456", "№20451234567890", "обл.Київська, р-н.Бучанський", "ТТН 2045 1234 5678 90", "Іваненко І.І. P.S. Дякуємо!", "№28, вул. Березинська, 58", "iOS 17.2.1", "1 250,50 грн, 9:00-18:00")
for t in OK_TEXTS:
    try:
        r.check_text(t)
        ok = True
    except r.Rejected:
        ok = False
    chk(f"дозволено: {t[:50]!r}", ok)
BAD_URLS = ("зайдіть на evil.com/pay", "plutustoys.com.ua.evil.com/x", "https://plutustoys.com.ua.evil.com/x", "https://plutustoys.com.ua@evil.com/", "https://plutustoys.com.ua\\@evil.com", "напишіть t.me/evilbot", "напишіть @evil_support_bot",
            "tg://resolve?domain=evil", "ftp://evil.com/x", "mailto:a@evil.com", "сайтhttps://evil.com/x", "ahttps://evil.com", "_https://evil.com", "1https://evil.com", "https://plutustoys.com.ua/a​https://evil.com/x",
            "https://plutustoys.com.ua/a,https://evil.com/x", "(https://plutustoys.com.ua/a)https://evil.com/x", 'https://plutustoys.com.ua/a"https://evil.com', "https://plutustoys.com.ua/a|https://evil.com",
            "[https://plutustoys.com.ua/](https://evil.com)", "https:​//evil.com", "ｈｔｔｐｓ://evil.com", "http://plutustoys.com.ua/x", "www.evil.com", "<a href=\"https://evil.com\">x</a>", "[сайт](https://evil.com)",
            "HTTPS://EVIL.COM/x", "https://PLUTUSTOYS.com.ua.evil.com/", "https://xn--e1afmkfd.xn--p1ai/", "http://192.168.0.1/x", "tel:+380501112233", "javascript:alert(1)", "bit.ly/abc",
            "https://plutustoys.com.ua.онлайн/pay", "https://plutustoys.com.ua.рф/pay", "https://plutustoys.com.ua.укр", "plutustoys.com.ua.онлайн", "https://plutustoys.com.ua。онлайн/", "https://plutustoys.com.uaї/",
            "пошта.com", "магазин-оплата.com/x", "evil.рф", "магазин.рф", "сайт.укр", "evil.бел", "eviĺ.com", "evilㅤ.com", "evil⠀.com", "evil。com", "evil｡com",
            "вул.рф/pay", "тел.рф", "кв.рф", "м.рф", "зайдіть м.рф/оплата", "пр.укр/x", "ід.онлайн", "оф.сайт", "пл.бел", "мкр.москва", "м.онлайн", "ім.орг", "вул.ком", "вул.Рф", "сайт-м.рф", "сайт-с.укр", "вул.Онлайн",
            "переходьте на 83921.xyz", "777.ru/pay", "1688.com", "12-34.top", "https://plutustoys.com.ua/?x=4111111111111111", "https://plutustoys.com.ua/?iban=UA213223130000026007233566001")
for t in BAD_URLS:
    try:
        r.check_text(t)
        ok = False
    except r.Rejected:
        ok = True
    chk(f"відхилено посилання: {t[:55]!r}", ok)
# ── 5. цифри
for t in ("картка 4111 1111 1111 1111", "4111-1111-1111-1111", "4111  1111  1111  1111", "4111.1111.1111.1111", "4111_1111_1111_1111", "4111/1111/1111/1111", "4111​1111​1111​1111", "4111,1111,1111,1111", "4111111111111111",
          "UA21 3223 1300 0002 6007 2335 6600 1", "UA21.3223.1300.0002.6007.2335.6600.1", "٤١١١ ١١١١ ١١١١ ١١١١",
          "41111111111111 11", "2045 1234 5678 90 1111", "1111 2045 1234 5678 90", "4111 1111 1111 11 4111", "PT-411111111 1111111", "UA21-3223-1300-0002-6007", "41111111 11111111", "4111111111 111111", "4111 111111111111", "PT-4111111111111111", "4111:1111:1111:1111", "4111#1111#1111#1111", "4111+1111+1111+1111", "4111 -- 1111 -- 1111 -- 1111", "4111 -.- 1111 -.- 1111 -.- 1111"):
    try:
        r.check_text(t)
        ok = False
    except r.Rejected:
        ok = True
    chk(f"відхилено цифри/IBAN: {t[:45]!r}", ok)
for t in ("керівний\x07", "   ", "а" * 1001):
    try:
        r.check_text(t.strip() if t.strip() else "")
        ok = False
    except r.Rejected:
        ok = True
    chk(f"відхилено: {t[:20]!r}", ok)

# ── 6. ліміти
fresh(extra=20)
for i in range(r.MAX_REPLIES_PER_CHAT_PER_HOUR):
    r._append(LOG, {"ts": (NOW - timedelta(minutes=5 + i)).isoformat(), "phase": "reserve", "id": f"x{i}", "chat_id": 111, "text": "x"})
b = Bot()
code, out = H("reply 111 " + b64("четверта"), b)
chk("3 відповіді чату за годину → 4-та відхилена (ліміт на чат), бот не викликаний", code == 2 and not b.calls and "цьому чату" in out)
fresh(extra=20)
for i in range(r.MAX_REPLIES_PER_HOUR):
    r._append(LOG, {"ts": (NOW - timedelta(minutes=10 + i)).isoformat(), "phase": "reserve", "id": f"g{i}", "chat_id": 5000 + i, "text": "x"})
b = Bot()
code, out = H("reply 111 " + b64("ще одна"), b)
chk("12 відповідей за годину → 13-та відхилена (глобальний ліміт)", code == 2 and not b.calls and "ліміт 12" in out)
code, out = H("reply 111 " + b64("ще одна"), Bot(), now=NOW + timedelta(hours=2))
chk("через 2 години ліміт знову вільний", code == 0)

# ── 7. гонка: паралельні виклики не перевищують ліміт
fresh(extra=24)
results = []
lock = threading.Lock()


def worker(i):
    code, out = r.handle("reply %d " % (1000 + i) + b64(f"відповідь {i}"), runner=Bot(delay=0.05), db_path=DB, log_path=LOG, now=NOW)
    with lock:
        results.append(code)


ths = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
[t.start() for t in ths]
[t.join() for t in ths]
sent = results.count(0)
chk(f"24 паралельні reply різним чатам: успішних РІВНО {r.MAX_REPLIES_PER_HOUR} (було 30/30 без замка), решта відхилено", sent == r.MAX_REPLIES_PER_HOUR and results.count(2) == 24 - r.MAX_REPLIES_PER_HOUR)

# ── 8. журнал недоступний → відправки немає
fresh()
bad_log = TMP / "dir_as_log"
bad_log.mkdir(exist_ok=True)
b = Bot()
code, out = r.handle("reply 111 " + b64("Привіт"), runner=b, db_path=DB, log_path=bad_log, now=NOW)
chk("журнал недоступний (OSError) → бот НЕ викликано, код 2, повідомлення про журнал", code == 2 and not b.calls and "журнал" in out)

# ── 9. «відправлено» лише за новим out; таймаут; збої бота
fresh()
b = Bot(record=False)
code, out = H("reply 333 " + b64("Дякуємо!"), b)
chk("текст збігається зі СТАРИМ out того ж чату, бот rc=0, але нового запису нема → НЕ «відправлено», код 1", code == 1 and "відправлено" not in out)
fresh()
b = Bot(rc=1)
code, out = H("reply 222 " + b64("Вітаю 2!"), b)
chk("бот rc=1 → код 1, у журналі bot_failed", code == 1 and json.loads(LOG.read_text(encoding="utf-8").splitlines()[-1])["result"] == "bot_failed")
fresh()
b = Bot(boom=subprocess.TimeoutExpired("bot", 90))
code, out = H("reply 222 " + b64("Вітаю 3!"), b)
chk("таймаут бота → код 1, «РЕЗУЛЬТАТ НЕВІДОМИЙ», у журналі timeout_unknown", code == 1 and "НЕВІДОМИЙ" in out and json.loads(LOG.read_text(encoding="utf-8").splitlines()[-1])["result"] == "timeout_unknown")

# ── 10. журнал: відхилення обмежені
fresh()
for i in range(70):
    H("reply 999 " + b64("Добрий день " + "я" * 900 + str(i)), Bot())
rej = [json.loads(x) for x in LOG.read_text(encoding="utf-8").splitlines() if '"phase": "rejected"' in x]
chk(f"70 відхилених спроб → у журналі ≤{r.MAX_REJECT_LOGS_PER_HOUR} записів, текст ≤200 символів, є sha1", 0 < len(rej) <= r.MAX_REJECT_LOGS_PER_HOUR and all(len(x["text"]) <= 200 and x["sha1"] for x in rej))
fresh()
old = r.LOG_ROTATE_BYTES
r.LOG_ROTATE_BYTES = 300
try:
    for i in range(10):
        r._append(LOG, {"ts": NOW.isoformat(), "phase": "reserve", "id": str(i), "chat_id": 1, "text": "т" * 50})
finally:
    r.LOG_ROTATE_BYTES = old
chk("ротація журналу: після перевищення розміру створено .1, основний файл малий", Path(str(LOG) + ".1").is_file() and LOG.stat().st_size < 600)

# ── 11. жорстко задані шляхи (env не читається)
src = (HERE / "site_chat_relay.py").read_text(encoding="utf-8")
chk("site_chat_relay.py не читає os.environ для шляхів/інтерпретатора (лише SSH_ORIGINAL_COMMAND)", src.count("os.environ") == 1 and "SSH_ORIGINAL_COMMAND" in src.split("os.environ")[1][:40])

# ── 12. локальна обгортка
cmd = w.build_remote_command(["reply", "111", "Привіт", "світ", "'; rm -rf /; echo '"])
parts = cmd.split(" ")
chk("support_relay: reply → «reply <id> <base64>» без підстановки тексту як є", parts[0] == "reply" and parts[1] == "111" and len(parts) == 3 and base64.b64decode(parts[2]).decode("utf-8") == "Привіт світ '; rm -rf /; echo '" and "rm -rf" not in cmd)
chk("support_relay: reply з stdin ('-'), BOM знімається", w.build_remote_command(["reply", "5", "-"], "﻿Текст\n").split(" ")[2] == b64("Текст"))
chk("support_relay: list / digest", w.build_remote_command(["list"]) == "list" and w.build_remote_command(["digest", "2"]) == "digest 2" and w.build_remote_command(["digest"]) == "digest")
for bad in ([], ["reply", "1"], ["list", "x"], ["foo"]):
    try:
        w.build_remote_command(bad)
        ok = False
    except SystemExit:
        ok = True
    chk(f"support_relay: некоректні аргументи {bad} → SystemExit", ok)

# ── 13. installer / remove (статична перевірка)
ins = (HERE / "install_support_relay.ps1").read_text(encoding="utf-8")
rem = (HERE / "remove_support_relay.ps1").read_text(encoding="utf-8")
chk("ps1: лише ASCII", all(ord(c) < 128 for c in ins) and all(ord(c) < 128 for c in rem))
chk("install: base64-доставка, резервна копія, ремонт відсутнього \\n, перевірка точного рядка, forced-command + restrict", "base64 -d | sh" in ins and "bak_support_relay" in ins and "tail -c1" in ins and "grep -cxF" in ins and 'command="/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_chat_relay.py",restrict' in ins)
chk("install: не друкує приватний ключ, самоперевірка list і id", "Get-Content \"$key.pub\"" in ins and "selftest id" in ins and 'Get-Content $key ' not in ins)
chk("remove: base64, резервна копія, видаляє лише рядок з початком command=… і хвостом support-relay, ABORT якщо зникає >1 рядка", "base64 -d | sh" in rem and "bak_support_relay_remove" in rem and "grep -v '^command=\"/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_chat_relay.py\"" in rem and "support-relay$'" in rem and "ABORT" in rem)

# ── 14. імітація установки на файл БЕЗ кінцевого \n (shell-логіка з install; лише якщо є sh)
sh = None
for cand in ("sh", "bash"):
    try:
        subprocess.run([cand, "-c", "true"], check=True, capture_output=True, timeout=20)
        sh = cand
        break
    except Exception:  # noqa: BLE001
        continue
if sh:
    ak = TMP / "authorized_keys"
    ak.write_bytes(b"ssh-ed25519 AAAAROOTKEY owner@pc")        # БЕЗ кінцевого \n
    line = 'command="/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_chat_relay.py",restrict ssh-ed25519 BLOBBLOB support-relay'
    script = f"""f='{ak.as_posix()}'
if [ -s $f ] && [ "$(tail -c1 $f | wc -l)" -eq 0 ]; then echo >> $f; fi
printf '%s\\n' '{line}' >> $f
"""
    subprocess.run([sh, "-c", script], check=True, capture_output=True, timeout=30)
    lines = ak.read_text(encoding="utf-8").splitlines()
    chk("установка на файл без кінцевого \\n: root-ключ і рядок support-relay — ОКРЕМІ рядки", len(lines) == 2 and lines[0] == "ssh-ed25519 AAAAROOTKEY owner@pc" and lines[1] == line)
    rm = "grep -v '^command=\"/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site_chat_relay.py\",restrict ssh-ed25519 [A-Za-z0-9+/=]* support-relay$'"
    subprocess.run([sh, "-c", f"{rm} '{ak.as_posix()}' > '{ak.as_posix()}.t'; cat '{ak.as_posix()}.t' > '{ak.as_posix()}'"], capture_output=True, timeout=30)
    chk("remove (regexp із ps1): лишається рівно root-ключ", ak.read_text(encoding="utf-8").splitlines() == ["ssh-ed25519 AAAAROOTKEY owner@pc"])
    ak.write_bytes(b"ssh-ed25519 AAAAROOTKEY owner@pc" + line.encode() + b"\n")                  # як було б при СКЛЕЄНОМУ рядку
    subprocess.run([sh, "-c", f"{rm} '{ak.as_posix()}' > '{ak.as_posix()}.t'; cat '{ak.as_posix()}.t' > '{ak.as_posix()}'"], capture_output=True, timeout=30)
    chk("remove: склеєний рядок (root-ключ + support-relay) НЕ видаляється (початок рядка не command=)", b"AAAAROOTKEY" in ak.read_bytes())

print("\nПідсумок:", "УСЕ ЗЕЛЕНЕ" if not F else f"ПРОВАЛИ: {F}")
sys.exit(1 if F else 0)
