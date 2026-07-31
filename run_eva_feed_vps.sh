#!/bin/bash
# run_eva_feed_vps.sh — VPS systemd-таймер (eva-feed.timer), ПОГОДИННИЙ цикл ТІЛЬКИ
# для EVA-фіда (пряме рішення власника 2026-07-31: свіжість залишків EVA раз на годину).
# Генерує eva_feed.xml на живому каталозі Toysi (власна формула ×1.45, розв'язана з
# Prom) і публікує ЛИШЕ його у feed-data. НЕ ганяє репрайсер і не тягне решту фідів —
# це лишається головному feed-pipeline (6 год). Кабінет EVA авто-імпортує з feed-data.
#
# Той самий read-only pull-ключ, що й vps_code_sync.sh/run_feed_pipeline_vps.sh — для
# restore-фолбеку нижче (читання feed-data). Публікацію (write-ключ) робить
# publish_eva_feed_vps.sh.
export GIT_SSH_COMMAND="ssh -i /opt/plutustoys/.ssh_deploy_pull/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/plutustoys/.ssh_deploy_pull/known_hosts"

set -e
cd /opt/plutustoys

echo "[EvaFeed] $(date -u +'%Y-%m-%d %H:%M UTC') — старт погодинного EVA-циклу."

# Генерація best-effort: якщо провалилась (напр. таймаут Toysi), НЕ падаємо —
# нижче спрацює restore-фолбек, а якщо і він порожній, publish пропустить публікацію,
# зберігши попередній робочий фід у feed-data (правило CLAUDE.md для best-effort кроку).
python3 generate_eva_feed.py || echo "[EvaFeed] generate_eva_feed.py провалився (best-effort) — пробую restore з feed-data."

# Restore-фолбек: якщо цей прогін не створив валідний eva_feed.xml, відновлюємо
# останню робочу версію з feed-data (публічний репозиторій, читання без write-ключа),
# щоб publish не стер робочий фід порожнім. eva_feed.xml раніше НЕ мав такого фолбеку
# (відомий ризик, CLAUDE.md GOTCHAS #1) — тут закриваємо його для погодинного циклу.
if [ ! -s feeds/eva_feed.xml ]; then
    echo "[EvaFeed] eva_feed.xml відсутній/порожній — відновлюю останню робочу версію з feed-data."
    git fetch origin feed-data 2>/dev/null || true
    mkdir -p feeds
    git show origin/feed-data:feeds/eva_feed.xml > feeds/eva_feed.xml 2>/dev/null || true
fi

bash publish_eva_feed_vps.sh

echo "[EvaFeed] $(date -u +'%Y-%m-%d %H:%M UTC') — погодинний EVA-цикл завершено."
