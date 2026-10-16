#!/bin/bash
# install_restart_watcher.sh — ОДНОРАЗОВО на VPS (root): ставить restart-if-changed.service/.timer, вмикає таймер і одразу запускає перевірку (перший запуск перезапускає бота з новим кодом).
set -euo pipefail
APP=/opt/plutustoys
cp "$APP/deploy/restart-if-changed.service" /etc/systemd/system/restart-if-changed.service
cp "$APP/deploy/restart-if-changed.timer" /etc/systemd/system/restart-if-changed.timer
systemctl daemon-reload
systemctl enable --now restart-if-changed.timer
systemctl start restart-if-changed.service
journalctl -u restart-if-changed.service -n 5 --no-pager | cut -c1-200
systemctl is-active site-chat-bot
systemctl list-timers --no-pager | grep restart-if-changed | cut -c1-150
