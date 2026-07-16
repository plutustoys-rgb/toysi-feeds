#!/bin/bash
# publish_scan_state.sh — публікує full_catalog_scan_state.json (VPS,
# нічний скан) в окрему гілку scan-state-data на GitHub, ізольовану від
# feed-data (яку публікує update-feeds.yml force-push'ем БЕЗ історії) —
# щоб два незалежні публікатори ніколи не перезаписували вміст один
# одного своїм force-push. Дозволяє update-feeds.yml (GH Actions)
# прочитати цей файл звичайним git fetch, без жодного нового секрету на
# боці GH Actions — лише ця гілка на VPS має власний, вузько
# призначений deploy-ключ (лише запис у цей репозиторій, ні до чого
# іншого доступу немає).
set -e
cd /opt/plutustoys
export GIT_SSH_COMMAND="ssh -i /opt/plutustoys/.ssh_scan_state/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/plutustoys/.ssh_scan_state/known_hosts"

WORKDIR=/opt/plutustoys/.scan_state_git
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
git init -q
git config user.name "scan-state-bot"
git config user.email "scan-state-bot@users.noreply.github.com"
cp /opt/plutustoys/full_catalog_scan_state.json .
git checkout --orphan scan-state-data -q
git add full_catalog_scan_state.json
git commit -q -m "Scan state update $(date -u +'%Y-%m-%d %H:%M UTC')"
git push --force git@github.com:plutustoys-rgb/toysi-feeds.git scan-state-data:scan-state-data
