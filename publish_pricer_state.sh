#!/bin/bash
# publish_pricer_state.sh — публікує prom_competitor_price_state.json +
# prom_competitor_pricer_summary.md (VPS, systemd-таймер prom-competitor-
# pricer) в окрему гілку pricer-state-data на GitHub, ізольовану від
# feed-data (яку публікує update-feeds.yml force-push'ем БЕЗ історії) —
# щоб два незалежні публікатори ніколи не перезаписували вміст один
# одного своїм force-push. Той самий принцип, що вже є для
# scan-state-data (publish_scan_state.sh) і kodv-ledger-data
# (publish_kodv_ledger.sh). Дозволяє update-feeds.yml (GH Actions) і
# full_catalog_competitor_scan.py (VPS) прочитати ці файли звичайним
# git fetch/публічним raw URL, без жодного нового секрету на боці GH
# Actions — лише ця гілка на VPS має власний, вузько призначений
# deploy-ключ (лише запис у цей репозиторій, ні до чого іншого доступу
# немає).
set -e
cd /opt/plutustoys
export GIT_SSH_COMMAND="ssh -i /opt/plutustoys/.ssh_pricer_state/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/plutustoys/.ssh_pricer_state/known_hosts"

WORKDIR=/opt/plutustoys/.pricer_state_git
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
git init -q
git config user.name "pricer-state-bot"
git config user.email "pricer-state-bot@users.noreply.github.com"
cp /opt/plutustoys/prom_competitor_price_state.json .
cp /opt/plutustoys/prom_competitor_pricer_summary.md .
git checkout --orphan pricer-state-data -q
git add prom_competitor_price_state.json prom_competitor_pricer_summary.md
git commit -q -m "Pricer state update $(date -u +'%Y-%m-%d %H:%M UTC')"
git push --force git@github.com:plutustoys-rgb/toysi-feeds.git pricer-state-data:pricer-state-data
