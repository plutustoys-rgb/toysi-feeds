#!/bin/bash
# publish_kodv_ledger.sh — публікує kodv_ledger.jsonl (daily_report.py, VPS)
# в окрему гілку kodv-ledger-data на GitHub, ізольовану від feed-data (та
# сама причина ізоляції, що й scan-state-data/publish_scan_state.sh: два
# незалежні force-push-публікатори на ОДНУ гілку затирали б один одного —
# живий інцидент 2026-07-19, code_report_2026-07-19_pt2.md).
#
# НА ВІДМІНУ від publish_scan_state.sh (--orphan + force-push, БЕЗ історії
# комітів — прийнятно для регенерованого щоразу з нуля скану): це
# ФІНАНСОВИЙ, накопичувальний журнал — втрата git-історії тут неприйнятна.
# Тому: підтягуємо РЕАЛЬНУ гілку (git fetch/checkout, не --orphan щоразу),
# звичайний git push (НЕ --force) — якщо push відхилено (конфлікт), скрипт
# явно провалюється замість тихого перезапису чужих змін.
set -e
cd /opt/plutustoys
export GIT_SSH_COMMAND="ssh -i /opt/plutustoys/.ssh_scan_state/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/plutustoys/.ssh_scan_state/known_hosts"

REPO="git@github.com:plutustoys-rgb/toysi-feeds.git"
BRANCH="kodv-ledger-data"
WORKDIR=/opt/plutustoys/.kodv_ledger_git

if [ ! -f /opt/plutustoys/kodv_ledger.jsonl ]; then
    echo "[publish_kodv_ledger] kodv_ledger.jsonl ще не існує (немає нових записів жодного разу) — нема чого публікувати."
    exit 0
fi

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
git init -q
git remote add origin "$REPO"
git config user.name "kodv-ledger-bot"
git config user.email "kodv-ledger-bot@users.noreply.github.com"

if git ls-remote --exit-code --heads origin "$BRANCH" > /dev/null 2>&1; then
    git fetch -q origin "$BRANCH"
    git checkout -q -b "$BRANCH" "origin/$BRANCH"
else
    git checkout -q --orphan "$BRANCH"
fi

cp /opt/plutustoys/kodv_ledger.jsonl .
git add kodv_ledger.jsonl
if git diff --cached --quiet; then
    echo "[publish_kodv_ledger] Без змін — немає нових записів."
    exit 0
fi

git commit -q -m "KODV ledger update $(date -u +'%Y-%m-%d %H:%M UTC')"
git push -q origin "$BRANCH"
echo "[publish_kodv_ledger] Опубліковано в $BRANCH."
