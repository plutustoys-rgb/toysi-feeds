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
#
# ВИПРАВЛЕНО (2026-07-28, пряме рішення власниці): Rozetka повністю
# прибрана з цього скрипта — ані feeds/rozetka_feed.xml, ані
# rozetka_static_selection.json. VPS більше НЕ викликає
# generate_rozetka_feed.py (run_feed_pipeline_vps.sh), тож локальної
# копії цих файлів на диску VPS взагалі не існує — публікація
# лишається виключно на GH Actions (update-feeds.yml), щоб дві сторони
# ніколи не писали в ту саму частину feed-data.
#
# КРИТИЧНО ВИПРАВЛЕНО (2026-07-28, знайдено ДО живого прогону): просте
# розділення "хто які файли додає" НЕ РЯТУЄ від взаємного затирання,
# поки обидва боки роблять orphan-коміт — кожен такий коміт БУДУЄ
# ПОРОЖНЄ дерево з нуля, тож хто б не запушив ПІЗНІШЕ, повністю стирає
# ВСЕ, чого немає в його власному коміті (включно з файлами Rozetka від
# GH Actions). Тому: перед стейджингом власних файлів ПІДТЯГУЄМО ПОВНЕ
# поточне дерево feed-data (git checkout origin/feed-data -- .) —
# комміт лишається "плоским" (без росту історії, той самий принцип, що
# й раніше), але тепер містить УСІ файли: свої (щойно перезаписані) і
# чужі (Rozetka від GH Actions — незмінні, збережені як є).
set -e
cd /opt/plutustoys
export GIT_SSH_COMMAND="ssh -i /opt/plutustoys/.ssh_feed_publish/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/plutustoys/.ssh_feed_publish/known_hosts"

WORKDIR=/opt/plutustoys/.feed_pipeline_git
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
git init -q
git remote add origin git@github.com:plutustoys-rgb/toysi-feeds.git
git config user.name "feed-pipeline-bot"
git config user.email "feed-pipeline-bot@users.noreply.github.com"

# Підтягуємо ПОВНЕ поточне дерево feed-data (включно з файлами Rozetka,
# за які відповідає GH Actions) — || true на випадок геть першого разу,
# коли гілка feed-data ще не існує.
git fetch --depth 1 origin feed-data 2>/dev/null || true
git checkout --orphan feed-data -q
git checkout origin/feed-data -- . 2>/dev/null || true

mkdir -p feeds
cp /opt/plutustoys/feeds/prom_feed_top.xml feeds/
cp /opt/plutustoys/prom_competitor_price_state.json .
cp /opt/plutustoys/own_product_links_cache.json .

git add -f feeds/prom_feed_top.xml \
    prom_competitor_price_state.json own_product_links_cache.json

# РОЗМОРОЖЕНО 2026-07-30 — eva_static_selection.json більше НЕ публікується (EVA
# ведеться живим фідом, знімок заморозки не використовується). eva_feed.xml і далі
# публікується в циклі нижче; при порожньому прогоні попередня версія зберігається
# автоматично через `git checkout origin/feed-data -- .` вище (рядок ~57).
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
