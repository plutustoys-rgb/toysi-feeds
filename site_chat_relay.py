# -*- coding: utf-8 -*-
"""site_chat_relay.py — ВУЗЬКИЙ канал до чату підтримки сайту (рішення власника 21.11.2026: чат веде Shopify-code, відповіді — її рішення).

Запускається на VPS ЛИШЕ як forced-command окремого SSH-ключа (authorized_keys: `command="…/site_chat_relay.py",restrict ssh-ed25519 … support-relay`). Тож власник ключа не отримує shell,
ні .env, ні orders.db — лише три дії через SSH_ORIGINAL_COMMAND:
  list                      → чати, де є вхідні повідомлення НОВІШІ за останню відповідь людини (сире листування клієнтів: ПІБ/телефони/адреси можуть бути в тексті — це потрібно тому, хто веде чат)
  digest [днів 1..14]       → `site_chat_bot.py digest N` (телефони/e-mail у збірці маскуються ботом; ПІБ і адреси — НІ)
  reply <chat_id> <base64>  → `site_chat_bot.py reply <chat_id> <текст>`; текст у base64 (UTF-8), бо кирилиця через ssh ламається у «????».
Запобіжники reply (аудит #1949 — 13 знахідок, усі враховано):
  • чат мусить мати вхідне повідомлення НОВІШЕ за останню відповідь людини (не можна писати «в нікуди» й повторно відповідати вже відповіданому);
  • ліміти: ≤12 відповідей/год загалом і ≤3/год на чат; резервація запису в журнал АТОМАРНА (flock) і робиться ДО відправки; не вдалося записати журнал → НЕ відправляємо;
  • текст 1..1000 без керівних; NFKC + вилучення невидимих (Cf); посилання лише `https://plutustoys.com.ua/…` (перевіряється ВИДАЛЕННЯМ дозволених URL і забороною решти: `://`, `@`, t.me, tg:/mailto:/…, будь-який домен);
  • без довгих цифрових рядків (≥13 цифр навіть через розділювачі, IBAN UA..); виняток — рівно 14 цифр підряд (ТТН Нової пошти);
  • «відправлено» лише якщо бот записав НОВИЙ рядок 'out' у цей чат (порівняння за id, не за текстом); таймаут бота → «результат невідомий».
Журнал support_relay.log (права 600, ротація >5 МБ): резервації (повний текст), результати, відхилення (≤200 символів + sha1, не більше 50/год). Жодних shell-викликів; шляхи/інтерпретатор ЖОРСТКО задані (env не читається).
Код виходу: 0 — ок; 2 — відхилено запобіжником; 1 — збій бота/невизначений результат."""
import base64
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
import uuid
from datetime import datetime, timedelta
from pathlib import Path

try:
    import fcntl
except ImportError:          # Windows (лише тести)
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
PYTHON = str(HERE / "venv" / "bin" / "python3")
BOT = HERE / "site_chat_bot.py"
CHAT_DB = HERE / "site_chat.db"
LOG = HERE / "support_relay.log"
MAX_REPLIES_PER_HOUR = 12
MAX_REPLIES_PER_CHAT_PER_HOUR = 3
MAX_REJECT_LOGS_PER_HOUR = 50
MAX_TEXT = 1000
MAX_OUT = 6000
LOG_ROTATE_BYTES = 5_000_000
BOT_TIMEOUT = 90
ALLOWED_HOST = "plutustoys.com.ua"
# дозволений URL: лише https://plutustoys.com.ua[/безпечний-шлях]; ПЕРЕД ним — не частина іншого токена (будь-який скрипт), ПІСЛЯ — не продовження хоста/схеми (УВАГА: Unicode — `\w`, `\.\w`: аудит #1949, раунд 2 — `…com.ua.онлайн`, `…uaї`)
_ALLOWED_URL_RE = re.compile(r"(?<![\w.@/:%\\-])https://plutustoys\.com\.ua(?:/[A-Za-z0-9._~%/\-?=&#+]*)?(?![\w@\\-]|:|\.\w)")
_ALLOWED_BARE_RE = re.compile(r"(?<![\w.@/\\-])plutustoys\.com\.ua(?![\w@/\\-]|\.\w)")
_SCHEME_WORD_RE = re.compile(r"(?i)\b(?:https?|ftp|tg|mailto|tel|file|javascript|data|sms|viber|whatsapp|skype|ws|wss|intent)\s*:")
# домено-подібне в БУДЬ-ЯКОМУ скрипті: токен із літерою, крапка, ≥2 літери одразу (без пробілу): `пошта.com`, `evil.рф`, `t.me`; «т.д.», «21.11.2026», «5.0», «30x20.5» — не спрацьовують
_DOMAIN_LIKE_RE = re.compile(r"[\w-]+\.[^\W\d_]{2,}|xn--|\bwww\b|\b\d{1,3}(?:\.\d{1,3}){3}\b", re.IGNORECASE)
# типові скорочення адрес («м.Київ», «вул.Шевченка», «обл.Київська», «р-н.Бучанський») — не домен: перед перевіркою додається пробіл після крапки
_ADDR_ABBR_RE = re.compile(r"(?i)\b(м|вул|обл|р-н|буд|кв|с|смт|пр|просп|пров|ім|мкр|бул|пл|оф|тел|ід)\.(?=[А-ЯІЇЄҐа-яіїєґ])")
_DOTS_RE = re.compile("[\u3002\uff0e\uff61\u2024\u2e3c\ufe52]")
_INVISIBLE = set("\u3164\u2800\u115f\u1160\u17b4\u17b5\uffa0\u180e")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TTN_RE = re.compile(r"(?<!\d)(?:\d{14}|\d{4}\s\d{4}\s\d{4}\s\d{2})(?!\d)")
_PHONE_RE = re.compile(r"(?<![\d+])(?:\+?38)?0\d{9}(?!\d)")
_ORDER_NO_RE = re.compile(r"(?i)\bPT-\d{8}-\d{6}-[0-9a-f]{6}\b|\bPT-\d{4,9}\b")
_IBAN_RE = re.compile(r"(?i)\bUA\s*\d{2}\s*\d{4}\s*\d{4}")


class Rejected(Exception):
    pass


def parse(cmd: str) -> tuple:
    """SSH_ORIGINAL_COMMAND → ('list',) | ('digest', N) | ('reply', chat_id:int, b64:str). Все інше → Rejected."""
    parts = (cmd or "").strip().split()
    if not parts:
        raise Rejected("порожня команда: list | digest [N] | reply <chat_id> <base64>")
    if parts[0] == "list" and len(parts) == 1:
        return ("list",)
    if parts[0] == "digest" and len(parts) <= 2:
        n = 1
        if len(parts) == 2:
            if not (parts[1].isascii() and parts[1].isdecimal()) or not 1 <= int(parts[1]) <= 14:
                raise Rejected("digest: днів має бути 1..14")
            n = int(parts[1])
        return ("digest", n)
    if parts[0] == "reply" and len(parts) == 3:
        if not re.fullmatch(r"[0-9]{1,15}", parts[1]):
            raise Rejected("reply: chat_id — лише цифри (до 15)")
        if not re.fullmatch(r"[A-Za-z0-9+/=]{1,6000}", parts[2]):
            raise Rejected("reply: текст має бути base64 (A-Za-z0-9+/=)")
        return ("reply", int(parts[1]), parts[2])
    raise Rejected("невідома команда: дозволено лише list | digest [N] | reply <chat_id> <base64>")


def decode_text(b64: str) -> str:
    try:
        return base64.b64decode(b64, validate=True).decode("utf-8").strip()
    except Exception:  # noqa: BLE001
        raise Rejected("reply: base64/UTF-8 некоректні") from None


def _normalize(text: str) -> str:
    """NFKC (повноширинні → звичайні) і вилучення невидимих символів категорії Cf (нульова ширина, керування напрямком тощо)."""
    t = _DOTS_RE.sub(".", unicodedata.normalize("NFKC", text))
    return "".join(c for c in t if unicodedata.category(c) not in ("Cf", "Mn", "Me") and c not in _INVISIBLE)


def _long_digit_chain(t: str) -> bool:
    """≥13 цифр: суцільний рядок АБО ланцюг груп (кожна ≤6 цифр) через 1..5 не-літерних розділювачів без коми (пробіли, дефіси, двокрапки, #, + тощо). Це лише захист від випадковості, а не від навмисного обходу."""
    total, prev_end, prev_len = 0, None, 0
    for m in re.finditer(r"\d+", t):
        ln = m.end() - m.start()
        sep = t[prev_end:m.start()] if prev_end is not None else None
        if sep is not None and 1 <= len(sep) <= 5 and re.fullmatch(r"[\W_]+", sep) and (len(sep) == 1 or ("," not in sep and ";" not in sep)) and 2 <= ln <= 12 and 3 <= prev_len <= 12:      # кома/«;» разом з пробілом = перелік (ціни); кома без пробілу — ланцюг; групи 3..6 цифр (дати з 2-цифрових частин не ланцюжаться; IBAN ловить окрема регулярка)
            total += ln
        else:
            total = ln
        if total >= 13:
            return True
        prev_end, prev_len = m.end(), ln
    return False


def check_text(text: str) -> None:
    """Технічні межі тексту (політику відповідей визначає той, хто веде чат). Відхиляє будь-яке посилання/контакт, окрім https://plutustoys.com.ua/…"""
    if not text:
        raise Rejected("reply: порожній текст")
    if len(text) > MAX_TEXT:
        raise Rejected(f"reply: текст {len(text)} символів, дозволено до {MAX_TEXT}")
    if _CTRL_RE.search(text):
        raise Rejected("reply: керівні символи в тексті")
    t = _normalize(text)
    rest = _ALLOWED_URL_RE.sub(" ", t)
    rest = _ALLOWED_BARE_RE.sub(" ", rest)
    chk_rest = _ADDR_ABBR_RE.sub(lambda m: m.group(0) + " ", rest)
    if "://" in rest or "@" in rest or "\\" in rest or _SCHEME_WORD_RE.search(rest) or _DOMAIN_LIKE_RE.search(chk_rest):
        raise Rejected(f"reply: дозволені лише посилання https://{ALLOWED_HOST}/… (знайдено інше посилання/домен/контакт/@)")
    if _long_digit_chain(_ORDER_NO_RE.sub(" ", _PHONE_RE.sub(" ", _TTN_RE.sub(" ", t)))) or _IBAN_RE.search(t):          # на ПОВНОМУ тексті: номер картки/IBAN у query дозволеного URL теж ловиться
        raise Rejected("reply: довгий цифровий рядок/IBAN (схоже на номер картки/рахунку) — не надсилаємо; ТТН — рівно 14 цифр підряд")


def _ro(db_path=None):
    p = Path(db_path or CHAT_DB)
    if not p.is_file():
        raise sqlite3.Error("нема БД чату")
    return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=10)


def chat_state(chat_id: int, db_path=None) -> tuple:
    """(last_in_id, last_out_id) — id останніх вхідного й вихідного (людина) повідомлень чату; None, якщо нема. Помилка БД → (None, None)."""
    try:
        con = _ro(db_path)
        try:
            li = con.execute("SELECT MAX(id) FROM messages WHERE chat_id=? AND direction='in'", (chat_id,)).fetchone()[0]
            lo = con.execute("SELECT MAX(id) FROM messages WHERE chat_id=? AND direction='out'", (chat_id,)).fetchone()[0]
            return li, lo
        finally:
            con.close()
    except sqlite3.Error:
        return None, None


def waiting_messages(db_path=None) -> list:
    """Вхідні повідомлення, новіші за останню відповідь людини в тому ж чаті (чат без відповідей — усі вхідні)."""
    con = _ro(db_path)
    try:
        return con.execute("SELECT m.id, m.ts, m.chat_id, m.text FROM messages m WHERE m.direction='in' AND m.id > COALESCE((SELECT MAX(o.id) FROM messages o WHERE o.chat_id=m.chat_id AND o.direction='out'), 0) ORDER BY m.id").fetchall()
    finally:
        con.close()


def out_recorded_after(chat_id: int, text: str, after_id, db_path=None) -> bool:
    """Бот записав НОВИЙ 'out' (id > after_id) із цим текстом. Збіг із давнім записом не рахується."""
    try:
        con = _ro(db_path)
        try:
            return con.execute("SELECT 1 FROM messages WHERE chat_id=? AND direction='out' AND text=? AND id>? LIMIT 1", (chat_id, text, after_id or 0)).fetchone() is not None
        finally:
            con.close()
    except sqlite3.Error:
        return False


@contextlib.contextmanager
def _locked(log_path: Path):
    """Ексклюзивний замок на час «перевірка ліміту → резервація запису». Нема замка (Windows без msvcrt) → без нього (лише тести)."""
    lock_path = Path(str(log_path) + ".lock")
    f = open(lock_path, "a")
    try:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
        elif msvcrt is not None:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(f, fcntl.LOCK_UN)
            elif msvcrt is not None:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            f.close()


def _read_log(log_path: Path) -> list:
    """Усі записи журналу. Файл є, але нечитабельний → OSError (викликач відмовляє: ліміт не можна перевірити)."""
    if not log_path.is_file():
        return []
    out = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append(log_path: Path, entry: dict) -> None:
    if log_path.is_file() and log_path.stat().st_size > LOG_ROTATE_BYTES:
        os.replace(log_path, str(log_path) + ".1")
    fd = os.open(str(log_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _within_hour(e: dict, now) -> bool:
    try:
        return datetime.fromisoformat(e["ts"]) >= now - timedelta(hours=1)
    except (KeyError, ValueError, TypeError):
        return False


def reserve(chat_id: int, text: str, log_path: Path, now: datetime) -> str:
    """Атомарно (flock): перевіряє ліміти за журналом і ЗАПИСУЄ резервацію до відправки. Не вдалося прочитати/записати журнал → Rejected (відправки НЕ буде)."""
    token = uuid.uuid4().hex[:12]
    try:
        with _locked(log_path):
            entries = _read_log(log_path)
            res = [e for e in entries if e.get("phase") == "reserve" and _within_hour(e, now)]
            if len(res) >= MAX_REPLIES_PER_HOUR:
                raise Rejected(f"reply: ліміт {MAX_REPLIES_PER_HOUR} відповідей на годину вичерпано")
            if sum(1 for e in res if e.get("chat_id") == chat_id) >= MAX_REPLIES_PER_CHAT_PER_HOUR:
                raise Rejected(f"reply: ліміт {MAX_REPLIES_PER_CHAT_PER_HOUR} відповідей на годину цьому чату вичерпано")
            _append(log_path, {"ts": now.replace(microsecond=0).isoformat(), "phase": "reserve", "id": token, "chat_id": chat_id, "text": text})
    except Rejected:
        raise
    except OSError:
        raise Rejected("reply: журнал недоступний — без запису в журнал відповіді не надсилаємо") from None
    return token


def finish(token: str, result: str, log_path: Path, now: datetime, rc=None) -> None:
    try:
        _append(log_path, {"ts": now.replace(microsecond=0).isoformat(), "phase": "result", "id": token, "result": result, "rc": rc})
    except OSError:
        pass


def log_rejected(chat_id, text, why: str, log_path: Path, now: datetime) -> None:
    """Відхилення пишемо ОБМЕЖЕНО: текст ≤200 символів + sha1, не більше MAX_REJECT_LOGS_PER_HOUR на годину (щоб журнал не став вектором DoS)."""
    try:
        with _locked(log_path):
            n = sum(1 for e in _read_log(log_path) if e.get("phase") == "rejected" and _within_hour(e, now))
            if n < MAX_REJECT_LOGS_PER_HOUR:
                t = text or ""
                _append(log_path, {"ts": now.replace(microsecond=0).isoformat(), "phase": "rejected", "chat_id": chat_id, "text": t[:200], "sha1": hashlib.sha1(t.encode("utf-8")).hexdigest()[:12], "why": why[:200]})
    except (OSError, Rejected):
        pass


def _run_bot(args: list, timeout: int = BOT_TIMEOUT) -> tuple:
    r = subprocess.run([PYTHON, str(BOT)] + args, cwd=str(HERE), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or "")[:MAX_OUT]


def _list_output(db_path=None) -> str:
    rows = waiting_messages(db_path)[-60:]                                   # найновіші; старіші відсікаються з голови
    if not rows:
        return "(черга порожня: на всі вхідні повідомлення вже відповіла людина)"
    return "\n".join(f"#{r[0]} {r[1]} чат {r[2]}: {str(r[3])[:300]}" for r in rows)[-MAX_OUT:]


def handle(cmd: str, runner=None, db_path=None, log_path=None, now=None) -> tuple:
    """→ (код_виходу, текст_для_stdout). runner(args)->(rc,out), db_path, log_path, now — підміни ЛИШЕ для тестів (у бойовому запуску жорстко задані константами)."""
    run = runner or _run_bot
    lp = Path(log_path or LOG)
    now = now or datetime.now()
    try:
        act = parse(cmd)
    except Rejected as e:
        return 2, f"ВІДХИЛЕНО: {e}"
    if act[0] == "list":
        try:
            return 0, _list_output(db_path)
        except sqlite3.Error:
            return 1, "БД чату недоступна"
    if act[0] == "digest":
        try:
            rc, out = run(["digest", str(act[1])])
        except subprocess.TimeoutExpired:
            return 1, "digest: таймаут бота"
        return (0 if rc == 0 else 1), out
    chat_id, text = act[1], None
    try:
        text = decode_text(act[2])
        check_text(text)
        last_in, last_out = chat_state(chat_id, db_path)
        if last_in is None:
            raise Rejected("reply: чат невідомий (нема вхідних повідомлень від цього chat_id) — відповідати можна лише тим, хто писав")
        if last_out is not None and last_in < last_out:
            raise Rejected("reply: на останнє повідомлення цього чату людина вже відповіла — чекаємо нового повідомлення клієнта")
        token = reserve(chat_id, text, lp, now)
    except Rejected as e:
        log_rejected(chat_id, text, str(e), lp, now)
        return 2, f"ВІДХИЛЕНО: {e}"
    try:
        rc, out = run(["reply", str(chat_id), text])
    except subprocess.TimeoutExpired:
        finish(token, "timeout_unknown", lp, now)
        return 1, "РЕЗУЛЬТАТ НЕВІДОМИЙ: бот не відповів за відведений час, повідомлення могло піти. Не повторюйте: перевірте `list` (чат зникне з черги, якщо відповідь записана)."
    except Exception as e:  # noqa: BLE001
        rc, out = 1, type(e).__name__
    if rc == 0 and not out_recorded_after(chat_id, text, last_out, db_path):
        rc, out = 1, "бот завершився без нового запису відповіді (токен не задано?)"
    finish(token, "sent" if rc == 0 else "bot_failed", lp, now, rc)
    if rc == 0:
        return 0, f"відправлено (chat {chat_id}, {len(text)} символів)"
    return 1, f"бот не надіслав (rc={rc}): {out[:200]}. Перед повтором перевірте `list`."


if __name__ == "__main__":
    os.umask(0o077)
    code, text = handle(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    print(text)
    sys.exit(code)
