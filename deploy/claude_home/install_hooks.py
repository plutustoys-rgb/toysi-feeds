#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_hooks.py — ставить хуки відновлення ролі в ~/.claude (запускає ВЛАСНИК, не агент).

Що робить (ідемпотентно, нічого не видаляє):
  1) копіює session-start.sh і pre-compact.sh з цієї теки в ~/.claude/hooks/;
  2) робить резервну копію ~/.claude/settings.json -> settings.json.bak_<час>;
  3) додає в settings.json події SessionStart (startup|resume|compact|clear) і PreCompact,
     якщо їх там ще нема; існуючі хуки (merge-guard, recall-guard, recall-inject) не чіпає.
Відкат: повернути settings.json з .bak_*.  Запуск:  python deploy\\claude_home\\install_hooks.py
"""
import json
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
SCRIPTS = ("session-start.sh", "pre-compact.sh")


def _cmd(script: str, timeout: int) -> dict:
    return {"type": "command", "command": f"bash {(HOOKS_DIR / script).as_posix()}", "shell": "bash", "timeout": timeout}


def main() -> int:
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    for name in SCRIPTS:
        text = (HERE / "hooks" / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        (HOOKS_DIR / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"[ok] {HOOKS_DIR / name}")
    claude_md = HOME_CLAUDE / "CLAUDE.md"   # рольові сесії мають cwd = домашня тека: репо-CLAUDE.md їм не вантажиться
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
    cfg = json.loads(SETTINGS.read_text(encoding="utf-8-sig")) if SETTINGS.exists() else {}
    bak = SETTINGS.with_name(f"settings.json.bak_{datetime.now():%Y%m%d_%H%M%S}")
    if SETTINGS.exists():
        shutil.copy2(SETTINGS, bak)
        print(f"[ok] резервна копія {bak}")
    hooks = cfg.setdefault("hooks", {})
    added = []
    for event, matcher, script, timeout in (("SessionStart", "startup|resume|compact|clear", "session-start.sh", 20),
                                             ("PreCompact", "", "pre-compact.sh", 20)):
        marker = script
        if any(marker in h.get("command", "") for blk in hooks.get(event, []) for h in blk.get("hooks", [])):
            print(f"[skip] {event}: уже є")
            continue
        blk = {"hooks": [_cmd(script, timeout)]}
        if matcher:
            blk["matcher"] = matcher
        hooks.setdefault(event, []).append(blk)
        added.append(event)
    SETTINGS.write_text(json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8")
    print(f"[ok] settings.json: додано {', '.join(added) or 'нічого нового'}")
    print("Готово. Нові сесії/ущільнення підхоплять хуки автоматично (за потреби — перезапустіть сесію).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
