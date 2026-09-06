#!/usr/bin/env bash
# Одноразова активація власного сайту plutustoys.com.ua на VPS. Запускати як root:
#   ssh -i ~/.ssh/plutustoys_vps root@45.94.157.4 "bash /opt/plutustoys/deploy/activate_site.sh"
#
# Робить автоматично: генерує статику (build_site.py), ставить+вмикає systemd-юніт
# site-order-api (:8901, лише localhost), ставить nginx (HTTP). Ідемпотентно — можна
# запускати повторно. DNS і TLS(certbot) — окремі ручні кроки (див. підказку в кінці).
# НЕ run-тестовано з цієї сесії (VPS недоступний) — вивід кроків покаже, де що.
set -euo pipefail

APP=/opt/plutustoys
PY="$APP/venv/bin/python3"

[ -x "$PY" ] || { echo "НЕМАЄ venv-python: $PY — перевір /opt/plutustoys/venv"; exit 1; }
[ -f "$APP/site_order_api.py" ] || { echo "НЕМАЄ $APP/site_order_api.py — чи підтягнувся master?"; exit 1; }

echo "==> 1/4 Генерую статичний сайт (build_site.py)"
"$PY" "$APP/site/build_site.py"

echo "==> 2/4 systemd-юніти (API + періодичний ребілд)"
cp "$APP/deploy/site-order-api.service" /etc/systemd/system/site-order-api.service
cp "$APP/deploy/site-rebuild.service" /etc/systemd/system/site-rebuild.service
cp "$APP/deploy/site-rebuild.timer" /etc/systemd/system/site-rebuild.timer
systemctl daemon-reload
systemctl enable --now site-order-api
systemctl enable --now site-rebuild.timer   # ребілд сайту 4×/день (свіжі ціни/наявність)
systemctl --no-pager --lines=0 status site-order-api || true
systemctl --no-pager --lines=0 status site-rebuild.timer || true

echo "==> 3/4 nginx (статика + проксі /api)"
if command -v nginx >/dev/null 2>&1; then
  cp "$APP/deploy/nginx-plutustoys.conf" /etc/nginx/sites-available/plutustoys.com.ua
  ln -sf /etc/nginx/sites-available/plutustoys.com.ua /etc/nginx/sites-enabled/plutustoys.com.ua
  if nginx -t; then
    systemctl reload nginx 2>/dev/null || systemctl restart nginx
  else
    echo "  nginx -t не пройшов — конфіг НЕ активовано, перевір вручну"
  fi
else
  echo "  nginx не встановлено. Постав: apt install -y nginx  — і повтори цей скрипт."
fi

echo "==> 4/4 Перевірка API (localhost)"
sleep 1
curl -sS --max-time 15 -o /dev/null -w "  api/np/city → HTTP %{http_code}\n" \
  "http://127.0.0.1:8901/api/np/city?q=%D0%9A%D0%B8%D1%97%D0%B2" \
  || echo "  API не відповів — дивись: journalctl -u site-order-api -n 40 --no-pager"

echo
echo "ГОТОВО (HTTP-рівень). Далі вручну:"
echo "  * DNS: A-запис plutustoys.com.ua і www -> 45.94.157.4 (у реєстратора домену)"
echo "  * TLS: certbot --nginx -d plutustoys.com.ua -d www.plutustoys.com.ua   (після того, як DNS резолвиться)"
echo "  * LiqPay бойовий: у $APP/.env додати LIQPAY_PUBLIC_KEY / LIQPAY_PRIVATE_KEY + LIQPAY_SANDBOX=0,"
echo "    потім: systemctl restart site-order-api"
