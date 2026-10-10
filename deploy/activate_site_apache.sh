#!/bin/bash
# activate_site_apache.sh — ОДНА команда активації власного сайту plutustoys.com.ua на VPS з Apache (Webuzo).
# Запускати як root (ідемпотентно, можна повторювати):
#   nohup bash /opt/plutustoys/deploy/activate_site_apache.sh > /var/log/activate_site.log 2>&1 &
#   tail -f /var/log/activate_site.log
#
# ЧОМУ НЕ activate_site.sh: той ставить nginx, а на :80/:443 цього VPS сидить Apache під Webuzo (як у Vartov-vhost'ів).
#
# ФАЗИ:
#  A. Перевірки (нічого не змінюють): інструменти, модулі Apache, дефолтні vhost'и на явній IP, відсутність чужого vhost'а
#     plutustoys.com.ua, доступ Apache-користувача до /opt/plutustoys/site (і НЕДОСТУП до .env/orders.db).
#  B. Збірка сайту + мапа 301 (з перевіркою, що вона не порожня) + systemd (site-order-api daemon, site-rebuild.timer).
#  C. HTTP-vhost (45.94.157.4:80): сайт по HTTP, перевіряється ЛОКАЛЬНО (curl --resolve), DNS ще не чіпаємо.
#  D. Чекає, поки DNS plutustoys.com.ua вкаже ТІЛЬКИ на цей VPS (A згодом усі публічні резолвери, AAAA немає) — до WAIT_DNS_MIN
#     хв (180), далі certbot (webroot, HTTP-01), HTTPS-vhost + редірект 80→443 + www→apex. Якщо DNS не встиг — вихід з кодом 2;
#     повторний запуск дійде до D.
# Відкат: кожна зміна Apache → configtest → graceful → (а) множини «ім'я+файл» усіх vhost'ів ДО ⊆ ПІСЛЯ, default server
#   ідентичний, (б) поведінкові проби (голий IP, shopify, vartov.app, сертифікати без SNI/з SNI) ІДЕНТИЧНІ до/після.
#   Інакше файл відновлюється з бекапу і Apache перезавантажується. Дефолтний vhost Webuzo НЕ редагується.
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
MIN_MAP_LINES="${MIN_MAP_LINES:-500}"
WAIT_DNS_MIN="${WAIT_DNS_MIN:-180}"
LE_EMAIL="${LE_EMAIL:-plutustoys@gmail.com}"
STAMP=$(date +%s)
TMPD=$(mktemp -d /tmp/activate_site.XXXXXX)

die() { echo "🚨 $*"; exit 1; }
[ "$(id -u)" = "0" ] || die "запускати як root"
exec 9>/var/lock/activate_site.lock
flock -n 9 || die "інший екземпляр activate_site_apache.sh уже працює"
cd "$APP" || die "нема $APP"
[ -x "$APACHECTL" ] || die "нема $APACHECTL"
[ -x "$PY" ] || die "нема venv-python $PY"
[ -f "$APP/site_order_api.py" ] || die "нема $APP/site_order_api.py (чи підтягнувся master?)"

echo "=== A. ПЕРЕВІРКИ (read-only) ==="
for c in curl openssl certbot awk sed sort comm su flock pgrep ss xargs timeout; do command -v "$c" >/dev/null 2>&1 || die "нема команди '$c' — нічого не змінено"; done
MODS=$("$APACHECTL" -M 2>&1)
for m in rewrite_module proxy_module proxy_http_module alias_module ssl_module; do
    grep -q "$m" <<< "$MODS" || die "Apache без $m — нічого не змінено."
done
echo "✅ інструменти й модулі rewrite/proxy/proxy_http/alias/ssl є"

[ -f "$WEBUZO_VH" ] || die "нема $WEBUZO_VH — не можу звірити дефолтні vhost'и"
for p in 80 443; do
    grep -q "<VirtualHost $IP:$p>" "$WEBUZO_VH" || die "дефолт Webuzo на :$p НЕ на явній IP $IP — наш vhost став би дефолтом для всього трафіку (клас пастки 22.09). Нічого не змінено."
done
echo "✅ дефолт Webuzo на :80 і :443 — на явній IP $IP; наші vhost'и йдуть тією ж групою"

DOM_RE=${DOMAIN//./\\.}
OTHER=$(grep -rlE "^[[:space:]]*(ServerName|ServerAlias)[[:space:]]+([^#]*[[:space:]])?(www\.)?${DOM_RE}([[:space:]]|\$)" "$CONFD" 2>/dev/null | grep -v "$VHOST" || true)
[ -z "$OTHER" ] || die "домен уже описаний в іншому файлі: $OTHER — розберись вручну, нічого не змінено."
echo "✅ чужих vhost'ів для $DOMAIN нема"

APUSER=$(ps -eo user:20,comm | awk '($2=="httpd"||$2=="apache2") && $1!="root"{print $1}' | sort -u | sed -n 1p)
[ -n "$APUSER" ] || die "не знайшов Apache-користувача (ps) — перевір, що Apache запущений"
echo "ℹ️  Apache працює від: $APUSER"

# ── знімки стану Apache (множини «ім'я файл» БЕЗ номерів рядків + поведінкові проби) ──
vh_pairs() { "$APACHECTL" -S 2>&1 | sed -nE 's/.*[[:space:]]([^ ]+) \((\/[^:)]+):[0-9]+\)$/\1 \2/p' | sort -u; }
# «default» = рядок "default server …" (розгорнута група) АБО компактний "ip:port  host (file:line)" (група з одним vhost)
vh_defaults() { "$APACHECTL" -S 2>&1 | grep -E 'default server|^[[:space:]]*[0-9.*]+:(80|443)[[:space:]]+[^ ]+ \(' | sed -nE 's/.*[[:space:]]([^ ]+) \((\/[^:)]+):[0-9]+\)$/\1 \2/p' | sort -u || true; }
cert_subject() {   # $1 = SNI-ім'я або порожньо
    local out
    out=$(echo | timeout 10 openssl s_client -connect "$IP:443" ${1:+-servername "$1"} 2>/dev/null | openssl x509 -noout -subject -issuer 2>/dev/null) || out="NOCERT"
    echo "${out:-NOCERT}" | tr '\n' ' '
}
code() { curl -sS --max-time 10 -o /dev/null -w '%{http_code}' "$@" 2>/dev/null || echo "ERR"; }
behavior() {
    echo "bare80=$(code "http://$IP/")"
    echo "shopify80=$(code -H 'Host: shopify.plutustoys.com.ua' "http://$IP/")"
    echo "vartov80=$(code -H 'Host: vartov.app' "http://$IP/")"
    echo "cert_nosni=$(cert_subject '')"
    echo "cert_shopify=$(cert_subject shopify.plutustoys.com.ua)"
    echo "cert_vartov=$(cert_subject vartov.app)"
}
snapshot() {   # $1 = префікс файлів
    vh_pairs > "$TMPD/$1.pairs"
    vh_defaults > "$TMPD/$1.defaults"
    behavior > "$TMPD/$1.behavior"
}
"$APACHECTL" -S >/dev/null 2>&1 || die "apachectl -S не працює ДО змін — Apache уже нездоровий, зупиняюсь"
snapshot start
[ -s "$TMPD/start.pairs" ] || die "apachectl -S не дав жодної пари «ім'я файл» — формат несподіваний, зупиняюсь"
echo "--- vhost'и ДО (ім'я файл):"; cat "$TMPD/start.pairs"
echo "--- поведінка ДО:"; cat "$TMPD/start.behavior"

echo ""
echo "=== B. ЗБІРКА + systemd ==="
"$PY" "$APP/site/build_site.py"
"$PY" "$APP/generate_prom_redirects.py" || echo "  (генератор мапи редиректів завершився з помилкою)"
[ -f "$APP/site/index.html" ] || die "build_site.py не створив $APP/site/index.html"
MAPLINES=$(grep -c '^[0-9]' "$MAPFILE" 2>/dev/null || true)
MAPLINES=${MAPLINES:-0}
echo "ℹ️  рядків у мапі 301: $MAPLINES"
if [ "$MAPLINES" -lt "$MIN_MAP_LINES" ] && [ "${ALLOW_SMALL_MAP:-0}" != "1" ]; then
    die "мапа 301 має лише $MAPLINES рядків (< $MIN_MAP_LINES): own_product_links_cache.json порожній/відсутній? Усі старі Prom-URL пішли б 301 на /catalog.html, а Google кешує 301. Apache НЕ чіпано. (Свідомо? ALLOW_SMALL_MAP=1)"
fi

can_read() { su -s /bin/sh "$APUSER" -c "test -r '$1'" 2>/dev/null; }
if ! can_read "$APP/site/index.html"; then
    echo "⚠️  $APUSER не читає $APP/site — додаю точковий ACL (лише traverse на $APP + читання site/)"
    command -v setfacl >/dev/null 2>&1 || die "нема setfacl і Apache не читає $APP/site. Дай доступ вручну і запусти знову. Apache НЕ чіпано."
    setfacl -m "u:$APUSER:--x" "$APP"
    setfacl -R -m "u:$APUSER:r-X" "$APP/site"
    setfacl -R -d -m "u:$APUSER:r-X" "$APP/site"
    can_read "$APP/site/index.html" || die "навіть після ACL $APUSER не читає $APP/site — розбери вручну. Apache НЕ чіпано."
    for f in "$APP/.env" "$APP/orders.db"; do
        if [ -e "$f" ] && can_read "$f"; then
            setfacl -x "u:$APUSER" "$APP" || true
            die "ПІСЛЯ ACL $APUSER читає $f (мав би бути недоступний) — ACL знято, Apache НЕ чіпано. Закрий права на файл вручну."
        fi
    done
fi
if [ ! -e "$MAPFILE" ] || ! can_read "$MAPFILE"; then
    command -v setfacl >/dev/null 2>&1 && setfacl -m "u:$APUSER:r--" "$MAPFILE" || true
    can_read "$MAPFILE" || die "$APUSER не читає $MAPFILE — Apache НЕ чіпано"
fi
echo "✅ $APUSER читає $APP/site і мапу"
for f in "$APP/.env" "$APP/orders.db"; do
    if [ -e "$f" ] && can_read "$f"; then echo "⚠️  УВАГА: $APUSER може читати $f ще ДО нас (існуюче право файла) — не наша зміна, але закрий: chmod o-r"; fi
done

if ! systemctl is-active --quiet site-order-api; then
    if ss -ltn 2>/dev/null | grep -q '127.0.0.1:8901 \|:8901 '; then die "порт 8901 уже зайнятий не нашим site-order-api — розбери вручну"; fi
fi
cp "$APP/deploy/site-order-api.service" /etc/systemd/system/site-order-api.service
cp "$APP/deploy/site-rebuild.service" /etc/systemd/system/site-rebuild.service
cp "$APP/deploy/site-rebuild.timer" /etc/systemd/system/site-rebuild.timer
systemctl daemon-reload
systemctl enable --now site-order-api
systemctl enable --now site-rebuild.timer
systemctl restart site-rebuild.timer || echo "⚠️  не вдалося перезапустити site-rebuild.timer — перезапусти вручну"   # підхопити ЗМІНЕНИЙ OnCalendar (enable --now на вже активному таймері розклад не оновлює)
sleep 2
systemctl is-active site-order-api >/dev/null || { journalctl -u site-order-api -n 30 --no-pager; die "site-order-api не запустився"; }
APICODE=$(code "http://127.0.0.1:8901/api/np/city?q=%D0%9A%D0%B8%D1%97%D0%B2")
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
    <FilesMatch "\.(py|pyc|md|sh)$">
        Require all denied
    </FilesMatch>
    <DirectoryMatch "/__pycache__">
        Require all denied
    </DirectoryMatch>
    <IfModule mod_headers.c>
        # заголовки безпеки (10.10.2026, зауваження Тестувальника): сторінки збирають ПІБ/телефон/адресу. CSP свідомо НЕ ставимо: у сторінках inline-скрипти й GA4/Pixel — спершу Report-Only окремим кроком.
        Header always set X-Content-Type-Options "nosniff"
        Header always set X-Frame-Options "SAMEORIGIN"
        Header always set Referrer-Policy "strict-origin-when-cross-origin"
        # статика змінюється рідко, імена без хешів → добовий кеш (LCP/повторні візити); HTML кешується браузером за замовчуванням евристикою, не чіпаємо
        <LocationMatch "^/assets/">
            Header set Cache-Control "public, max-age=86400"
        </LocationMatch>
    </IfModule>
    <IfModule mod_deflate.c>
        AddOutputFilterByType DEFLATE text/html text/css text/plain application/javascript application/json image/svg+xml
    </IfModule>
    RewriteEngine On
    RewriteMap promredir "txt:@MAPFILE@"
    # старі Prom-URL (укр. і рос. версії; SEO-замовлення 2026-10-06, GSC: 5 583 URL у 404) → наша картка / каталог (301)
    # 09.10.2026: мовний префікс ОПЦІЙНИЙ — Google (GMC «Интернет-магазин», «Не указана цена» 1 564) тримає й старі Prom-URL БЕЗ /ua/ (/p3138857147-….html → було 404). Наші сторінки (/product-…, /catalog.html…) цим правилам не відповідають.
    RewriteRule ^/(?:(?:ua|ru)/)?p([0-9]+)- ${promredir:$1|/catalog.html} [R=301,L]
    RewriteRule ^/(?:(?:ua|ru)/)?g[0-9]+- /catalog.html [R=301,L]
    RewriteRule ^/(?:(?:ua|ru)/)?product_list(?:/|$) /catalog.html [R=301,L]
    RewriteRule ^/(?:(?:ua|ru)/)?site_contacts(?:\.html)?$ /contacts.html [R=301,L]
    RewriteRule ^/(?:(?:ua|ru)/)?site_ / [R=301,L]
    # 10.10.2026 (SEO п.4): браузери/боти просять ці URL у корені — віддаємо наші файли внутрішнім rewrite (не редірект)
    RewriteRule ^/favicon\.ico$ /assets/favicon.ico [L]
    RewriteRule ^/apple-touch-icon(?:-precomposed)?\.png$ /assets/apple-touch-icon.png [L]
    RewriteRule ^/(?:ua|ru)/?$ / [R=301,L]
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

render_vhost() {   # $1 = http | https  → у stdout
    local mode="$1"
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
        echo "    <IfModule mod_headers.c>"
        echo "        Header always set Strict-Transport-Security \"max-age=604800\""
        echo "    </IfModule>"
        acme_tpl | sub
        echo "    RewriteEngine On"
        echo "    RewriteCond %{HTTP_HOST} ^www\\. [NC]"
        echo "    RewriteRule ^/(.*)\$ https://$DOMAIN/\$1 [R=301,L]"
        body_tpl | sub | grep -v '^    RewriteEngine On$'
        echo "</VirtualHost>"
    fi
}

apache_alive() { pgrep -x httpd >/dev/null 2>&1 || pgrep -x apache2 >/dev/null 2>&1; }

write_vhost() {   # $1 = http | https
    local mode="$1"
    local tmp="$VHOST.new.$STAMP" bak=""
    local tag="w${mode}$(date +%s)"   # окремим рядком: у одному `local a=.. b=$a` під set -u bash дає «unbound variable» (знайдено першим живим запуском 05.10)
    render_vhost "$mode" > "$tmp" || { rm -f "$tmp"; return 1; }
    snapshot "$tag.pre"           # СВІЖИЙ знімок прямо перед зміною (не з початку скрипта)
    [ -s "$TMPD/$tag.pre.pairs" ] || { echo "🚨 знімок ПЕРЕД зміною порожній — не чіпаю"; rm -f "$tmp"; return 1; }
    if [ -f "$VHOST" ]; then bak="$VHOST.bak.$STAMP.$mode"; cp "$VHOST" "$bak" || { echo "🚨 не вдалось зробити бекап — не чіпаю"; rm -f "$tmp"; return 1; }; fi
    mv "$tmp" "$VHOST"
    restore() {
        echo "↩️  ВІДКАТ"
        if [ -n "$bak" ]; then cp "$bak" "$VHOST"; else rm -f "$VHOST"; fi
        if "$APACHECTL" -t && "$APACHECTL" graceful; then echo "↩️  відкат застосовано (configtest+graceful ОК)"; else echo "🚨🚨🚨 ВІДКАТ НЕ ЗАСТОСУВАВСЯ — Apache може бути зламаний. Перевір apachectl -t / -S НЕГАЙНО."; fi
    }
    if ! "$APACHECTL" -t; then echo "🚨 configtest провалився"; restore; return 1; fi
    if ! "$APACHECTL" graceful; then echo "🚨 graceful провалився"; restore; return 1; fi
    sleep 3
    apache_alive || { echo "🚨 процес Apache не живий після graceful"; restore; return 1; }
    # поведінкова проба може миготіти відразу після graceful (gunicorn, повільний reload) — до 3 спроб
    local try
    for try in 1 2 3; do
        snapshot "$tag.post"
        cmp -s "$TMPD/$tag.pre.behavior" "$TMPD/$tag.post.behavior" && break
        echo "ℹ️  поведінка ще не збіглась (спроба $try/3) — чекаю"; sleep 4
    done
    local bad=0
    [ -s "$TMPD/$tag.post.pairs" ] || { echo "🚨 apachectl -S після зміни порожній"; bad=1; }
    if [ -n "$(comm -23 "$TMPD/$tag.pre.pairs" "$TMPD/$tag.post.pairs")" ]; then
        echo "🚨 зникли vhost'и («ім'я файл»), що були ДО:"; comm -23 "$TMPD/$tag.pre.pairs" "$TMPD/$tag.post.pairs"; bad=1
    fi
    if ! cmp -s "$TMPD/$tag.pre.defaults" "$TMPD/$tag.post.defaults"; then
        echo "🚨 змінився default server:"; diff "$TMPD/$tag.pre.defaults" "$TMPD/$tag.post.defaults" || true; bad=1
    fi
    if ! grep -q "^$DOMAIN " "$TMPD/$tag.post.pairs"; then echo "🚨 нашого vhost'а ($DOMAIN) нема в apachectl -S"; bad=1; fi
    if [ "$mode" = "https" ]; then   # пара «ім'я файл» не доводить, що зареєстровано саме :443 — перевіряємо сертифікат живцем
        case "$(cert_subject "$DOMAIN")" in *"Let's Encrypt"*) ;; *) echo "🚨 :443 для $DOMAIN не віддає Let's Encrypt"; bad=1;; esac
    fi
    if ! cmp -s "$TMPD/$tag.pre.behavior" "$TMPD/$tag.post.behavior"; then
        echo "🚨 ПОВЕДІНКА чужих сайтів/дефолту змінилась:"; diff "$TMPD/$tag.pre.behavior" "$TMPD/$tag.post.behavior" || true; bad=1
    fi
    if [ "$bad" = "1" ]; then restore; return 1; fi
    echo "✅ vhost ($mode) застосовано; чужі vhost'и, default server і поведінка (shopify, vartov.app, сертифікати) — без змін"
}

cert_has_www() { local t; t=$(openssl x509 -in "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" -noout -text 2>/dev/null || true); grep -q "DNS:$WWW" <<< "$t"; }
HAVE_WWW_CERT=0
CERT_OK=0
[ -s "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ] && CERT_OK=1
if [ "$CERT_OK" = "1" ] && cert_has_www; then HAVE_WWW_CERT=1; fi

if [ "$CERT_OK" = "0" ]; then
    write_vhost http || die "HTTP-vhost не застосовано, Apache відкочено"
    echo ""
    echo "--- ЛОКАЛЬНА перевірка сайту через curl --resolve (DNS ще не чіпали) ---"
    R="--resolve $DOMAIN:80:$IP"
    HP=$(code $R "http://$DOMAIN/")
    HC=$(code $R "http://$DOMAIN/catalog.html")
    HA=$(code $R "http://$DOMAIN/api/np/city?q=%D0%9A%D0%B8%D1%97%D0%B2")
    HX=$(code $R "http://$DOMAIN/build_site.py")
    # справжній prom_id із мапи → має дати 301 на /product-<id>.html
    MAPKEY=$(awk '/^[0-9]/{print $1; exit}' "$MAPFILE")
    MAPVAL=$(awk '/^[0-9]/{print $2; exit}' "$MAPFILE")
    HR=$(curl -sS --max-time 10 $R -o /dev/null -w '%{http_code} %{redirect_url}' "http://$DOMAIN/ua/p${MAPKEY}-test.html" 2>/dev/null || echo "ERR")
    HR0=$(curl -sS --max-time 10 $R -o /dev/null -w '%{http_code} %{redirect_url}' "http://$DOMAIN/ua/p1-test.html" 2>/dev/null || echo "ERR")
    echo "  /                  : $HP (очікую 200)"
    echo "  /catalog.html      : $HC (очікую 200)"
    echo "  /api/np/city       : $HA (очікую 200)"
    echo "  /build_site.py     : $HX (очікую 403/404, не 200)"
    echo "  /ua/p${MAPKEY}-test: $HR (очікую 301 …$MAPVAL)"
    echo "  /ua/p1-test        : $HR0 (очікую 301 …/catalog.html)"
    [ "$HP" = "200" ] && [ "$HC" = "200" ] || die "сайт локально не віддається (/=$HP, /catalog.html=$HC). DNS НЕ переводити! Дивись: tail /usr/local/apps/apache2/logs/error_log"
    [ "$HA" = "200" ] || die "/api/np/city=$HA — API не проксіюється. DNS НЕ переводити! (journalctl -u site-order-api)"
    [ "$HX" != "200" ] || die "build_site.py віддається по HTTP (DocumentRoot) — DNS НЕ переводити!"
    case "$HR" in "301 "*"$MAPVAL") ;; *) die "301 з мапи не працює ($HR) — DNS НЕ переводити!";; esac
    case "$HR0" in "301 "*"/catalog.html") ;; *) die "непокритий Prom-URL не веде на /catalog.html ($HR0) — DNS НЕ переводити!";; esac
    echo ""
    echo "✅ ФАЗА C ГОТОВА: сайт працює на VPS по HTTP (чужі vhost'и не зачеплено). МОЖНА ПЕРЕВОДИТИ DNS:"
    echo "   у кабінеті Prom: A для @ та www -> $IP, AAAA (якщо є) — видалити. Онлайн-оплата (LiqPay) ще не підключена: prepaid покаже"
    echo "   повідомлення «оберіть оплату при отриманні»; накладений платіж працює."
fi

echo ""
echo "=== D. ЧЕКАЮ DNS -> $IP (до $WAIT_DNS_MIN хв), далі certbot + HTTPS ==="
resolvers_a() {   # друкує рядки "resolver<TAB>ip-список" для A (кожен публічний резолвер окремо)
    local n="$1" r out
    for r in 8.8.8.8 1.1.1.1; do
        if command -v dig >/dev/null 2>&1; then out=$(dig +short +time=3 +tries=1 A "$n" "@$r" 2>/dev/null | grep -E '^[0-9.]+$' | sort -u | tr '\n' ' ' || true)
        elif command -v host >/dev/null 2>&1; then out=$(host -W 3 -t A "$n" "$r" 2>/dev/null | awk '/has address/{print $NF}' | sort -u | tr '\n' ' ' || true)
        else out="NOTOOL"; fi
        echo "$r $out"
    done
}
has_aaaa() {
    local n="$1" r out
    for r in 8.8.8.8 1.1.1.1; do
        if command -v dig >/dev/null 2>&1; then out=$(dig +short +time=3 +tries=1 AAAA "$n" "@$r" 2>/dev/null | grep -cE '^[0-9a-fA-F:]+:[0-9a-fA-F:]*$' || true); [ "${out:-0}" != "0" ] && return 0
        fi
    done
    return 1
}
dns_points_here() {   # ТІЛЬКИ наш IP у кожного публічного резолвера і жодного AAAA
    local n="$1" line ok=1
    while IFS= read -r line; do
        [ "$(awk '{print $2}' <<< "$line")" = "NOTOOL" ] && return 1
        [ "$(awk '{$1=""; print $0}' <<< "$line" | xargs)" = "$IP" ] || ok=0
    done < <(resolvers_a "$n")
    [ "$ok" = "1" ] || return 1
    ! has_aaaa "$n"
}

if [ "$CERT_OK" = "0" ]; then
    command -v dig >/dev/null 2>&1 || command -v host >/dev/null 2>&1 || die "нема ні dig, ні host — не можу перевірити DNS; HTTP-сайт працює. Встанови bind-utils і запусти знову."
    DEADLINE=$(( $(date +%s) + WAIT_DNS_MIN * 60 ))
    until dns_points_here "$DOMAIN"; do
        if [ "$(date +%s)" -ge "$DEADLINE" ]; then
            echo "⏳ DNS за $WAIT_DNS_MIN хв не перейшов. Сайт по HTTP працює; після зміни DNS запусти цей скрипт ще раз — він дійде до certbot."
            exit 2
        fi
        echo "$(date +%T) DNS $DOMAIN ще не тільки $IP: $(resolvers_a "$DOMAIN" | tr '\n' ';') AAAA=$(has_aaaa "$DOMAIN" && echo є || echo нема)"
        sleep 60
    done
    echo "✅ DNS $DOMAIN -> тільки $IP (усі публічні резолвери, AAAA немає)"
    TOKEN="ptprobe-$STAMP"
    echo "$TOKEN" > "$WEBROOT/.well-known/acme-challenge/ptprobe"
    chmod 644 "$WEBROOT/.well-known/acme-challenge/ptprobe"
    GOT=""
    for i in 1 2 3; do
        GOT=$(curl -sS --max-time 10 --resolve "$DOMAIN:80:$IP" "http://$DOMAIN/.well-known/acme-challenge/ptprobe" 2>/dev/null || true)
        [ "$GOT" = "$TOKEN" ] && break
        echo "  проба ACME-шляху ($i/3): отримано '${GOT:0:40}' — чекаю"; sleep 20
    done
    rm -f "$WEBROOT/.well-known/acme-challenge/ptprobe"
    [ "$GOT" = "$TOKEN" ] || die "ACME-шлях не віддає токен — certbot не запускаю (HTTP-сайт лишається). Перевір вручну."

    DOMS="-d $DOMAIN"
    if dns_points_here "$WWW"; then DOMS="$DOMS -d $WWW"; echo "ℹ️  www теж вказує сюди — додаю до сертифіката"; else echo "⚠️  www ще не вказує (тільки) сюди — сертифікат лише на $DOMAIN (повтор скрипта додасть www)"; fi
    OKCERT=0
    for i in 1 2 3; do
        # shellcheck disable=SC2086
        if certbot certonly --webroot -w "$WEBROOT" $DOMS --non-interactive --agree-tos -m "$LE_EMAIL" \
              --cert-name "$DOMAIN" --expand --deploy-hook "$APACHECTL graceful"; then OKCERT=1; break; fi
        echo "certbot спроба $i/3 невдала — пауза 90с"; sleep 90
    done
    [ "$OKCERT" = "1" ] || die "certbot не видав сертифікат (HTTP-сайт лишається робочим). Дивись /var/log/letsencrypt/letsencrypt.log"
    CERT_OK=1
    if cert_has_www; then HAVE_WWW_CERT=1; fi
fi

if [ "$CERT_OK" = "1" ]; then
    if [ "$HAVE_WWW_CERT" = "0" ] && dns_points_here "$WWW"; then
        if certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" -d "$WWW" --non-interactive --agree-tos -m "$LE_EMAIL" \
              --cert-name "$DOMAIN" --expand --deploy-hook "$APACHECTL graceful"; then HAVE_WWW_CERT=1; else echo "⚠️  www до сертифіката додати не вдалось"; fi
    fi
    write_vhost https || die "HTTPS-vhost не застосовано, Apache відкочено (HTTP-варіант міг лишитись — перевір)"
    echo ""
    echo "--- ПЕРЕВІРКА HTTPS ---"
    CERT_OUT=$(cert_subject "$DOMAIN")
    echo "наш домен (SNI): $CERT_OUT"
    case "$CERT_OUT" in *"Let's Encrypt"*) ;; *) die "HTTPS віддає не Let's Encrypt для $DOMAIN — розбери вручну";; esac
    echo "контроль (без SNI, дефолт Webuzo): $(cert_subject '')"
    echo "  https /            : $(code "https://$DOMAIN/")"
    echo "  https /api/np/city : $(code "https://$DOMAIN/api/np/city?q=%D0%9A%D0%B8%D1%97%D0%B2")"
    echo "  http  / (редірект) : $(curl -sS --max-time 10 -o /dev/null -w '%{http_code} -> %{redirect_url}' "http://$DOMAIN/" 2>/dev/null || echo ERR)"
    HSTS_HDR=$(curl -sI --max-time 10 "https://$DOMAIN/" 2>/dev/null | grep -i '^strict-transport' | tr -d '\r' || true)
    echo "  HSTS               : ${HSTS_HDR:-НЕМАЄ (mod_headers відсутній? — HSTS не ввімкнено, решта працює)}"
    echo "  https /ua/ (301 → /): $(curl -sS --max-time 10 -o /dev/null -w '%{http_code} -> %{redirect_url}' "https://$DOMAIN/ua/" 2>/dev/null || echo ERR)"
    echo "  https /ru/p1-x (301 → каталог/картка): $(curl -sS --max-time 10 -o /dev/null -w '%{http_code} -> %{redirect_url}' "https://$DOMAIN/ru/p1-x.html" 2>/dev/null || echo ERR)"
    certbot renew --dry-run --cert-name "$DOMAIN" >/dev/null 2>&1 && echo "✅ certbot renew --dry-run для $DOMAIN ОК" || echo "⚠️  certbot renew --dry-run для $DOMAIN НЕ пройшов — перевір /var/log/letsencrypt/letsencrypt.log"
    echo ""
    echo "🎉 ГОТОВО: https://$DOMAIN працює. Далі (не тут): LiqPay-ключі в $APP/.env, SYSTEM_MAP, GMC."
fi
