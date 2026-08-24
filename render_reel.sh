#!/bin/bash
# render_reel.sh — рендер рілса зі спеки + хостинг на feed-data/media, друк IG-готового video_url.
#
# Прибирає ОСТАННЮ ручну дію (scp відео): одна команда замість «scp mp4 на VPS + рендер + хостинг».
# Фаза 4 порту рендеру рілсів. ПУБЛІКАЦІЮ В IG навмисно лишає ОКРЕМИМ явним кроком (авто-постинг
# публічного контенту за таймером — окремий дозвіл власника, не тут).
#
# ЗАПУСК (на VPS):
#   bash render_reel.sh reels_specs/my_reel.json
#   → рендерить reels/<out>.mp4, хостить у feed-data/media, друкує raw video_url + команду для IG.
#
# Спека — JSON (див. reels_specs/_TEMPLATE.json і докстрінг reel_build.py):
#   {out, endline, endscene, items:[{id, hook, name, spec:[stat,tail], image?}]}
set -euo pipefail

SPEC="${1:?Вжиток: bash render_reel.sh reels_specs/spec.json}"
[ -s "$SPEC" ] || { echo "[render_reel] спека порожня/відсутня: $SPEC" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PLUTUS_VENV_PY:-/opt/plutustoys/venv/bin/python3}"
[ -x "$PY" ] || PY="$(command -v python3)"

# 1. РЕНДЕР (reel_build сам звіряє наявність + matte-гейт ендсцени; недоплетений mp4 не лишає)
"$PY" "$HERE/reel_build.py" "$SPEC"

# ім'я виходу зі спеки
OUT="$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1],encoding='utf-8'))['out'])" "$SPEC")"
MP4="$HERE/reels/$OUT.mp4"
[ -s "$MP4" ] || { echo "[render_reel] рендер не дав файл: $MP4" >&2; exit 1; }

# 2. ХОСТИНГ у feed-data/media → публічний raw video_url (останній рядок publish_reel_video.sh)
URL="$(bash "$HERE/publish_reel_video.sh" "$MP4" | tail -1)"

echo "[render_reel] ✅ ГОТОВО."
echo "  відео:      $MP4"
echo "  IG video_url: $URL"
echo "  ДАЛІ (публікація в IG — ручний/окремий крок):"
echo "    $PY $HERE/social_auto_poster.py --reel \"$URL\" --caption-file <підпис.txt> --publish"
