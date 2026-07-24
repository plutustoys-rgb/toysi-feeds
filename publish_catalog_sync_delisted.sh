#!/bin/bash
# publish_catalog_sync_delisted.sh — публікує _delisted_since з локального
# prom_competitor_price_state.json на VPS (пише prom_catalog_sync.py::
# deactivate(), НЕ prom_competitor_pricer.py — той працює в GH Actions,
# зовсім інший файл на іншій машині) в окрему гілку
# catalog-sync-delisted-data, той самий принцип ізоляції, що вже є для
# scan-state-data/kodv-ledger-data/pricer-state-data — окремий вузько
# призначений deploy-ключ, лише запис у цей репозиторій.
#
# ДОДАНО (2026-07-24, живий root-cause: Prom import падав на "Поле status:
# Позиція недоступна для оновлення" для 179 SKU — товари реально й
# назавжди видалені через prom_catalog_sync.py::deactivate() (SKU випав з
# топ-970), але generate_prom_feed_top.py::_margin() перевіряв ЛИШЕ
# delisted_since від prom_competitor_pricer.py::delist() — інший,
# паралельний шлях реального видалення, НІКОЛИ не мав власної пам'яті).
set -e
cd /opt/plutustoys
export GIT_SSH_COMMAND="ssh -i /opt/plutustoys/.ssh_catalog_sync_delisted/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/plutustoys/.ssh_catalog_sync_delisted/known_hosts"

# Немає що публікувати, якщо prom_catalog_sync.py ще не видалив жодного
# товару цього прогону (файл не існує чи порожній _delisted_since) —
# не помилка, просто нічого нового.
if [ ! -s /opt/plutustoys/prom_competitor_price_state.json ]; then
    echo "[publish_catalog_sync_delisted] prom_competitor_price_state.json відсутній/порожній — нічого публікувати."
    exit 0
fi

WORKDIR=/opt/plutustoys/.catalog_sync_delisted_git
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
git init -q
git config user.name "catalog-sync-delisted-bot"
git config user.email "catalog-sync-delisted-bot@users.noreply.github.com"
cp /opt/plutustoys/prom_competitor_price_state.json .
git checkout --orphan catalog-sync-delisted-data -q
git add prom_competitor_price_state.json
git commit -q -m "Catalog-sync delisted update $(date -u +'%Y-%m-%d %H:%M UTC')"
git push --force git@github.com:plutustoys-rgb/toysi-feeds.git catalog-sync-delisted-data:catalog-sync-delisted-data
