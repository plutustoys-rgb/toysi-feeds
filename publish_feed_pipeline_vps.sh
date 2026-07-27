#!/bin/bash
# publish_feed_pipeline_vps.sh — публікує згенеровані фіди + стан-файли
# (VPS, feed-pipeline.timer, після run_feed_pipeline_vps.sh) у гілку
# feed-data на GitHub — той самий orphan+force-push патерн, що вже є в
# publish_scan_state.sh/publish_catalog_sync_delisted.sh, і той самий
# набір файлів/умовних додавань, що раніше публікував крок "Publish
# feeds to feed-data" в .github/workflows/update-feeds.yml.
#
# Ключ vps-feed-publish (write, ЛИШЕ на цей репозиторій) — гілка
# ЖОРСТКО закодована як feed-data нижче, без жодного параметра: навіть
# якби сам ключ технічно міг писати куди завгодно (GitHub deploy-ключі
# не обмежуються гілкою), цей скрипт структурно не вміє торкнутись
# нічого іншого. Master додатково захищений branch protection
# (require PR review) як другий рубіж.
#
# prom_competitor_price_state.json публікується сюди й далі — не лише
# для Prom-імпорту (він її не читає напряму), а тому що
# apply_live_dumping_fix.py (ручний, разовий контролер) досі фетчить
# цей файл з feed-data як джерело правди (_fetch_fresh_price_state()).
set -e
cd /opt/plutustoys
export GIT_SSH_COMMAND="ssh -i /opt/plutustoys/.ssh_feed_publish/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/plutustoys/.ssh_feed_publish/known_hosts"

WORKDIR=/opt/plutustoys/.feed_pipeline_git
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
git init -q
git config user.name "feed-pipeline-bot"
git config user.email "feed-pipeline-bot@users.noreply.github.com"

mkdir -p feeds
cp /opt/plutustoys/feeds/rozetka_feed.xml feeds/
cp /opt/plutustoys/feeds/prom_feed_top.xml feeds/
cp /opt/plutustoys/prom_competitor_price_state.json .
cp /opt/plutustoys/own_product_links_cache.json .

git checkout --orphan feed-data -q
git add -f feeds/rozetka_feed.xml feeds/prom_feed_top.xml \
    prom_competitor_price_state.json own_product_links_cache.json

# rozetka_static_selection.json (PR #179 — замінює rozetka_feed_membership_state.json,
# більше не пишеться generate_rozetka_feed.py) — той самий умовний
# принцип, що й для необов'язкових фідів нижче: якщо файл ще не існує
# (перший прогін ще не завершився), не блокувати публікацію решти.
if [ -s /opt/plutustoys/rozetka_static_selection.json ]; then
    cp /opt/plutustoys/rozetka_static_selection.json .
    git add -f rozetka_static_selection.json
fi

for f in google_merchant_feed.xml meta_feed.xml bing_feed.xml eva_feed.xml; do
    if [ -s "/opt/plutustoys/feeds/$f" ]; then
        cp "/opt/plutustoys/feeds/$f" "feeds/$f"
        git add -f "feeds/$f"
    else
        echo "[PublishFeedPipeline] feeds/$f відсутній/порожній цього прогону — НЕ опубліковано (решта фідів публікуються як завжди)."
    fi
done

git commit -q -m "Feed pipeline update (VPS) $(date -u +'%Y-%m-%d %H:%M UTC')"
git push --force git@github.com:plutustoys-rgb/toysi-feeds.git feed-data:feed-data
