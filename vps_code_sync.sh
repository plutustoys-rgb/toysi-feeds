#!/bin/bash
# vps_code_sync.sh — тягне свіжий master у /opt/plutustoys (справжній git-клон,
# після одноразової ручної адаптації за vps_runbook_2026-07-27_git_autodeploy.md)
# через окремий read-only деплой-ключ vps-pull-deploy, і звітує результат у
# vps_code_sync_state.json (читає check_autodeploy_status() у
# service_watchdog.py).
#
# Навмисно `merge --ff-only`, а не `reset --hard`: якщо на VPS раптом є
# локальний коміт/дрейф, якого нема в origin/master (не мало б статись за
# нормальної роботи — увесь код рухається через PR у master), скрипт явно
# провалюється замість тихого відкидання цього стану. Той самий принцип, що
# й у publish_kodv_ledger.sh — "провалитись голосно замість тихо перезаписати".
#
# Усі service-таймери на VPS зараз запускаються заново з диска на кожен
# власний тик (oneshot, не довгоживучі демони) — тому підхоплення нового
# коду не потребує явного `systemctl restart` після пулу: наступний тик
# кожного сервісу сам прочитає вже оновлені файли.
set -e
cd /opt/plutustoys
export GIT_SSH_COMMAND="ssh -i /opt/plutustoys/.ssh_deploy_pull/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/plutustoys/.ssh_deploy_pull/known_hosts"

BEFORE=$(git rev-parse HEAD)
git fetch origin master

if git merge --ff-only origin/master; then
    AFTER=$(git rev-parse HEAD)
    if [ "$BEFORE" != "$AFTER" ]; then
        CHANGED=$(git diff --name-only "$BEFORE" "$AFTER")
    else
        CHANGED=""
    fi
    # Авто-застосування systemd-юнітів (найновіше-24 п.1): копіює нові/змінені *.service/*.timer
    # у /etc/systemd/system, вмикає нові таймери, знімає зниклі — щоб не робити це вручну на
    # кожен деплой. Керує ЛИШЕ нашими юнітами (див. deploy_systemd_units.sh); збій НЕ валить sync
    # коду (|| true). Потребує root; під нерутом м'яко пропускає.
    bash deploy_systemd_units.sh || echo "[sync] застосування юнітів не вдалось (код синхронізовано)."
    python3 vps_code_sync_report.py --status ok --commit "$AFTER" --changed "$CHANGED"
else
    CURRENT=$(git rev-parse HEAD)
    python3 vps_code_sync_report.py --status failed --commit "$CURRENT" \
        --reason "non-fast-forward (локальний дрейф на VPS відносно origin/master)"
    exit 1
fi
