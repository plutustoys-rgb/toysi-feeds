#!/usr/bin/env bash
# SessionStart (startup|resume|compact|clear) — відновлення ролі: роль + що було в роботі (PreCompact) + картка каналів + ядро правил.
# Логіка в recall.py --session-start (тестується test_recall_session.py). Збій/таймаут = порожній вивід, старт сесії не блокується.
export PYTHONIOENCODING=utf-8
cd C:/Users/smach/rozetka_agent 2>/dev/null || exit 0
timeout 15 python recall.py --session-start 2>/dev/null
exit 0
