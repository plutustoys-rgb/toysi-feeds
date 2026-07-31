#!/bin/bash
# publish_eva_feed_vps.sh — публікує ЛИШЕ feeds/eva_feed.xml у гілку feed-data
# (VPS, eva-feed.timer — окремий ПОГОДИННИЙ цикл тільки для EVA, без репрайсера й
# без решти фідів). Той самий orphan+force-push патерн, що й publish_feed_pipeline_vps.sh,
# але торкається виключно eva_feed.xml — решта файлів feed-data (prom_feed_top.xml,
# google/meta/bing, rozetka, стан-файли) переносяться незмінними через імпорт повного
# дерева (git checkout origin/feed-data -- .), рівно як у головному публікаторі.
#
# НАВІЩО ОКРЕМО (пряме рішення власника 2026-07-31): власник хоче свіжість залишків
# EVA раз на годину. Головний feed-pipeline (6 год) у тому ж прогоні ганяє репрайсер
# Prom --apply і тягне повний каталог Toysi — прискорювати ЙОГО до 1 год означало б
# 6× частіше міняти ціни Prom і 6× навантаження на API. Тому EVA винесено в окремий
# легкий цикл: лише generate_eva_feed.py + ця публікація, без репрайсера.
#
# КРИТИЧНО — СЕРІАЛІЗАЦІЯ З feed-pipeline (flock нижче): eva-feed (1 год) і
# feed-pipeline (6 год) обидва роблять orphan-force-push у feed-data. Без спільного
# локу пізніший force-push із коміту, що імпортував СТАРІШЕ дерево, відкинув би фіди
# іншого боку (напр. google/meta/bing від feed-pipeline) до наступного його прогону —
# до 6 год регресії. Спільний lock гарантує, що імпорт дерева завжди бачить останній
# push. Той самий lock додано і в publish_feed_pipeline_vps.sh.
set -e
cd /opt/plutustoys

# Спільний lock публікацій у feed-data (див. коментар вище). Чекаємо до 300с — якщо
# інша публікація триває довше, пропускаємо цей погодинний тик (наступний за годину).
exec 9>/opt/plutustoys/.feeddata_publish.lock
flock -w 300 9 || { echo "[PublishEva] feed-data lock не взято за 300с — публікацію EVA пропущено цього тику."; exit 0; }

# Порожній/відсутній eva_feed.xml НЕ публікуємо (інакше стерли б робочий фід);
# попередня версія у feed-data зберігається сама через імпорт дерева нижче.
if [ ! -s /opt/plutustoys/feeds/eva_feed.xml ]; then
    echo "[PublishEva] feeds/eva_feed.xml відсутній/порожній — публікацію пропущено (попередня версія у feed-data лишається)."
    exit 0
fi

export GIT_SSH_COMMAND="ssh -i /opt/plutustoys/.ssh_feed_publish/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/plutustoys/.ssh_feed_publish/known_hosts"

WORKDIR=/opt/plutustoys/.eva_feed_git
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
git init -q
git remote add origin git@github.com:plutustoys-rgb/toysi-feeds.git
git config user.name "eva-feed-bot"
git config user.email "eva-feed-bot@users.noreply.github.com"

# Підтягуємо ПОВНЕ поточне дерево feed-data (усі інші фіди/стани — за них
# відповідають feed-pipeline і GH Actions) — || true на випадок, коли гілки
# feed-data ще не існує. Далі перезаписуємо ЛИШЕ eva_feed.xml.
git fetch --depth 1 origin feed-data 2>/dev/null || true
git checkout --orphan feed-data -q
git checkout origin/feed-data -- . 2>/dev/null || true

mkdir -p feeds
cp /opt/plutustoys/feeds/eva_feed.xml feeds/eva_feed.xml
git add -f feeds/eva_feed.xml

# Якщо eva_feed.xml не змінився (той самий вміст, що вже у feed-data) — commit із
# --allow-empty дав би порожній коміт; git commit без змін завершиться помилкою під
# set -e. Тому перевіряємо наявність застейджених змін.
if git diff --cached --quiet; then
    echo "[PublishEva] eva_feed.xml не змінився з попередньої публікації — нічого пушити."
    exit 0
fi

git commit -q -m "EVA feed update (VPS, погодинний) $(date -u +'%Y-%m-%d %H:%M UTC')"
git push --force git@github.com:plutustoys-rgb/toysi-feeds.git feed-data:feed-data
echo "[PublishEva] eva_feed.xml опубліковано у feed-data."
