#!/bin/bash
# activate_site_apache.sh — ОДНА команда активації власного сайту plutustoys.com.ua на VPS з Apache (Webuzo).
# Запускати як root (ідемпотентно, можна повторювати):
#   nohup bash /opt/plutustoys/deploy/activate_site_apache.sh > /var/log/activate_site.log 2>&1 &
#   tail -f /var/log/activate_site.log
#
# ЧОМУ НЕ activate_site.sh: той ставить nginx, а на :80/:443 цього VPS сидить Apache під Webuzo (як у Vartov-vhost'ів).
#
# ФАЗИ:
#  A. Перевірки (нічого не змінюють): модулі Apache, дефолтні vhost'и на явній IP, відсутність чужого vhost'а
#     plutustoys.com.ua, доступ Apache-користувача до /opt/plutustoys/site.
#  B. Збірка сайту + мапа 301 + systemd (site-order-api daemon, site-rebuild.timer).
#  C. HTTP-vhost (45.94.157.4:80): сайт по HTTP, перевіряється ЛОКАЛЬНО (curl --resolve), DNS ще не чіпаємо.
#  D. Чекає, поки DNS plutustoys.com.ua вкаже на цей VPS (до WAIT_DNS_MIN хв, за замовчуванням 180), далі certbot
#     (webroot, HTTP-01), далі HTTPS-vhost + редірект 80→443 + www→apex. Якщо DNS не встиг — повторний запуск
#     скрипта дойде до D.
# Відкат: кожна зміна Apache-конфіга → configtest → graceful → звірка, що всі vhost'и, які були ДО, лишились;
# інакше файл видаляється/відновлюється з бекапу. Дефолтний vhost Webuzo НЕ редагується.
set -euo pipefail

APP=/opt/plutustoys
PY="$APP/venv/bin/python3"
IP=45.94.157.4
DOMAIN=plutustoys.com.ua
WWW="www.$DOMAIN"
APACHECTL=/usr/local/apps/apache2/bin/apachectl
CONFD=/usr/local/apps/apache2/etc/conf.d
VHOST="$CONFD/z-plutustoys-com-ua.conf"      # z- префікс: вантажиться ПІСЛЯ webuzoVH.conf (урок 22.09)
WEBUZO_VH="$CONFD/webuzoVH.conf"
WEBROOT=/var/webuzo-data/www                  # той самий webroot ACME, що в прецеденті Vartov
MAPFILE="$APP/prom_redirects_apache.map"
WAIT_DNS_MIN="${WAIT_DNS_MIN:-180}"
LE_EMAIL="${LE_EMAIL:-plutustoys@gmail.com}"
STAMP=$(date +%s)

die() { echo "🚨 $*"; exit 1; }
[ "$(id -u)" = "0" ] || die "запускати як root"
[ -x "$APACHECTL" ] || die "нема $APACHECTL"
[ -x "$PY" ] || die "нема venv-python $PY"
[ -f "$APP/site_order_api.py" ] || die "нема $APP/site_order_api.py (чи підтягнувся master?)"

echo "=== A. ПЕРЕВІРКИ (read-only) ==="
MODS=$("$APACHECTL" -M 2>&1)
for m in rewrite_module proxy_module proxy_http_module; do
    grep -q "$m" <<< "$MODS" || die "Apache без $m — не можу ні проксіювати /api, ні робити 301. Нічого не змінено."
done
echo "✅ модулі rewrite/proxy/proxy_http завантажені"

[ -f "$WEBUZO_VH" ] || die "нема $WEBUZO_VH — не можу звірити дефолтні vhost'и"
for p in 80 443; do
    grep -q "<VirtualHost $IP:$p>" "$WEBUZO_VH" || die "дефолт Webuzo на :$p НЕ на явній IP $IP — наш vhost став би дефолтом для всього трафіку (клас пастки 22.09). Нічого не змінено."
done
echo "✅ дефолт Webuzo на :80 і :443 — на явній IP $IP; наші vhost'и йдуть тією ж групою"

OTHER=$(grep -rlE "ServerName[[:space:]]+(www\.)?$DOMAIN|ServerAlias[[:space:]].*$DOMAIN" "$CONFD" 2>/dev/null | grep -v "$VHOST" || true)
[ -z "$OTHER" ] || die "домен уже описаний в іншому файлі: $OTHER — розберись вручну, нічого не змінено."
echo "✅ чужих vhost'ів для $DOMAIN нема"

APUSER=$(ps -eo user:20,comm | awk '($2=="httpd"||$2=="apache2") && $1!="root"{print $1}' | sort -u | sed -n 1p)
[ -n "$APUSER" ] || die "не знайшов Apache-користувача (ps) — перевір, що Apache запущений"
echo "ℹ️  Apache працює від: $APUSER"

"$APACHECTL" -S > "/tmp/apache_vhosts_before.$STAMP" 2>&1 || die "apachectl -S не працює ДО змін — Apache уже нездоровий, зупиняюсь"
BEFORE=$(grep -E ":(80|443)[[:space:]]" "/tmp/apache_vhosts_before.$STAMP" || true)
echo "--- vhost'и на :80/:443 ДО:"; echo "$BEFORE"
[ -n "$BEFORE" ] || die "жодного рядка :80/:443 у apachectl -S — формат несподіваний"

echo ""
echo "=== B. ЗБІРКА + systemd ==="
"$PY" "$APP/site/build_site.py"
"$PY" "$APP/generate_prom_redirects.py" || echo "  (мапа редиректів не згенерована — перевір own_product_links_cache.json)"
[ -f "$MAPFILE" ] || printf '# порожня мапа (fallback)\n' > "$MAPFILE"
[ -f "$APP/site/index.html" ] || die "build_site.py не створив $APP/site/index.html"

can_read() { runuser -u "$APUSER" -- test -r "$APP/site/index.html" 2>/dev/null; }
if ! can_read; then
    echo "⚠️  $APUSER не читає $APP/site — додаю точковий ACL, не відкриваючи решту world-read"
    command -v setfacl >/dev/null 2>&1 || die "нема setfacl і Apache не читає $APP/site. Дай доступ вручну і запусти знову. Apache НЕ чіпано."
    setfacl -m "u:$APUSER:--x" "$APP"
    setfacl -R -m "u:$APUSER:r-X" "$APP/site"
    setfacl -m "u:$APUSER:r--" "$MAPFILE"
    can_read || die "навіть після ACL $APUSER не читає $APP/site — розбери вручну. Apache НЕ чіпано."
fi
echo "✅ $APUSER читає $APP/site"
runuser -u "$APUSER" -- test -r "$MAPFILE" 2>/dev/null || { command -v setfacl >/dev/null 2>&1 && setfacl -m "u:$APUSER:r--" "$MAPFILE"; }

cp "$APP/deploy/site-order-api.service" /etc/systemd/system/site-order-api.service
cp "$APP/deploy/site-rebuild.service" /etc/systemd/system/site-rebuild.service
cp "$APP/deploy/site-rebuild.timer" /etc/systemd/system/site-rebuild.timer
systemctl daemon-reload
systemctl enable --now site-order-api
systemctl enable --now site-rebuild.timer
sleep 2
systemctl is-active site-order-api >/dev/null || { journalctl -u site-order-api -n 30 --no-pager; die "site-order-api не запустився"; }
APICODE=$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "http://127.0.0.1:8901/api/np/city?q=%D0%9A%D0%B8%D1%97%D0%B2" || true)
echo "ℹ️  API локально: HTTP $APICODE (200 = автокомпліт НП працює)"

echo ""
echo "=== C/D. APACHE VHOST ==="
install -d -m 755 "$WEBROOT/.well-known/acme-challenge"

body_tpl() {
cat <<'EOF'
    DocumentRoot "@APP@/site"
    DirectoryIndex index.html
    <Directory "@APP@/site">
        Options -Indexes +FollowSymLinks
        AllowOverride None
        Require all granted
    </Directory>
    <IfModule mod_deflate.c>
        AddOutputFilterByType DEFLATE text/html text/css text/plain application/javascript application/json image/svg+xml
    </IfModule>
    RewriteEngine On
    RewriteMap promredir "txt:@MAPFILE@"
    RewriteRule ^/ua/p([0-9]+)- ${promredir:$1|/catalog.html} [R=301,L]
    ProxyPreserveHost On
    ProxyPass /api/ http://127.0.0.1:8901/api/ retry=0 timeout=30
    ProxyPassReverse /api/ http://127.0.0.1:8901/api/
EOF
}
acme_tpl() {
cat <<'EOF'
    Alias /.well-known/acme-challenge/ @WEBROOT@/.well-known/acme-challenge/
    <Directory "@WEBROOT@/.well-known/acme-challenge/">
        Options None
        AllowOverride None
        Require all granted
    </Directory>
EOF
}
sub() { sed -e "s#@APP@#$APP#g" -e "s#@MAPFILE@#$MAPFILE#g" -e "s#@WEBROOT@#$WEBROOT#g"; }

write_vhost() {   # $1 = http | https
    local mode="$1" tmp="$VHOST.new.$STAMP" bak="" lost=0 line
    {
        echo "# z-plutustoys-com-ua.conf — власний сайт plutustoys.com.ua (генерує deploy/activate_site_apache.sh, режим: $mode)."
        echo "# Явна IP (як у дефолтного vhost'а Webuzo) і z-префікс — НЕ міняти, інакше SNI-групи розійдуться."
        if [ "$mode" = "http" ]; then
            echo "<VirtualHost $IP:80>"
            echo "    ServerName $DOMAIN"
            echo "    ServerAlias $WWW"
            acme_tpl | sub
            body_tpl | sub
            echo "</VirtualHost>"
        else
            echo "<VirtualHost $IP:80>"
            echo "    ServerName $DOMAIN"
            echo "    ServerAlias $WWW"
            acme_tpl | sub
            echo "    RewriteEngine On"
            echo "    RewriteCond %{REQUEST_URI} !^/\\.well-known/acme-challenge/"
            echo "    RewriteRule ^/(.*)\$ https://$DOMAIN/\$1 [R=301,L]"
            echo "</VirtualHost>"
            echo ""
            echo "<VirtualHost $IP:443>"
            echo "    ServerName $DOMAIN"
            if [ "$HAVE_WWW_CERT" = "1" ]; then echo "    ServerAlias $WWW"; fi
            echo "    SSLEngine on"
            echo "    SSLCertificateFile /etc/letsencrypt/live/$DOMAIN/fullchain.pem"
            echo "    SSLCertificateKeyFile /etc/letsencrypt/live/$DOMAIN/privkey.pem"
            acme_tpl | sub
            echo "    RewriteEngine On"
            echo "    RewriteCond %{HTTP_HOST} ^www\\. [NC]"
            echo "    RewriteRule ^/(.*)\$ https://$DOMAIN/\$1 [R=301,L]"
            body_tpl | sub | grep -v '^    RewriteEngine On$'
            echo "</VirtualHost>"
        fi
    } > "$tmp"
    if [ -f "$VHOST" ]; then bak="$VHOST.bak.$STAMP"; cp "$VHOST" "$bak"; fi
    mv "$tmp" "$VHOST"
    restore() { if [ -n "$bak" ]; then cp "$bak" "$VHOST"; else rm -f "$VHOST"; fi; "$APACHECTL" -t && "$APACHECTL" graceful || true; }
    if ! "$APACHECTL" -t; then echo "🚨 configtest провалився — відкат"; restore; return 1; fi
    if ! "$APACHECTL" graceful; then echo "🚨 graceful провалився — відкат"; restore; return 1; fi
    sleep 1
    if ! "$APACHECTL" -S > "/tmp/apache_vhosts_after.$STAMP" 2>&1; then echo "🚨 apachectl -S після reload не працює — відкат"; restore; return 1; fi
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        grep -qF "$line" "/tmp/apache_vhosts_after.$STAMP" || { echo "🚨 зник vhost, що був ДО: $line"; lost=1; }
    done <<< "$BEFORE"
    if [ "$lost" = "1" ]; then echo "🚨 існуючі vhost'и змінились — відкат"; restore; return 1; fi
    echo "✅ vhost ($mode) застосовано, існуючі vhost'и неторкані"
}

HAVE_WWW_CERT=0
CERT_OK=0
[ -s "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ] && CERT_OK=1
cert_has_www() { local t; t=$(openssl x509 -in "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" -noout -text 2>/dev/null || true); grep -q "DNS:$WWW" <<< "$t"; }
if [ "$CERT_OK" = "1" ] && cert_has_www; then HAVE_WWW_CERT=1; fi

if [ "$CERT_OK" = "0" ]; then
    write_vhost http || die "HTTP-vhost не застосовано, Apache відкочено"
    echo ""
    echo "--- ЛОКАЛЬНА перевірка сайту через curl --resolve (DNS ще не чіпали) ---"
    HP=$(curl -sS --max-time 15 --resolve "$DOMAIN:80:$IP" -o /dev/null -w '%{http_code}' "http://$DOMAIN/" || true)
    HC=$(curl -sS --max-time 15 --resolve "$DOMAIN:80:$IP" -o /dev/null -w '%{http_code}' "http://$DOMAIN/catalog.html" || true)
    HA=$(curl -sS --max-time 15 --resolve "$DOMAIN:80:$IP" -o /dev/null -w '%{http_code}' "http://$DOMAIN/api/np/city?q=%D0%9A%D0%B8%D1%97%D0%B2" || true)
    HR=$(curl -sS --max-time 15 --resolve "$DOMAIN:80:$IP" -o /dev/null -w '%{http_code} -> %{redirect_url}' "http://$DOMAIN/ua/p1-test.html" || true)
    echo "  /            : $HP (очікую 200)"
    echo "  /catalog.html: $HC (очікую 200)"
    echo "  /api/np/city : $HA (очікую 200)"
    echo "  /ua/p1-test  : $HR (очікую 301 -> .../catalog.html)"
    [ "$HP" = "200" ] && [ "$HC" = "200" ] || die "сайт локально не віддається (/=$HP, /catalog.html=$HC). DNS НЕ переводити! Дивись: tail /usr/local/apps/apache2/logs/error_log"
    DEF=$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "http://$IP/" || true)
    echo "  контроль: голий IP (дефолт Webuzo) -> HTTP $DEF (як і раніше)"
    echo ""
    echo "✅ ФАЗА C ГОТОВА: сайт працює на VPS по HTTP. МОЖНА ПЕРЕВОДИТИ DNS: A plutustoys.com.ua та www -> $IP"
fi

echo ""
echo "=== D. ЧЕКАЮ DNS -> $IP (до $WAIT_DNS_MIN хв), далі certbot + HTTPS ==="
dns_ips() {
    local n="$1" r
    for r in 8.8.8.8 1.1.1.1; do
        if command -v dig >/dev/null 2>&1; then dig +short +time=3 +tries=1 A "$n" "@$r" 2>/dev/null || true
        elif command -v host >/dev/null 2>&1; then host -W 3 -t A "$n" "$r" 2>/dev/null | awk '/has address/{print $NF}' || true
        fi
    done
    getent ahostsv4 "$n" 2>/dev/null | awk '{print $1}' || true
}
dns_points_here() { local out; out=$(dns_ips "$1" || true); grep -qx "$IP" <<< "$out"; }

if [ "$CERT_OK" = "0" ]; then
    DEADLINE=$(( $(date +%s) + WAIT_DNS_MIN * 60 ))
    until dns_points_here "$DOMAIN"; do
        if [ "$(date +%s)" -ge "$DEADLINE" ]; then
            echo "⏳ DNS за $WAIT_DNS_MIN хв не перейшов. Сайт по HTTP працює; після зміни DNS запусти цей скрипт ще раз — він дійде до certbot."
            exit 0
        fi
        echo "$(date +%T) DNS $DOMAIN ще не $IP: $(dns_ips "$DOMAIN" | sort -u | tr '\n' ' ')"
        sleep 60
    done
    echo "✅ DNS $DOMAIN -> $IP"
    TOKEN="ptprobe-$STAMP"
    echo "$TOKEN" > "$WEBROOT/.well-known/acme-challenge/ptprobe"
    chmod 644 "$WEBROOT/.well-known/acme-challenge/ptprobe"
    GOT=""
    for i in 1 2 3 4 5; do
        GOT=$(curl -sS --max-time 10 "http://$DOMAIN/.well-known/acme-challenge/ptprobe" || true)
        [ "$GOT" = "$TOKEN" ] && break
        echo "  проба ACME ($i/5): отримано '${GOT:0:40}' — чекаю"; sleep 30
    done
    rm -f "$WEBROOT/.well-known/acme-challenge/ptprobe"
    [ "$GOT" = "$TOKEN" ] || die "ACME-проба не збіглась — certbot не запускаю (HTTP-сайт лишається). Перевір вручну."

    DOMS="-d $DOMAIN"
    if dns_points_here "$WWW"; then DOMS="$DOMS -d $WWW"; echo "ℹ️  www теж вказує сюди — додаю до сертифіката"; else echo "⚠️  www ще не вказує сюди — сертифікат лише на $DOMAIN (повтор скрипта додасть www)"; fi
    OKCERT=0
    for i in 1 2 3; do
        if certbot certonly --webroot -w "$WEBROOT" $DOMS --non-interactive --agree-tos -m "$LE_EMAIL" \
              --cert-name "$DOMAIN" --deploy-hook "$APACHECTL graceful"; then OKCERT=1; break; fi
        echo "certbot спроба $i/3 невдала — пауза 90с"; sleep 90
    done
    [ "$OKCERT" = "1" ] || die "certbot не видав сертифікат (HTTP-сайт лишається робочим). Дивись /var/log/letsencrypt/letsencrypt.log"
    CERT_OK=1
    if cert_has_www; then HAVE_WWW_CERT=1; fi
fi

if [ "$CERT_OK" = "1" ]; then
    if [ "$HAVE_WWW_CERT" = "0" ] && dns_points_here "$WWW"; then
        if certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" -d "$WWW" --non-interactive --agree-tos -m "$LE_EMAIL" \
              --cert-name "$DOMAIN" --deploy-hook "$APACHECTL graceful"; then HAVE_WWW_CERT=1; else echo "⚠️  www до сертифіката додати не вдалось"; fi
    fi
    write_vhost https || die "HTTPS-vhost не застосовано, Apache відкочено (HTTP-варіант міг лишитись — перевір)"
    echo ""
    echo "--- ПЕРЕВІРКА HTTPS ---"
    CERT_OUT=$(echo | openssl s_client -connect "$IP:443" -servername "$DOMAIN" 2>/dev/null | openssl x509 -noout -subject -issuer 2>&1)
    echo "$CERT_OUT"
    grep -q "Let's Encrypt" <<< "$CERT_OUT" || die "HTTPS віддає не Let's Encrypt — розбери вручну"
    DEFC=$(echo | openssl s_client -connect "$IP:443" 2>/dev/null | openssl x509 -noout -subject 2>&1)
    echo "контроль (без SNI, дефолт Webuzo): $DEFC"
    echo "  https /            : $(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "https://$DOMAIN/" || true)"
    echo "  https /api/np/city : $(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "https://$DOMAIN/api/np/city?q=%D0%9A%D0%B8%D1%97%D0%B2" || true)"
    echo "  http  / (редірект) : $(curl -sS --max-time 15 -o /dev/null -w '%{http_code} -> %{redirect_url}' "http://$DOMAIN/" || true)"
    echo ""
    echo "🎉 ГОТОВО: https://$DOMAIN працює. Далі (не тут): LiqPay-ключі в $APP/.env, SYSTEM_MAP, GMC."
fi
