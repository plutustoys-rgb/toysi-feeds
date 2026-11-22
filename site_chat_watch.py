# -*- coding: utf-8 -*-
"""site_chat_watch.py — сторож чату підтримки сайту (рішення власника 21.11.2026: «зроби контроль повідомлень, щоб я не нагадував, або автоматизуй»; делеговано Коду).

VPS-таймер кожні 10 хв (site-chat-watch.timer): читає site_chat.db ЛИШЕ для читання (mode=ro), знаходить вхідні повідомлення, на які людина (direction='out') не відповіла довше за WAIT_MIN хвилин (але не старші за MAX_AGE_H годин — давній
хвіст не лавиною), і шле ОДИН агрегований алерт власнику ЧЕРЕЗ БОТА ЧАТУ (SITE_CHAT_BOT_TOKEN → SITE_CHAT_OWNER_CHAT_ID — той самий канал, яким власник уже отримує копії повідомлень клієнтів).
НЕ через telegram_notify: його сито telegram_triage («лише замовлення», рішення власника 12.10) глушить усе, крім замовлень, а правила сита міняє лише власник (аудит #1961, знахідка 1).
БЕЗ тексту клієнтів і без chat_id: лише кількість чатів/повідомлень і найдовше очікування (текст читає той, хто веде чат, через `support_relay.py list`).
Запобіжники: алерти лише 08:00–22:00 за Києвом (вночі копиться до ранку); повтор для тієї самої «найстарішої очікуваної репліки» не частіше ніж раз на REPEAT_H годин; штамп повтору ставиться ЛИШЕ після реально надісланого повідомлення
(нема токена/власника/збій Telegram → штамп не ставиться, повтор через 10 хв); свій стан — site_chat_watch_state.json (атомарний запис). Помилка читання БД (нема файлу, пошкоджена) → код 1 (юніт failed → health_check), тимчасове блокування БД ботом → код 0.
Автовідповіді бота (kind='auto') відповіддю людини НЕ вважаються (як у site_chat_bot.list-new). `--dry` — лише надрукувати, нічого не слати."""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
WAIT_MIN = 120      # СТРАХОВКА (рішення власника 22.11: кожне повідомлення будить того, хто веде чат, а не власника): власнику — лише якщо ніхто не відповів 2 год
MAX_AGE_H = 72
REPEAT_H = 4
DAY_START, DAY_END = 8, 22      # години (початок включно, кінець виключно), за Києвом
STATE_FILE = HERE / "site_chat_watch_state.json"


class DbError(Exception):
    pass


def _db_path(db_path=None) -> Path:
    return Path(db_path or os.environ.get("SITE_CHAT_DB") or (HERE / "site_chat.db"))


def waiting(db_path=None, now=None, wait_min: int = WAIT_MIN, max_age_h: int = MAX_AGE_H) -> list:
    """[(msg_id, chat_id, ts, wait_minutes)] — вхідні, новіші за останню відповідь людини, старші за wait_min хв і молодші за max_age_h год. Нема БД/пошкоджена → DbError; тимчасово заблокована → []."""
    p = _db_path(db_path)
    now = now or datetime.now()
    if not p.is_file():
        raise DbError(f"нема файлу БД чату {p.name}")
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            rows = con.execute("SELECT m.id, m.chat_id, m.ts FROM messages m WHERE m.direction='in' AND m.id > COALESCE((SELECT MAX(o.id) FROM messages o WHERE o.chat_id=m.chat_id AND o.direction='out'), 0) ORDER BY m.id").fetchall()
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            print("[site_chat_watch] БД тимчасово заблокована ботом — наступного циклу", file=sys.stderr)
            return []
        raise DbError(f"БД чату недоступна: {type(e).__name__}") from None
    except sqlite3.Error as e:
        raise DbError(f"БД чату пошкоджена/недоступна: {type(e).__name__}") from None
    out = []
    for mid, cid, ts in rows:
        try:
            age = (now - datetime.fromisoformat(str(ts))).total_seconds() / 60
        except ValueError:
            continue
        if wait_min <= age <= max_age_h * 60:
            out.append((mid, cid, ts, int(age)))
    return out


def build_alert(rows: list, wait_min: int = WAIT_MIN):
    """(dedup_key, text) або None. Ключ прив'язаний до id найстарішого очікуваного повідомлення. Без chat_id і без тексту клієнтів."""
    if not rows:
        return None
    chats = {r[1] for r in rows}
    oldest = min(rows, key=lambda r: r[0])
    worst = max(r[3] for r in rows)
    text = (f"🟠 Чат сайту: {len(rows)} повідомл. у {len(chats)} чат(ах) без відповіді людини понад {wait_min} хв (найдовше {worst} хв) — страховка: Shopify-code не відповіла. "
            "Кожне нове повідомлення її будить через `support_relay_watch.py`; якщо не відповідає — напишіть їй в сесію.")
    return f"site_chat_unanswered:{oldest[0]}", text


def kyiv_hour(now=None) -> int:
    """Година за Києвом. Явний `now` (тести) беремо як є; інакше — поточний час у Europe/Kyiv (tzdata немає → локальний)."""
    if now is not None:
        return now.hour
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Kyiv")).hour
    except Exception:  # noqa: BLE001
        return datetime.now().hour


def _load_state(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def send_owner(text: str) -> bool:
    """Повідомлення власнику через бота чату (як копії клієнтських повідомлень). Нема токена/власника або збій Telegram → False (без винятку; токен у лог не потрапляє)."""
    token = os.environ.get("SITE_CHAT_BOT_TOKEN", "").strip()
    owner = os.environ.get("SITE_CHAT_OWNER_CHAT_ID", "").strip()
    if not token or not owner:
        print("[site_chat_watch] SITE_CHAT_BOT_TOKEN/SITE_CHAT_OWNER_CHAT_ID не задано — алерт не надіслано", file=sys.stderr)
        return False
    try:
        from site_chat_bot import TelegramAPI
        TelegramAPI(token).call("sendMessage", chat_id=int(owner), text=text)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[site_chat_watch] алерт не надіслано: {type(e).__name__}", file=sys.stderr)
        return False


def run(dry=False, db_path=None, now=None, send=None, state_path=None, wait_min: int = WAIT_MIN) -> int:
    t0 = now or datetime.now()
    try:
        rows = waiting(db_path, t0, wait_min)
    except DbError as e:
        print(f"[site_chat_watch] {e}", file=sys.stderr)
        return 1
    msg = build_alert(rows, wait_min)
    if msg is None:
        print(f"[site_chat_watch] немає повідомлень без відповіді довше за {wait_min} хв (і не старших за {MAX_AGE_H} год)")
        return 0
    key, text = msg
    print("[site_chat_watch]", text)
    if dry:
        return 0
    if not DAY_START <= kyiv_hour(now) < DAY_END:
        print("[site_chat_watch] ніч (до 08:00/після 22:00 за Києвом) — алерт відкладено до ранку")
        return 0
    sp = Path(state_path or STATE_FILE)
    state = _load_state(sp)
    last = state.get(key)
    if isinstance(last, (int, float)) and time.time() - last < REPEAT_H * 3600:
        print("[site_chat_watch] цей алерт уже надсилався менше ніж", REPEAT_H, "год тому")
        return 0
    if (send or send_owner)(text):
        state = {k: v for k, v in state.items() if isinstance(v, (int, float)) and time.time() - v < 7 * 86400}      # старі ключі чистимо
        state[key] = time.time()
        try:
            _save_state(sp, state)
        except OSError as e:
            print(f"[site_chat_watch] стан не збережено ({type(e).__name__}) — можливий повтор", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env")
    except ImportError:
        pass
    sys.exit(run(dry="--dry" in sys.argv))
