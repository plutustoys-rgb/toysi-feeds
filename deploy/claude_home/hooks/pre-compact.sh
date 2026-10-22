#!/usr/bin/env bash
# PreCompact — перед ущільненням контексту скидає «що в роботі» в .local_secrets/role_state/<роль>.md
# (читає SessionStart[compact]). Логіка в recall.py --save-state. Нічого не друкує; збій мовчазний.
export PYTHONIOENCODING=utf-8
cd C:/Users/smach/rozetka_agent 2>/dev/null || exit 0
timeout 15 python recall.py --save-state >/dev/null 2>&1
exit 0
