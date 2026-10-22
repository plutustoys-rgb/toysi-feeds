#!/usr/bin/env bash
# PreCompact — перед ущільненням контексту скидає «що в роботі» в .local_secrets/role_state/<роль>.md
# (читає SessionStart[compact]). Логіка в recall.py --save-state. Ущільнення НЕ блокується; збій називається у stderr.
export PYTHONIOENCODING=utf-8
cd C:/Users/smach/rozetka_agent 2>/dev/null || { echo "⚠️ recall: PreCompact — каталог репо недоступний, стан не збережено" >&2; exit 0; }
timeout 15 python recall.py --save-state >/dev/null 2>&1
rc=$?
[ "$rc" -ne 0 ] && echo "⚠️ recall: PreCompact не зберіг стан (rc=$rc)" >&2
exit 0
