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

Останнє живе зняття: **локаль — 2026-08-20 (15 тасків, drift-check зелений)**; **VPS — 2026-08-21 (22 юніти, re-drift-check на VPS зелений: 19 бандл + social-poster-fb/ig підтверджено + social-dead-post-cleaner знайдено самим drift-check)**.

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

### Бізнес-консультант — стратегія / unit-економіка (нове 2026-08-25)
- **Місія:** рахує unit-економіку, перевіряє гіпотези на ЖИВИХ даних ДО того, як під них будується код; формулює
  розвилки власнику з варіантами+мінусами. Висновки ДОРАДЧІ — рішення за власником/доменним агентом.
- **Володіє:** стратегічні рекомендації; `CONSULTANT_CHANNEL.md` (його I/O з Кодом/координатором).
- **НЕ торкається:** код, публікація назовні, ціни/каталог, контент — усе через профільні агенти/власника.
- **Ескалація:** постановка задач → координатор (Cowork); факт-уточнення → канал профільного агента; розвилки → `OWNER_INBOX.md`.
  Онбординг-відповіді Коду — `PlutusToys_avtonomiya/онбординг_консультанта_відповіді_Код.md`.

---

## 2. АВТОМАТИКИ (cron / таски — крутяться самі) × МІСЦЕ × ЩО РОБЛЯТЬ

> Машинно-читаний реєстр для `system_map_driftcheck.py` — унизу файлу (розділ 6). Таблиці тут — людям.

### 2А. Локальна Windows-машина (підтверджено живо 2026-08-20)

| Task | Що запускає | Домен |
|---|---|---|
| `PlutusToys_AgentWatch` | `agent_watch.py` (30 хв) | координація (будить SEO/SMM/Код на нові записи каналів) |
| `PlutusToys_SystemMapDriftCheck` | `system_map_driftcheck.py --alert` (щодня 08:40) | сам звіряє цей SSOT із живими тасками, Telegram-алерт при дрейфі |
| `PlutusToys_CriticalCalendar` | `critical_watch.py` (візуальне вікно) | світлофор термінів ключів/балансів/абонплат (критичний календар) |
| `PlutusToys_RozetkaLocalChain` | `run_rozetka_local.py` | Rozetka: pull цін → **товарознавець** → commit membership → фід |
| `PlutusToys_RozetkaPricePull` | `rozetka_price_monitor.py` | Rozetka: пул цін конкурентів |
| `PlutusToys_RozetkaKeepalive` | `rozetka_price_monitor.py --keepalive` | Rozetka: тримати сесію вітрини теплою |
| `PlutusToys_PromCatalogHistory` | `prom_cabinet_catalog.py --summary` | Prom: денний знімок каталогу (+ лічильники повноти) |
| `PlutusToys_PromCabinetKeepalive` | `prom_notifications_scraper.py --keepalive` | Prom: тримати кабінетну сесію теплою |
| `PlutusToys_PromConvergenceMonitor` | `prom_convergence_monitor.py` | Prom: контроль збіжності каталогу до 6000 |
| `PlutusToys-TelegramOutbox` | `telegram_outbox_processor.py` | інфра: черга вихідних Telegram |
| `PlutusToys-CabinetAudit` | `local_cabinet_audit.ps1` | аудит кабінетів (Prom/Rozetka) + КОДВ-леджери кандидатів: `rozetka_commission_ledger`, `eva_orders_ledger`, `eva_commission_ledger` (фактична комісія EVA з картки замовлення, замість 15%-оцінки), `prom_commission_ledger` (комісія Prom з Orders API у графу 9, §3 довідника), **`rozetkapay_registry_kandydaty`** (СТОРНО-детектор з реєстру FC/RozetkaPay: банк-сторно → кандидат, крос-звірка з книгою read-only) — усі пишуть кандидатів у документи_КОДВ, книгу не пишуть. **+ `marketplace_requirements_gate.py`** — структурний гейт дисципліни: кожен фід, що вимагає авторитетного довідника площадки (дерево категорій/атрибути), мусить мати його ЗБЕРЕЖЕНИМ у репо + робочою автоперевіркою; реєстр у самому скрипті (EVA=enforced з `eva_category_reference.csv`+`verify_eva_category_map.py`; Prom/Rozetka/ALLO/Google=audit_pending). Порушення enforced → Telegram-алерт (крок гониться з увімкненим Telegram). **+ `archive_channels.py --apply`** — auto-archiver каналів агентів: старі записи (поза останніми ~40 / старші 30д) → `archive/<роль>/`, тримає гарячі канали лінивими (data-safe: append в архів ДО перепису гарячого) |
| `PlutusToys-Graph6Daily` | `graph6_daily.ps1` → `graph6_daily.py` | КОДВ: собівартість реалізованих замовлень Toysi (кабінет «Історія замовлень», лише «Відвантажене») → кандидати графи 6 у документи_КОДВ (read-only, книгу не пише) |
| `PlutusToys-NovaPayRegistryArchiver` | `novapay_registry_archiver.ps1` → `kodv_mail_archiver.py` | КОДВ: архів реєстрів NovaPay + актів звірки НоваПошта **+ реєстрів FC/RozetkaPay** («реєстр платежів» → тека RozetkaPay) **+ виписок ПриватБанку** (best-guess маркери, тека ПриватБанк — звірити за першим листом) у документи_КОДВ (read-only IMAP, книгу не пише) |
| `PlutusToys-ChecboxRegistrySync` | `checkbox_registry_sync.ps1` → `checkbox_registry_sync.py` | КОДВ: нові фіскальні чеки Checkbox → кандидати доходу у документи_КОДВ (read-only API, книгу не пише) |

> **Чому Rozetka + Prom-кабінет + agent_watch крутяться ЛОКАЛЬНО, а не на VPS:** вітрина/кабінет
> захищені антиботом, який пропускає лише справжній Chrome із профілем (bundled chromium → 403/500);
> agent_watch будить сесії через локальний `claude`. VPS — headless, туди це не переноситься.
>
> **Панель керування (on-demand, НЕ таска):** `control_panel.py` — локальний сервер `127.0.0.1:8787` (запуск `run_panel.bat` або `python control_panel.py` з теки репо). Дає: статуси 5 агентів (Код/SEO/SMM/Консультант/Бухгалтер) + telegram-дайджест (`telegram_digest.py`, по запиту з `reports/telegram_alerts.md`) + критичні плитки; по кожному агенту — чат (`claude -p`), задача-в-канал (agent_watch підхопить), термінал (shell у теці репо), **«відкрити сесію» = ПОВНИЙ агент** (`claude --agent plutus-<роль>` у теці репо: роль+правила з `.claude/agents/plutus-{seo,smm,kod,consultant,kodv}.md` + навичка через `skills:` — seo-agent/plutustoys-smm/business-consultant/accountant; канали Cowork через --add-dir). Синк-скіли завантажуються раз: `CLAUDE_CODE_SYNC_SKILLS=1 claude -p ...` → `~/.claude/skills/synced/`. localhost-only + CSRF (`X-Panel`). Запускає власник, не cron.

### 2Б. VPS (45.94.157.4, `/opt/plutustoys`, systemd `.timer`+`.service`, venv-python) — знято живо 2026-08-20

> ✅ **ЗВІРЕНО re-drift-check НА VPS 2026-08-21:** 22 юніти нижче — ЖИВІ й ПОВНІ. Історія: 19 знято бандлом;
> `social-poster-fb`/`social-poster-ig` дописано з доків і re-drift-check ПІДТВЕРДИВ (їх нема в «живого нема»);
> `social-dead-post-cleaner` — сам drift-check ЗНАЙШОВ як «живе, не в мапі» (наочно: механізм ловить те, що
> людина проґавила), дописано. 3 системні apt/journal відфільтровано. Розклади (OnCalendar) — у `list-timers`.

| Unit (`.service`, є парний `.timer`) | ExecStart | Домен |
|---|---|---|
| `order-pipeline` | `order_pipeline.py` | замовлення: ЄДИНИЙ процесор — забір(poll_once)+bank_check+роутинг послідовно. enabled+active ~15хв |
| `order-router` | `order_router.py` | ⛔ **DISABLED leftover** — поглинуто order-pipeline (звірено 2026-08-20: enabled=disabled, inactive) |
| `orders-watcher` | `orders_watcher.py` | ⛔ **DISABLED leftover** — поглинуто order-pipeline (звірено 2026-08-20: enabled=disabled, inactive) |
| `order-status-tracker` | `order_status_tracker.py` | замовлення: статуси доставки/ТТН. enabled+active |
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
| `social-poster-fb` | `social_auto_poster.py` (fb) | Соцмережі: автопост FB 1×/день 11:00 (домен SMM) |
| `social-poster-ig` | `social_auto_poster.py` (ig) | Соцмережі: автопост IG 1×/день (домен SMM) |
| `social-dead-post-cleaner` | `social_dead_post_cleaner.py` | Соцмережі: чистка мертвих FB-постів (404 товар) — знайдено re-drift-check 2026-08-21 |
| `novapay-statement` | `novapay_statement.py` | КОДВ: звірка COD через IMAP NovaPay |
| `daily-report` | `daily_report.py` | зведення в Telegram |
| `deadline-reminder` | `deadline_reminder.py` | дедлайни/платежі |
| `service-watchdog` | `service_watchdog.py` | алерти застою + дрейф автодеплою |

> **✅ ДРЕЙФ order-flow РОЗВ'ЯЗАНО (звірено живо 2026-08-20):** `order-router` і `orders-watcher` на VPS
> **DISABLED + inactive** — це мертві файли-юніти, поглинуті `order-pipeline` (він робить poll_once+
> bank_check+route послідовно в одному процесі, щоб не було гонки). Активні лише `order-pipeline` +
> `order-status-tracker`. Подвійної обробки/гонки НЕМА. Косметичний хвіст: файли-юніти `order-router`/
> `orders-watcher` можна прибрати (`systemctl disable` вже стоїть; видалення `.timer/.service` — за бажанням,
> не критично). `vps-code-sync` — у видимому виводі бандла не потрапив; підтвердити drift-check на VPS.

### 2В. GitHub Actions (`plutustoys-rgb/toysi-feeds`)
- `update-feeds.yml` — cron 4 год: генерує ЛИШЕ `feeds/rozetka_feed.xml` (відокремлено від VPS, щоб не було гонки orphan-force-push).
- ~~`claude-review.yml`~~ — **ВИДАЛЕНО 2026-09-02** (не профінансований `anthropics/claude-code-action`, падав червоним на кожному PR; аудит тепер лише внутрішньосесійний субагент).

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
- **Соцмережі/SMM:** `social_auto_poster` (вкл. IG-Reels `--reel`), `social_dead_post_cleaner`,
  `plutus_overlay`, `meta_conversions_client`, `publish_reel_video.sh` (хостинг відео у feed-data/media
  → публічний raw-URL для Reels), `social_ledger_report` (ledger→CSV + розклад-vs-факт для SMM),
  `social_insights_report` (per-post метрики IG+FB→CSV; FB отримав scope read_insights 2026-08-21,
  FB PagePost вимагає інших полів/метрик, ніж IG),
  `fb_token_refresh.py` (ручний VPS-утиліт: короткий FB-токен → довгостроковий Page-токен у `.env`,
  verify-before-write + `.env.bak`; замінює ад-хок одноряковик при додаванні дозволів)
  → **SMM** (стратегія) + Код (механіка).
- **EVA:** `eva_cabinet_scraper`, `eva_catalog_auditor`, `eva_orders_client`, `generate_eva_feed` → Код + SEO (якість).
- **ALLO:** `allo_cabinet_scraper`, `generate_allo_feed` → Код.
- **Toysi / RoyalToys (постачальник):** `toysi_cabinet_scraper`, `parser` (fetch_toysi_catalog),
  `royaltoys_parser`, `compare_royaltoys_toysi`, `generate_royaltoys_feed` → Код.
- **КОДВ (фінанси):** `checkbox_client`, `novapay_statement`, `weekly_balance_digest`, `daily_report`
  → **КОДВ/власник**.
- **Telegram / сповіщення (спільна інфра):** `telegram_notify`, `telegram_outbox_processor`,
  `telegram_userbot_client`, `telegram_userbot_login` → Код.
- **Координація / інфра / деплой:** `agent_watch`, `service_watchdog`, `vps_code_sync_report`,
  `deadline_reminder`, `system_map_driftcheck`,
  `recall.py` (антидубль/пам'ять: «що вже зроблено про X» з git+коду+SYSTEM_MAP+CODE_LOG;
  `--file` = дубль-варта, exit 3 якщо схоже вже є. Хук `recall-guard.sh` [локальний, `~/.claude/hooks/`]
  автоматично впорскує його перед створенням нового файлу — щоб після ущільнення сесії не робити дублі) → Код (координатор).

---

## 4. АЛГОРИТМ СПІВПРАЦІ (консолідовано з BOOTSTRAP / COORDINATION_PROTOCOL / CODE_LOG)

1. **Канали — асинхронно через файли** (живі spawn_task/SendMessage між агентами НЕ працюють — перевірено):
   `SEO_CHANNEL.md` (Код↔SEO+якість), `MARKETING_CHANNEL.md` (Код↔SMM). Newest-on-top, тег `[X → Y] дата`.
2. **Пробудження:** `PlutusToys_AgentWatch` (30 хв) читає канали й будить агента через `claude -p`, коли
   з'явився новий `[X → тобі]` запис. Анти-пінг-понг: підтвердження/закриті НЕ відписуються; денна стеля
   пробуджень; тиша ≠ поломка (агент може бути заблокований на власнику).
2a. **Автономія Кода (B, 2026-08-21):** розбуджений headless-Код має Bash → сам збирає код, ганяє тести й
   ВІДКРИВАЄ PR. АЛЕ мерж headless заблоковано ЖОРСТКО (env `PLUTUS_AGENT_HEADLESS` → хук `merge-guard.sh`
   відмовляє `gh pr merge` навіть із маркером). Тобто автономний Код готує PR, а незалежний аудит+мерж
   робить ІНТЕРАКТИВНА сесія/власник. Раніше headless мав лише файлові інструменти → не міг PR → усе
   чекало людину (корінь «SMM чекає Кода»). SEO/SMM лишаються файлово-only (їм код не потрібен).
2b. **Пробудження не спрацьовує — діагностика:** стан у `.local_secrets/agent_watch/<роль>.json`
   (`wakes_today`, `seen`, `last_wake_at`); канали newest-on-top (свіже — вгорі, не в `tail`).
2c. **Анти-тиха-втрата (D, 2026-08-21):** якщо агент прокинувся на запит (exit-0), але НІЧОГО не написав у
   канал — монітор шле Telegram-алерт (запит міг загубитись). seen усе одно просувається (без циклів).
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
    "PlutusToys_AgentWatch", "PlutusToys_SystemMapDriftCheck",
    "PlutusToys_RozetkaLocalChain", "PlutusToys_RozetkaPricePull",
    "PlutusToys_RozetkaKeepalive", "PlutusToys_PromCatalogHistory", "PlutusToys_PromCabinetKeepalive",
    "PlutusToys_PromConvergenceMonitor", "PlutusToys_CriticalCalendar",
    "PlutusToys-TelegramOutbox", "PlutusToys-CabinetAudit",
    "PlutusToys-Graph6Daily", "PlutusToys-NovaPayRegistryArchiver",
    "PlutusToys-ChecboxRegistrySync"
  ],
  "vps_units": [
    "order-pipeline", "order-router", "orders-watcher", "order-status-tracker",
    "feed-pipeline", "eva-feed", "eva-catalog-auditor", "meta-feed-coverage-monitor",
    "catalog-health-monitor", "full-catalog-scan", "prom-catalog-sync", "prom-catalog-auditor",
    "prom-competitor-pricer", "prom-review-requester", "prom-chat-bot",
    "social-poster-fb", "social-poster-ig", "social-dead-post-cleaner",
    "novapay-statement", "daily-report", "deadline-reminder", "service-watchdog"
  ],
  "gh_workflows": ["update-feeds.yml"]
}
```
