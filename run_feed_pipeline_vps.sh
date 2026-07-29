#!/bin/bash
# run_feed_pipeline_vps.sh — VPS systemd-таймер (feed-pipeline.timer),
# ЗАМІНЮЄ ланцюжок генерації фідів + репрайсер, який раніше виконувався
# в .github/workflows/update-feeds.yml — КРІМ Rozetka (навмисно, див.
# нижче).
#
# НАВІЩО (2026-07-27, пряме рішення власниці — "переведемо усі
# репрайсери на VPS, топчемося на місці і не можемо зробити конкурентну
# вітрину вже місяць"): живий вимір показав 42.5% топ-6000 товарів
# НІКОЛИ не проходили через репрайсер (нуль price_state-запису, гола
# формула собівартість+комісія) — ротаційний бюджет (ROTATION_BATCH_SIZE)
# розтягнутий на 6x ширший топ без відповідного збільшення пропускної
# спроможності, а GH Actions 6-годинна стеля не дозволяла просто
# обробляти все за один прогін.
#
# ВИПРАВЛЕНО (2026-07-28, пряме рішення власниці — розділити зони
# відповідальності після живого інциденту з паралельними прогонами
# update-feeds.yml, що затерли одне одного): Rozetka ПОВНІСТЮ прибрана
# з цього скрипта. Причини: (1) rozetka_static_selection.json —
# заморожений список на час модерації (див. generate_rozetka_feed.py),
# і перший тестовий прогін цього скрипта на VPS довів живо, що диск
# міг НЕ мати цього файлу локально (перший прогін узагалі) — код тоді
# перерахував би заморожений список заново, порушуючи заборону
# власниці; (2) навіть якби файл був на диску, VPS і GH Actions писали
# б у ТОЙ САМИЙ feeds/rozetka_feed.xml у feed-data — точно той клас
# гонки паралельних публікацій, що вже спричинив інцидент 2026-07-19 і
# повторно 2026-07-28 (prom_feed_top.xml впав з 6000 до 1575 офферів
# через паралельний workflow_dispatch). Тепер: GH Actions пише ЛИШЕ
# feeds/rozetka_feed.xml, VPS пише все інше — файли, за які відповідає
# кожна сторона, НІКОЛИ не перетинаються.
#
# КЛЮЧОВЕ СПРОЩЕННЯ проти update-feeds.yml: жодного кроку "Restore X
# from feed-data/scan-state-data/catalog-sync-delisted-data" тут немає.
# Усі стани (prom_competitor_price_state.json, full_catalog_scan_state.json,
# own_product_links_cache.json) просто лежать локально на диску VPS і
# природно переживають між прогонами — цей "round-trip через git-гілку
# лише тому, що кожен GH Actions runner стартує з чистого диска" був
# потрібен ЛИШЕ через ефемерність hosted-runner'а, якої тут більше немає.
#
# ВИПРАВЛЕНО (знахідка аудиту pt6, 2026-07-27): попередня версія мала
# `set -e` БЕЗ фолбеку для двох критичних кроків (репрайсер,
# generate_prom_feed_top.py) — на відміну від update-feeds.yml, де обидва
# мають continue-on-error + фолбек на останню робочу версію. Наслідок:
# транзієнтний збій (одна погана відповідь API) обривав би ВЕСЬ цикл,
# включно з публікацією — гірший режим відмови, ніж CI, якому ця міграція
# мала б бути покращенням. Тепер обидва явно перевіряються, з фолбеком.
#
# ВИПРАВЛЕНО (та сама знахідка): пишемо feed_pipeline_state.json через
# feed_pipeline_report.py після КОЖНОГО прогону (успіх/degraded/failed) —
# check_feed_pipeline_vps_status() (service_watchdog.py) читає цей файл,
# замінюючи check_feed_pipeline_schedule() (моніторила update-feeds.yml
# через GitHub API — стає непридатною, щойно schedule: там закоментовано).
#
# ВИПРАВЛЕНО (2026-07-28, живий інцидент — перший тестовий прогін упав
# на "rozetka_feed.xml відсутній навіть після фолбеку"): `git fetch`
# нижче раніше не встановлював GIT_SSH_COMMAND — репозиторій
# підключений через SSH (git@github.com:...), тож fetch МОВЧКИ
# провалювався без явного ключа (2>/dev/null || true ховав саму
# помилку). Той самий read-only ключ, що вже використовує
# vps_code_sync.sh для автопулу master.
export GIT_SSH_COMMAND="ssh -i /opt/plutustoys/.ssh_deploy_pull/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/opt/plutustoys/.ssh_deploy_pull/known_hosts"

set -e
cd /opt/plutustoys

echo "[FeedPipeline] $(date -u +'%Y-%m-%d %H:%M UTC') — старт повного циклу."

FAIL_REASON=""

python3 generate_google_feed.py || echo "[FeedPipeline] generate_google_feed.py провалився (best-effort, buyBox-кеш лишається попереднім)"
python3 generate_meta_feed.py || echo "[FeedPipeline] generate_meta_feed.py провалився (best-effort)"
python3 generate_bing_feed.py || echo "[FeedPipeline] generate_bing_feed.py провалився (best-effort)"

if ! python3 prom_competitor_pricer.py --apply; then
    FAIL_REASON="prom_competitor_pricer.py --apply провалився"
    echo "[FeedPipeline] ПОПЕРЕДЖЕННЯ: $FAIL_REASON — продовжую з наявним (не порожнім, персистентним) prom_competitor_price_state.json."
fi

python3 generate_eva_feed.py || echo "[FeedPipeline] generate_eva_feed.py провалився (best-effort, prep-only)"

# Rozetka навмисно ВІДСУТНЯ тут (2026-07-28, пряме рішення власниці) —
# лишається виключно на GH Actions (update-feeds.yml), щоб GH Actions і
# VPS ніколи не писали в той самий feeds/rozetka_feed.xml у feed-data.
# Див. докстрінг на початку файлу.

if ! python3 generate_prom_feed_top.py; then
    FAIL_REASON="${FAIL_REASON:+$FAIL_REASON; }generate_prom_feed_top.py провалився"
    echo "[FeedPipeline] ПОПЕРЕДЖЕННЯ: генерація prom_feed_top.xml провалилась — відновлюю останню робочу версію з feed-data (публічний репозиторій, читання без ключа) перед публікацією."
    git fetch origin feed-data 2>/dev/null || true
    git show origin/feed-data:feeds/prom_feed_top.xml > feeds/prom_feed_top.xml 2>/dev/null || true
fi

for f in feeds/prom_feed_top.xml; do
    if [ ! -s "$f" ]; then
        echo "[FeedPipeline] КРИТИЧНО: $f відсутній/порожній навіть після фолбеку — публікація ПРОПУЩЕНА цього прогону." >&2
        python3 feed_pipeline_report.py --status failed --reason "$f відсутній/порожній навіть після фолбеку з feed-data"
        python3 -c "
from telegram_notify import send_telegram_message
send_telegram_message('🚨 VPS feed-pipeline: $f відсутній/порожній навіть після фолбеку — публікація пропущена цього прогону.')
"
        exit 1
    fi
done

bash publish_feed_pipeline_vps.sh

if [ -z "$FAIL_REASON" ]; then
    python3 feed_pipeline_report.py --status ok
else
    python3 feed_pipeline_report.py --status degraded --reason "$FAIL_REASON"
fi

echo "[FeedPipeline] $(date -u +'%Y-%m-%d %H:%M UTC') — цикл завершено."
