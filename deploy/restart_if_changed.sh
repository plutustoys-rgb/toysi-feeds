#!/bin/bash
# restart_if_changed.sh — перезапускає site-chat-bot, коли змінився site_chat_bot.py (автосинхронізація кладе код, але довгі служби не перезапускає).
# Викликає restart-if-changed.timer кожні 5 хв. Перший запуск (нема збереженого хешу) перезапускає службу. Збій підйому → хеш НЕ зберігається (повтор через 5 хв) + Telegram-алерт.
# ЛИШЕ бот «Написати». Приймання замовлень (site-order-api, оплата) тут свідомо немає: його перезапускає людина.
set -u
APP=/opt/plutustoys
STATE=/var/lib/plutustoys-restart
mkdir -p "$STATE"

check() {
    local svc="$1" file="$2" new old
    new=$(sha256sum "$APP/$file" 2>/dev/null | cut -d' ' -f1)
    [ -n "$new" ] || { echo "нема $APP/$file"; return 1; }
    old=$(cat "$STATE/$svc.sha" 2>/dev/null || true)
    [ "$new" = "$old" ] && return 0
    if ! "$APP/venv/bin/python3" -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$APP/$file"; then
        echo "🚨 синтаксична помилка в $file: $svc НЕ перезапущено"
        return 1
    fi
    systemctl restart "$svc"
    sleep 4
    if systemctl is-active --quiet "$svc"; then
        echo "$new" > "$STATE/$svc.sha"
        echo "✅ $svc перезапущено (новий $file ${new:0:12})"
    else
        echo "🚨 $svc не піднялась після оновлення $file"
        ( cd "$APP" && "$APP/venv/bin/python3" -c 'import sys; from telegram_notify import send_throttled_alert; send_throttled_alert("restart_if_changed_" + sys.argv[1], "⚠️ " + sys.argv[1] + " не піднялась після оновлення коду (restart_if_changed.sh): journalctl -u " + sys.argv[1], cooldown_sec=6*3600)' "$svc" ) || true
        return 1
    fi
}

check site-chat-bot site_chat_bot.py
