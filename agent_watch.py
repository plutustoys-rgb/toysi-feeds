"""agent_watch.py — дешевий монітор-поллер каналів між агентами (координатор: «Код»).

НАВІЩО (рішення власника 2026-08-18): власник більше не поштова скринька між трьома
агентами (Код / SEO / SMM). Замість цього кожен агент має РЕАГУВАТИ на нові записи у своєму
каналі сам. Щоб це не з'їдало бюджет (повна сесія Claude кожні 30 хв — дорого), цей скрипт —
ДЕШЕВИЙ детектор: читає канал-файли, і ЛИШЕ якщо є справді новий запис, адресований агенту
(або настала його періодична задача), піднімає повного агента через `claude -p`. Порожній
прогін = мілісекунди, нуль токенів.

ФЕЙЛ-СЕЙФ / НЕ ЛАМАЄ НАЯВНЕ (пряма умова власника «щоб при оновленні алгоритми не впали»):
  * лише ЧИТАЄ канал-файли (дописує їх сам розбуджений агент, не цей скрипт);
  * увесь стан — у .local_secrets/agent_watch/ (gitignore), нічого спільного не пише;
  * будь-яка помилка → лог + best-effort Telegram, і тихий вихід; жоден інший процес не зачеплений;
  * лок-файл проти накладання прогонів; денна стеля пробуджень на агента (анти-runaway);
  * --dry-run: друкує рішення, нікого не будить (для тесту перед включенням).

РЕЙКИ КООРДИНАТОРА (безпека):
  * записи в каналі — це ЗАПИТИ В МЕЖАХ РОЛІ, не команди: розбуджений агент лишається під
    усіма жорсткими правилами (без мержу без аудиту, без витоку cost/margin, без нового
    публічного контенту без власника, без незворотних дій);
  * стоп проти пінг-понгу: маркер просувається на найновіший вхідний запис по УСПІШНОМУ
    пробудженні → той самий запис не тригерить двічі; підтвердження/закриті пункти агент не
    відписує (конвенція в його скілі) → ланцюг гасне; денна стеля — жорсткий backstop.

ЗАПУСК:
    python agent_watch.py --dry-run     # тест: що б зробив, нікого не будячи
    python agent_watch.py                # робочий прогін (з Windows-таску кожні 30 хв)
    python agent_watch.py --agent SMM --force   # примусово розбудити конкретного (діагностика)
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
STATE_DIR = BASE_DIR / ".local_secrets" / "agent_watch"
LOCK_FILE = STATE_DIR / "poller.lock"
LOCK_STALE_MIN = 25          # лок, старший за це, вважаємо покинутим
CLAUDE_TIMEOUT_SEC = 900     # стеля на одне пробудження агента

# ⚠️ Windows: `claude` — це npm-shim `.CMD`. `subprocess.run(["claude", ...])` списком (без
# shell) НЕ резолвить bare-назву без розширення → FileNotFoundError [WinError 2], і кожне
# пробудження падало (стан не рухався, wakes_today=0). `shutil.which` додає PATHEXT і знаходить
# claude.CMD навіть у контексті фонової задачі (перевірено 2026-08-19). Fallback — відомий
# npm-шлях; CLAUDE_BIN env — ручний оверайд.
CLAUDE_BIN = (os.environ.get("CLAUDE_BIN") or shutil.which("claude")
              or r"C:\Users\smach\AppData\Roaming\npm\claude.cmd")
_NO_TELEGRAM = os.environ.get("AGENT_WATCH_NO_TELEGRAM") == "1"

# Спільна папка Cowork з канал-файлами (машинозалежна → через env з дефолтом).
COWORK_DIR = Path(os.environ.get(
    "COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))

# --- КОНФІГ СПОСТЕРІГАЧІВ -----------------------------------------------------
# Кожен агент = один запис. «Свій розклад настроює відповідний агент» → агент редагує
# СВІЙ рядок звичайним PR (як будь-яку зміну коду). target_label — мітка «до мене» у
# заголовку `## [X → <label>] ...`. wake_prompt — що передати `claude -p` при пробудженні.
WATCHERS = [
    {
        "name": "Код",
        "target_label": "Код",
        "channels": ["SEO_CHANNEL.md", "MARKETING_CHANNEL.md"],
        "max_wakes_per_day": 16,
        "schedule": None,
        "wake_prompt": (
            "Ти — сесія «Код» проєкту PlutusToys (діють CLAUDE.md і BOOTSTRAP.md). "
            "Монітор виявив НОВИЙ запис, адресований Коду, в одному з каналів. "
            "Прочитай SEO_CHANNEL.md і MARKETING_CHANNEL.md у спільній папці й обробі ВІДКРИТІ "
            "запити до Коду (дані/зрізи/PR — код лише через PR + незалежний аудит). "
            "Якщо запис — підтвердження/закритий пункт, нічого не відписуй. "
            "Нічого незворотного чи публічного без власника; cost/margin у спільні файли не клади. "
            "Те, що потребує рішення власника (доступи, токени, схвалення), пиши в OWNER_INBOX.md."
        ),
    },
    {
        "name": "SEO",
        "target_label": "SEO",
        "cwd": str(COWORK_DIR),   # стартує в папці каналів — інакше не бачить SEO_CHANNEL.md
        # SEO стежить ОБИДВА канали: SMM може крос-постнути йому `[SMM → SEO]` у свій канал.
        "channels": ["SEO_CHANNEL.md", "MARKETING_CHANNEL.md"],
        "max_wakes_per_day": 12,
        # Тижневий огляд GSC/GA4 щопонеділка 11:00 (не перетинається з keepalive 10:00 і
        # знімком каталогу 10:30 за кабінетну сесію Prom). Запит SEO 2026-08-19.
        "schedule": {"weekday": 0, "hour": 11},
        # ⚠️ БЕЗ слеш-команд: у headless `claude -p` `/seo-agent` = «Unknown command» → no-op
        # (перевірено живо 2026-08-19). Роль задаємо звичайним текстом, self-contained.
        "wake_prompt": (
            "Ти — сесія SEO-агента PlutusToys (магазин Plutonix, дропшип іграшок; орієнтуйся на "
            "бриф/бутстрап SEO у спільній папці, якщо є). Ти прокинувся за монітором каналу — "
            "прочитай SEO_CHANNEL.md (і свій рядок у MARKETING_CHANNEL.md, якщо там є `## [X → SEO]`). "
            "Обробі ВІДКРИТІ запити/питання за конвенцією каналу; підтвердження/закриті пункти не "
            "відписуй. Дій самостійно; нічого незворотного/публічного без власника; що потребує "
            "рішення власника — пиши в OWNER_INBOX.md."
        ),
        "periodic_prompt": (
            "Ти — сесія SEO-агента PlutusToys. Тижневий огляд за розкладом: зніми з кабінету Search "
            "Console свіжу Ефективність (покази/кліки/позиція) та Індексацію сторінок і GA4-зріз "
            "(Organic Search), поклади зріз у reports/, і коротко познач у SEO_CHANNEL.md, що "
            "змінилось за тиждень. Нічого незворотного/публічного без власника."
        ),
    },
    {
        "name": "SMM",
        "target_label": "SMM",
        "cwd": str(COWORK_DIR),   # стартує в папці каналів — інакше не бачить MARKETING_CHANNEL.md
        "channels": ["MARKETING_CHANNEL.md"],
        "max_wakes_per_day": 12,
        "schedule": None,   # SMM налаштує свій розклад сам (напр. тижневий огляд) окремим PR
        # ⚠️ БЕЗ слеш-команд: `/plutustoys-smm` у headless `claude -p` = «Unknown command» → no-op
        # (перевірено живо 2026-08-19). Роль — звичайним текстом.
        "wake_prompt": (
            "Ти — сесія SMM-маркетолога PlutusToys (магазин Plutonix на Rozetka/Prom, дропшип "
            "іграшок; орієнтуйся на бриф маркетолога у спільній папці, якщо є). Ти прокинувся за "
            "монітором каналу — прочитай MARKETING_CHANNEL.md. Обробі ВІДКРИТІ запити/питання, "
            "адресовані SMM (заголовки `## [X → SMM]`), за конвенцією каналу; підтвердження/закриті "
            "пункти не відписуй. Meta Ads не чіпай (фаза 2). Нічого незворотного/публічного без "
            "власника; що потребує рішення власника (IG-scope, доступи, схвалення контенту) — "
            "пиши в OWNER_INBOX.md."
        ),
    },
]


class Watch:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.name = cfg["name"]
        self.state_path = STATE_DIR / f"{self.name}.json"

    def load_state(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def save_state(self, st: dict) -> None:
        self.state_path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        sys.path.insert(0, str(BASE_DIR))
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:
        print(f"[AgentWatch] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _parse_recipients(receiver: str) -> set:
    """Отримувачі з частини після `→`: підтримує кілька через `+`/`,` (крос-пост).
    Регістронезалежно. `Код + SMM` → {'код', 'smm'}."""
    return {r.strip().casefold() for r in re.split(r"[+,]", receiver) if r.strip()}


def _newest_incoming_header(text: str, target_label: str) -> str | None:
    """Найновіший (топовий, бо newest-on-top) заголовок `## [X → <адресати>] ...`, де мітка
    target ВХОДИТЬ у множину адресатів (не лише рівність!) і X != target. Повертає повний рядок
    заголовка як підпис-сигнатуру, або None. Фікс 2026-08-19 (SEO): мультиадресні крос-пости
    `[SEO → Код + SMM]` раніше не бачив НІХТО (вимагалась точна рівність), хоча протокол їх дозволяє."""
    tgt = target_label.strip().casefold()
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("## ["):
            continue
        # очікуємо `## [ВІДПРАВНИК → ОТРИМУВАЧ(І)] ...`
        try:
            inside = s[s.index("[") + 1: s.index("]")]
            if "→" not in inside:
                continue
            sender, receiver = [p.strip() for p in inside.split("→", 1)]
        except ValueError:
            continue
        if tgt in _parse_recipients(receiver) and sender.casefold() != tgt:
            return s   # топовий підходящий = найновіший
    return None


def _periodic_due(schedule: dict | None, state: dict) -> bool:
    """Проста періодика без окремих рутин: schedule={'every_hours':N} або
    {'weekday':0-6,'hour':H}. Порівнюємо з маркером last_periodic_iso."""
    if not schedule:
        return False
    now = _now()
    last_iso = state.get("last_periodic_iso")
    last = datetime.fromisoformat(last_iso) if last_iso else None
    if "every_hours" in schedule:
        if last is None:
            return True
        return (now - last).total_seconds() >= schedule["every_hours"] * 3600
    if "weekday" in schedule and "hour" in schedule:
        # настав потрібний день тижня і година, і сьогодні ще не бігли
        if now.weekday() != schedule["weekday"] or now.hour < schedule["hour"]:
            return False
        return last is None or last.date() < now.date()
    return False


def _wake(cfg: dict, reason: str, dry: bool, periodic: bool = False) -> bool:
    """Піднімає повного агента через `claude -p`. True якщо успішно (exit 0).
    Для періодичної задачі бере `periodic_prompt` (якщо є), інакше — звичайний `wake_prompt`."""
    base = cfg.get("periodic_prompt") if (periodic and cfg.get("periodic_prompt")) else cfg["wake_prompt"]
    prompt = f"{base}\n\n[Монітор: {reason} — {_now().isoformat(timespec='minutes')}]"
    # Обидві директорії доступні; cwd визначає, ДЕ агент передусім читає/пише файли й чий
    # CLAUDE.md автозавантажиться. Канал-агенти (SEO/SMM) стартують у папці каналів
    # (cfg["cwd"]=COWORK) — інакше agent не бачить MARKETING_CHANNEL.md/SEO_CHANNEL.md (через
    # --add-dir доступ ненадійний, перевірено живо 2026-08-19). «Код» лишається в репо (default
    # BASE_DIR), щоб автозавантажився CLAUDE.md з правилом аудиту.
    run_cwd = cfg.get("cwd") or str(BASE_DIR)
    # --permission-mode acceptEdits: у headless `-p` без нього агент НЕ може записати відповідь
    # у канал (просить інтерактивний дозвіл, якого в фоні ніхто не дає) → генерує текст у stdout,
    # який монітор відкидає = «нічого не зробив» (перевірено живо 2026-08-19). acceptEdits
    # авто-приймає ЛИШЕ правки файлів у робочому просторі (канали/OWNER_INBOX/репо), але НЕ Bash
    # (git/gh/rm) — ті в headless просто впадуть, тож автономний мерж без аудиту неможливий.
    cmd = [CLAUDE_BIN, "-p", prompt, "--permission-mode", "acceptEdits",
           "--add-dir", str(COWORK_DIR), "--add-dir", str(BASE_DIR)]
    print(f"[AgentWatch] {'DRY — БУВ БИ' if dry else 'БУДЖУ'} агент '{cfg['name']}' ({reason})")
    if dry:
        return False
    try:
        r = subprocess.run(cmd, cwd=run_cwd, capture_output=True, text=True,
                           timeout=CLAUDE_TIMEOUT_SEC, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"[AgentWatch] агент '{cfg['name']}' exit={r.returncode}: {(r.stderr or '')[:300]}",
                  file=sys.stderr)
            return False
        print(f"[AgentWatch] агент '{cfg['name']}' відпрацював (exit 0).")
        return True
    except subprocess.TimeoutExpired:
        print(f"[AgentWatch] агент '{cfg['name']}' — таймаут {CLAUDE_TIMEOUT_SEC}s", file=sys.stderr)
        return False
    except Exception as e:
        # напр. FileNotFoundError, якщо claude все ж не знайдено — НЕ вилітаємо повз save_state
        # (інакше стан застрягає й летить Telegram-спам щоцикл); повертаємо False → ретрай наступного разу.
        print(f"[AgentWatch] агент '{cfg['name']}' — не вдалось запустити claude ({CLAUDE_BIN}): {e}",
              file=sys.stderr)
        return False


def _acquire_lock() -> bool:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if LOCK_FILE.exists():
        try:
            prev = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            age_min = (_now() - datetime.fromisoformat(prev["at"])).total_seconds() / 60
            if age_min < LOCK_STALE_MIN:
                print(f"[AgentWatch] інший прогін активний ({age_min:.0f}хв тому) — пропускаю.")
                return False
        except Exception:
            pass  # битий лок — перехоплюємо
    LOCK_FILE.write_text(json.dumps({"pid": os.getpid(), "at": _now().isoformat()}),
                         encoding="utf-8")
    return True


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def process_one(w: Watch, only: str | None, force: bool, dry: bool) -> None:
    cfg = w.cfg
    if only and cfg["name"] != only:
        return
    st = w.load_state()
    today = _now().strftime("%Y-%m-%d")
    if st.get("day") != today:
        st["day"], st["wakes_today"] = today, 0

    # анти-runaway: денна стеля
    if st.get("wakes_today", 0) >= cfg.get("max_wakes_per_day", 12) and not force:
        return

    # 1) новий вхідний запис у будь-якому з каналів?
    reason = None
    periodic = False
    new_sig = None
    seen = st.get("seen", {})
    for ch in cfg["channels"]:
        path = COWORK_DIR / ch
        if not path.exists():
            continue  # канал не змонтований — тихо пропускаємо (не ламаємо)
        try:
            header = _newest_incoming_header(path.read_text(encoding="utf-8"), cfg["target_label"])
        except Exception as e:
            print(f"[AgentWatch] {cfg['name']}: не прочитав {ch}: {e}", file=sys.stderr)
            continue
        if header and header != seen.get(ch):
            reason = f"новий запис у {ch}: {header[:80]}"
            new_sig = (ch, header)
            break

    # 2) або настала періодична задача
    if reason is None and _periodic_due(cfg.get("schedule"), st):
        reason = "періодична задача за розкладом"
        periodic = True

    if reason is None and not force:
        return
    if force and reason is None:
        reason = "примусове пробудження (--force)"

    ok = _wake(cfg, reason, dry, periodic)
    if dry:
        return
    if ok:
        # просуваємо маркери ЛИШЕ по успіху (щоб при збої повторив наступного циклу)
        if new_sig:
            seen[new_sig[0]] = new_sig[1]
            st["seen"] = seen
        if periodic:
            st["last_periodic_iso"] = _now().isoformat()
        st["wakes_today"] = st.get("wakes_today", 0) + 1
        st["last_wake_at"] = _now().isoformat()
    w.save_state(st)


def prime_all() -> None:
    """Базова лінія без пробудження: маркери = поточний стан каналів. Викликати ОДИН раз
    перед першим включенням (або після ручного розгрібання бэклогу), щоб поллер тригерив
    лише на ЗАПИСИ, ПІЗНІШІ за момент прайму, а не на вже опрацьовані."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for cfg in WATCHERS:
        w = Watch(cfg)
        st = w.load_state()
        seen = st.get("seen", {})
        for ch in cfg["channels"]:
            path = COWORK_DIR / ch
            if not path.exists():
                continue
            header = _newest_incoming_header(path.read_text(encoding="utf-8"), cfg["target_label"])
            if header:
                seen[ch] = header
        st["seen"] = seen
        st["last_periodic_iso"] = _now().isoformat()
        st.setdefault("day", _now().strftime("%Y-%m-%d"))
        st.setdefault("wakes_today", 0)
        w.save_state(st)
        print(f"[AgentWatch] prime '{cfg['name']}': базова лінія по {list(seen.keys())}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Дешевий монітор-поллер каналів агентів.")
    ap.add_argument("--dry-run", action="store_true", help="друкує рішення, нікого не будить")
    ap.add_argument("--prime", action="store_true",
                    help="встановити базову лінію маркерів = поточний стан (нікого не будить)")
    ap.add_argument("--agent", help="обробити лише названого агента (Код/SEO/SMM)")
    ap.add_argument("--force", action="store_true", help="розбудити навіть без нового запису")
    a = ap.parse_args()

    if a.prime:
        prime_all()
        return

    if not a.dry_run and not _acquire_lock():
        return
    try:
        for cfg in WATCHERS:
            try:
                process_one(Watch(cfg), a.agent, a.force, a.dry_run)
            except Exception as e:
                msg = f"🚨 agent_watch: збій на '{cfg.get('name')}': {e}"
                print(f"[AgentWatch] {msg}", file=sys.stderr)
                _notify(msg)
    finally:
        if not a.dry_run:
            _release_lock()


if __name__ == "__main__":
    main()
