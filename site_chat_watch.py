# -*- coding: utf-8 -*-
"""site_chat_watch.py — сторож чату підтримки сайту (рішення власника 21.11.2026: «зроби контроль повідомлень, щоб я не нагадував, або автоматизуй»; делеговано Коду).

VPS-таймер кожні 10 хв (site-chat-watch.timer): читає site_chat.db ЛИШЕ для читання (mode=ro), знаходить вхідні повідомлення, на які людина (direction='out') не відповіла довше за WAIT_MIN хвилин, і шле ОДИН агрегований Telegram-алерт
(telegram_notify.send_throttled_alert): скільки чатів/повідомлень чекають, найдовше очікування, chat_id. БЕЗ тексту клієнтів, без ПІБ/телефонів/адрес — лише числа й chat_id (текст читає той, хто веде чат, через `support_relay.py list`).
Запобіжники: алерти лише у «людські» години (08:00–22:00 за часом VPS = Київ), вночі — копиться до ранку; повтор алерту для тієї самої «найстарішої очікуваної репліки» не частіше ніж раз на REPEAT_H годин; помилка БД/алерту не валить юніт (код виходу 0).
Автовідповіді бота (kind='auto') відповіддю людини НЕ вважаються (як у site_chat_bot.list-new). Запускається без аргументів; `--dry` — лише надрукувати, нічого не слати."""
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
CHAT_DB = HERE / "site_chat.db"
WAIT_MIN = 30
REPEAT_H = 4
DAY_START, DAY_END = 8, 22      # години (включно з початком, виключно кінець), час VPS


def waiting(db_path=None, now=None, wait_min: int = WAIT_MIN) -> list:
    """[(msg_id, chat_id, ts, wait_minutes)] — вхідні, новіші за останню відповідь людини в чаті, старші за wait_min хв. Нема БД/збій → []."""
    p = Path(db_path or CHAT_DB)
    now = now or datetime.now()
    if not p.is_file():
        return []
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            rows = con.execute("SELECT m.id, m.chat_id, m.ts FROM messages m WHERE m.direction='in' AND m.id > COALESCE((SELECT MAX(o.id) FROM messages o WHERE o.chat_id=m.chat_id AND o.direction='out'), 0) ORDER BY m.id").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    out = []
    for mid, cid, ts in rows:
        try:
            age = (now - datetime.fromisoformat(str(ts))).total_seconds() / 60
        except ValueError:
            continue
        if age >= wait_min:
            out.append((mid, cid, ts, int(age)))
    return out


def build_alert(rows: list):
    """(dedup_key, text) або None. Ключ прив'язаний до id найстарішого очікуваного повідомлення: нова «найстаріша» → новий алерт; та сама → не частіше REPEAT_H."""
    if not rows:
        return None
    chats = sorted({r[1] for r in rows})
    oldest = min(rows, key=lambda r: r[0])
    worst = max(r[3] for r in rows)
    ids = ", ".join(str(c) for c in chats[:8]) + (f" (+{len(chats) - 8})" if len(chats) > 8 else "")
    text = (f"🟠 Чат сайту: {len(rows)} повідомл. у {len(chats)} чат(ах) без відповіді людини понад {WAIT_MIN} хв (найдовше {worst} хв). chat_id: {ids}. "
            "Читати й відповідати: `python support_relay.py list` / `reply` (відповідає Shopify-code).")
    return f"site_chat_unanswered:{oldest[0]}", text


def run(dry=False, db_path=None, now=None, alert=None) -> int:
    now = now or datetime.now()
    rows = waiting(db_path, now)
    msg = build_alert(rows)
    if msg is None:
        print("[site_chat_watch] немає повідомлень без відповіді довше за", WAIT_MIN, "хв")
        return 0
    key, text = msg
    print("[site_chat_watch]", text)
    if dry:
        return 0
    if not DAY_START <= now.hour < DAY_END:
        print("[site_chat_watch] ніч (до 08:00/після 22:00) — алерт відкладено до ранку")
        return 0
    try:
        if alert is None:
            from telegram_notify import send_throttled_alert as alert
        alert(key, text, cooldown_sec=REPEAT_H * 3600)
    except Exception as e:  # noqa: BLE001
        print(f"[site_chat_watch] алерт не надіслано: {type(e).__name__}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env")
    except ImportError:
        pass
    sys.exit(run(dry="--dry" in sys.argv))
