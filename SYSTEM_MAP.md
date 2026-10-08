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

### Продажник — прямі виробники / представництво (агент+вотчер додано 2026-09-17)
- **Місія:** пошук виробників іграшок і підготовка ґрунту для ПРЯМОГО представництва/дистрибуції (вища маржа + унікальні SKU поза дропшип-каналом Toysi). Досліджує й готує ЧЕРНЕТКИ, не веде переговорів сам.
- **Володіє:** кандидати-виробники, чернетки звернень/аргументів; `SELLER_CHANNEL.md` (його I/O). Керується стратегією Консультанта й пріоритетами власника.
- **НЕ торкається:** зовнішні контакти/зобов'язання (лише власник), код (через Кода), дропшип-асортимент Toysi, ціни/каталог площадок.
- **Автоматика:** `plutus-seller.md` (визначення) + запис у `agent_watch.py` (будиться на `## [X → Продажник]` у `SELLER_CHANNEL.md`) + `recall.py --config seller`. Панель піднімає повну персону `--agent plutus-seller`.
- **Ескалація:** технічне → Коду в каналі; розвилки/рішення власника (бюджет, умови, контакти) → `OWNER_INBOX.md`.

### Виконавець — виконання оплачених замовлень Upwork (агент+вотчер додано 2026-09-18, замовлення Консультанта)
- **Місія:** виконує оплачені замовлення клієнта Upwork рівно за його критеріями (дослідження/збір/звірка даних). Продажник знаходить і продає, Виконавець робить, Консультант звіряє й здає — три ролі свідомо не змішуються.
- **Володіє:** деліверабли замовлень (формат за ТЗ клієнта), чекліст критеріїв, списки прийнятих/відхилених кандидатів; `EXECUTOR_CHANNEL.md` (його I/O з Консультантом). 🔴 Жоден рядок деліверабла не існує без живого джерела цієї сесії — «не знайдено» ОК, вигадане значення НІ.
- **НЕ торкається:** спілкування з клієнтом (лише Консультант), умови/ціна/строки/обсяг контракту, податковий профіль/дані акаунта/виведення коштів, пошук нових замовлень (Продажник), код у репозиторії (Код).
- **Автоматика:** `plutus-executor.md` (визначення, `tools:` +браузер — штатний режим повної сесії) + запис у `agent_watch.py` (будиться на `## [X → Виконавець]` у `EXECUTOR_CHANNEL.md`/`CONSULTANT_CHANNEL.md`, модель `sonnet` не `haiku`, БЕЗ розкладу — під замовлення, не за годинником; фонове пробудження без браузера — лише читає канал, ніколи не звітує про зібрані дані) + `recall.py --config executor`. Панель піднімає повну персону `--agent plutus-executor`.
- **Ескалація:** технічне → Коду в каналі; сумніви щодо критеріїв/розвилки → Консультанту в `EXECUTOR_CHANNEL.md`; рішення власника (бюджет, умови, акаунт) → `OWNER_INBOX.md`.

---

## 2. АВТОМАТИКИ (cron / таски — крутяться самі) × МІСЦЕ × ЩО РОБЛЯТЬ

> Машинно-читаний реєстр для `system_map_driftcheck.py` — унизу файлу (розділ 6). Таблиці тут — людям.

### 2А. Локальна Windows-машина (підтверджено живо 2026-08-20)

| Task | Що запускає | Домен |
|---|---|---|
| `PlutusToys_AgentWatch` | `agent_watch.py` (30 хв) | координація (будить SEO/SMM/Код/Продажник/Виконавець на нові записи каналів; Консультант — так само + періодично раз на 3 год 09:00-21:00 Київ) |
| `PlutusToys_SystemMapDriftCheck` | `system_map_driftcheck.py --alert` (щодня 08:40) | сам звіряє цей SSOT із живими тасками, Telegram-алерт при дрейфі |
| `PlutusToys_CriticalCalendar` | `critical_watch.py` (візуальне вікно) | світлофор термінів ключів/балансів/абонплат (критичний календар) |
| `PlutusToys_RozetkaLocalChain` | `run_rozetka_local.py` | Rozetka: pull цін → **товарознавець** → commit membership → фід |
| `PlutusToys_RozetkaPricePull` | `rozetka_price_monitor.py` | Rozetka: пул цін конкурентів |
| `PlutusToys_RozetkaKeepalive` | `rozetka_price_monitor.py --keepalive` | Rozetka: тримати сесію вітрини теплою |
| `PlutusToys_PromCatalogHistory` | `prom_cabinet_catalog.py --summary` | Prom: денний знімок каталогу (+ лічильники повноти) |
| `PlutusToys_PromCabinetKeepalive` | `prom_notifications_scraper.py --keepalive` | Prom: тримати кабінетну сесію теплою |
| `PlutusToys_EvaCabinetKeepalive` | `eva_cabinet_scraper.py --keepalive` | EVA: тримати кабінетну сесію теплою (той самий патерн — сесія падала, бо `scrape()` лише читав, ніколи не пересохраняв storageState; закрив після 3+ днів мертвого входу, 2026-09-18) |
| `PlutusToys_SellerWatchdog` | `plutus_seller_watchdog.py` (кожні 15 хв) | Продажник: детекція смерті живої `/loop`-сесії (вікно консолі з заголовком «Продажник*» відсутнє) + один Telegram-алерт + запис у `SELLER_CHANNEL.md` на епізод смерті (повтор — раз на 3 год, без спаму щоцикл). **БЕЗ авторелончу навмисно:** `.claude/agents/plutus-seller.md` і `wake_prompt`-Продажника в `agent_watch.py` досі стара місія «виробники іграшок», без Upwork і без браузерних `tools:` — сліпий `--agent plutus-seller` підняв би не той напрям і замаскував би падіння. Закрив інцидент 2026-09-18 (claude.exe app-hang, Event Log Id=1002 20:25:12): жива сесія Продажника загинула разом з процесом і лишалась непоміченою >24 год, поки Консультант не помітив мовчання каналу вручну |
| `PlutusToys_PromConvergenceMonitor` | `prom_convergence_monitor.py` | Prom: контроль збіжності каталогу до 6000 |
| `PlutusToys_MarketplaceActions` | `run_marketplace_actions.py` (кожні 6 год) | EVA/ALLO: періодичний автоцикл ДІЙ — EVA повний імпорт «через посилання» (нові товари→модерація) + ALLO авто-зіставлення майстра «Зіставлення даних» + подача «Нових» на модерацію (`eva_cabinet_scraper.py --full-import --apply`, `allo_cabinet_scraper.py --auto-cycle --apply`). Best-effort; на протухлій сесії скрейпери сигналять (Telegram) і пропускають, не діють наосліп. Разовий `--login` власником per-платформа. Закрив прогалину: Rozetka/Prom мали автоцикл, EVA/ALLO — ні (наказ власника 2026-08-31) |
| `PlutusToys-TelegramOutbox` | `telegram_outbox_processor.py` | інфра: черга вихідних Telegram |
| `PlutusToys-CabinetAudit` | `local_cabinet_audit.ps1` | аудит кабінетів (Prom/Rozetka) + КОДВ-леджери кандидатів: `rozetka_commission_ledger`, `eva_orders_ledger`, `eva_commission_ledger` (фактична комісія EVA з картки замовлення, замість 15%-оцінки), `prom_commission_ledger` (комісія Prom з Orders API у графу 9, §3 довідника), **`rozetkapay_registry_kandydaty`** (СТОРНО-детектор + еквайринг-частина графи 9 з реєстру FC/RozetkaPay: банк-сторно та Rozetka-еквайринг → кандидат, крос-звірка з книгою read-only), **`novapay_registry_kandydaty`** (COD-платежі NovaPay, яких НЕ внесено в книгу → кандидат; реюзає парсер `novapay_statement`, крос-звірка з книгою read-only — book-check десктопний, бо VPS-`novapay_statement` звіряє лише з orders_db), **`toysi_returns_kandydaty`** (повернення товарів з таблиці «Взаєморозрахунки» toysi.ua/contact_info — читає ВСІ доступні напівмісяці, текстовим пошуком звіряє toysi-id/ТС-номер у наративі книги, графа 5 АБО графа 12; звірено живо 2026-09-18: 7/7 повернень знайдено, 2 з них ГЕНУІНО відсутні в книзі, не були відомі навіть попередньому ручному аудиту), **`vchasno_cabinet_scraper`** (⚠️ ВИПРАВЛЕНО 2026-09-19: попередній запис тут стверджував "Вчасно КЕП-гейтований, не скриптується" — помилково, живо перевірено: логін edo.vchasno.ua — Google OAuth. Playwright+storageState, той самий патерн, що EVA/Prom — `--login` раз власником, headless далі; читає список зовнішніх документів `/app/documents?folder_id=6008`, завантажує нові за номером (крос-звірка з локальним архівом), маршрутизує в підтеку за ЄДРПОУ контрагента) **+ `vchasno_akty_kandydaty`** (акти edo.vchasno.ua — читає файли `документи_КОДВ/*/*/*_akt_*`, які `vchasno_cabinet_scraper` тепер кладе автоматично (раніше — вручну живою сесією), номер документа з ІМЕНІ файлу, текстовим пошуком звіряє з книгою, графа 5 АБО графа 12 + числове ядро без провідних нулів/префікса — бухгалтер цитує Нову Пошту кирилицею «НП-», ALLO без префікса, Rozetka/Prom дослівно, HostIQ з 1-літерним суфіксом («1375492H»); звірено живо 2026-09-18/19 на реальних актах), **`privat_statement_kandydaty`** (щоденна виписка ПриватБанку — лист-нагадування без MIME-вкладення, `kodv_mail_archiver.py` витягує підписане посилання `att.privatbank.ua/efile/<токен>` з HTML-тіла й качає PDF БЕЗ логіну в Приват24 (не платний Автоклієнт API — `a6fbc01`); `pdfplumber.extract_tables()`; з 2026-10-05 (запит головного бухгалтера) спершу КЛАСИФІКУЄ операцію за призначенням і звіряє за класом: LiqPay — за SOID замовлення і еквайрингом брутто−нетто в тексті книги; виплата RozetkaPay — X/Y з реєстрами RozetkaPay за діапазоном дат + кожне замовлення в книзі; комісія банку — за базовою сумою платежу або сумою комісій за день; власні перекази / депозит Toysi / застава Rozetka — «не P&L», у кандидати не йдуть; решта — сума ±1 день у сирих графах 2/3/6/7/8/9/10. Звіт .md розбито на розділи без ПІБ фізосіб) — усі пишуть кандидатів у документи_КОДВ, книгу не пишуть. **+ `akty_zvirka`** (з 2026-10-06, запит головного бухгалтера, «одне правило» довідник §3: звіт-звірка місячних актів контрагентів — Prom, НоваПей, Розетка Пей, Термінал Розетка, РУШ/EVA, АЛЛО — із сумою кандидатів комісій: суму акта читає з PDF Вчасно (pdfplumber; суми звірено з журналом (142): NovaPay 5,89/10,38, RozetkaPay 105,43, EVA 103,78+69,24, Rozetka 322,13/61,20, Prom ×1,2 без пакета), кандидатів NovaPay/RozetkaPay бере з УСІХ архівних реєстрів (повно), Rozetka/EVA/Prom — з реєстру кандидатів/щоденних json (⚠️ неповно, позначено); колонки «акт є/нема», сума акта, сума кандидатів, різниця, «акт у книзі рядком №» (власний рядок vs лише згадка); при розбіжності — перелік замовлень потоку; ручна поправка суми/періоду — `документи_КОДВ/_akty_sumy.json`; READ-ONLY щодо книги, пише лише `документи_КОДВ/_zvirka_aktiv.md/.json`; разовий `kandydaty_backfill_fee.py [--apply]` — донаповнення `fee` у старих open-записах NovaPay/RozetkaPay) **+ `marketplace_requirements_gate.py`** — структурний гейт дисципліни: кожен фід, що вимагає авторитетного довідника площадки (дерево категорій/атрибути), мусить мати його ЗБЕРЕЖЕНИМ у репо + робочою автоперевіркою; реєстр у самому скрипті (EVA=enforced з `eva_category_reference.csv`+`verify_eva_category_map.py`; Prom/Rozetka/ALLO/Google=audit_pending). Порушення enforced → Telegram-алерт (крок гониться з увімкненим Telegram). **+ `archive_channels.py --apply`** — auto-archiver каналів агентів: старі записи (поза останніми ~40 / старші 30д) → `archive/<роль>/`, тримає гарячі канали лінивими (data-safe: append в архів ДО перепису гарячого) **+ `desktop_code_sync.py`** (з 2026-10-06; усі десктопні задачі — CabinetAudit, RozetkaLocalChain, Checkbox/Graph6/NovaPay-архіватор — перед роботою підтягують змерджений `master` у `C:\Users\smach\rozetka_agent` через `merge --ff-only`; раніше копія стояла на застарілій гілці й аудитовані PR на десктоп не потрапляли; нічого не затирає: лише гілка master + чисті відстежувані файли, інакше пропуск із рядком у логу) |
| `PlutusToys-Graph6Daily` | `graph6_daily.ps1` → `graph6_daily.py` | КОДВ: собівартість реалізованих замовлень Toysi (кабінет «Історія замовлень», лише «Відвантажене») → кандидати графи 6 у документи_КОДВ (read-only, книгу не пише) |
| `PlutusToys-NovaPayRegistryArchiver` | `novapay_registry_archiver.ps1` → `kodv_mail_archiver.py` | КОДВ: архів реєстрів NovaPay + актів звірки НоваПошта **+ реєстрів FC/RozetkaPay** («реєстр платежів» → тека RozetkaPay) **+ виписок ПриватБанку** (best-guess маркери, тека ПриватБанк — звірити за першим листом) у документи_КОДВ (read-only IMAP, книгу не пише) |
| `PlutusToys-ChecboxRegistrySync` | `checkbox_registry_sync.ps1` → `checkbox_registry_sync.py` | КОДВ: нові фіскальні чеки Checkbox → кандидати доходу у документи_КОДВ (read-only API, книгу не пише) |
| `PlutusToys_KandydatyStaleCheck` | `kandydaty_registry.py stale-check` (щодня 08:30) | КОДВ: сигнал «книга стоїть» (Аудитор, 2026-09-29) — відкриті кандидати реєстру старші 2 діб, не визнані винятком → ОДИН throttled Telegram-алерт (раз/добу, `send_throttled_alert`). Читає той самий `_vidkryti_kandydaty.json`, що наповнюють kandydaty-скрипти нижче |
| `PlutusToys_RozetkaReturnsMonitor` | `rozetka_returns_monitor.py` (кожні 4 год) | Rozetka Delivery: незабрані/відмовлені RMP-посилки (замовлення 11/12/19) → Telegram на ПЕРЕХОДІ стадії; «Очікує відправника» = ДІЯ: забрати в Алматинська, 4 («Реєстр повернення відправлень»). NP-автоповернення на RZ-посилки не діє. Читає Orders API + публічний трекінг RZ-Delivery, нічого не змінює |

> **`kandydaty_registry.py` — спільний ПЕРСИСТЕНТНИЙ реєстр відкритих кандидатів** (2026-09-18,
> розширено 2026-09-28/29): незалежний від курсора КОЖНОГО джерела (курсор = «бачили в джерелі»,
> реєстр = «ще НЕ в книзі» — окремі поняття, щоб незастосований факт не зникав, коли курсор рухається
> далі). Джерела зараз: `checkbox`, `privat_statement`, `toysi_returns`, `vchasno_akty` (з 2026-09-18)
> **+ `rozetka_commission`, `eva_commission`, `novapay_registry`, `rozetkapay_registry`** (2026-09-28,
> той самий клас бага — курсор губив факти назавжди; `rozetka_commission`/`rozetkapay_registry`
> реєструють роялті/логістику/сторно/еквайринг ОКРЕМИМИ записами `order_id:kind`, не комбінованою
> сумою — бухгалтер пише компоненти в Графу 5 окремо) **+ `rozetka_reserve_release`** (2026-09-29,
> §1в: «Зняття резерву за невиконане замовлення» — товар покупець не отримав, роялті НІКОЛИ не
> прийде, гроші йому належить повернути; `rozetka_commission_ledger.py` шле ПРЯМИЙ Telegram-алерт
> на кожен НОВИЙ запис цього джерела, окремо від щоденного зведення — це не "бракує рядка в книзі",
> а "власнику треба знати одразу"). Критерій закриття — `amount_applied_in_text()`
> (числове порівняння, не текстовий substring; негативний контекст типу «бракує N» виключено) через
> `resolve_open_candidates_by_text()`. CLI: `python kandydaty_registry.py report|stale-check|ack <key> <причина>`
> — `ack` пише `_vidkryti_kandydaty_ack.json` (визнаний виняток, лишається видимим у звіті, не сигналить
> stale-check повторно). Бекфіл (`--backfill`, `collect(ignore_cursor=True)`): зроблено ЖИВО 2026-09-29
> для `rozetka_commission` (+11 фактів) і `eva_commission` (+23 факти) — best-effort у межах ПОТОЧНОГО
> вікна кабінету (~20 рядків/вкладку), НЕ повна історія. `novapay_registry`/`rozetkapay_registry` —
> той самий механізм ще НЕ портовано (нема доказу такого ж класу втрати для цих двох джерел).

> **Чому Rozetka + Prom-кабінет + agent_watch крутяться ЛОКАЛЬНО, а не на VPS:** вітрина/кабінет
> захищені антиботом, який пропускає лише справжній Chrome із профілем (bundled chromium → 403/500);
> agent_watch будить сесії через локальний `claude`. VPS — headless, туди це не переноситься.
>
> **Панель керування (on-demand, НЕ таска):** `control_panel.py` — локальний сервер `127.0.0.1:8787` (запуск `run_panel.bat` або `python control_panel.py` з теки репо). Дає: статуси 7 агентів (Код/SEO/SMM/Продажник/Виконавець/Консультант/Бухгалтер) + telegram-дайджест (`telegram_digest.py`, по запиту з `reports/telegram_alerts.md`) + критичні плитки; по кожному агенту — чат (`claude -p`), задача-в-канал (agent_watch підхопить), термінал (shell у теці репо), **«відкрити сесію» = ПОВНИЙ агент** (`claude --agent plutus-<роль>` у теці репо: роль+правила з `.claude/agents/plutus-{seo,smm,kod,consultant,kodv}.md` + навичка через `skills:` — seo-agent/plutustoys-smm/business-consultant/accountant; канали Cowork через --add-dir). Синк-скіли завантажуються раз: `CLAUDE_CODE_SYNC_SKILLS=1 claude -p ...` → `~/.claude/skills/synced/`. localhost-only + CSRF (`X-Panel`). Запускає власник, не cron.

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
| `order-status-tracker` | `order_status_tracker.py` | замовлення: статуси доставки/ТТН + **автоповернення НП** при скасуванні покупцем (`_maybe_create_np_return` → `nova_poshta.create_return_order` orderCargoReturn; гейт `NP_RETURN_APPLY`, дефолт DRY-RUN; ідемпотентно `np_return_created_at`; довідник `технічні_вимоги_маркетплейсів/nova_poshta.md`). enabled+active |
| `np-warehouse-sync` | `nova_poshta_warehouse_cache.py` | замовлення: нічна (03:00) синхронізація локального кешу довідника відділень/поштоматів НП (`np_warehouses_cache.db`, посторінково `AddressGeneral.getWarehouses`) — за офіційною рекомендацією НП («оновлювати щоночі»). `nova_poshta.warehouse_by_ref()` читає кеш ПЕРШИМ (мілісекунди, без throttle-ризику), фолбек на живий запит лише при кеш-місі. Закриває структурно throttle-клас, що спричинив інцидент 906260104 (PR #553-556 закрили ретраєм; це — корінь). Read-only до НП, пише лише у власний файл, orders.db не чіпає |
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
  **Prom delisted-коло (08.10.2026, історія #97→#98→0175329→#255, `prom-delisted-circle-history` у пам'яті):** `_delisted_since[pid]` — ФАКТ ІСТОРІЇ, не перемикач; повернення пишеться в `_readded_at[pid]`, ефективно виключені = `competitor_pricing.effective_delisted` (`load_delisted_pids` віддає лише їх). `_recheck_delisted_pids`: черга за `_recheck_at` на записі (мертві не їдять партію), Фаза 1 — «відсутній на Prom і ніколи не створювався» (НЕМАЄ жодного доказу існування: `_ever_live`/журнал штовхнутих/запис ціни в price_state/own_link prom_id/категорія в кеші/«вже повертали» — `_ever_live` сліпий до невидимих груп, журнал прунить 404, тож за ним самим «never-created» хибний) знімається без гейту живості (кап `READD_CLEAR_CAP_PER_RUN`/прогін), Фаза 2 (колись існували) — лише за `prom_readd_policy.json` (off/canary/all; зараз canary на ОДНОМУ pid 258006 — тест «чи створює Prom заново видалений external_id», решта off до результату), грація `READD_GRACE_HOURS` проти ghost-блоку (`_ghost_check_candidates`), відмова після `READD_GIVEUP_HOURS`; лічильники «чому нуль» — рядок «Recheck delisted — підсумок», `_meta.delisted_recheck`/`readd_stats`, Telegram після 3 прогонів «перевірено ≥100, знято 0». Тест `test_delisted_readd.py`.
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
- **Власний сайт-магазин `plutustoys.com.ua`:** `site/build_site.py` (статичний генератор з каталогу Toysi:
  головна/каталог/категорії/картки/кошик/пошук, `site/assets/*`; **ціна = знижена ціна Toysi × 1.5 (як EVA), каталог = увесь in-stock Toysi,
  сайт НЕ залежить від Prom/маркетплейсів — жодних цін конкурентів**), `liqpay_client` (підпис/колбек LiqPay, sandbox),
  `site_order_api` (HTTP-шар: NP-автокомпліт + `POST /api/order` prepaid+payment_confirmed=0 + колбек LiqPay).
  Веб-замовлення = `platform='site'`, форвард у Toysi через наявний order-pipeline лише по підтвердженій оплаті.
  **VPS = Apache під Webuzo (nginx немає)**: активація — `deploy/activate_site_apache.sh` (одна команда: збірка → systemd → Apache-vhost
  `z-plutustoys-com-ua.conf` на явній IP → чекає DNS → certbot webroot → HTTPS; відкат + звірка чужих vhost'ів до/після). `deploy/nginx-plutustoys.conf`/
  `activate_site.sh` — лише довідка для хоста з nginx.
  Деплой-артефакти — `deploy/site-order-api.service` (venv-python daemon, :8901), `deploy/site-rebuild.{service,timer}`
  (ребілд сайту кожні 2 год (з 2026-10-06; було 4×/день) — свіжі ціни/наявність + оновлення GMC-редиректів; Apache перечитує мапу за mtime), `deploy/nginx-plutustoys.conf`,
  `deploy/activate_site.sh` (nginx-хост), `deploy/DEPLOY_SITE.md`.
  **GMC-міграція:** `generate_prom_redirects.py` інвертує `own_product_links_cache.json` → мапа 301 (nginx + Apache `RewriteMap txt:`)
  `/ua/p{prom_id}-*.html` → `/product-{toysi_id}.html`, щоб при перенесенні домену з Prom на VPS не пропала
  видимість у Google Merchant (старі Prom-URL з фіда не впали в 404).
  **🟢 АКТИВОВАНО 2026-10-05:** сайт у живу на `https://plutustoys.com.ua` (Apache-vhost `z-plutustoys-com-ua.conf`, сертифікат LE; DNS на
  **Cloudflare** — NS `anna/kevin.ns.cloudflare.com`, записи DNS only, НЕ Prom; реєстратор imena.ua). VPS-юніти `site-order-api` (daemon) і
  `site-rebuild.timer` (кожні 2 год) увімкнено `activate_site_apache.sh` — дописати їх у §2Б/§6 (drift-check це підкаже).
  Онлайн-оплата LiqPay ще НЕ підключена (працює накладений платіж).
  → Код (механіка) + SMM (дизайн) + власник (LiqPay).
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
- **Політика «що не продаємо» (усі вітрини):** `forbidden_products.py` → Код. ЄДИНА константа заборонених Toysi-категорій (зараз «мечі, ножі та шаблі», 246 SKU, рішення власника 2026-10-07: дитячий магазин, репутація) + `is_forbidden(item)`. Гейт стоїть у `generate_prom_feed._build_xml`, `generate_prom_feed_top.is_excluded_category` (звідти Google/Meta/Bing/ALLO через `select_top_items`), `generate_eva_feed` (`_qualifies_for_feed` ВИЩЕ за промо-обхід категорій + `_build_xml`), `generate_rozetka_feed` (обидва дубльовані фільтри), `generate_allo_feed` (і заморожений знімок фільтрується при читанні), `site/build_site` (старі `product-<pid>.html` прибирає крок 8 build). НЕ фільтр у `parser.fetch_toysi_catalog`: каталог читають `order_router`/звіти/репрайсер, невідомий SKU = «немає в наявності» (`order_router.py:190-206`) і відкрите замовлення зависло б. `test_forbidden_products.py` (`--live`, `--feeds DIR`) падає, якщо з'явиться генератор вітрини без політики; НОВИЙ генератор мусить викликати `is_forbidden`. Окрім категорії — `FORBIDDEN_PRODUCT_IDS` (6 точкових pid, 07.10: сувенірні ножі/сокира/катана/KUNAI + ніж із тюбиком крові). `forbidden_watch.py` — детектор-ДОПОВІДАЧ (ключ за цілим словом ніж/меч/сокира/…, НЕ автозаборона: precision ≈ 8%): щодня з `catalog_health_monitor` (VPS-таймер) шле Telegram про нові нерозглянуті кандидати і пише `blade_unreviewed` в `catalog_health_history.jsonl`; «переглянуті» — `forbidden_products_reviewed.json` (verdict+дата+хто).
- **Toysi / RoyalToys (постачальник):** `toysi_cabinet_scraper`, `parser` (fetch_toysi_catalog),
  `royaltoys_parser`, `compare_royaltoys_toysi`, `generate_royaltoys_feed` → Код.
  `zoobaza_parser` (ZooBaza, товари для тварин, публічний YML; cost=price/1.5 (до 04.10 було /1.4); корми не беремо, одяг за прапорцем) → Код;
  ЛИШЕ парсер — не підключений до фідів/цін/замовлень (комісії по категоріях і ручні замовлення чекають рішень).
  **ZooBaza — повністю окремий контур** (рішення власника 06.10; схема `ZooBaza_схема_від_А_до_Я_2026-10-07.md` у Cowork): `zoobaza_prom_feed.py` (окремий Prom-фід пілоту: білий список `zoobaza_pilot_skus.txt`, ВСІ id з префіксом `zb-`, Prom-категорії 181201/181206/181203, ціна = max(РРЦ опт×1,5; підлога чистої маржі після комісії 8% і еквайрингу), fail-closed, атомарний запис, БЕЗ публікації; не імпортує Toysi-модулів) і `zoobaza_intake.py` (claim замовлень з `zb-`-позиціями у статуси `zoobaza_hold`/`zoobaza_mixed_hold` + власний `zoobaza_state.json`; **поки НЕ підключений до `order_pipeline`/`orders_db`** — дві мінімальні точки дотику чекають рішення власника D1). Гард `assert_cost_constant` 2026-10-07 спіймав зміну коефіцієнта фіду постачальника ×1,4→×1,5 (ціна фіду = РРЦ); дефолт `ZOOBAZA_FEED_TO_OPT=1.5`.
  `zoobaza_competitor_report` — разовий read-only звіт «найдешевший довірений конкурент на Prom по SKU ZooBaza» (та сама `find_best_competitor`, що для іграшок, + варіант із перевіркою розміру без одиниці; підлога параметрична за комісією).
- **Накладений платіж НП = цілі гривні (08.10.2026, запит головного бухгалтера 06–07.10):** `cod_amount.py` → Код. НП приймає накладений платіж лише цілими гривнями і відкидає копійки (підлога; на orders.db сума дробових частин COD-замовлень III кв. = 13,41 ₴ — ТОЧНО ПІДЛОГА). `order_router.build_toysi_order` передає Toysi `moneyback = collected_cod_amount(...)` (лише carrier=nova_poshta, COD), `order_status_tracker._maybe_issue_receipt` видає COD+НП-чек на ту саму суму (ціна ОДНІЄЇ одиниці найдорожчого рядка зменшується на копійки, рядок з qty>1 розщеплюється; нових полів Checkbox API нема). Укрпошта, Rozetka Delivery, передоплата — без змін; уже видані чеки не чіпаються (рішення бухгалтера). Тест `test_cod_amount.py`.
- **КОДВ (фінанси):** `checkbox_client`, `novapay_statement`, `weekly_balance_digest`, `daily_report`,
  `kodv_book_writer.py` (єдина точка запису НОВОГО рядка книги — механічно перевіряє дубль номера
  документа в Графі 5 ПЕРЕД записом, PR #572; замінює ad-hoc openpyxl-виклики для нових рядків,
  правки існуючих рядків лишаються ручними),
  `kodv_return_receipt.py` (ручний CLI: фіскальний чек RETURN Checkbox на повне повернення за серіалом продажу; живий запуск
  лише з `--expect-fiscal`, замок `.local_secrets/kodv_return_<serial>.lock`, дубль-перевірка; запускає КОДВ після дозволу власника)
  → **КОДВ/власник**.
- **Telegram / сповіщення (спільна інфра):** `telegram_notify`, `telegram_outbox_processor`,
  `telegram_userbot_client`, `telegram_userbot_login` → Код.
- **Координація / інфра / деплой:** `agent_watch`, `service_watchdog`, `vps_code_sync_report`,
  `deadline_reminder`, `system_map_driftcheck`,
  `recall.py` (антидубль/пам'ять: «що вже зроблено про X» з git+коду+SYSTEM_MAP+CODE_LOG;
  `--file` = дубль-варта, exit 3 якщо схоже вже є. Хук `recall-guard.sh` [локальний, `~/.claude/hooks/`]
  автоматично впорскує його перед створенням нового файлу — щоб після ущільнення сесії не робити дублі;
  `--config <роль> --restore` = картка стану (заголовки останніх записів каналів ролі + відкрите в OWNER_INBOX),
  `--detect-role <транскрипт>` = роль сесії з її 1-го повідомлення (cwd у всіх ролей однаковий); хук
  `recall-inject.sh` [локальний] підключає їх окремо — зміна самого хука за власником),
  `archive_channels.py` (auto-archiver проти розпухання: SEO_CHANNEL/КОДВ_CHANNEL/MARKETING_CHANNEL/
  CONSULTANT_CHANNEL.md [Cowork] + `STATUS.md`[Cowork] + `CODE_LOG.md`[репо] за віком/лімітом кількості
  запису в `archive/<роль>/`; `OWNER_INBOX.md`[Cowork] — лише явно ЗАКРИТІ записи, ВІДКРИТІ/неоднозначні
  ніколи не архівуються. Запускає `PlutusToys-CabinetAudit` через `local_cabinet_audit.ps1 --apply`
  щодня — виконує ЖИВУ робочу копію файла в теці репо незалежно від git-гілки/мержу, тому недомерджені
  правки цього скрипта вже впливають на продакшн-канали до аудиту/PR, архітектурний аудит 2026-09-14)
  → Код (координатор).

---

## 4. АЛГОРИТМ СПІВПРАЦІ (консолідовано з BOOTSTRAP / COORDINATION_PROTOCOL / CODE_LOG)

1. **Канали — асинхронно через файли** (живі spawn_task/SendMessage між агентами НЕ працюють — перевірено):
   `SEO_CHANNEL.md` (Код↔SEO+якість), `MARKETING_CHANNEL.md` (Код↔SMM). Newest-on-top, тег `[X → Y] дата`.
2. **Пробудження:** `PlutusToys_AgentWatch` (30 хв) читає канали й будить агента через `claude -p`, коли
   з'явився новий `[X → тобі]` запис. Анти-пінг-понг: підтвердження/закриті НЕ відписуються; денна стеля
   пробуджень; тиша ≠ поломка (агент може бути заблокований на власнику).
2a. **🔴 ВИПРАВЛЕНО (2026-09-18, застаріле більше місяця, двічі позначено боргом — бриф переозброєння
   Продажника §6.3 і онбординг Кода):** цей пункт раніше стверджував «модель B — headless-Код має Bash →
   сам збирає код і ВІДКРИВАЄ PR». **Це скасовано.** Коміт `d7861e2` (2026-08-21) — «реверт B → модель A»
   — і живо перевірено 2026-09-14/17: усі чотири вотчери (`Код`/`SEO`/`SMM`/`Продажник`) на дефолтному
   наборі `allowed_tools = Read, Edit, Write, Glob, Grep`, **БЕЗ Bash**. Чинна модель — **A**, як і
   описано в 2d нижче: headless-Код лише ТРІАЖИТЬ (читає канал, пише «прийнято в чергу» + рядок у
   `OWNER_INBOX.md`), код/PR готує й відкриває ІНТЕРАКТИВНА сесія. SEO/SMM/Продажник лишаються
   файлово-only (їм Bash взагалі не давали, не лише відібрали).
2b. **Пробудження не спрацьовує — діагностика:** стан у `.local_secrets/agent_watch/<роль>.json`
   (`wakes_today`, `seen`, `last_wake_at`); канали newest-on-top (свіже — вгорі, не в `tail`).
2c. ~~**Анти-тиха-втрата (D, 2026-08-21)**~~ — **ПРИБРАНО 2026-09-21 (PR #583).** Раніше: якщо агент
   прокинувся на запит (exit-0), але НІЧОГО не написав у канал — монітор слав Telegram-алерт. Живий аудит
   `reports/telegram_alerts.md` показав, що це 168/590 (28%) УСІХ Telegram-повідомлень системи за весь час —
   найбільше джерело шуму; звірено живо проти `SELLER_CHANNEL.md`, реального бага не ловив (агент відповідав,
   просто з затримкою в години). Власник наказав прибрати. `seen` і далі просувається як і раніше (без циклів),
   лише алерт і mtime-детекція, що його живила, видалені.
2d. **Автопробудження НЕ вантажить персону/CLAUDE.md/скіл (перевірено живо 2026-09-14, архітектурний
   аудит):** `agent_watch.py`'s `_wake()` (headless `claude -p`) НЕ передає `--agent <slug>` і стартує з
   `cwd=COWORK_DIR`, тому `.claude/agents/plutus-{kod,seo,smm}.md`, корене-репо `CLAUDE.md` (гейт доказу
   включно) і скіли (`/seo-agent`, `/plutustoys-smm`) НЕ завантажуються — headless-сесія керується ЛИШЕ
   інлайн-текстом `wake_prompt`/`periodic_prompt` у самому `agent_watch.py` (ручний паралельний
   переказ правил, без автоматичної звірки з джерелом істини). Headless-Код додатково НЕ має доступу до
   репо (пісочниця) — лише тріаж у чергу, код/PR не пише. **Лише ручний запуск** (панель керування
   `control_panel.py`, `--agent <slug>` + `cwd=BASE_DIR`) вантажить повну персону+`CLAUDE.md`+скіл.
   КОДВ (Бухгалтер) свого запису у `WATCHERS` НЕМАЄ (не має власної headless-сесії, що будиться на
   щось адресоване ЇЙ) — йде лише ручним шляхом (панель). **Консультант — Є в `WATCHERS`
   (додано 2026-09-19, замовлення CONSULTANT_CHANNEL.md 18.09 (5)):** подієво на `## [X →
   Консультант]` у `SELLER_CHANNEL.md`/`CONSULTANT_CHANNEL.md`/`КОДВ_CHANNEL.md` + періодично раз
   на 3 год у вікні 09:00-21:00 Київ (`schedule={"every_hours":3,"hour_start":9,"hour_end":21}`,
   новий параметр `_periodic_due()`), той самий headless-ліміт — без браузера, лише inline
   `wake_prompt`/`periodic_prompt`. Замовлення (6) «браузер мені ПОТРІБЕН для вибіркової
   перевірки» закрито ПОВНОЮ сесією, не вотчером: `.claude/agents/plutus-consultant.md:4` тепер
   має ті самі 9 браузерних `tools:`, що й Виконавець (панель → Консультант → 🔗 відкрити сесію).
   **АЛЕ** (знахідка
   Консультанта, виправлено 2026-09-17, PR #561): вотчер «Код» тепер ДОДАТКОВО слухає
   `CONSULTANT_CHANNEL.md`/`КОДВ_CHANNEL.md` (раніше лише `SEO_CHANNEL.md`/`MARKETING_CHANNEL.md`) —
   інакше запис `## [X → Код]` у цих двох каналах НІКОЛИ не будив headless-Код. Продажник — Є в
   `agent_watch.py` (додано 2026-09-17): будиться на `## [X → Продажник]` у `SELLER_CHANNEL.md`,
   як SEO/SMM (headless, лише інлайн-`wake_prompt`, без повної персони).
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
    "PlutusToys_EvaCabinetKeepalive", "PlutusToys_SellerWatchdog",
    "PlutusToys_PromConvergenceMonitor", "PlutusToys_CriticalCalendar",
    "PlutusToys_MarketplaceActions",
    "PlutusToys-TelegramOutbox", "PlutusToys-CabinetAudit",
    "PlutusToys-Graph6Daily", "PlutusToys-NovaPayRegistryArchiver",
    "PlutusToys-ChecboxRegistrySync", "PlutusToys_KandydatyStaleCheck", "PlutusToys_RozetkaReturnsMonitor"
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
