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
# ⛔ ВИМКНЕНО 2026-08-10 (фінансовий витік): scan-state-data — гілка ПУБЛІЧНОГО репо
# (feed-data мусить лишатись публічним для Google/Meta), тож branch-«ізоляція» приватності
# НЕ давала — full_catalog_scan_state.json містив `cost` (собівартість) + `margin_pct` для
# ~3016 SKU, читабельних будь-ким за raw-URL. Той самий клас витоку, що KODV/NovaPay.
# Коментар вище «щоб update-feeds.yml (GH Actions) прочитав» ЗАСТАРІВ: 2026-07-28 той workflow
# звузили ВИКЛЮЧНО до Rozetka — читач гілки зник, лишилась гола публікація фінданих. Жоден код
# більше не фетчить цю гілку назад (git fetch/show/clone/raw — порожньо); локальний
# /opt/plutustoys/full_catalog_scan_state.json (джерело правди репрайсера) недоторканий.
# Публічний пуш ЗУПИНЕНО; гілку scan-state-data видалено. Тіло нижче лишено для історії й НЕ виконується.
echo "[publish_scan_state] ВИМКНЕНО: публікація стану скану зупинена (витік собівартості/маржі)." >&2
exit 0

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
