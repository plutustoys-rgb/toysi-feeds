# toysi-feeds

Автоматична генерація XML/YML фідів для Rozetka та Prom.ua з каталогу постачальника Toysi.

## Публічні URL фідів

Фіди публікуються в окрему гілку `feed-data` (не в `main`), одним коммітом, що щоразу
перезаписується force-push'ем — це не дає git-історії рости на кожен запуск (кожні 4 години).

- Rozetka: `https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/rozetka_feed.xml`
- Prom.ua: `https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/prom_feed.xml`

Саме ці посилання треба вказувати в кабінетах продавця на Rozetka та Prom.ua як джерело прайс-листа.

## Як це працює

`.github/workflows/update-feeds.yml` кожні 4 години:
1. тягне каталог Toysi (`parser.py`, ключ з секрету `TOYSI_API_KEY`),
2. генерує `feeds/rozetka_feed.xml` і `feeds/prom_feed.xml`,
3. публікує обидва файли в гілку `feed-data` (force-push, без накопичення історії).

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

Workflow можна запустити вручну: вкладка **Actions** → **Update Toysi feeds** → **Run workflow**.
