#!/bin/bash
# publish_vps_health_private.sh — публікує знімок стану VPS (vps_health.py) у ПРИВАТНУ гілку ops-health репо plutustoys-private-ledger
# (той самий репо й deploy-ключ, що publish_kodv_ledger_private.sh), щоб сесія «Код» бачила збої VPS без SSH (рішення власника 01.11.2026).
#
# ЧОМУ ПРИВАТНИЙ, а не feed-data: знімок містить тексти алертів (гривні, id замовлень); feed-data ПУБЛІЧНИЙ (витік cost/margin 10.08).
# ЧОМУ ОКРЕМА ГІЛКА ops-health + force-push: історія знімків не потрібна (це «зараз»), а фінансовий журнал у main лишається недоторканим.
# Викликається щогодини з run_eva_feed_vps.sh (вже існуючий погодинний таймер eva-feed — нового юніта на VPS не потрібно).
# Best-effort: збій тут НЕ валить EVA-цикл; його видно на десктопі як «знімок VPS застарілий» (health_check.py) — це й є сигнал.
cd /opt/plutustoys || exit 0
KEY_DIR=/opt/plutustoys/.ssh_private_ledger
if [ ! -f "$KEY_DIR/deploy_key" ]; then
    echo "[publish_vps_health_private] нема $KEY_DIR/deploy_key — публікацію пропущено."
    exit 0
fi
export GIT_SSH_COMMAND="ssh -i $KEY_DIR/deploy_key -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$KEY_DIR/known_hosts"
REPO="git@github.com:plutustoys-rgb/plutustoys-private-ledger.git"
BRANCH="ops-health"
OUT=/opt/plutustoys/reports/vps_health.json
WORKDIR=/opt/plutustoys/.vps_health_git

if ! timeout 120 /opt/plutustoys/venv/bin/python3 /opt/plutustoys/vps_health.py --out "$OUT"; then
    echo "[publish_vps_health_private] vps_health.py впав — публікацію пропущено."
    exit 0
fi

rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
cd "$WORKDIR" || exit 0
git init -q
git remote add origin "$REPO"
git config user.name "vps-health-bot"
git config user.email "vps-health-bot@users.noreply.github.com"
git checkout -q --orphan "$BRANCH"
cp "$OUT" vps_health.json
git add vps_health.json
git commit -q -m "VPS health $(date -u +'%Y-%m-%d %H:%M UTC')"
if timeout 120 git push -q --force origin "$BRANCH"; then
    echo "[publish_vps_health_private] знімок опубліковано (гілка $BRANCH)."
else
    echo "[publish_vps_health_private] push не вдався (мережа/ключ) — знімок не оновлено." >&2
fi
rm -rf "$WORKDIR"
exit 0
