#!/bin/bash
# run_feed_pipeline_vps.sh — VPS systemd-таймер (feed-pipeline.timer),
# ЗАМІНЮЄ весь ланцюжок генерації фідів + репрайсер, який раніше
# виконувався в .github/workflows/update-feeds.yml.
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
# КЛЮЧОВЕ СПРОЩЕННЯ проти update-feeds.yml: жодного кроку "Restore X
# from feed-data/scan-state-data/catalog-sync-delisted-data" тут немає.
# Усі стани (prom_competitor_price_state.json, full_catalog_scan_state.json,
# own_product_links_cache.json, rozetka_static_selection.json) просто
# лежать локально на диску VPS і природно переживають між прогонами —
# цей "round-trip через git-гілку лише тому, що кожен GH Actions
# runner стартує з чистого диска" був потрібен ЛИШЕ через ефемерність
# hosted-runner'а, якої тут більше немає.
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

# Той самий фолбек-принцип, що й update-feeds.yml: якщо preflight чи
# сама генерація провалюються, feeds/rozetka_feed.xml лишається
# останньою версією, яку встиг записати попередній успішний прогін —
# publish-крок нижче публікує те, що реально є на диску.
if python3 generate_rozetka_feed.py --preflight; then
    python3 generate_rozetka_feed.py
else
    echo "[FeedPipeline] Rozetka preflight провалився — feeds/rozetka_feed.xml НЕ перегенеровано, лишається попередня робоча версія."
fi

if ! python3 generate_prom_feed_top.py; then
    FAIL_REASON="${FAIL_REASON:+$FAIL_REASON; }generate_prom_feed_top.py провалився"
    echo "[FeedPipeline] ПОПЕРЕДЖЕННЯ: генерація prom_feed_top.xml провалилась — відновлюю останню робочу версію з feed-data (публічний репозиторій, читання без ключа) перед публікацією."
    git fetch origin feed-data 2>/dev/null || true
    git show origin/feed-data:feeds/prom_feed_top.xml > feeds/prom_feed_top.xml 2>/dev/null || true
fi

for f in feeds/rozetka_feed.xml feeds/prom_feed_top.xml; do
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
