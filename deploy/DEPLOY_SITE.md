# Розгортання власного сайту `plutustoys.com.ua` на VPS

Код автодеплоїться в `/opt/plutustoys` (read-only pull master, `vps_code_sync.sh`), тож ці
файли вже там. Нижче — **одноразові** привілейовані кроки (systemd/nginx/TLS/DNS + бойові
ключі LiqPay). Виконувати ключем (без пароля), напр.:

```
ssh -i ~/.ssh/plutustoys_vps root@45.94.157.4 "<команда>"
```

## 0. DNS (у реєстратора домену)
A-запис `plutustoys.com.ua` і `www` → IP VPS (45.94.157.4).

## 1. Згенерувати статичний сайт
```
/opt/plutustoys/venv/bin/python3 /opt/plutustoys/site/build_site.py
```
Створює `/opt/plutustoys/site/*.html` + `index.json`. **Оновлення цін/наявності:** поставити
періодичний ребілд (напр. щоденний systemd-timer або в наявний feed-pipeline) — `index.json`
є джерелом серверних цін для `POST /api/order`.

## 2. systemd-юніт API
```
cp /opt/plutustoys/deploy/site-order-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now site-order-api
systemctl status site-order-api --no-pager
```
Юніт слухає `127.0.0.1:8901` (лише локально; назовні — через nginx).

## 3. nginx (статика + проксі /api)
```
cp /opt/plutustoys/deploy/nginx-plutustoys.conf /etc/nginx/sites-available/plutustoys.com.ua
ln -sf /etc/nginx/sites-available/plutustoys.com.ua /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

## 4. TLS (Let's Encrypt)
```
certbot --nginx -d plutustoys.com.ua -d www.plutustoys.com.ua
```
certbot сам додасть 443 + редірект 80→443.

## 5. LiqPay: sandbox → бойовий (коли власник зареєструє компанію)
У `/opt/plutustoys/.env`:
```
LIQPAY_PUBLIC_KEY=<public_key з кабінету LiqPay>
LIQPAY_PRIVATE_KEY=<private_key>
LIQPAY_SANDBOX=0
```
Потім `systemctl restart site-order-api`. До цього — sandbox (без списань); без ключів
замовлення приймаються без онлайн-оплати (менеджер підтверджує вручну).

## Money-safety (нагадування)
Веб-замовлення пишуться в ту саму `orders.db`, що й order-pipeline, як `platform='site'`,
`prepaid`, `payment_confirmed=0`. Форвард у Toysi (реальна закупівля) стається ЛИШЕ після
підтвердженого серверного колбека LiqPay (`get_orders_ready_to_forward`). Фіскальний чек
(Checkbox) звʼязується після підключення бойового LiqPay.

## Після активації — оновити SYSTEM_MAP
Коли `site-order-api` увімкнено, додати його у SYSTEM_MAP §2Б (VPS-юніти) і §6 (реєстр для
`system_map_driftcheck.py`) — це персистентний daemon (`.service` без `.timer`).
