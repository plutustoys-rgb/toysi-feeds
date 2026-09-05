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
    "consultant": [COWORK_DIR / "CONSULTANT_CHANNEL.md", COWORK_DIR / "OWNER_INBOX.md",
                   COWORK_DIR / "онбординг_консультанта_відповіді_Код.md", COWORK_DIR / "STATUS.md",
                   COWORK_DIR / "archive" / "consultant"],
    "kodv": [COWORK_DIR / "КОДВ_CHANNEL.md", COWORK_DIR / "КОДВ_журнал.md",
             COWORK_DIR / "КОДВ_норми_довідник.md", COWORK_DIR / "документи_КОДВ",
             COWORK_DIR / "OWNER_INBOX.md", COWORK_DIR / "archive" / "kodv"],
}
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
    a = ap.parse_args()
    if not a.query and not a.file:
        ap.print_help()
        sys.exit(0)
    sys.exit(recall(a.query, a.file, a.config))


if __name__ == "__main__":
    main()
