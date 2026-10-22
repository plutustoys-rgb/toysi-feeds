#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_hooks.py — ставить хуки відновлення ролі та vps-audit-guard у ~/.claude (запускає ВЛАСНИК, не агент).

  python deploy\\claude_home\\install_hooks.py --dry-run   # показати підсумковий settings.json, НІЧОГО не писати
  python deploy\\claude_home\\install_hooks.py             # встановити
  python deploy\\claude_home\\install_hooks.py --rollback  # повернути settings.json з останнього бекапу
  python deploy\\claude_home\\install_hooks.py --restore-automode [--dry-run]
                                                           # + відновити блок autoMode з транскрипта 9d996807

settings.json глобальний: зламаний хук валить УСІ сесії всіх ролей, тому:
  • перед будь-яким записом settings.json копіюється в теку бекапів ПОЗА ~/.claude (типово
    Cowork\\backups_claude_settings; перевизначення PLUTUS_BACKUP_DIR), шлях бекапу друкується; не вдалось
    зробити бекап -> нічого не пишемо;
  • ідемпотентність: хук уже є, якщо ТОЧНА команда збігається; settings.json не переписується, коли додавати нічого;
  • settings.json, що не розбирається, -> зрозуміла помилка і нічого не записано (ні скриптів, ні CLAUDE.md);
  • чужі налаштування (theme, permissions, autoMode, env, інші хуки) не чіпаються.
Додається: SessionStart(startup|resume|compact|clear), PreCompact, PreToolUse(Bash|PowerShell) -> vps-audit-guard.
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
HOME_CLAUDE = Path.home() / ".claude"
HOOKS_DIR = HOME_CLAUDE / "hooks"
SETTINGS = HOME_CLAUDE / "settings.json"
SCRIPTS = ("session-start.sh", "pre-compact.sh", "vps-audit-guard.sh")
BACKUP_DIR = Path(os.environ.get("PLUTUS_BACKUP_DIR") or
                  r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\backups_claude_settings")

# Блок autoMode (24 пункти `environment`) втрачено в інциденті 21.10; єдина копія — результат читання settings.json у
# транскрипті сесії 9d996807 (рішення власника 22.10: «відновлюй»). У публічне репо ТІЛО блоку НЕ кладемо — установник
# витягує його з транскрипта сам (лише з --restore-automode) і НІКОЛИ не перезаписує вже наявний autoMode.
AUTO_MODE_TRANSCRIPT = Path(os.environ.get("PLUTUS_AUTOMODE_TRANSCRIPT") or
                            (HOME_CLAUDE / "projects" / "C--Users-smach" / "9d996807-3983-4067-861a-614edbd6b2d0.jsonl"))

# (подія, matcher, скрипт, таймаут)
WANTED = (
    ("SessionStart", "startup|resume|compact|clear", "session-start.sh", 20),
    ("PreCompact", "", "pre-compact.sh", 20),
    ("PreToolUse", "Bash", "vps-audit-guard.sh", 15),
    ("PreToolUse", "PowerShell", "vps-audit-guard.sh", 15),
)


def command_for(script: str) -> str:
    return f"bash {(HOOKS_DIR / script).as_posix()}"


def load_settings():
    if not SETTINGS.exists():
        return {}
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        print(f"[ПОМИЛКА] {SETTINGS} не розбирається як JSON ({e}). Нічого не записано. "
              f"Виправте файл або поверніть з бекапу ({BACKUP_DIR}).")
        sys.exit(2)
    if not isinstance(data, dict):
        print(f"[ПОМИЛКА] {SETTINGS}: очікувався JSON-об'єкт. Нічого не записано.")
        sys.exit(2)
    return data


def has_hook(hooks_cfg: dict, event: str, matcher: str, command: str) -> bool:
    for blk in hooks_cfg.get(event, []):
        if (blk.get("matcher") or "") != matcher:
            continue
        if any(h.get("command", "").strip() == command for h in blk.get("hooks", [])):
            return True
    return False


def load_auto_mode_from_transcript():
    """Дослівний блок autoMode з транскрипта: шукаємо результат інструмента (tool_result), текст якого — JSON
    settings.json з ключем autoMode. Повертає dict або None. Нічого не вигадує: немає точного збігу — None."""
    try:
        f = AUTO_MODE_TRANSCRIPT.open(encoding="utf-8")
    except OSError:
        return None
    with f:
        for line in f:
            if "autoMode" not in line or "tool_result" not in line:   # у JSONL лапки всередині рядка екрановані (\"autoMode\")
                continue
            try:
                rec = json.loads(line)
                blocks = rec["message"]["content"]
            except (ValueError, KeyError, TypeError):
                continue
            if not isinstance(blocks, list):
                continue
            for b in blocks:
                text = b.get("content") if isinstance(b, dict) and b.get("type") == "tool_result" else None
                if isinstance(text, list):
                    text = "\n".join(x.get("text", "") for x in text if isinstance(x, dict))
                if not isinstance(text, str) or '"autoMode"' not in text or "{" not in text:
                    continue
                try:
                    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
                except ValueError:
                    continue
                block = data.get("autoMode") if isinstance(data, dict) else None
                if (isinstance(block, dict) and isinstance(block.get("environment"), list) and block["environment"]
                        and "�" not in json.dumps(block, ensure_ascii=False)):
                    return block
    return None


def plan(cfg: dict, auto_block=None):
    """Повертає (нова конфігурація, список доданого). Не пише на диск."""
    new = json.loads(json.dumps(cfg))
    hooks = new.setdefault("hooks", {})
    added = []
    if auto_block is not None and "autoMode" not in new:   # існуючий autoMode власника НІКОЛИ не перезаписуємо
        new["autoMode"] = auto_block
        added.append(f"autoMode (з транскрипта, {sum(len(v) if isinstance(v, list) else 1 for v in auto_block.values())} пунктів)")
    for event, matcher, script, timeout in WANTED:
        cmd = command_for(script)
        if has_hook(hooks, event, matcher, cmd):
            continue
        blk = {"hooks": [{"type": "command", "command": cmd, "shell": "bash", "timeout": timeout}]}
        if matcher:
            blk["matcher"] = matcher
        hooks.setdefault(event, []).append(blk)
        added.append(f"{event}[{matcher or '*'}] -> {script}")
    return new, added


def make_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / f"settings.json.{datetime.now():%Y%m%d_%H%M%S_%f}"
    shutil.copy2(SETTINGS, dst)
    return dst


def rollback() -> int:
    backups = sorted(BACKUP_DIR.glob("settings.json.*")) if BACKUP_DIR.exists() else []
    backups = [b for b in backups if ".before_rollback" not in b.name]
    if not backups:
        print(f"[ПОМИЛКА] бекапів нема в {BACKUP_DIR}")
        return 1
    last = backups[-1]
    if SETTINGS.exists():
        keep = BACKUP_DIR / f"settings.json.before_rollback_{datetime.now():%Y%m%d_%H%M%S_%f}"
        shutil.copy2(SETTINGS, keep)
        print(f"[ok] поточний settings.json збережено як {keep}")
    shutil.copy2(last, SETTINGS)
    print(f"[ok] settings.json повернено з {last}")
    return 0


def install(dry_run: bool, restore_automode: bool = False) -> int:
    cfg = load_settings()                      # 1) спершу розібрати — при помилці нічого не пишемо
    auto_block = None
    if restore_automode:
        if "autoMode" in cfg:
            print("[skip] autoMode: у settings.json вже є — не перезаписую")
        else:
            auto_block = load_auto_mode_from_transcript()
            if auto_block is None:
                print(f"[ПОМИЛКА] блок autoMode не знайдено в {AUTO_MODE_TRANSCRIPT} — нічого не записано.")
                return 1
            print(f"[ok] autoMode знайдено в транскрипті: {len(auto_block.get('environment', []))} пунктів environment")
    new_cfg, added = plan(cfg, auto_block)
    if dry_run:
        print("[dry-run] нічого не записано. Було б додано:" if added else "[dry-run] додавати нічого — усе вже стоїть.")
        for a in added:
            print(f"   + {a}")
        print(json.dumps(new_cfg, ensure_ascii=False, indent=4))
        return 0
    backup = None
    if added and SETTINGS.exists():   # бекап лише коли settings.json справді змінюватиметься (інакше «останній бекап» = уже змінений)
        try:
            backup = make_backup()             # 2) бекап ПЕРЕД будь-яким записом; не вдався -> виходимо
        except OSError as e:
            print(f"[ПОМИЛКА] бекап не вдався ({e}) — нічого не записано.")
            return 1
        print(f"[ok] резервна копія settings.json: {backup}")
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    for name in SCRIPTS:
        text = (HERE / "hooks" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        (HOOKS_DIR / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"[ok] {HOOKS_DIR / name}")
    claude_md = HOME_CLAUDE / "CLAUDE.md"      # рольові сесії мають cwd = домашня тека: репо-CLAUDE.md їм не вантажиться
    if claude_md.exists():
        print(f"[skip] {claude_md}: вже є (не перезаписую)")
    else:
        sys.path.insert(0, str(HERE.parent.parent))
        import recall
        claude_md.write_text(
            "# CLAUDE.md (рівень користувача) — ядро правил PlutusToys\n\n"
            "Репо: C:\\Users\\smach\\rozetka_agent (toysi-feeds). Повні правила — його CLAUDE.md і SYSTEM_MAP.md (SSOT).\n"
            "Роль сесії бери зі своєї персони (.claude/agents/plutus-*.md); картка стану: "
            "`python recall.py --config <роль> --restore`.\n\n" + recall.CORE_RULES + "\n", encoding="utf-8")
        print(f"[ok] {claude_md}")
    if added:
        SETTINGS.write_text(json.dumps(new_cfg, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        print("[ok] settings.json: додано " + "; ".join(added))
    else:
        print("[skip] settings.json: усі хуки вже стоять, файл не переписую")
    print("Готово. Відкат: python deploy\\claude_home\\install_hooks.py --rollback. "
          "Нові сесії/ущільнення підхоплять хуки автоматично.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="показати підсумок і нічого не писати")
    ap.add_argument("--rollback", action="store_true", help="повернути settings.json з останнього бекапу")
    ap.add_argument("--restore-automode", action="store_true",
                    help="додати блок autoMode, витягнутий дослівно з транскрипта сесії 9d996807 (лише якщо autoMode ще нема)")
    a = ap.parse_args()
    return rollback() if a.rollback else install(a.dry_run, a.restore_automode)


if __name__ == "__main__":
    sys.exit(main())
