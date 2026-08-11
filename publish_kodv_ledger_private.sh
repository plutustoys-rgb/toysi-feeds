#!/bin/bash
# publish_kodv_ledger_private.sh — пушить kodv_ledger.jsonl (пише daily_report.py на VPS)
# у ПРИВАТНИЙ репо plutustoys-private-ledger (гілка main), щоб локальна сесія-бухгалтер
# читала graph6_cogs (собівартість) і решту без ручного перезапиту Toysi (order_positions
# для старих замовлень віддає 404 «Заказ не найден» — і локально, і на VPS; єдине джерело
# точного історичного COGS — цей леджер, зафіксований у момент форварду).
#
# ЧОМУ ПРИВАТНИЙ, а не toysi-feeds: toysi-feeds ПУБЛІЧНИЙ (Google/Meta тягнуть фіди без
# токена), тож фінжурнал там читався будь-ким за raw-URL — реальний витік (PR #240, той
# самий клас, що NovaPay). Приватний репо закриває це: VPS пушить за deploy-key, локальний
# бухгалтер пулить за токеном власника — назовні нічого.
#
# НАКОПИЧУВАЛЬНИЙ (не orphan-force-push): це ФІНАНСОВИЙ журнал, git-історію тут втрачати
# НЕ можна. Тому: підтягуємо реальну гілку (fetch/checkout), звичайний push (НЕ --force) —
# при конфлікті скрипт голосно провалюється замість тихо перезаписати чужі зміни.
set -e
cd /opt/plutustoys
export GIT_SSH_COMMAND="ssh -i /opt/plutustoys/.ssh_private_ledger/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/plutustoys/.ssh_private_ledger/known_hosts"

REPO="git@github.com:plutustoys-rgb/plutustoys-private-ledger.git"
BRANCH="main"
WORKDIR=/opt/plutustoys/.kodv_ledger_private_git

if [ ! -f /opt/plutustoys/kodv_ledger.jsonl ]; then
    echo "[publish_kodv_ledger_private] kodv_ledger.jsonl ще не існує — нема чого публікувати."
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
    echo "[publish_kodv_ledger_private] Без змін — немає нових записів."
    exit 0
fi

git commit -q -m "KODV ledger (private) update $(date -u +'%Y-%m-%d %H:%M UTC')"
git push -q origin "$BRANCH"
echo "[publish_kodv_ledger_private] Опубліковано в приватний репо, гілка $BRANCH."
