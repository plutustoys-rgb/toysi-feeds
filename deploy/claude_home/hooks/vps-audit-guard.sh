#!/usr/bin/env bash
# PreToolUse(Bash|PowerShell) guard — ВІДНОВЛЕНО 22.10.2026 за описом із archive/consultant/2026-10_archive.md:936-941
# (запис Коду 22.09.2026) та живим форматом маркера .vps_audit_ok. Оригінал втрачено в інциденті run_all_tests.
# Поведінка: будь-яка команда, що ШЛЕ скрипт `_vps_*.sh` на VPS (ssh/scp/sftp), блокується, доки sha256 САМЕ ЦІЄЇ
# версії файлу не записаний у <repo>/.vps_audit_ok (рядки «<sha256>  <імя_файлу>», формат sha256sum; зірочка перед
# іменем допустима). Змінив файл після аудиту -> хеш інший -> знову блок. Файл не знайдено -> блок (не можемо звірити).
# Команди, що лише згадують файл (git add, cat, ls, grep), НЕ блокуються: потрібні і ssh/scp/sftp, і `_vps_*.sh`.
# Збій самого хука (нема python тощо) -> ПРОПУСКАЄМО команду з рядком-попередженням у stderr, не лишаємо тишу.
# Змінні для тестів: VPS_GUARD_REPO (каталог репо з .vps_audit_ok; типово C:/Users/smach/rozetka_agent).
in=$(cat)
case "$in" in
  *_vps_*) : ;;          # швидкий вихід: у вводі нема жодного `_vps_`
  *) exit 0 ;;
esac
HOOK_IN="$in" python - <<'PY'
import hashlib, json, os, re, sys

REPO = os.environ.get("VPS_GUARD_REPO") or "C:/Users/smach/rozetka_agent"
try:
    payload = json.loads(os.environ.get("HOOK_IN", "") or "{}")
except Exception as e:  # noqa: BLE001
    print(f"vps-audit-guard: ввід хука не розібрано ({type(e).__name__}) — команду пропущено без перевірки", file=sys.stderr)
    sys.exit(0)

cmd = ((payload.get("tool_input") or {}).get("command")) or ""
if not re.search(r"(?<![\w.-])(ssh|scp|sftp)(\.exe)?(?=\s)", cmd, re.I):
    sys.exit(0)  # нічого не шлемо на сервер
names = sorted(set(re.findall(r"(?<![\w.-])(_vps_[\w.-]*\.sh)\b", cmd)))
if not names:
    sys.exit(0)

cwd = payload.get("cwd") or ""
marker = {}
try:
    with open(os.path.join(REPO, ".vps_audit_ok"), encoding="utf-8", errors="replace") as f:
        for ln in f:
            parts = ln.strip().split(None, 1)
            if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                marker.setdefault(parts[1].lstrip("*").strip(), set()).add(parts[0].lower())
except OSError:
    pass


def find(name):
    for base in (cwd, REPO):
        if base and os.path.isfile(os.path.join(base, name)):
            return os.path.join(base, name)
    m = re.search(r"([^\s\"']*" + re.escape(name) + r")", cmd)   # шлях, якщо вказано в команді
    if m and os.path.isfile(m.group(1)):
        return m.group(1)
    return None


problems = []
for name in names:
    path = find(name)
    if not path:
        problems.append(f"{name}: файл не знайдено (cwd/репо/шлях у команді) — нема що звірити")
        continue
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if digest not in marker.get(name, set()):
        problems.append(f"{name}: sha256 {digest[:12]}… НЕ записаний у .vps_audit_ok (змінений після аудиту або аудиту не було)")

if problems:
    reason = ("VPS-СКРИПТ БЕЗ АУДИТУ ЦІЄЇ ВЕРСІЇ: " + "; ".join(problems) +
              ". Правило (CLAUDE.md): скрипти на VPS теж проходять незалежний аудит ДО передачі. Після ЧИСТОГО аудиту "
              "допиши в rozetka_agent/.vps_audit_ok рядок `<sha256>  <імя_файлу>` (sha256sum файлу) і повтори.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}, ensure_ascii=False))
sys.exit(0)
PY
exit 0
