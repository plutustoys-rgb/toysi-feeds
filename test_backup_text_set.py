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
leak_copy = (dest / "cowork" / "LEAK.md").read_text(encoding="utf-8") if "cowork/LEAK.md" in files else ""
chk("файл із секретом скопійовано, але секретний РЯДОК замасковано (значення нема в копії)", "cowork/LEAK.md" in files
    and SECRET not in leak_copy and "ПРИХОВАНО БЕКАПОМ" in leak_copy and "колись вставили" in leak_copy)
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

# --- аудит PR #962: скан секретів — реальні форми, що проходили
FAKE_HEX = "0123456789abcdef" * 2 + "0123"          # 36 hex
cases = {
    "hex поруч зі словом «Ключ»": f"Новий правильний ключ (з кабінету ФОП): `{FAKE_HEX}`, діє до 09.07.2027",
    "hex у «Ключ ФОП НП (…)»": f"Ключ ФОП НП ({FAKE_HEX})",
    "placeholder-слово НЕ вимикає скан рядка": "Wait... token=Zq8Lm2Xw9RtY4pKd7A",
    "JSON note example + api_key": '{"note":"example","api_key":"Zq8Lm2Xw9RtY4pKd7A"}',
    "токен Telegram": "бот 123456789:AAH_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R",
    "пароль у URL": "https://user:S3cretPw!x@host.example/path",
    "Basic-авторизація": "Authorization: Basic dXNlcjpwYXNzd29yZDEyMzQ1Ng==",
    "sshpass": "sshpass -p MyRootPw99 ssh root@x",
    "PROM_KEY=": "PROM_KEY=Zq8Lm2Xw9RtY4pKd7A",
    "пароль зі спецсимволами": "password: P@ss!w0rd#2026x",
    "PIN": "CHECKBOX_CASHIER_PIN=1044934402",
    "PIN: слово без префікса": "PIN: 482915",
    "pin= малими": "pin=482915",
    "ПІН кирилицею": "ПІН: 482915",
}
for label, txt in cases.items():
    chk(f"скан ловить: {label}", bool(bk.scan_text(txt)))
for label, txt in {"звичайний текст": "Мерж PR 962 після аудиту, файл recall.py",
                   "плейсхолдер у значенні": "token = <ВАШ_ТОКЕН> і api_key=xxxxxxxxxxxxxxxxxxxx",
                   "git-sha без слова-ключа": "коміт " + "ab" * 20}.items():
    chk(f"скан НЕ ловить: {label}", not bk.scan_text(txt))

sdir = tmp / "scan2"
cw2, ch2, rl2, d2 = sdir / "c", sdir / "h", sdir / "r", sdir / "d"
for d in (cw2, ch2 / "hooks", ch2 / "projects" / "P" / "memory", rl2):
    d.mkdir(parents=True)
(cw2 / "plan.md").write_text(f"Ключ ФОП НП ({FAKE_HEX})\n", encoding="utf-8")
(cw2 / "utf16.md").write_bytes("token=Zq8Lm2Xw9RtY4pKd7A\n".encode("utf-16"))
(cw2 / "КОДВ_CHANNEL.md").write_text("IBAN та фінрішення", encoding="utf-8")
(cw2 / "key.md").write_text("-----BEGIN RSA PRIVATE KEY-----" + chr(10) + "ABC" + chr(10) + "-----END RSA PRIVATE KEY-----" + chr(10), encoding="utf-8")
(cw2 / "ok.md").write_text("нормальний документ\n", encoding="utf-8")
bk.backup(cw2, ch2, rl2, d2, push=False)
files2 = {p.relative_to(d2).as_posix() for p in d2.rglob("*") if p.is_file() and ".git" not in p.parts}
plan_copy = (d2 / "cowork" / "plan.md").read_text(encoding="utf-8") if "cowork/plan.md" in files2 else ""
chk("hex-ключ у plan.md: файл скопійовано, ключ замасковано", "cowork/plan.md" in files2 and FAKE_HEX not in plan_copy and "ПРИХОВАНО" in plan_copy)
u16 = (d2 / "cowork" / "utf16.md").read_text(encoding="utf-8", errors="replace") if "cowork/utf16.md" in files2 else ""
chk("UTF-16 файл: секрет замасковано", "cowork/utf16.md" in files2 and "Zq8Lm2" not in u16 and "ПРИХОВАНО" in u16)
chk("КОДВ_CHANNEL.md (фінанси) не йде в GitHub-копію", "cowork/КОДВ_CHANNEL.md" not in files2)
chk("нормальний файл скопійовано", "cowork/ok.md" in files2)
chk("файл із приватним ключем не копіюється взагалі", "cowork/key.md" not in files2)
rep2 = (d2 / "_report_last.md").read_text(encoding="utf-8")
chk("звіт без значень секретів", FAKE_HEX not in rep2 and "Zq8Lm2" not in rep2)

# --- запобіжник по групах: пам'ять зникла, а Cowork цілий -> СТОП
sdir3 = tmp / "scan3"
cw3, ch3, rl3, d3 = sdir3 / "c", sdir3 / "h", sdir3 / "r", sdir3 / "d"
mem3 = ch3 / "projects" / "P" / "memory"
for d in (cw3, ch3 / "hooks", mem3, rl3):
    d.mkdir(parents=True)
for i in range(40):
    (cw3 / f"doc{i}.md").write_text(f"документ {i}\n", encoding="utf-8")
for i in range(6):
    (mem3 / f"m{i}.md").write_text(f"пам'ять {i}\n", encoding="utf-8")
chk("групи: перший бекап ок", bk.backup(cw3, ch3, rl3, d3, push=False) == 0)
for p in mem3.glob("*.md"):
    p.unlink()                                   # пам'ять зникла (~13% усіх файлів — загальний відсоток цього не бачить)
rc = bk.backup(cw3, ch3, rl3, d3, push=False)
chk("запобіжник по групі: пам'ять зникла -> exit 3", rc == 3)
chk("пам'ять у копії збережена", len(list((d3 / "claude_home" / "projects" / "P" / "memory").glob("*.md"))) == 6)

# --- без remote: не падає
chk("push без remote не падає (лише попередження)", bk.backup(cw, ch, rl, dest, push=True) == 0)

# --- чесність: майданчик усередині домашньої теки без remote — не бекап (прохання Консультанта 22.10)
hdir = tmp / "honest"
cw4, ch4, rl4, d4 = hdir / "c", hdir / "h", hdir / "r", hdir / "home" / "dest"
for d in (cw4, ch4, rl4):
    d.mkdir(parents=True)
(cw4 / "a.md").write_text("звичайний документ\n", encoding="utf-8")
(cw4 / "plan.md").write_text(f"Ключ ФОП НП ({FAKE_HEX})\n", encoding="utf-8")
(cw4 / "code.md").write_text("token_ratio = fuzz.ratio_of_something_long(a, b)\n", encoding="utf-8")
bk.HOME = hdir / "home"          # dest усередині «домашньої теки»
bk.backup(cw4, ch4, rl4, d4, push=False)
rep4 = (d4 / "_report_last.md").read_text(encoding="utf-8")
chk("dest у домашній теці без remote: звіт каже «ЦЕ НЕ БЕКАП, А МАЙДАНЧИК»", "ЦЕ НЕ БЕКАП" in rep4)
chk("звіт розділяє «імовірні секрети» і «ймовірно хибні»", "Імовірні СЕКРЕТИ" in rep4 and "Ймовірно хибні" in rep4
    and rep4.index("plan.md") < rep4.index("code.md"))
chk("hex-ключ у групі СЕКРЕТИ, а ключ=значення з коду — у групі хибних",
    "plan.md" in rep4.split("Ймовірно хибні")[0] and "code.md" in rep4.split("Ймовірно хибні")[1])
subprocess.run(["git", "-C", str(d4), "remote", "add", "origin", str(tmp / "nowhere.git")], capture_output=True)
bk.backup(cw4, ch4, rl4, d4, push=False)
chk("з налаштованим remote попередження зникає", "ЦЕ НЕ БЕКАП" not in (d4 / "_report_last.md").read_text(encoding="utf-8"))
d5 = tmp / "outside" / "dest"
bk.HOME = hdir / "otherhome"
bk.backup(cw4, ch4, rl4, d5, push=False)
chk("dest поза домашньою текою: попередження немає", "ЦЕ НЕ БЕКАП" not in (d5 / "_report_last.md").read_text(encoding="utf-8"))

sys.exit(1 if F else 0)
