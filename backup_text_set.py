#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backup_text_set.py — резервна копія КРИТИЧНОГО ТЕКСТОВОГО набору, що не лежить у git (22.10.2026, аудит
архітектури після інциденту run_all_tests: Cowork-папка, ~/.claude — хуки, settings, пам'ять — НЕ під git).

Що копіюється (білий список, лише текстові розширення, файл ≤ MAX_FILE_MB):
  • Cowork: файли кореня (канали, журнали, плани), технічні_вимоги_маркетплейсів/, SEO_метрики/;
  • ~/.claude: settings.json, CLAUDE.md, hooks/, projects/*/memory/;
  • rozetka_agent: .vps_audit_ok (маркер аудитів VPS-скриптів).
НЕ копіюється ніколи: документи_КОДВ і КОДВ_журнал* (фінанси/податкова первинка — рішення Консультанта: не в GitHub, їм місце на VPS), mascot_new та будь-які
медіа/бінарні файли (їм місце на VPS), .env/ключі/сертифікати, .local_secrets (живі сесії кабінетів).

БЕЗПЕКА:
  1) СКАН НА СЕКРЕТИ перед копіюванням: файл із підозрілим вмістом НЕ копіюється, а потрапляє у звіт `_report_last.md`
     (шлях:рядок:тип — БЕЗ значення). Власник/Код розглядає й або чистить файл, або додає в ALLOW_SECRET_LIKE.
  2) Дзеркало з ЗАПОБІЖНИКОМ: якщо б синхронізація видалила >30% наявних файлів копії (повторення інциденту: джерело
     раптом порожнє) — видалення НЕ виконується, exit 3, копія й історія лишаються цілі.
  3) Локальний git у DEST (історія = відкат на будь-який день). `git push` лише коли налаштований remote origin
     (приватне репо — рішення і створення за власником); збій push → exit 4, але локальний коміт є.

Запуск:  python backup_text_set.py [--dry-run] [--no-push]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOME = Path(os.environ.get("USERPROFILE") or Path.home())
DEFAULT_COWORK = Path(os.environ.get("PLUTUS_COWORK_DIR") or r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya")
DEFAULT_REPO = Path(__file__).resolve().parent
DEFAULT_DEST = Path(os.environ.get("PLUTUS_BACKUP_REPO") or r"C:\Users\smach\plutus_backup_repo")

MAX_FILE_MB = 20
TEXT_EXT = {".md", ".txt", ".json", ".jsonl", ".csv", ".tsv", ".py", ".ps1", ".sh", ".bat", ".cmd", ".yml", ".yaml",
            ".xml", ".html", ".htm", ".js", ".cfg", ".ini", ".toml", ".sql", ".log", ""}
NEVER_NAMES = re.compile(r"(?i)^(КОДВ_журнал.*|\.env.*|.*\.pem|.*\.key|.*\.p12|.*\.pfx|credentials.*|.*secrets?\.json|id_rsa.*|id_ed25519.*)$")
COWORK_DIRS = ("технічні_вимоги_маркетплейсів", "SEO_метрики")
ALLOW_SECRET_LIKE: set = set()   # відносні шляхи, які власник свідомо дозволив попри збіг (наприклад, документація з прикладом)
DELETE_GUARD_RATIO = 0.30

_PLACEHOLDER = re.compile(r"(?i)(<[^>]+>|\.\.\.|x{4,}|your[_-]|example|placeholder|changeme|\*{3,}|\[приховано\]|redacted)")
SECRET_RULES = [
    ("приватний ключ", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("префікс токена", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_\-]{20,}|"
                                  r"xox[abprs]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{35}|ya29\.[0-9A-Za-z_\-]{20,})")),
    ("Bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{24,}")),
    ("ключ=значення", re.compile(r"""(?ix)["']?[\w.-]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|access[_-]?key|client[_-]?secret)[\w.-]*["']?\s*[=:]\s*["']?([A-Za-z0-9_\-/+=.]{16,})""")),
]


def scan_text(text: str) -> list:
    """Повертає [(номер рядка, тип)] підозрілих місць (значення НЕ повертаємо й не друкуємо)."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for kind, rx in SECRET_RULES:
            m = rx.search(line)
            if not m:
                continue
            frag = m.group(0)
            if _PLACEHOLDER.search(frag) or _PLACEHOLDER.search(line[:300]) and kind == "ключ=значення":
                continue
            hits.append((n, kind))
            break
    return hits


def collect(cowork: Path, claude_home: Path, repo_local: Path) -> list:
    """[(джерело Path, відносний шлях у копії)] за білим списком."""
    out = []

    def add_file(p: Path, rel: str):
        out.append((p, rel.replace("\\", "/")))

    if cowork.is_dir():
        for p in sorted(cowork.iterdir()):
            if p.is_file():
                add_file(p, f"cowork/{p.name}")
        for d in COWORK_DIRS:
            base = cowork / d
            if base.is_dir():
                for p in sorted(base.rglob("*")):
                    if p.is_file():
                        add_file(p, f"cowork/{d}/{p.relative_to(base).as_posix()}")
    for name in ("settings.json", "CLAUDE.md"):
        p = claude_home / name
        if p.is_file():
            add_file(p, f"claude_home/{name}")
    hooks = claude_home / "hooks"
    if hooks.is_dir():
        for p in sorted(hooks.rglob("*")):
            if p.is_file():
                add_file(p, f"claude_home/hooks/{p.relative_to(hooks).as_posix()}")
    projects = claude_home / "projects"
    if projects.is_dir():
        for mem in sorted(projects.glob("*/memory")):
            for p in sorted(mem.rglob("*")):
                if p.is_file():
                    add_file(p, f"claude_home/projects/{mem.parent.name}/memory/{p.relative_to(mem).as_posix()}")
    marker = repo_local / ".vps_audit_ok"
    if marker.is_file():
        add_file(marker, "repo_local/.vps_audit_ok")
    return out


def classify(src: Path, rel: str):
    """Повертає (ok, причина_відмови, [hits])."""
    if NEVER_NAMES.match(src.name):
        return False, "ім'я файлу з ключами/секретами", []
    if src.suffix.lower() not in TEXT_EXT:
        return False, "не текстовий формат (бінарні/медіа — на VPS)", []
    try:
        size = src.stat().st_size
    except OSError as e:
        return False, f"не прочитати ({e.__class__.__name__})", []
    if size > MAX_FILE_MB * 1024 * 1024:
        return False, f"більше {MAX_FILE_MB} МБ", []
    try:
        text = src.read_bytes().decode("utf-8", errors="replace")
    except OSError as e:
        return False, f"не прочитати ({e.__class__.__name__})", []
    hits = scan_text(text)
    if hits and rel not in ALLOW_SECRET_LIKE:
        return False, "підозра на секрет", hits
    return True, "", []


def run_git(dest: Path, *args):
    r = subprocess.run(["git", "-C", str(dest), *args], capture_output=True, timeout=300)
    return r.returncode, r.stdout.decode("utf-8", "replace").strip(), r.stderr.decode("utf-8", "replace").strip()


def backup(cowork: Path, claude_home: Path, repo_local: Path, dest: Path, dry_run=False, push=True) -> int:
    items = collect(cowork, claude_home, repo_local)
    keep, skipped = [], []
    for src, rel in items:
        ok, why, hits = classify(src, rel)
        (keep if ok else skipped).append((src, rel, why, hits))
    keep_rel = {rel for _, rel, _, _ in keep}

    # запобіжник від масового видалення
    existing = []
    if dest.is_dir():
        for top in ("cowork", "claude_home", "repo_local"):
            base = dest / top
            if base.is_dir():
                existing += [p.relative_to(dest).as_posix() for p in base.rglob("*") if p.is_file()]
    to_delete = [r for r in existing if r not in keep_rel]
    if existing and len(to_delete) > DELETE_GUARD_RATIO * len(existing):
        print(f"[СТОП] синхронізація видалила б {len(to_delete)} із {len(existing)} файлів копії (>"
              f"{int(DELETE_GUARD_RATIO * 100)}%). Джерела порожні/недоступні? Нічого не змінено.")
        return 3

    lines = ["# Звіт останнього бекапу (час — у git-коміті; без мітки часу, щоб без змін не було порожніх комітів)",
             f"Скопійовано: {len(keep)}; пропущено: {len(skipped)}; видалено з копії (зникли в джерелі): {len(to_delete)}", ""]
    if skipped:
        lines.append("## Пропущено (НЕ в копії)")
        for src, rel, why, hits in skipped:
            extra = "; " + ", ".join(f"рядок {n}: {k}" for n, k in hits[:5]) if hits else ""
            lines.append(f"- `{rel}` — {why}{extra}")
    report = "\n".join(lines) + "\n"
    print(report)
    if dry_run:
        print("[dry-run] нічого не записано")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        run_git(dest, "init", "-b", "main")
        run_git(dest, "config", "user.name", "PlutusToys backup")
        run_git(dest, "config", "user.email", "backup@plutustoys.invalid")
    for src, rel, _, _ in keep:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    for rel in to_delete:
        try:
            (dest / rel).unlink()
        except OSError:
            pass
    (dest / "_report_last.md").write_text(report, encoding="utf-8")
    run_git(dest, "add", "-A")
    rc, _, _ = run_git(dest, "diff", "--cached", "--quiet")
    if rc == 0:
        print("[ok] змін нема — коміт не потрібен")
    else:
        rc, out, err = run_git(dest, "commit", "-m", f"backup {datetime.now():%Y-%m-%d %H:%M}")
        print("[ok] коміт" if rc == 0 else f"[ПОМИЛКА] коміт: {err}")
        if rc != 0:
            return 1
    if push:
        rc, url, _ = run_git(dest, "remote", "get-url", "origin")
        if rc != 0:
            print("[увага] remote origin не налаштований — лише локальний коміт (приватне репо створює власник)")
        else:
            rc, out, err = run_git(dest, "push", "-u", "origin", "main")
            print("[ok] push виконано" if rc == 0 else f"[ПОМИЛКА] push: {err[:300]}")
            if rc != 0:
                return 4
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="лише звіт, нічого не копіювати й не комітити")
    ap.add_argument("--no-push", action="store_true", help="не пушити, навіть якщо remote налаштовано")
    ap.add_argument("--cowork", default=str(DEFAULT_COWORK))
    ap.add_argument("--claude-home", default=str(HOME / ".claude"))
    ap.add_argument("--repo-local", default=str(DEFAULT_REPO))
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    a = ap.parse_args()
    return backup(Path(a.cowork), Path(a.claude_home), Path(a.repo_local), Path(a.dest), a.dry_run, not a.no_push)


if __name__ == "__main__":
    sys.exit(main())
