"""recall.py — «що вже зроблено/вирішено про X» з git + коду + SYSTEM_MAP + CODE_LOG.

МЕТА: не покладатись на пам'ять сесії (після ущільнення агент забуває й формує ДУБЛІ вже
зробленого — головна біль власника). Джерело правди — репозиторій, а не контекст. Викликати:
  • ВРУЧНУ перед роботою:  python recall.py "fb token refresh"
  • режим ДУБЛЬ-ВАРТА:      python recall.py --file fb_token_refresh.py
    (exit 3, якщо схожий файл/модуль уже є — хук recall-guard використовує цей код, щоб
     впорснути попередження В МИТЬ створення, не покладаючись на те, чи агент згадав перевірити).

Детермінований, дешевий (git+grep), без LLM — тож у хуку не коштує токенів і не бреше.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Не залежати від ambient-кодування stdout: на дефолтному Windows/cp1251-Python вивід із емодзі
# (🔴) чи стрілками/галочками (→ ✅) з git/SYSTEM_MAP/CODE_LOG інакше падає UnicodeEncodeError —
# і хук recall-guard, що спирається на exit-код, мовчки не спрацьовує (аудит PR #370). errors=replace
# — щоб НІКОЛИ не падати на екзотичному гліфі, а не лише перекодувати.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
ARCHIVE_DIR = BASE_DIR / "archive"   # холодний архів повної історії (див. archive/README.md)
MEMORY_DIR = Path(os.environ.get(    # durable памʼять агента (факти/фідбек/проєкт) — ЧИТАТИ ПЕРШИМ
    "CLAUDE_MEMORY_DIR", r"C:\Users\smach\.claude\projects\C--Users-smach\memory"))
COWORK_DIR = Path(os.environ.get(
    "PLUTUS_COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))

# Профілі ролей: кожен агент шукає у СВОЇХ джерелах (не в коді — SEO/SMM працюють у каналах, не в
# репо). `recall.py --config seo "тема"` → шукає лише в цих файлах/папках + їх archive. Дублі в них
# інші, ніж у коду: повторне дослідження/закритий запит (напр. SEO подав уже закриті «укр-назви»).
_CONFIGS = {
    "seo": [COWORK_DIR / "SEO_CHANNEL.md", COWORK_DIR / "MARKETING_CHANNEL.md",
            COWORK_DIR / "OWNER_INBOX.md", COWORK_DIR / "archive" / "seo"],
    "smm": [COWORK_DIR / "MARKETING_CHANNEL.md", COWORK_DIR / "OWNER_INBOX.md",
            COWORK_DIR / "archive" / "smm"],
    "seller": [COWORK_DIR / "SELLER_CHANNEL.md", COWORK_DIR / "OWNER_INBOX.md",
               COWORK_DIR / "archive" / "seller"],
    "executor": [COWORK_DIR / "EXECUTOR_CHANNEL.md", COWORK_DIR / "CONSULTANT_CHANNEL.md",
                 COWORK_DIR / "OWNER_INBOX.md", COWORK_DIR / "archive" / "executor"],
    "consultant": [COWORK_DIR / "CONSULTANT_CHANNEL.md", COWORK_DIR / "SEO_CHANNEL.md",
                   COWORK_DIR / "SELLER_CHANNEL.md", COWORK_DIR / "КОДВ_CHANNEL.md",
                   COWORK_DIR / "OWNER_INBOX.md",
                   COWORK_DIR / "онбординг_консультанта_відповіді_Код.md", COWORK_DIR / "STATUS.md",
                   COWORK_DIR / "archive" / "consultant"],
    "kodv": [COWORK_DIR / "КОДВ_CHANNEL.md", COWORK_DIR / "КОДВ_журнал.md",
             COWORK_DIR / "КОДВ_норми_довідник.md", COWORK_DIR / "документи_КОДВ",
             COWORK_DIR / "OWNER_INBOX.md", COWORK_DIR / "archive" / "kodv"],
    # 22.10: ролі, яких бракувало (після ущільнення вони не мали картки стану).
    "kod": [COWORK_DIR / "EXECUTOR_CHANNEL.md", COWORK_DIR / "CONSULTANT_CHANNEL.md",
            COWORK_DIR / "SEO_CHANNEL.md", COWORK_DIR / "MARKETING_CHANNEL.md",
            COWORK_DIR / "КОДВ_CHANNEL.md", COWORK_DIR / "OWNER_INBOX.md"],
    "architector": [COWORK_DIR / "OWNER_INBOX.md", COWORK_DIR / "COORDINATOR_LOG.md",
                    COWORK_DIR / "STATUS.md", COWORK_DIR / "EXECUTOR_CHANNEL.md"],
    "tester": [COWORK_DIR / "тест_сайту_журнал.md", COWORK_DIR / "OWNER_INBOX.md",
               COWORK_DIR / "archive" / "tester"],
}
# Стан ролей між ущільненнями контексту: <роль>.md (пише PreCompact) + <id сесії>.role (кеш ролі).
# У .local_secrets (gitignored).
ROLE_STATE_DIR = Path(os.environ.get("PLUTUS_ROLE_STATE_DIR") or (BASE_DIR / ".local_secrets" / "role_state"))
# Токени, що нічого не кажуть про тему (щоб не матчити пів-репо). Латиниця (код) + укр (канали).
_STOP = {"py", "sh", "test", "tests", "util", "utils", "the", "and", "for", "new", "old",
         "tmp", "temp", "client", "run", "main", "get", "set", "make", "data", "file",
         "для", "або", "вже", "що", "цей", "той", "при", "над", "під", "так", "але",
         "який", "яка", "яке", "наш", "наша", "які", "теж", "усе", "все", "цього", "того",
         "було", "буде", "щоб", "тому", "його", "цю", "цим", "них", "them"}


def _terms(text: str) -> list:
    # [\W_]+ (Unicode): розбиває і на не-літери, і на підкреслення — тож і код-стеми
    # (fb_token_refresh → fb,token,refresh), і кирилиця (укр-назви → укр,назви) токенізуються.
    toks = [t for t in re.split(r"[\W_]+", (text or "").lower()) if t]
    return [t for t in toks if len(t) >= 3 and t not in _STOP]


def _git(args: list) -> str:
    try:
        r = subprocess.run(["git", "-C", str(BASE_DIR)] + args,
                           capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _tracked_files() -> list:
    return [f for f in _git(["ls-files"]).splitlines() if f.strip()]


def _similar_files(stem_terms: list, files: list) -> list:
    """Файли, чий стем ділить >=2 значущі токени з новою назвою (або той самий стем) — дубль-сигнал."""
    st = set(stem_terms)
    hits = []
    for f in files:
        base = Path(f).stem
        ftoks = set(_terms(base))
        shared = st & ftoks
        if len(shared) >= 2 or (st and st == ftoks):
            hits.append((f, sorted(shared)))
    return hits


def _archive_hits(terms: list) -> list:
    """Вказівники на файли холодного архіву, що метчать тему (за назвою АБО вмістом). Повертаємо
    лише шляхи (не вміст) — щоб recall лишався коротким; повну сагу читати відкривши файл."""
    if not ARCHIVE_DIR.exists() or not terms:
        return []
    hits = []
    for p in sorted(ARCHIVE_DIR.glob("*.md")):
        if p.name.lower() == "readme.md":
            continue
        name_low = p.name.lower()
        if any(t in name_low for t in terms):
            hits.append(f"archive/{p.name}")
            continue
        try:
            if any(t in p.read_text(encoding="utf-8", errors="replace").lower() for t in terms):
                hits.append(f"archive/{p.name}")
        except Exception:
            continue
    return hits[:8]


def _search_roots(roots: list, terms: list) -> list:
    """Пошук теми у ДЖЕРЕЛАХ ролі (файли + *.md у папках). Повертає рядки 'файл: сніпет'."""
    out = []
    for r in roots:
        try:
            if r.is_file():
                for s in _grep_file(r, terms, r.name):
                    out.append(f"{r.name}: {s}")
            elif r.is_dir():
                for p in sorted(r.glob("*.md")):
                    for s in _grep_file(p, terms, p.name):
                        out.append(f"{r.name}/{p.name}: {s}")
        except Exception:
            continue
    return out[:12]


def _grep_file(path: Path, terms: list, label: str) -> list:
    if not path.exists() or not terms:
        return []
    out = []
    try:
        for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
            low = ln.lower()
            if any(t in low for t in terms):
                out.append(ln.strip()[:140])
    except Exception:
        return []
    return out[:6]


def _memory_hits(terms: list) -> list:
    """Збіги в durable-памʼяті агента (memory/*.md). ЧИТАТИ ПЕРШИМ — це найсвіжіші висновки/правила."""
    if not MEMORY_DIR.exists() or not terms:
        return []
    scored = []
    for p in sorted(MEMORY_DIR.glob("*.md")):
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        low = txt.lower()
        score = sum(low.count(t) for t in terms)   # ранг за кількістю збігів термінів
        if not score:
            continue
        snip = ""
        for ln in txt.splitlines():
            l = ln.strip()
            if (l and any(t in l.lower() for t in terms)
                    and not l.startswith(("---", "name:", "description:", "metadata", "node_type", "type:", "originSessionId", "modified"))):
                snip = l[:140]
                break
        scored.append((score, f"memory/{p.name}: {snip}" if snip else f"memory/{p.name}"))
    scored.sort(key=lambda x: x[0], reverse=True)   # найрелевантніше першим (щоб не обрізалось капом)
    return [h for _, h in scored[:8]]


# Підписи авторів у заголовках OWNER_INBOX (`## [Автор] дата — ...`) для кожної ролі.
_ROLE_AUTHORS = {
    "seo": ("seo",), "smm": ("smm",), "seller": ("продажник",), "executor": ("виконавець",),
    "consultant": ("консультант",), "kodv": ("кодв", "бухгалтер", "головний бухгалтер", "аудитор"),
    # "[код" співпало б із "[кодв…" (бухгалтер) — тому з закриттям/стрілкою.
    "kod": ("код]", "код →", "code-agent"), "architector": ("архітектор",), "tester": ("тестувальник",),
}
# Ознаки ролі в ПЕРШОМУ повідомленні сесії (єдине місце, де роль названо: cwd у всіх однаковий).
# Рахуємо збіги по ролях; нічия або нуль → "" (фолбек: хук лишає поведінку без змін).
_ROLE_MARKERS = {
    "seo": ("seo-агент", "seo агент", "plutus-seo", "агента-seo", "агент-seo", "агент seo", "новий seo"),
    "smm": ("агент-маркетолог", "маркетолог соцмереж", "plutus-smm", "plutustoys-smm", "агента-smm", "агент-smm", "агент smm", "smm-агент", "smm агент"),
    "seller": ("агент-продажник", "агента-продажника", "агент продажник", "plutus-seller", "plutus-продажник"),
    "executor": ("агент-виконавець", "агента-виконавця", "plutus-executor", "агент виконавець"),
    "consultant": ("бізнес-консультант", "бізнес консультант", "бізнес-консультанта", "plutus-consultant",
                   "business-consultant"),
    "kodv": ("головний бухгалтер", "агент-бухгалтер", "агента-бухгалтера", "plutus-kodv", "kodv-independent",
             "kodv-legislative", "ти бухгалтер", "агент бухгалтер"),
    "kod": ("you are code-agent", "plutus-kod", "агент-код", "агента-код", "ти — код ", "ти - код "),
    "architector": ("agent-architector", "агент-архітектор", "агента-архітектора", "ти — архітектор", "ти - архітектор"),
    "tester": ("plutus-tester", "агент-тестувальник", "агента-тестувальника", "тестувальник сайту"),
}


_ID_WINDOW = 140


def _role_cache_file(transcript_path: str) -> Path:
    return ROLE_STATE_DIR / f"{Path(transcript_path).stem}.role"


def set_role(transcript_path: str, role: str) -> None:
    """Запам'ятати роль сесії (id сесії = ім'я .jsonl). Читається detect_role/SessionStart."""
    if role in _CONFIGS and transcript_path:
        ROLE_STATE_DIR.mkdir(parents=True, exist_ok=True)
        _role_cache_file(transcript_path).write_text(role, encoding="utf-8")


def detect_role(transcript_path: str) -> str:
    """Роль сесії: PLUTUS_ROLE (env) → кеш сесії → перше user-повідомлення транскрипта;
    "" якщо не визначилась (нуль збігів АБО нічия між ролями — краще нічого, ніж не та роль).
    Знайдену за текстом роль кешуємо за id сесії — після ущільнення вона вже не зникне."""
    env_role = os.environ.get("PLUTUS_ROLE", "").strip().lower()
    if env_role in _CONFIGS:
        return env_role
    try:
        cached = _role_cache_file(transcript_path).read_text(encoding="utf-8").strip()
        if cached in _CONFIGS:
            return cached
    except OSError:
        pass
    role = _detect_role_from_text(transcript_path)
    if role:
        try:
            set_role(transcript_path, role)
        except OSError:
            pass
    return role


def _detect_role_from_text(transcript_path: str) -> str:
    import json
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            first = ""
            for i, line in enumerate(f):
                if i > 400:
                    break
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") == "user":
                    c = (o.get("message") or {}).get("content")
                    first = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                    break
    except Exception:
        return ""
    # Лише ПЕРШІ _ID_WINDOW символів: там роль названо ("Ти — агент-X"); далі вона лише ЗГАДУЄТЬСЯ
    # (колега в тексті, перелік ролей) — аудит PR #604: так SMM→seo і архітектор→consultant.
    low = first[:_ID_WINDOW].lower()
    # kod: маркер без межі слова зачепив би kodv («plutus-kodv», «агент-кодв…») — аудит PR #961 B1.
    def _count(role, m):
        return len(re.findall(re.escape(m) + (r"(?!\w)" if role == "kod" and not m.endswith(" ") else ""), low))
    scores = {r: sum(_count(r, m) for m in ms) for r, ms in _ROLE_MARKERS.items()}
    best = max(scores.values(), default=0)
    if best == 0:
        return ""
    leaders = [r for r, v in scores.items() if v == best]
    return leaders[0] if len(leaders) == 1 else ""


def restore_card(config: str, n: int = 4, cap: int = 2800) -> str:
    """Картка стану ролі (для «продовжуй/далі» після ущільнення, коли теми для пошуку нема):
    заголовки N останніх записів її каналів (newest-on-top, з датами) + ВІДКРИТІ рядки
    OWNER_INBOX цієї ролі. Лише заголовки — достатньо, щоб не пропустити запис, якого не читав.
    Бюджет `cap` ділиться по секціях: відкриті пункти (найважливіше) резервуються першими, канали
    урізаються ЦІЛИМИ рядками з кінця — ніколи посеред рядка (аудит PR #604)."""
    roots = _CONFIGS.get(config)
    if not roots:
        return ""
    if config == "kod":
        n = min(n, 2)   # kod має 5 каналів — по 2 свіжі заголовки, щоб вмістилось у cap (інші ролі без змін)
    chan = []
    for r in roots:
        if not (r.is_file() and r.name.upper().endswith("_CHANNEL.MD")):
            continue
        heads = []
        try:
            for ln in r.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.startswith("## ["):
                    heads.append(ln[3:].strip()[:130])
                    if len(heads) >= n:
                        break
        except Exception:
            continue
        if heads:
            chan.append(f"{r.name} (угорі = найновіше):")
            chan += [f"   • {h}" for h in heads]
    authors = _ROLE_AUTHORS.get(config, ())
    opened = []
    try:
        for ln in (COWORK_DIR / "OWNER_INBOX.md").read_text(encoding="utf-8", errors="replace").splitlines():
            low = ln.lower()
            if (ln.startswith("## [") and any(f"[{a}" in low for a in authors) and "відкрито" in low
                    and not any(x in low for x in ("зроблено", "закрито", "✅"))):
                opened.append(f"   • {ln[3:].strip()[:130]}")
    except Exception:
        pass
    inbox = (["OWNER_INBOX — ВІДКРИТІ пункти цієї ролі:"] + opened[:5]) if opened else []
    head = f"=== СТАН [{config}]: останні записи каналів і відкриті пункти ==="
    budget = cap - len(head) - 1 - sum(len(x) + 1 for x in inbox)
    kept, used = [], 0
    for ln in chan:
        if used + len(ln) + 1 > budget:
            break
        kept.append(ln)
        used += len(ln) + 1
    lines = [head] + kept + inbox
    while len(lines) > 1 and len(chr(10).join(lines)) > cap:   # страховка: ціле-рядкове урізання
        lines.pop()
    out = chr(10).join(lines)
    return out if len(lines) > 1 and len(out) <= cap else ""


# ---------------------------------------------------------------------------------------------
# Відновлення ролі після ущільнення/перезапуску (22.10.2026, скарга власника: «після стискання
# агент забуває, хто він і на чому зупинився»). Два хуки Claude Code:
#   PreCompact   → recall.py --save-state   : скидає «що в роботі» в ROLE_STATE_DIR/<роль>.md
#   SessionStart → recall.py --session-start: роль + стан + картка каналів + ядро правил (≤3000 симв.)
# Детерміновано, без LLM, найгірший випадок = порожній вивід (хук нічого не ламає).
# ---------------------------------------------------------------------------------------------
CORE_RULES = (
    "ЯДРО ПРАВИЛ (повні — SYSTEM_MAP.md та CLAUDE.md репо rozetka_agent):\n"
    "• Твердження про систему — лише з доказом file:line, прочитаним ЦІЄЇ сесії; не вигадуй і не вгадуй.\n"
    "• Мерж у toysi-feeds — лише після незалежного аудиту (.audit_ok з номером PR); косметика — власна перевірка.\n"
    "• Гроші/ціни/відправлення/публікації назовні — лише з явного слова власника; пороги — з виміряного розподілу.\n"
    "• Секрети не друкувати, .env не source-ити; VPS: діагностика read-only, запис — власник.\n"
    "• Канали *_CHANNEL.md: newest-on-top, `## [Від → Кому] дата`; між живими сесіями — SendMessage.\n"
    "• Власнику — коротко, українською; «зроблено» лише з перевіркою."
)
_MASK = "[приховано]"
# Порядок важливий: спершу конкретні форми (ключ=значення, Bearer, прапорці, відомі префікси), потім довгі випадкові рядки.
_SECRET_PATTERNS = [
    # Authorization: Bearer <токен> / Bearer <токен>
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{6,}"), r"\1 " + _MASK),
    # ключ[=:]значення, у т.ч. JSON-ключі в лапках і *_SECRET_KEY / PRIVATE_KEY / LIQPAY_PRIVATE_KEY
    (re.compile(r"""(?ix)(["']?[\w.-]*(?:token|password|passwd|pwd|secret|api[_-]?key|private[_-]?key|access[_-]?key|authorization|пароль|парол\w*)[\w.-]*["']?\s*[=:]\s*)(["']?)[^\s"',;}]+\2"""),
     r"\1" + _MASK),
    # --password x / --pass=x / --token x
    (re.compile(r"(?i)(--?(?:password|passwd|pass|token|secret|api-key)[ =])\S+"), r"\1" + _MASK),
    # mysql -p'x' / -p"x"
    (re.compile(r"""(\s-p)(['"])[^'"]+\2"""), r"\1" + _MASK),
    # українське «пароль: x» / «пароль x» у запитах
    (re.compile(r"(?i)\b(парол[ьяюі]\w*\s*[:=—-]?\s*)\S+"), r"\1" + _MASK),
    # відомі префікси токенів (навіть короткі)
    (re.compile(r"\b(?:gh[pousr]_|github_pat_|sk-|xox[abprs]-|AKIA|AIza|ya29\.)[A-Za-z0-9_\-.]{8,}"), _MASK),
    # довгий випадковий рядок: ≥32 символи, є і літера, і цифра; git-SHA (рівно 40 hex) не чіпаємо
    (re.compile(r"\b(?![0-9a-f]{40}\b)(?=[A-Za-z0-9_\-]*[A-Za-z])(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{32,}\b"), _MASK),
]


def _redact(text: str) -> str:
    for rx, rep in _SECRET_PATTERNS:
        text = rx.sub(rep, text)
    return text


_SYS_BLOCK_RE = re.compile(r"(?s)<(system-reminder|task-notification|local-command-\w+|command-\w+)\b.*?</\1>")
_SKIP_PREFIXES = ("<system-reminder", "<task-notification", "<command-", "<local-command-", "Caveat:",
                  "[Request interrupted", "This session is being continued")


def _msg_text(content) -> str:
    """Текст повідомлення транскрипта (str або список блоків); tool_result — пропускаємо. Службові вставки
    (<system-reminder…> тощо) вирізаємо ПО-БЛОКОВО, щоб не губити справжній запит, який іде поруч (аудит PR #961)."""
    if isinstance(content, str):
        return _SYS_BLOCK_RE.sub("", content).strip()
    parts = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            t = _SYS_BLOCK_RE.sub("", b.get("text", "")).strip()
            if t:
                parts.append(t)
    return "\n".join(parts)


def _transcript_tail(transcript_path: str, max_bytes: int = 1_500_000) -> list:
    import json
    out = []
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            raw = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return out
    for ln in raw:
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if isinstance(o, dict):
            out.append(o)
    return out


def save_state(payload: dict) -> str:
    """PreCompact: механічно (без LLM) складає «що в роботі» з хвоста транскрипта і пише його
    в ROLE_STATE_DIR/<роль>.md (роль невідома → _session_<id>.md). Повертає шлях або ""."""
    import json
    from datetime import datetime
    tp = payload.get("transcript_path") or ""
    entries = _transcript_tail(tp)
    if not entries:
        return ""
    users, assistants, actions = [], [], []
    for o in entries:
        if not isinstance(o, dict):
            continue
        msg = o.get("message") if isinstance(o.get("message"), dict) else {}
        if o.get("type") == "user" and not o.get("isMeta") and not o.get("isCompactSummary"):
            t = _msg_text(msg.get("content"))
            if t and not t.startswith(_SKIP_PREFIXES):
                users.append(t)
        elif o.get("type") == "attachment" and isinstance(o.get("attachment"), dict):
            a = o["attachment"]   # повідомлення власника, надіслані ПОСЕРЕД ходу агента
            if a.get("type") == "queued_command" and (a.get("origin") or {}).get("kind") == "human":
                t = _msg_text(a.get("prompt") or "")
                if t and not t.startswith(_SKIP_PREFIXES):
                    users.append(t)
        elif o.get("type") == "assistant":
            c = msg.get("content")
            t = _msg_text(c).strip()
            if t:
                assistants.append(t)
            for b in (c if isinstance(c, list) else []):
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    arg = inp.get("file_path") or inp.get("command") or inp.get("url") or inp.get("description") or ""
                    actions.append(f"{b.get('name')}: {_redact(str(arg))[:90]}")   # спершу маскуємо, потім ріжемо
    role = detect_role(tp) if tp else ""
    stem = Path(tp).stem if tp else "unknown"
    lines = [f"# Стан перед ущільненням — {datetime.now().isoformat(timespec='seconds')} "
             f"(trigger={payload.get('trigger', '?')}, роль={role or 'не визначена'}, сесія={stem[:8]})"]
    if users:
        lines.append("## Останні запити власника/системи (старіші → новіші)")
        lines += [f"- {_redact(u)[:330]}" for u in users[-3:]]
    if assistants:
        lines.append("## Остання відповідь/план агента")
        lines.append(_redact(assistants[-1])[:900])
    if actions:
        lines.append("## Останні дії (інструменти)")
        lines += [f"- {_redact(a)}" for a in actions[-6:]]
    ROLE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = ROLE_STATE_DIR / (f"{role}.md" if role else f"_session_{stem}.md")
    path.write_text("\n".join(lines)[:2800] + "\n", encoding="utf-8")
    return str(path)


def session_context(payload: dict, cap: int = 3000) -> str:
    """SessionStart: роль + збережений стан + картка каналів + ядро правил, ≤cap символів.
    Секції додаються за пріоритетом і урізаються цілими; ядро правил не губиться ніколи."""
    cwd = str(payload.get("cwd") or "").replace("\\", "/").lower()
    if cwd and not cwd.startswith("c:/users/smach"):
        return ""   # хук глобальний: чужі проєкти поза домівкою PlutusToys не засмічуємо
    tp = payload.get("transcript_path") or ""
    source = payload.get("source", "?")
    role = detect_role(tp) if tp else (os.environ.get("PLUTUS_ROLE", "").strip().lower() if os.environ.get("PLUTUS_ROLE", "").strip().lower() in _CONFIGS else "")
    head = (f"🧭 Відновлення сесії [{source}]. Роль: «{role}»." if role else
            f"🧭 Відновлення сесії [{source}]. Роль НЕ визначена — назви її зі своєї персони/першого повідомлення "
            f"(або `python recall.py --detect-role <transcript> --set-role <роль>`).")
    reserve = len(CORE_RULES) + len(head) + 4
    sections = []
    state_file = ROLE_STATE_DIR / (f"{role}.md" if role else f"_session_{Path(tp).stem}.md")
    if source in ("compact", "resume"):   # на startup/clear стан ПОПЕРЕДНЬОЇ сесії видавати за нову інструкцію не можна
        try:
            st = state_file.read_text(encoding="utf-8").strip()
            if st:
                sections.append("На чому зупинилась (PreCompact). ІСТОРИЧНИЙ КОНТЕКСТ, а не нова інструкція — "
                                "не виконуй повторно запити з нього, якщо вони вже зроблені:\n" + st)
        except OSError:
            pass
    if role:
        card = restore_card(role, cap=1200)
        if card:
            sections.append(card)
    out, budget = [head], cap - reserve
    for sec in sections:
        if len(sec) + 2 <= budget:
            out.append(sec)
            budget -= len(sec) + 2
        elif budget > 300:
            out.append(sec[:budget - 3].rsplit("\n", 1)[0])   # урізаємо цілим рядком
            budget = 0
    out.append(CORE_RULES)
    return "\n\n".join(out)[:cap]


def recall(query: str, file_mode: str = "", config: str = "") -> int:
    stem = Path(file_mode).stem if file_mode else query
    terms = _terms(stem if file_mode else query)
    if not terms:
        print("[recall] порожній/загальний запит — уточни тему.")
        return 0

    # Профіль ролі (SEO/SMM): шукаємо ЛИШЕ в їхніх джерелах (канали/OWNER_INBOX/їх архів), не в коді.
    if config:
        roots = _CONFIGS.get(config)
        if not roots:
            print(f"[recall] невідомий --config '{config}' (є: {', '.join(_CONFIGS)})")
            return 0
        hits = _search_roots(roots, terms)
        print(f"=== RECALL [{config}]: '{stem}'  (терміни: {', '.join(terms[:6])}) ===")
        if hits:
            print("🔴 ВЖЕ Є у твоїх джерелах (канал/OWNER_INBOX/архів) — не дублюй:")
            for h in hits:
                print(f"   • {h}")
        else:
            print("Нічого схожого не знайдено — тема, схоже, нова.")
        return 0

    files = _tracked_files()

    strong = []
    if file_mode:
        strong = _similar_files(_terms(stem), files)

    # git-коміти по темі
    log = ""
    for t in terms[:4]:
        log += _git(["log", "--oneline", "-i", "--grep", t, "--max-count", "6", "--all"])
    commits = sorted(set(l for l in log.splitlines() if l.strip()))[:10]

    # SYSTEM_MAP + CODE_LOG
    sysmap = _grep_file(BASE_DIR / "SYSTEM_MAP.md", terms, "SYSTEM_MAP")
    codelog = _grep_file(COWORK_DIR / "CODE_LOG.md", terms, "CODE_LOG")
    archive = _archive_hits(terms)
    memory = _memory_hits(terms)

    # означення у коді (def/class) з термінами
    defs = []
    try:
        rg = subprocess.run(["git", "-C", str(BASE_DIR), "grep", "-niE",
                             r"^(def|class|async def) .*(" + "|".join(re.escape(t) for t in terms[:4]) + ")"],
                            capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace")
        defs = [l[:140] for l in (rg.stdout or "").splitlines()][:8]
    except Exception:
        pass

    dup = bool(strong)
    print(f"=== RECALL: '{stem}'  (терміни: {', '.join(terms[:6])}) ===")
    if memory:
        print("Памʼять (durable факти/правила — ЧИТАЙ ПЕРШИМ):")
        for m in memory:
            print(f"   • {m}")
    if strong:
        print("🔴 МОЖЛИВИЙ ДУБЛЬ — уже є схожі файли:")
        for f, sh in strong[:6]:
            print(f"   • {f}   (спільне: {', '.join(sh)})")
    if defs:
        print("Означення в коді (def/class):")
        for d in defs:
            print(f"   • {d}")
    if commits:
        print("Коміти по темі:")
        for c in commits:
            print(f"   • {c}")
    if sysmap:
        print("SYSTEM_MAP:")
        for s in sysmap:
            print(f"   • {s}")
    if codelog:
        print("CODE_LOG:")
        for s in codelog:
            print(f"   • {s}")
    if archive:
        print("Архів (повна історія — відкрий файл для деталей):")
        for a in archive:
            print(f"   • {a}")
    if not (strong or defs or commits or sysmap or codelog or archive or memory):
        print("Нічого схожого не знайдено — тема, схоже, нова.")
    return 3 if dup else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", default="", help="тема для пошуку історії")
    ap.add_argument("--file", default="", help="режим дубль-варта для нового файлу (exit 3 якщо схоже вже є)")
    ap.add_argument("--config", default="", choices=["", *_CONFIGS.keys()],
                    help="профіль ролі (seo/smm): шукати в ЇХНІХ джерелах (канали/OWNER_INBOX/архів), не в коді")
    ap.add_argument("--restore", action="store_true",
                    help="з --config <роль>: картка стану (заголовки останніх записів каналів + відкрите в OWNER_INBOX)")
    ap.add_argument("--detect-role", default="", metavar="TRANSCRIPT",
                    help="надрукувати роль сесії за її транскриптом (порожньо, якщо не визначилась)")
    ap.add_argument("--set-role", default="", metavar="ROLE",
                    help="з --detect-role <transcript>: запам'ятати роль сесії вручну (ім'я з --config)")
    ap.add_argument("--session-start", action="store_true",
                    help="хук SessionStart: JSON зі stdin → JSON additionalContext (роль+стан+картка+ядро правил)")
    ap.add_argument("--save-state", action="store_true",
                    help="хук PreCompact: JSON зі stdin → скинути «що в роботі» в .local_secrets/role_state/<роль>.md")
    a = ap.parse_args()
    if a.session_start or a.save_state:
        import json
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            sys.exit(0)
        try:
            if a.save_state:
                save_state(payload)
            else:
                ctx = session_context(payload)
                if ctx.strip():
                    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                             "additionalContext": ctx}}, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 — хук не має ламати старт сесії
            print(f"[recall] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(0)
    if a.set_role:
        if a.set_role not in _CONFIGS or not a.detect_role:
            ap.error(f"--set-role потребує --detect-role <transcript> і роль з: {', '.join(_CONFIGS)}")
        set_role(a.detect_role, a.set_role)
        print(a.set_role)
        sys.exit(0)
    if a.detect_role:
        print(detect_role(a.detect_role))
        sys.exit(0)
    if a.restore:
        if not a.config:
            ap.error("--restore потребує --config <роль>")
        print(restore_card(a.config))
        sys.exit(0)
    if not a.query and not a.file:
        ap.print_help()
        sys.exit(0)
    sys.exit(recall(a.query, a.file, a.config))


if __name__ == "__main__":
    main()
