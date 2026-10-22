#!/usr/bin/env bash
# PreToolUse(Write) — ДУБЛЬ-ВАРТА. Перед створенням НОВОГО .py/.sh/.md у репо toysi-feeds
# автоматично запускає recall.py --file і, якщо схожий файл/модуль УЖЕ Є (exit 3), впорскує
# перелік наявного в контекст АГЕНТА через additionalContext. Мета (біль власника): не
# покладатись на те, чи агент згадав перевірити — харнес підсовує «вже зроблено» в мить створення,
# щоб після ущільнення сесії не формувались дублі. НЕ блокує і НЕ питає власника — це прапор для
# АГЕНТА (рішення «дубль чи ні» — його, не власника; пряме зауваження власника 2026-08-29).
# UTF-8 для всіх python-викликів хука: recall.py та JSON-емітер друкують емодзі/стрілки (🔴 →),
# які на дефолтному cp1251-Windows інакше падають UnicodeEncodeError → хук би мовчки не спрацював.
# PYTHONIOENCODING (не PYTHONUTF8) — бо перебиває навіть явно виставлений ambient cp1251.
export PYTHONIOENCODING=utf-8
in=$(cat)
fp=$(printf '%s' "$in" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null)
[ -z "$fp" ] && exit 0
# лише код/скрипти/доки в дереві rozetka_agent
case "$fp" in *rozetka_agent*|*rozetka-agent*) : ;; *) exit 0 ;; esac
case "$fp" in *.py|*.sh|*.md) : ;; *) exit 0 ;; esac
# НОВИЙ файл? (наявний = редагування, дубль-ризику нема)
[ -e "$fp" ] && exit 0
repo="/c/Users/smach/rozetka_agent"
[ -f "$repo/recall.py" ] || exit 0
rep=$(cd "$repo" && python recall.py --file "$(basename "$fp")" 2>/dev/null)
[ "$?" = "3" ] || exit 0
# дубль-сигнал → впорскуємо звіт у контекст АГЕНТА (additionalContext) — власника НЕ смикаємо,
# запис проходить нормально, а агент сам вирішує «дубль чи ні» ДО того, як рухатись далі.
REP="$rep" BN="$(basename "$fp")" python - <<'PY'
import json, os
rep = os.environ.get("REP", "")[:1500]
bn = os.environ.get("BN", "")
note = (f"⚠️ ДУБЛЬ-ВАРТА (для тебе, агенте): перед створенням {bn} recall.py знайшов схоже в репо:\n\n{rep}\n\n"
        "ТИ маєш вирішити: якщо це справді НОВИЙ файл — продовжуй; якщо функціональний дубль — "
        "РЕДАГУЙ наявний, не створюй новий. Не виноси це рішення на власника.")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
      "additionalContext": note}}, ensure_ascii=False))
PY
exit 0
