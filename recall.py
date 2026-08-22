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

BASE_DIR = Path(__file__).resolve().parent
COWORK_DIR = Path(os.environ.get(
    "PLUTUS_COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
# Токени, що нічого не кажуть про тему (щоб не матчити пів-репо).
_STOP = {"py", "sh", "test", "tests", "util", "utils", "the", "and", "for", "new", "old",
         "tmp", "temp", "client", "run", "main", "get", "set", "make", "data", "file"}


def _terms(text: str) -> list:
    toks = [t for t in re.split(r"[^a-zA-Z0-9]+", (text or "").lower()) if t]
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


def recall(query: str, file_mode: str = "") -> int:
    stem = Path(file_mode).stem if file_mode else query
    terms = _terms(stem if file_mode else query)
    if not terms:
        print("[recall] порожній/загальний запит — уточни тему.")
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
    if not (strong or defs or commits or sysmap or codelog):
        print("Нічого схожого не знайдено — тема, схоже, нова.")
    return 3 if dup else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="?", default="", help="тема для пошуку історії")
    ap.add_argument("--file", default="", help="режим дубль-варта для нового файлу (exit 3 якщо схоже вже є)")
    a = ap.parse_args()
    if not a.query and not a.file:
        ap.print_help()
        sys.exit(0)
    sys.exit(recall(a.query, a.file))


if __name__ == "__main__":
    main()
