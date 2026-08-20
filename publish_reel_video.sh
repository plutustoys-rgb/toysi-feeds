#!/bin/bash
# publish_reel_video.sh — кладе ГОТОВЕ рендер-відео у гілку feed-data (media/) на GitHub і друкує
# публічний raw-URL. Той самий механізм і той самий write-ключ, що publish_feed_pipeline_vps.sh —
# щоб дати IG-Reels публічний video_url (єдиний блокер; сама публікація Reel уже є в
# social_auto_poster.py --reel). Заявка SMM 2026-08-19.
#
# КРИТИЧНО (як у publish_feed_pipeline_vps.sh): перед пушем робимо `git checkout origin/feed-data -- .`
# — orphan-force-push будує дерево з нуля, тож без відновлення повного дерева ми б СТЕРЛИ всі фіди.
# Тут ми ЛИШЕ ДОДАЄМО media/<file> поверх відновленого дерева. Спільний feed-data lock — той самий.
#
# ЗАПУСК (на VPS):
#   bash publish_reel_video.sh /opt/plutustoys/reels/plutus_p1.mp4
#   → друкує: https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/media/plutus_p1.mp4
# Далі: social_auto_poster.py --reel "<той URL>" --caption "..." --publish
#
# ⚠️ НЕ ЗВІРЕНО ЖИВО: чи IG приймає саме raw.githubusercontent як video_url (відомий ризик — GitHub
# raw інколи віддає octet-stream/редірект, і IG-інжест може відхилити). Перший прогін = жива проба:
# якщо media_publish поверне помилку інжесту — треба інший хост (напр. окрема nginx-локація на VPS).
set -e

SRC="${1:?Вкажи шлях до відео: bash publish_reel_video.sh /path/to/video.mp4}"
[ -s "$SRC" ] || { echo "[PublishReel] файл порожній/відсутній: $SRC" >&2; exit 1; }
FNAME="$(basename "$SRC")"

cd /opt/plutustoys

# Спільний lock feed-data (той самий, що feed-pipeline/eva-feed) — щоб не було гонки orphan-force-push.
exec 9>/opt/plutustoys/.feeddata_publish.lock
flock -w 300 9 || { echo "[PublishReel] feed-data lock не взято за 300с — пропущено." >&2; exit 1; }

export GIT_SSH_COMMAND="ssh -i /opt/plutustoys/.ssh_feed_publish/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/plutustoys/.ssh_feed_publish/known_hosts"

WORKDIR=/opt/plutustoys/.reel_publish_git
rm -rf "$WORKDIR"; mkdir -p "$WORKDIR"; cd "$WORKDIR"
git init -q
git remote add origin git@github.com:plutustoys-rgb/toysi-feeds.git
git config user.name "reel-publish-bot"
git config user.email "reel-publish-bot@users.noreply.github.com"

# ВІДНОВЛЮЄМО повне дерево feed-data (щоб не стерти фіди), потім ДОДАЄМО лише media/<file>.
git fetch --depth 1 origin feed-data 2>/dev/null || true
git checkout --orphan feed-data -q
git checkout origin/feed-data -- . 2>/dev/null || true

mkdir -p media
cp "$SRC" "media/$FNAME"
git add -f "media/$FNAME"
git commit -q -m "Publish reel video $FNAME $(date -u +'%Y-%m-%d %H:%M UTC')"
git push --force git@github.com:plutustoys-rgb/toysi-feeds.git feed-data:feed-data

echo "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/media/$FNAME"
