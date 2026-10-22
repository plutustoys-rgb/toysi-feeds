#!/usr/bin/env bash
# UserPromptSubmit — авто-recall. ВІДНОВЛЕНО 21.10.2026 з опису (оригінал втрачено в інциденті run_all_tests).
# Що робить: на кожен промпт впорскує в контекст агента (additionalContext) «що вже зроблено про тему»:
#  1) роль сесії — recall.py --detect-role <transcript_path> (порожньо → фолбек без ролі);
#  2) слова продовження (продовжуй/далі/давай далі/так роби) у відомій ролі → картка стану
#     recall.py --config <роль> --restore; без ролі → нічого;
#  3) інакше — записи каналів ролі (recall.py "<тема>" --config <роль>) СПЕРШУ, потім загальний пошук;
#     результат ≤3000 символів. Роль не визначена → лише загальний пошук.
# Самодіагностика: причина збою — рядком «⚠️ recall: …», не тиша. Таймаути 3+9+5 с (ліміт хука 25 с).
# PYTHONIOENCODING (не PYTHONUTF8): cp1251-Windows інакше валить emoji/кирилицю мовчки.
export PYTHONIOENCODING=utf-8
in=$(cat)
HOOK_IN="$in" python - <<'PY'
import json, os, subprocess, sys

REPO = "C:/Users/smach/rozetka_agent"
CONTINUE = {"продовжуй", "далі", "давай далі", "так роби", "продовжуй далі"}


def emit(text):
    if text.strip():
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                 "additionalContext": text[:3000]}}, ensure_ascii=False))
    sys.exit(0)


def run(args, timeout):
    r = subprocess.run([sys.executable, "recall.py"] + args, cwd=REPO, capture_output=True,
                       timeout=timeout, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return r.returncode, r.stdout.decode("utf-8", "replace").strip()


try:
    payload = json.loads(os.environ.get("HOOK_IN", "") or "{}")
except Exception:
    sys.exit(0)
prompt = (payload.get("prompt") or "").strip()
low = prompt.lower().strip(" .!?,")
if not prompt or prompt.startswith("/") or not os.path.isfile(REPO + "/recall.py"):
    sys.exit(0)

role, notes = "", []
tp = payload.get("transcript_path") or ""
if tp:
    try:
        rc, out = run(["--detect-role", tp], 3)
        role = out.splitlines()[-1].strip() if (rc == 0 and out) else ""
    except Exception as e:  # noqa: BLE001
        notes.append(f"⚠️ recall: роль не визначена через збій ({type(e).__name__})")

if low in CONTINUE:
    if not role:
        sys.exit(0)
    try:
        rc, out = run(["--config", role, "--restore"], 5)
        if rc == 0 and out:
            emit(f"🧭 Картка стану ролі «{role}» (авто-recall):\n{out}")
        notes.append("⚠️ recall: картку стану не отримано")
    except Exception as e:  # noqa: BLE001
        notes.append(f"⚠️ recall: картку стану не отримано ({type(e).__name__})")
    emit("\n".join(notes))

if len(prompt.encode("utf-8")) < 14:
    sys.exit(0)

topic = prompt[:200].replace("\n", " ")
parts = []
if role:
    try:
        rc, out = run([topic, "--config", role], 9)
        if out:
            parts.append(f"Записи каналів ролі «{role}»:\n{out}")
        else:
            notes.append("⚠️ recall: записи ролі не отримано")
    except Exception as e:  # noqa: BLE001
        notes.append(f"⚠️ recall: записи ролі не отримано ({type(e).__name__})")
try:
    rc, out = run([topic], 5)
    if out:
        parts.append(("Загальний пошук:\n" if role else "") + out)
except Exception as e:  # noqa: BLE001
    notes.append(f"⚠️ recall: загальний пошук не спрацював ({type(e).__name__})")
body = "\n\n".join(parts)
if body:
    body = "📎 авто-recall (що вже зроблено про тему):\n" + body
emit("\n".join(notes + ([body] if body else [])))
PY
exit 0
