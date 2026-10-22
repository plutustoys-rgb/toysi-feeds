#!/usr/bin/env bash
# SessionStart (startup|resume|compact|clear) — відновлення ролі: роль + що було в роботі (PreCompact) + картка каналів + ядро правил.
# Логіка в recall.py --session-start (тестується test_recall_session.py). Збій/таймаут НЕ блокує старт сесії:
# сесія стартує без картки, а причина називається рядком «⚠️ recall: …» (не тиша; той самий принцип, що в recall-inject.sh).
export PYTHONIOENCODING=utf-8
cd C:/Users/smach/rozetka_agent 2>/dev/null || { printf '%s' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"⚠️ recall: каталог репо rozetka_agent недоступний — відновлення ролі пропущено."}}'; exit 0; }
out=$(timeout 15 python recall.py --session-start 2>/dev/null)
rc=$?
if [ "$rc" -ne 0 ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"⚠️ recall: відновлення ролі не спрацювало (rc=%s%s) — сесія стартує без картки; роль і стан бери зі своєї персони та каналів."}}' "$rc" "$([ "$rc" = 124 ] && echo ', таймаут')"
else
  printf '%s' "$out"
fi
exit 0
