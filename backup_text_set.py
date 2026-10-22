#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backup_text_set.py — резервна копія КРИТИЧНОГО ТЕКСТОВОГО набору, що не лежить у git (22.10.2026, аудит
архітектури після інциденту run_all_tests: Cowork-папка, ~/.claude — хуки, settings, пам'ять — НЕ під git).

Що копіюється (білий список, лише текстові розширення, файл ≤ MAX_FILE_MB):
  • Cowork: файли кореня (канали, журнали, плани), технічні_вимоги_маркетплейсів/, SEO_метрики/;
  • ~/.claude: settings.json, CLAUDE.md, hooks/, projects/*/memory/;
  • rozetka_agent: .vps_audit_ok (маркер аудитів VPS-скриптів).
  • скіли ролей Claude (%APPDATA%/Claude/local-agent-mode-sessions/skills-plugin/*/*/skills): лише *.md і *.txt (SKILL.md та довідки; скрипти/схеми вбудованих скілів — ні) + manifest.json;
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
NEVER_NAMES = re.compile(r"(?i)^(КОДВ_.*|\.local_secrets.*|\.env.*|.*\.pem|.*\.key|.*\.p12|.*\.pfx|credentials.*|.*secrets?\.json|id_rsa.*|id_ed25519.*)$")
COWORK_DIRS = ("технічні_вимоги_маркетплейсів", "SEO_метрики")
# відносні шляхи, які свідомо дозволено попри збіг. .vps_audit_ok — лише «sha256  _vps_*.sh», назви на кшталт *_key.sh дають хибний збіг
ALLOW_SECRET_LIKE: set = {"repo_local/.vps_audit_ok"}
MASK_LINE = "[РЯДОК ПРИХОВАНО БЕКАПОМ: підозра на секрет]"
DELETE_GUARD_RATIO = 0.30

_TRIVIAL_VALUES = {"none", "null", "true", "false", "string", "os.environ", "environ"}
_PLACEHOLDER = re.compile(r"(?i)(<[^>]+>|\.\.\.|x{4,}|your[_-]|example|placeholder|changeme|\*{3,}|\[приховано\]|redacted)")
SECRET_RULES = [
    ("приватний ключ", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("префікс токена", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_\-]{20,}|"
                                  r"xox[abprs]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{35}|ya29\.[0-9A-Za-z_\-]{20,})")),
    ("Bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{24,}")),
    ("Basic-авторизація", re.compile(r"(?i)\bauthorization\W{1,4}basic\s+[A-Za-z0-9+/=]{12,}")),
    ("токен Telegram", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}\b")),
    ("пароль у URL", re.compile(r"://[^/\s:@]+:[^@\s/]{3,}@")),
    ("sshpass", re.compile(r"(?i)\bsshpass\s+-p\s*\S+")),
    # ключ=значення: слова-ключі ширші (…key, …pin, пароль), значення без пробілів ≥12 (≥16 для звичайних слів);
    # паролі зі спецсимволами теж ловимо
    ("ключ=значення", re.compile(r"""(?ix)["']?[\w.-]*(?:password|passwd|пароль|secret|token|api[_-]?key|private[_-]?key|access[_-]?key|client[_-]?secret|_key|_pin|\bpin|\bkey)[\w.-]*["']?\s*[=:]\s*["'`]?([^\s"'`,;<>]{12,})""")),
    ("PIN", re.compile(r"""(?i)(?:_pin|(?<![A-Za-z])pin|(?<!\w)пін)[\w.-]*["']?\s*[=:]\s*["'`]?\d{4,}""")),
    # голий довгий hex (>=32) у рядку зі словом «ключ/key/токен/пароль/secret/PIN» — напр. «Ключ ФОП НП: `<hex32>`»
    ("hex-секрет поруч зі словом-ключем", re.compile(r"(?i)^(?=.*(?:ключ|\bkey\b|токен|token|парол|password|secret|секрет|\bpin\b)).*\b[0-9a-f]{32,}\b")),
]


def scan_text(text: str) -> list:
    """Повертає [(номер рядка, тип)] підозрілих місць (значення НЕ повертаємо й не друкуємо)."""
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        for kind, rx in SECRET_RULES:
            m = rx.search(line)
            if not m:
                continue
            # плейсхолдер перевіряємо ЛИШЕ на самому знайденому значенні, не на всьому рядку (аудит PR #962:
            # «Wait... token=<реальне>» раніше проходило), і лише для рівня «ключ=значення»
            val = m.group(1) if m.groups() else m.group(0)
            if kind == "ключ=значення" and (_PLACEHOLDER.search(val) or val.lower() in _TRIVIAL_VALUES):
                continue
            hits.append((n, kind))
            break
    return hits


SKILL_EXT = {".md", ".txt"}


def discover_skills_dirs() -> list:
    """Теки `skills` усіх встановлених наборів скілів Claude (опис ролей лежить ПОЗА git і ПОЗА ~/.claude)."""
    base = Path(os.environ.get("APPDATA") or (HOME / "AppData" / "Roaming")) / "Claude" / "local-agent-mode-sessions" / "skills-plugin"
    return sorted(p for p in base.glob("*/*/skills") if p.is_dir()) if base.is_dir() else []


def collect(cowork: Path, claude_home: Path, repo_local: Path, skills_dirs=()) -> list:
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
    for sk in skills_dirs:
        # префікс = перші 8 символів двох id-тек набору (шлях <набір>/<сесія>/skills), щоб різні набори не злились
        tag = "_".join(x[:8] for x in (sk.parent.parent.name, sk.parent.name))
        for p in sorted(sk.rglob("*")):
            if p.is_file() and p.suffix.lower() in SKILL_EXT:
                add_file(p, f"claude_skills/{tag}/{p.relative_to(sk).as_posix()}")
        manifest = sk.parent / "manifest.json"
        if manifest.is_file():
            add_file(manifest, f"claude_skills/{tag}/manifest.json")
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
        data = src.read_bytes()
    except OSError as e:
        return False, f"не прочитати ({e.__class__.__name__})", []
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):     # UTF-16 (PowerShell 5.1 `>`) — інакше скан його не бачив би
        text = data.decode("utf-16", errors="replace")
    else:
        text = data.decode("utf-8", errors="replace")
    hits = scan_text(text)
    if hits and rel not in ALLOW_SECRET_LIKE:
        if any(k == "приватний ключ" for _, k in hits):
            return False, "приватний ключ у файлі", hits            # багаторядковий блок — файл не копіюємо взагалі
        # Підозрілі РЯДКИ маскуємо, решту документа копіюємо (важливі великі документи, напр. план, не випадають із
        # копії через один рядок). Нумерація — та сама splitlines, що в scan_text.
        bad = {n for n, _ in hits}
        masked = "".join((MASK_LINE + ("\n" if ln.endswith(("\n", "\r")) else "")) if i in bad else ln
                         for i, ln in enumerate(text.splitlines(keepends=True), 1))
        return True, "masked", (masked.encode("utf-8"), hits)
    return True, "", (data, [])   # саме ті байти, що пройшли скан, і копіюємо — файл не читається вдруге


GUARD_MIN_FILES = 5   # групи менші за це не мають сенсу міряти відсотком


def _group(rel: str) -> str:
    """Група для запобіжника: cowork | claude_home/projects | claude_home/hooks | claude_home | repo_local."""
    parts = rel.split("/")
    if parts[0] == "claude_home" and len(parts) > 2 and parts[1] in ("projects", "hooks"):
        return f"claude_home/{parts[1]}"
    return parts[0]


WEAK_KIND = "ключ=значення"


def _under_home(p: Path) -> bool:
    try:
        return p.resolve().is_relative_to(HOME.resolve())
    except (OSError, ValueError):
        return False


def _remote_configured(dest: Path) -> bool:
    if not (dest / ".git").exists():
        return False
    try:
        return subprocess.run(["git", "-C", str(dest), "remote", "get-url", "origin"], capture_output=True, timeout=30).returncode == 0
    except Exception:
        return False


def run_git(dest: Path, *args):
    r = subprocess.run(["git", "-C", str(dest), *args], capture_output=True, timeout=300)
    return r.returncode, r.stdout.decode("utf-8", "replace").strip(), r.stderr.decode("utf-8", "replace").strip()


def backup(cowork: Path, claude_home: Path, repo_local: Path, dest: Path, dry_run=False, push=True, skills_dirs=()) -> int:
    items = collect(cowork, claude_home, repo_local, skills_dirs)
    keep, skipped = [], []
    for src, rel in items:
        ok, why, extra = classify(src, rel)
        if ok:
            keep.append((src, rel, why, extra))      # extra = (байти для запису, [(рядок, тип)] замаскованих)
        else:
            skipped.append((src, rel, why, extra))   # extra = [(рядок, тип)]
    keep_rel = {rel for _, rel, _, _ in keep}

    # запобіжник від масового видалення — ПО ГРУПАХ (аудит PR #962: пам'ять/hooks складають лише ~12% файлів,
    # тож загальний відсоток не помітив би, що вони зникли знову)
    existing = []
    if dest.is_dir():
        for top in ("cowork", "claude_home", "repo_local", "claude_skills"):
            base = dest / top
            if base.is_dir():
                existing += [p.relative_to(dest).as_posix() for p in base.rglob("*") if p.is_file()]
    to_delete = [r for r in existing if r not in keep_rel]
    if existing and len(to_delete) > DELETE_GUARD_RATIO * len(existing):      # загальний рівень
        print(f"[СТОП] синхронізація видалила б {len(to_delete)} із {len(existing)} файлів копії (>"
              f"{int(DELETE_GUARD_RATIO * 100)}%). Джерела порожні/недоступні? Нічого не змінено.")
        return 3
    for group in sorted({_group(r) for r in existing}):
        g_exist = [r for r in existing if _group(r) == group]
        g_del = [r for r in to_delete if _group(r) == group]
        if len(g_exist) >= GUARD_MIN_FILES and len(g_del) > DELETE_GUARD_RATIO * len(g_exist):
            print(f"[СТОП] група «{group}»: синхронізація видалила б {len(g_del)} із {len(g_exist)} файлів копії "
                  f"(>{int(DELETE_GUARD_RATIO * 100)}%). Джерело порожнє/недоступне? Нічого не змінено.")
            return 3

    lines = ["# Звіт останнього бекапу (час — у git-коміті; без мітки часу, щоб без змін не було порожніх комітів)",
             f"Скопійовано: {len(keep)}; пропущено: {len(skipped)}; видалено з копії (зникли в джерелі): {len(to_delete)}", ""]
    if _under_home(dest) and not _remote_configured(dest):
        lines.insert(1, f"⚠️ **ЦЕ НЕ БЕКАП, А МАЙДАНЧИК.** Копія лежить у {dest} — усередині домашньої теки ({HOME}), яку вже стирав "
                        f"інцидент 21.10 (shutil.rmtree(USERPROFILE)), і приватний remote не налаштований. Захистом стає push у приватне "
                        f"репо АБО копія поза цим диском (знімок на VPS: deploy/cowork_snapshot_to_vps.cmd включає цю теку).")
    # Групи за ймовірністю: «ключ=значення» майже завжди код/URL (хибні спрацювання), решта — справжні кандидати в секрети.
    masked_files = [(rel, mh) for _, rel, why, (_, mh) in keep if mh]
    strong = [(rel, [h for h in mh if h[1] != WEAK_KIND]) for rel, mh in masked_files]
    strong = [(rel, mh) for rel, mh in strong if mh]
    weak = [(rel, [h for h in mh if h[1] == WEAK_KIND]) for rel, mh in masked_files]
    weak = [(rel, mh) for rel, mh in weak if mh]
    if strong:
        lines.append("## 🔴 Імовірні СЕКРЕТИ (hex-ключ/токен/пароль/PIN) — замасковано в копії; прибрати з джерела й, можливо, ротувати")
        for rel, mh in strong:
            lines.append(f"- `{rel}` — " + ", ".join(f"рядок {n}: {k}" for n, k in mh[:8]))
        lines.append("")
    if weak:
        lines.append("## 🟡 Низька ймовірність (ключ=значення: переважно код/URL, але може бути й пароль) — замасковано, перевірити вибірково")
        for rel, mh in weak:
            lines.append(f"- `{rel}` — " + ", ".join(f"рядок {n}" for n, k in mh[:8]))
        lines.append("")
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
    failed = []
    for src, rel, _, (data, _mh) in keep:
        target = dest / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)                 # саме ті байти, що пройшли скан
        except OSError as e:
            failed.append((rel, e.__class__.__name__))
    if failed:                                       # зайнятий/зниклий файл не валить усе: коміт робимо, код виходу 1
        report += "\n## Не вдалось записати\n" + "\n".join(f"- `{r}` — {w}" for r, w in failed) + "\n"
        print(f"[увага] не вдалось записати {len(failed)} файлів: " + ", ".join(r for r, _ in failed[:5]))
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
            if _under_home(dest):
                print("[УВАГА] Це НЕ бекап, а майданчик: копія в домашній теці, яку вже стирав інцидент 21.10. "
                      "Захист — push у приватне репо або знімок на VPS (deploy/cowork_snapshot_to_vps.cmd).")
        else:
            rc, out, err = run_git(dest, "push", "-u", "origin", "main")
            print("[ok] push виконано" if rc == 0 else f"[ПОМИЛКА] push: {err[:300]}")
            if rc != 0:
                return 4
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="лише звіт, нічого не копіювати й не комітити")
    ap.add_argument("--no-push", action="store_true", help="не пушити, навіть якщо remote налаштовано")
    ap.add_argument("--cowork", default=str(DEFAULT_COWORK))
    ap.add_argument("--claude-home", default=str(HOME / ".claude"))
    ap.add_argument("--repo-local", default=str(DEFAULT_REPO))
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--skills-dir", action="append", default=None, help="тека skills (можна кілька); за замовчуванням — автопошук у %APPDATA%/Claude")
    a = ap.parse_args()
    skills = [Path(x) for x in a.skills_dir] if a.skills_dir else discover_skills_dirs()
    return backup(Path(a.cowork), Path(a.claude_home), Path(a.repo_local), Path(a.dest), a.dry_run, not a.no_push, skills)


if __name__ == "__main__":
    sys.exit(main())
