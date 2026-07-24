#!/bin/bash
# run_prom_competitor_pricer_vps.sh — обгортка для systemd-таймера
# prom-competitor-pricer.timer (VPS). Замінює кроки, які раніше робив
# GH Actions job навколо самого python-виклику:
#
# 1. own_product_links_cache.json (джерело buyBox для find_best_competitor())
#    ПИШЕ лише generate_google_feed.py (GH Actions) — репрайсер його лише
#    читає (пасивно, live_lookup_extra/rotated_out шляхи не додають нових
#    записів). Тому тут — простий READ-ONLY публічний fetch найсвіжішої
#    копії з гілки feed-data (без git, без ключа — той самий раунд-тріп
#    працює для звичайного generate_prom_feed_top.py на GH Actions).
# 2. Сам прогін репрайсера (--apply завжди, як і плановий cron-режим
#    раніше — force/force-circuit-breaker лишаються ручними прапорцями
#    для одноразового виклику зі свого терміналу на VPS, не тут).
# 3. Публікація результату (prom_competitor_price_state.json +
#    prom_competitor_pricer_summary.md) у pricer-state-data —
#    publish_pricer_state.sh.
set -e
cd /opt/plutustoys

curl -sf --max-time 30 \
  "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/own_product_links_cache.json" \
  -o own_product_links_cache.json \
  || echo '{}' > own_product_links_cache.json

python3 prom_competitor_pricer.py --apply

bash publish_pricer_state.sh
