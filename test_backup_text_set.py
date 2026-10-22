#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_backup_text_set.py — регрес backup_text_set.py (резервна копія критичного текстового набору).

ІНВАРІАНТИ (рішення Консультанта/власника 22.10):
  - документи_КОДВ, медіа/бінарні, .env/ключі НЕ потрапляють у копію;
  - файл із підозрою на секрет НЕ копіюється, у звіті є шлях:рядок:тип, але НЕ значення секрету;
  - плейсхолдери/приклади (<TOKEN>, xxxx) не блокують нормальні документи;
  - повторний запуск без змін не створює коміт; зміна → новий коміт (історія = відкат);
  - запобіжник: джерела раптом порожні (>30% видалень) → exit 3, копія й історія ціли;
  - --dry-run нічого не пише. Усе у тимчасових теках, без мережі й push.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import backup_text_set as bk

F = []


def chk(name, cond):
    print(f"[{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        F.append(name)


tmp = Path(tempfile.mkdtemp())
cw, ch, rl, dest = tmp / "cowork", tmp / "claude", tmp / "repo", tmp / "dest"
for d in (cw, ch / "hooks", ch / "projects" / "P" / "memory", rl, cw / "SEO_метрики", cw / "документи_КОДВ", cw / "mascot_new"):
    d.mkdir(parents=True, exist_ok=True)
SECRET = "ghp_" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8Qr9St0"
(cw / "OWNER_INBOX.md").write_text("## [SEO] 2026-10-22 — нормальний документ\nприклад: token = <ВАШ_ТОКЕН> і api_key=xxxxxxxxxxxxxxxxxxxx\n", encoding="utf-8")
(cw / "LEAK.md").write_text(f"колись вставили\nGITHUB_TOKEN={SECRET}\n", encoding="utf-8")
(cw / "SEO_метрики" / "m.csv").write_text("a,b\n1,2\n", encoding="utf-8")
(cw / "документи_КОДВ" / "pi.md").write_text("податкова первинка", encoding="utf-8")
(cw / "mascot_new" / "x.md").write_text("не в наборі", encoding="utf-8")
(cw / "video.mp4").write_bytes(b"\x00\x01")
(cw / "КОДВ_журнал.md").write_text("фінансовий журнал", encoding="utf-8")
(cw / "КОДВ_журнал_backup_x.md").write_text("копія журналу", encoding="utf-8")
(cw / ".env").write_text("A=1", encoding="utf-8")
(ch / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")
(ch / "hooks" / "merge-guard.sh").write_text("#!/bin/bash\n", encoding="utf-8")
(ch / "projects" / "P" / "memory" / "MEMORY.md").write_text("- індекс\n", encoding="utf-8")
(rl / ".vps_audit_ok").write_text("a" * 64 + "  _vps_x.sh\n", encoding="utf-8")

rc = bk.backup(cw, ch, rl, dest, push=False)
files = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file() and ".git" not in p.parts}
chk("перший бекап: код 0", rc == 0)
chk("нормальні файли скопійовано (Cowork, SEO_метрики, settings, hooks, memory, маркер)",
    {"cowork/OWNER_INBOX.md", "cowork/SEO_метрики/m.csv", "claude_home/settings.json", "claude_home/hooks/merge-guard.sh",
     "claude_home/projects/P/memory/MEMORY.md", "repo_local/.vps_audit_ok"} <= files)
chk("документи_КОДВ / mascot_new / mp4 / .env НЕ скопійовано",
    not any(x in f for f in files for x in ("документи_КОДВ", "КОДВ_журнал", "mascot_new", ".mp4", "cowork/.env")))
chk("файл із секретом НЕ скопійовано", "cowork/LEAK.md" not in files)
report = (dest / "_report_last.md").read_text(encoding="utf-8")
chk("звіт називає LEAK.md і рядок, але НЕ значення секрету", "LEAK.md" in report and "рядок 2" in report and SECRET not in report
    and "Ab1Cd2" not in report)
chk("плейсхолдери не блокують нормальний документ", "cowork/OWNER_INBOX.md" in files)
git_log = subprocess.run(["git", "-C", str(dest), "log", "--oneline"], capture_output=True).stdout.decode().strip().splitlines()
chk("є рівно один коміт", len(git_log) == 1)

# --- повторний запуск без змін
bk.backup(cw, ch, rl, dest, push=False)
chk("без змін — коміт не створено",
    len(subprocess.run(["git", "-C", str(dest), "log", "--oneline"], capture_output=True).stdout.decode().strip().splitlines()) == 1)

# --- зміна → новий коміт
(cw / "OWNER_INBOX.md").write_text("## [SEO] 2026-10-23 — змінено\n", encoding="utf-8")
bk.backup(cw, ch, rl, dest, push=False)
n = len(subprocess.run(["git", "-C", str(dest), "log", "--oneline"], capture_output=True).stdout.decode().strip().splitlines())
chk("зміна джерела → новий коміт", n >= 2)
old = subprocess.run(["git", "-C", str(dest), "show", "HEAD~1:cowork/OWNER_INBOX.md"], capture_output=True).stdout.decode("utf-8", "replace")
chk("історія зберігає попередню версію (відкат можливий)", "нормальний документ" in old)

# --- dry-run
dest2 = tmp / "dest2"
rc = bk.backup(cw, ch, rl, dest2, dry_run=True, push=False)
chk("dry-run: нічого не створено", rc == 0 and not dest2.exists())

# --- запобіжник: джерела порожні
empty_cw, empty_ch, empty_rl = tmp / "e1", tmp / "e2", tmp / "e3"
for d in (empty_cw, empty_ch, empty_rl):
    d.mkdir()
before = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file() and ".git" not in p.parts}
rc = bk.backup(empty_cw, empty_ch, empty_rl, dest, push=False)
after = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file() and ".git" not in p.parts}
chk("запобіжник: порожні джерела -> exit 3, копія цілa", rc == 3 and before == after)

# --- без remote: не падає
chk("push без remote не падає (лише попередження)", bk.backup(cw, ch, rl, dest, push=True) == 0)

sys.exit(1 if F else 0)
