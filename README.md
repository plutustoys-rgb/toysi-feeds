# toysi-feeds

Автоматична генерація XML/YML фідів для Rozetka та Prom.ua з каталогу постачальника Toysi.

## Публічні URL фідів

Rozetka та `prom_feed_top.xml` публікуються в окрему гілку `feed-data` (не в `main`),
одним коммітом, що щоразу перезаписується force-push'ем — це не дає git-історії рости
на кожен запуск (кожні 4 години).

- Rozetka: `https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/rozetka_feed.xml`
- Prom.ua (повний каталог): `https://hqua0406413.online-vm.com:8443/prom_feed.xml`

**⚠️ URL для Prom.ua змінився 2026-07-14** — якщо в кабінеті Prom прив'язаний старий
`raw.githubusercontent.com/.../prom_feed.xml`, його треба замінити на новий вище
(Налаштування → Прайс-листи). Причина: `prom_feed.xml` виріс за межі жорсткого ліміту
GitHub 100 МБ/файл — публікація на `feed-data` падала щоразу з 2026-07-13. Файл тепер
роздається постійно з окремого vhost на VPS (Apache, справжній Let's Encrypt сертифікат,
порт 8443 — ізольовано від дефолтного Webuzo-вхосту на 80/443, щоб нічого на VPS не
зачепити). `rozetka_feed.xml`/`prom_feed_top.xml` лишаються на GitHub без змін — обидва
й так укладаються в ліміт.

## Як це працює

`.github/workflows/update-feeds.yml` кожні 4 години:
1. тягне каталог Toysi (`parser.py`, ключ з секрету `TOYSI_API_KEY`),
2. генерує `feeds/rozetka_feed.xml`, `feeds/prom_feed.xml`, `feeds/prom_feed_top.xml`,
3. вивантажує `prom_feed.xml` на VPS через rsync/SSH (секрети `FEED_DEPLOY_SSH_KEY`/
   `FEED_DEPLOY_HOST`; SSH-ключ на VPS обмежено через `rrsync` лише до запису в
   один конкретний каталог — навіть у разі витоку ключа довільну команду виконати
   неможливо),
4. публікує `rozetka_feed.xml` + `prom_feed_top.xml` у гілку `feed-data` (force-push,
   без накопичення історії).

Гілка `main` містить лише код і завжди чиста.

## Локальний запуск

```bash
pip install -r requirements.txt
cp .env.example .env   # і впишіть свій TOYSI_API_KEY
python generate_rozetka_feed.py
python generate_prom_feed.py
```

Ключ до XML-фіда Toysi беріть в особистому кабінеті toysi.ua, розділ **API**.

## Налаштування GitHub Actions

Settings → Secrets and variables → Actions → New repository secret:
`TOYSI_API_KEY` = ваш ключ.

`FEED_DEPLOY_SSH_KEY`/`FEED_DEPLOY_HOST` — SSH-доступ для вивантаження `prom_feed.xml`
на VPS (2026-07-14). Приватний ключ дає доступ ЛИШЕ до rsync у один каталог на VPS
(обмежено через `rrsync`), не до shell — регенерувати за потреби на самому VPS
(`ssh-keygen`, дописати публічний ключ в `/root/.ssh/authorized_keys` з тим самим
`command="/usr/bin/rrsync -wo ..."` префіксом, оновити секрет).

Workflow можна запустити вручну: вкладка **Actions** → **Update Toysi feeds** → **Run workflow**.
