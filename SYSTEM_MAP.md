# SYSTEM_MAP.md — єдине джерело правди (SSOT) про ролі, автоматики, модулі та їх взаємозв'язки

> **Це головна мапа проєкту. Читати ПЕРШИМ будь-якою сесією/агентом, до STATUS.md.**
> Замінює застарілий `AGENTS_INVENTORY.md` (той датований 29.07 і збрехав про склад агентів і деплой).
>
> **Чому цей файл існує і як він НЕ гниє (правило власника, 2026-08-20):**
> 1. **Будується з РЕАЛЬНОСТІ, не з пам'яті** — автоматики знято живо (`Get-ScheduledTask` локально,
>    `systemctl list-timers` на VPS), ролі/модулі — з коду й каналів, не «як мали б бути».
> 2. **Самопідтримний** — БУДЬ-ЯКИЙ PR, що додає/змінює/прибирає агента, автоматику, модуль чи межу
>    відповідальності, **у ТОМУ Ж PR оновлює цей файл**. Аудитор PR це перевіряє.
> 3. **Звіряється скриптом** — `python system_map_driftcheck.py` діфить живу систему проти реєстру
>    нижче й алертить на дрейф (запобіжник сам каже, де збрехав). Ганяється локально й на VPS.
> 4. **Обов'язковий перший читаний** — BOOTSTRAP.md і CLAUDE.md указують сюди.

Останнє живе зняття: **локаль — 2026-08-20 (14 тасків, drift-check зелений)**; **VPS — 2026-08-20 (бандл systemctl, 19 юнітів)**.

---

## 1. РОЛІ (Claude-сесії — запускаються людиною, не cron)

Кожна роль: **місія · ВОЛОДІЄ · НЕ торкається · ескалація**. «Володіє» = приймає рішення й відповідає;
код реалізує **Код** через PR+аудит незалежно від того, чий домен.

### Cowork — координатор-зведення / стратегія
- **Місія:** зводить стан у STATUS.md, готує звіти власнику, тримає загальну картину. Дописи Cowork —
  ДОРАДЧІ; рішення й відповідальність — за агентом напряму (рішення власника 2026-08-19).
- **Володіє:** `STATUS.md` (єдиний писар), зведення OWNER_INBOX для власника.
- **НЕ торкається:** код (лише read-only git), рішення в чужих доменах.
- **Ескалація:** — (сам є шаром до власника).

### Код (Code Desktop) — інфраструктура + координатор
- **Місія:** пише ВЕСЬ код проєкту (фіди, пайплайни, скрейпери, order-flow, репрайсер, інтеграції),
  через PR + незалежний аудит. Координатор безпеки й роботи.
- **Володіє:** реалізація всіх модулів; технічна архітектура; деплой; `CODE_LOG.md` (єдиний писар),
  `COORDINATOR_LOG.md`. Веде цей SYSTEM_MAP.
- **НЕ торкається:** промо-стратегія (SEO/SMM), ЦІНА/маржа/склад асортименту (власник/КОДВ), контент-рішення.
- **Ескалація:** розвилки власника → `OWNER_INBOX.md`; міжагентні запити → канали.

### Security-аудит — незалежний рев'ю кожного PR
- **Місія:** перевіряє КОЖЕН PR ПЕРЕД мержем живо (git diff, синтетичні тести), не на слово.
- **Реалізація (2026-08-17):** внутрішньосесійний субагент Коду (інструмент `Agent`,
  `subagent_type: general-purpose`), НЕ окрема людино-сесія, НЕ spawn_task-чіп. Гейт мержу — маркер
  `.audit_ok` (див. CLAUDE.md).
- **Володіє:** вердикт «чисто / не чисто». **НЕ торкається:** нічого не пише в код.

### SEO + якість каталогу — (об'єднано з товарознавцем, рішення власника 2026-08-20)
- **Місія:** ЩО за товари в каталозі і НАСКІЛЬКИ повні їхні картки. Одна роль над усім наповненням.
- **Володіє (домен, не код):**
  - **Відбір товарів (товарознавство):** стеження за `rozetka_merchant_agent` — які товари онбордити
    на Rozetka (конкурентні/унікальні); критерії відбору; чистка фото.
  - **Якість карток:** описи (`seo_content_*`), характеристики/атрибути (повнота — `prom_cabinet_catalog`
    лічильники, `export_catalog_scorecard`), категоризація на площадках, brand/GTIN.
  - **Топ товарів** = конкурентність × попит × повнота (аналітичний зріз, не комерційне рішення).
  - Merchant Center / Search Console / органічний пошук.
- **НЕ торкається:** ЦІНА/маржа/собівартість (Код+КОДВ), СКЛАД асортименту — що завозити/виводити
  (власник), механіка постингу (SMM), реалізація коду (Код — через PR).
- **Ескалація:** технічні запити → `SEO_CHANNEL.md` до Коду; розвилки власника → `OWNER_INBOX.md`.

### SMM (маркетолог соцмереж)
- **Місія:** соц-контент-стратегія (наратив Плутуса, гачки, каденція, формати), FB/IG, залученість,
  блогери, відгуки, community; фаза 2 — платна соціалка.
- **Володіє:** ЩО/КОЛИ/З ЯКИМ гачком постити; креатив; блогер-кампанії; збір відгуків; споживач
  «топ товарів» від SEO (куди вести пости).
- **НЕ торкається:** каталог/ціни/фіди/membership, механіка постингу «як технічно» (Код), пошук/Merchant (SEO).
- **Ескалація:** технічні запити → `MARKETING_CHANNEL.md` до Коду; розвилки власника → `OWNER_INBOX.md`.

### КОДВ (бухгалтер / фінанси) — окрема роль, фінансова
- **Місія:** собівартість, маржа, ціна, unit-економіка, фінзвірка (Checkbox/ПРРО, NovaPay, Privat), баланс.
- **Володіє:** усе фінансове; «топ за ПРИБУТКОМ» = маржа × попит-зріз SEO. Джерело істини —
  `документи_КОДВ/`, `КОДВ_журнал.md`.
- **НЕ торкається:** промо/каталог/код (крім фін-модулів через запит до Коду).

---

## 2. АВТОМАТИКИ (cron / таски — крутяться самі) × МІСЦЕ × ЩО РОБЛЯТЬ

> Машинно-читаний реєстр для `system_map_driftcheck.py` — унизу файлу (розділ 6). Таблиці тут — людям.

### 2А. Локальна Windows-машина (підтверджено живо 2026-08-20)

| Task | Що запускає | Домен |
|---|---|---|
| `PlutusToys_AgentWatch` | `agent_watch.py` (30 хв) | координація (будить SEO/SMM/Код на нові записи каналів) |
| `PlutusToys_RozetkaLocalChain` | `run_rozetka_local.py` | Rozetka: pull цін → **товарознавець** → commit membership → фід |
| `PlutusToys_RozetkaPricePull` | `rozetka_price_monitor.py` | Rozetka: пул цін конкурентів |
| `PlutusToys_RozetkaKeepalive` | `rozetka_price_monitor.py --keepalive` | Rozetka: тримати сесію вітрини теплою |
| `PlutusToys_PromCatalogHistory` | `prom_cabinet_catalog.py --summary` | Prom: денний знімок каталогу (+ лічильники повноти) |
| `PlutusToys_PromCabinetKeepalive` | `prom_notifications_scraper.py --keepalive` | Prom: тримати кабінетну сесію теплою |
| `PlutusToys_PromConvergenceMonitor` | `prom_convergence_monitor.py` | Prom: контроль збіжності каталогу до 6000 |
| `PlutusToys-TelegramOutbox` | `telegram_outbox_processor.py` | інфра: черга вихідних Telegram |
| `PlutusToys-CabinetAudit` | `local_cabinet_audit.ps1` | аудит кабінетів (Prom/Rozetka) |
| `PlutusToys-Graph6Daily` | `graph6_daily.ps1` | КОДВ/звітність |
| `PlutusToys-KODVDailyCheck` | `kodv_daily_check.ps1` | КОДВ: денна перевірка |
| `PlutusToys-NovaPayRegistryArchiver` | `novapay_registry_archiver.ps1` | КОДВ: архів реєстрів NovaPay |
| `PlutusToys-PrivatDailySync` | `privat_daily_sync.ps1` | КОДВ: синк Privat |
| `PlutusToys-ChecboxRegistrySync` | `checkbox_registry_sync.ps1` | КОДВ: синк Checkbox/ПРРО |

> **Чому Rozetka + Prom-кабінет + agent_watch крутяться ЛОКАЛЬНО, а не на VPS:** вітрина/кабінет
> захищені антиботом, який пропускає лише справжній Chrome із профілем (bundled chromium → 403/500);
> agent_watch будить сесії через локальний `claude`. VPS — headless, туди це не переноситься.

### 2Б. VPS (45.94.157.4, `/opt/plutustoys`, systemd `.timer`+`.service`, venv-python) — знято живо 2026-08-20

> ⚠️ **ЧАСТКОВЕ зняття — не вважати повним.** Знято з виводу бандла власника 2026-08-20, але вивід був
> обрізаний (частина рядків не потрапила). Тому цей список — НИЖНЯ межа («точно є»), а не «це все».
> **АВТОРИТЕТ повноти = `python system_map_driftcheck.py` НА VPS:** він порівняє живі юніти з `vps_units`
> (розділ 6) і висвітить кожен, що живий-але-не-в-мапі → тоді доповнюємо. Розклади (OnCalendar) — у `list-timers`.

| Unit (`.service`, є парний `.timer`) | ExecStart | Домен |
|---|---|---|
| `order-pipeline` | `order_pipeline.py` | замовлення: забір/збереження/пересил (RZ ТТН, маркування Toysi) |
| `order-router` | `order_router.py` | замовлення: роутинг у Toysi ⚠️ (див. дрейф нижче) |
| `orders-watcher` | `orders_watcher.py` | замовлення: полінг нових ⚠️ (див. дрейф нижче) |
| `order-status-tracker` | `order_status_tracker.py` | замовлення: статуси доставки/ТТН |
| `feed-pipeline` | (генерація фідів + репрайсер) | фіди Prom-top/Google/Meta/Bing, публікація `feed-data` |
| `eva-feed` | (генерація EVA-фіда) | EVA-фід окремим юнітом |
| `eva-catalog-auditor` | `eva_catalog_auditor.py` | аудит каталогу EVA |
| `meta-feed-coverage-monitor` | `meta_feed_coverage_monitor.py` | покриття Meta-фіда |
| `catalog-health-monitor` | `catalog_health_monitor.py` | здоров'я каталогу |
| `full-catalog-scan` | `full_catalog_competitor_scan.py` | нічний скан цін конкурентів |
| `prom-catalog-sync` | `refresh_scan_deps.sh` (ExecStartPre) → `prom_catalog_sync.py` | Prom: деактивація неконкурентних лістингів |
| `prom-catalog-auditor` | `prom_catalog_auditor.py` | Prom: щоденний аудит каталогу |
| `prom-competitor-pricer` | `prom_competitor_pricer.py --apply` | Prom: репрайсер конкурентних цін |
| `prom-review-requester` | `prom_review_requester.py --send` | Prom: запити відгуків (домен SMM) |
| `prom-chat-bot` | `prom_chat_bot.py` | Prom: автовідповіді в чаті |
| `novapay-statement` | `novapay_statement.py` | КОДВ: звірка COD через IMAP NovaPay |
| `daily-report` | `daily_report.py` | зведення в Telegram |
| `deadline-reminder` | `deadline_reminder.py` | дедлайни/платежі |
| `service-watchdog` | `service_watchdog.py` | алерти застою + дрейф автодеплою |

> **⚠️ ЗНАЙДЕНИЙ ДРЕЙФ (2026-08-20, приклад цінності SSOT):** на VPS ЖИВІ окремими юнітами
> `order-pipeline` + `order-router` + `orders-watcher` — хоча доки казали, що `order-pipeline` ЗАМІНИВ
> watcher+router (щоб прибрати гонку). Або order-flow легітимно розкладено на 4 юніти, або є
> надлишок/подвійний запуск. **Не чіпати наосліп** — окреме рішення власника/Коду: з'ясувати живо
> (чи всі активні, чи конфліктують за orders.db) і або задокументувати як задумане, або прибрати зайве.
> `vps-code-sync` (автопул master) — у видимому виводі не потрапив; підтвердити першим drift-check на VPS.

### 2В. GitHub Actions (`plutustoys-rgb/toysi-feeds`)
- `update-feeds.yml` — cron 4 год: генерує ЛИШЕ `feeds/rozetka_feed.xml` (відокремлено від VPS, щоб не було гонки orphan-force-push).
- ~~`claude-review.yml`~~ — **ВИМКНЕНО** (не профінансований; аудит тепер лише внутрішньосесійний субагент). Хвіст — видалити workflow.

---

## 3. МОДУЛІ × ДОМЕН × хто СТЕЖИТЬ (Код реалізує всі)

- **Фіди/генерація:** `generate_{prom,prom_top,rozetka,google,meta,bing,eva,allo,royaltoys}_feed.py`, `parser.py`,
  `catalog_health_monitor`, `catalog_size_tracker`, `link_cache_validator`, `meta_feed_coverage_monitor`
  → Код (інфра), дані/якість — **SEO+якість каталогу**.
- **Ціноутворення/репрайсинг:** `competitor_pricing`, `repricer`, `apply_prices`, `apply_live_dumping_fix`,
  `full_catalog_competitor_scan`, `prom_competitor_pricer`, `rozetka_competitor_repricer`, `price_state_redact`
  → Код + **КОДВ/власник** (маржа/ціна/флор). НЕ SEO.
- **Товарознавець (Rozetka онбординг) — домен SEO+якість:** `rozetka_merchant_agent`, `rozetka_merchant_commit`,
  `run_rozetka_local` (ланцюг), `rozetka_client`, `rozetka_cabinet_scraper`, `rozetka_price_monitor`.
- **RZ Delivery / доставка Rozetka:** `rozetka_delivery_client`, `rozetka_rz_delivery_monitor` → Код.
- **SEO-контент/якість каталогу:** `seo_content_db`, `seo_content_generator`, `audit_prom_characteristics`,
  `prom_cabinet_catalog`, `export_catalog_scorecard`, `prom_analytics_scraper`, `prom_catalog_auditor`,
  `export_review_candidates`, `gmc_scraper` → **SEO+якість**.
- **Prom інфра/скрейп/чат:** `prom_api_client`, `prom_cabinet_scraper`, `prom_notifications_scraper`,
  `prom_catalog_sync`, `prom_convergence_monitor`, `prom_pushed_ledger`, `prom_chat_bot`, `prom_chat_db`,
  `prom_review_requester` → Код (інфра); сигнали — SEO; відгуки (`prom_review_requester`) — SMM.
- **Замовлення/доставка/фінзвірка замовлень:** `order_pipeline`, `order_router`, `orders_watcher`, `orders_db`,
  `order_status_tracker`, `toysi_order_submit`, `nova_poshta`, `ukrposhta_client`, `bank_check`,
  `reconcile_revenue` → Код + **КОДВ** (звірка).
- **Соцмережі/SMM:** `social_auto_poster`, `social_dead_post_cleaner`, `plutus_overlay`,
  `meta_conversions_client` → **SMM** (стратегія) + Код (механіка).
- **EVA:** `eva_cabinet_scraper`, `eva_catalog_auditor`, `eva_orders_client`, `generate_eva_feed` → Код + SEO (якість).
- **ALLO:** `allo_cabinet_scraper`, `generate_allo_feed` → Код.
- **Toysi / RoyalToys (постачальник):** `toysi_cabinet_scraper`, `parser` (fetch_toysi_catalog),
  `royaltoys_parser`, `compare_royaltoys_toysi`, `generate_royaltoys_feed` → Код.
- **КОДВ (фінанси):** `checkbox_client`, `novapay_statement`, `weekly_balance_digest`, `daily_report`
  → **КОДВ/власник**.
- **Telegram / сповіщення (спільна інфра):** `telegram_notify`, `telegram_outbox_processor`,
  `telegram_userbot_client`, `telegram_userbot_login` → Код.
- **Координація / інфра / деплой:** `agent_watch`, `service_watchdog`, `vps_code_sync_report`,
  `deadline_reminder`, `system_map_driftcheck` → Код (координатор).

---

## 4. АЛГОРИТМ СПІВПРАЦІ (консолідовано з BOOTSTRAP / COORDINATION_PROTOCOL / CODE_LOG)

1. **Канали — асинхронно через файли** (живі spawn_task/SendMessage між агентами НЕ працюють — перевірено):
   `SEO_CHANNEL.md` (Код↔SEO+якість), `MARKETING_CHANNEL.md` (Код↔SMM). Newest-on-top, тег `[X → Y] дата`.
2. **Пробудження:** `PlutusToys_AgentWatch` (30 хв) читає канали й будить агента через `claude -p`, коли
   з'явився новий `[X → тобі]` запис. Анти-пінг-понг: підтвердження/закриті НЕ відписуються; денна стеля
   пробуджень; тиша ≠ поломка (агент може бути заблокований на власнику).
2b. **Пробудження не спрацьовує — діагностика:** стан у `.local_secrets/agent_watch/<роль>.json`
   (`wakes_today`, `seen`, `last_wake_at`); канали newest-on-top (свіже — вгорі, не в `tail`).
3. **Власність файлів (проти гонок перезапису):** `STATUS.md`→лише Cowork; `CODE_LOG.md`→лише Код;
   канали→теговані записи, чужі не редагувати; `OWNER_INBOX.md`→усі складають розвилки власнику, Cowork зводить.
4. **Код тільки через PR + незалежний аудит ПЕРЕД мержем** (CLAUDE.md, гейт `.audit_ok`). SEO/SMM код не пишуть —
   формулюють запит у канал, Код вертає № PR.
5. **Ескалація до власника:** будь-що, що впирається в його рішення/доступ/гроші → `OWNER_INBOX.md`.
6. **Фінанси (cost/margin) — НІКОЛИ в спільні файли/канали** (був витік NovaPay). Лише агрегати без собівартості.
7. **Читай-перш-ніж-будуй:** перед новим модулем/агентом — звірити цей SYSTEM_MAP (чи вже є власник/модуль),
   інакше дублі (реальний випадок: scorecard vs товарознавець, 2026-08-20).

---

## 5. РОЗМІЩЕННЯ (де що живе)

- **Код (репо `toysi-feeds`):** `master`. Локальна робоча копія — `C:\Users\smach\rozetka_agent`; VPS — `/opt/plutustoys` (автопул 15 хв).
- **Стан/секрети:** локальні `.local_secrets/` (сесії кабінетів, знімки, курсори) + `.env`; на VPS — свій `.env`.
  Публічні фіди/стан — гілка `feed-data` (orphan force-push; фінполя редагуються `price_state_redact`).
- **Координаційні доки (Cowork-папка `PlutusToys_avtonomiya/`):** STATUS, CODE_LOG, канали, OWNER_INBOX,
  BOOTSTRAP, COORDINATOR_LOG, GOTCHAS, `технічні_вимоги_маркетплейсів/`, `документи_КОДВ/`.
- **Цей SSOT:** у РЕПО (версіонується, синхриться на VPS і локаль, звіряється скриптом). BOOTSTRAP/CLAUDE.md → сюди.

---

## 6. МАШИННО-ЧИТАНИЙ РЕЄСТР АВТОМАТИК (для `system_map_driftcheck.py` — не редагувати вручну недбало)

```json
{
  "local_tasks": [
    "PlutusToys_AgentWatch", "PlutusToys_RozetkaLocalChain", "PlutusToys_RozetkaPricePull",
    "PlutusToys_RozetkaKeepalive", "PlutusToys_PromCatalogHistory", "PlutusToys_PromCabinetKeepalive",
    "PlutusToys_PromConvergenceMonitor", "PlutusToys-TelegramOutbox", "PlutusToys-CabinetAudit",
    "PlutusToys-Graph6Daily", "PlutusToys-KODVDailyCheck", "PlutusToys-NovaPayRegistryArchiver",
    "PlutusToys-PrivatDailySync", "PlutusToys-ChecboxRegistrySync"
  ],
  "vps_units": [
    "order-pipeline", "order-router", "orders-watcher", "order-status-tracker",
    "feed-pipeline", "eva-feed", "eva-catalog-auditor", "meta-feed-coverage-monitor",
    "catalog-health-monitor", "full-catalog-scan", "prom-catalog-sync", "prom-catalog-auditor",
    "prom-competitor-pricer", "prom-review-requester", "prom-chat-bot",
    "novapay-statement", "daily-report", "deadline-reminder", "service-watchdog"
  ],
  "gh_workflows": ["update-feeds.yml"]
}
```
