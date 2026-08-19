# VPS systemd-інвентар (що реально крутиться на /opt/plutustoys)

**Навіщо:** довго був дрейф — на VPS активні systemd-юніти, яких НЕМА в git (їх ставили вручну,
поза `deploy_systemd_units.sh`). Це була сліпа пляма: Код бачив лише код у репо, але не бачив, ЯК
і КОЛИ він запускається. Знято живим зведенням 2026-08-19 (`ExecStart` + розклад таймера).

**Статус юнітів:** ці ~17 — VPS-only (НЕ керуються `deploy_systemd_units.sh`, якого чіпають лише
юніти з кореня репо). У репо-корені лежать інші: social-poster-fb/ig, catalog-health-monitor,
link-cache-validator, meta-feed-coverage-monitor, prom-review-requester, social-dead-post-cleaner.

## Інвентар VPS-only юнітів (2026-08-19)

| Юніт | ExecStart (скрипт) | Розклад | Призначення |
|---|---|---|---|
| **vps-code-sync** | `vps_code_sync.sh` | OnBootSec=2min, **кожні 15 хв** | ⭐ АВТО-ДЕПЛОЙ: git pull master + deploy_systemd_units. Тобто код у master сам доїжджає на VPS ≤15 хв (ручний `vps_code_sync` лише пришвидшує). |
| **order-pipeline** | `order_pipeline.py` | *:09,24,39,54 (кожні 15 хв) | ⭐ MERGED fetch/save/forward замовлень (об'єднав orders-watcher+order-router 2026-07-16, закрив timer-race). |
| **orders-watcher** | `orders_watcher.py` | *:09,24,39,54 | ⚠️ ОКРЕМИЙ юніт із ТИМ САМИМ часом, що order-pipeline. Схоже, СТАЛИЙ (мав бути вимкнений при мержі 07-16). Перевірити, чи `.timer` активний — інакше подвійний fetch. |
| **order-router** | `order_router.py` | *:11,26,41,56 | ⚠️ ОКРЕМИЙ форвард (2 хв після watcher). Так само схоже на сталий після merge в order-pipeline. Перевірити активність. |
| **order-status-tracker** | `order_status_tracker.py` | OnBootSec=5min, кожні 30 хв | Оновлення статусів/ТТН назад у маркетплейси. |
| **bank-check** | `bank_check.py` | OnBootSec=3min, кожні 15 хв | Підтвердження оплати передоплачених (PRIVAT). Зараз автоперевірка off → ручне. |
| **service-watchdog** | `service_watchdog.py` | OnBootSec=6min, кожні 10 хв | Алерт у Telegram, якщо orders-watcher/bank-check застрягли. |
| **feed-pipeline** | `run_feed_pipeline_vps.sh` | OnBootSec=5min, **кожні 6 год** | Генерація всіх фідів. |
| **eva-feed** | `run_eva_feed_vps.sh` | щогодини :30 | EVA-фід. |
| **prom-catalog-sync** | `prom_catalog_sync.py --apply` | 07,11,15,19:30 | Синхронізація Prom-каталогу. |
| **prom-catalog-auditor** | `prom_catalog_auditor.py` | *-*-* 08:00 | Аудит Prom-каталогу. |
| **prom-competitor-pricer** | `prom_competitor_pricer.py --apply` | *-*-* 06:00 | Репрайсер Prom (до аудитора о 08:00). |
| **prom-chat-bot** | `prom_chat_bot.py` | OnBootSec=2min, кожні 5 хв | Prom-чат-бот. |
| **eva-catalog-auditor** | `eva_catalog_auditor.py` | *-*-* 07:30 | Аудит EVA-каталогу. |
| **full-catalog-scan** | `full_catalog_scan.py` | *-*-* 01:00 | Повний скан каталогу Toysi. |
| **daily-report** | `daily_report.py` | *-*-* 09:00 | Щоденний звіт. |
| **deadline-reminder** | `deadline_reminder.py` | Пн 09:00 | Нагадування про дедлайни. |
| **novapay-statement** | `novapay_statement.py` | OnBootSec=5min, кожні 30 хв | NovaPay-виписки. |

## 🔴 Знахідки, які ховала сліпа пляма
1. **АВТО-ДЕПЛОЙ існує** (`vps-code-sync` кожні 15 хв) — код у master сам доїжджає на VPS. Тобто мої «треба задеплоїти» були зайві (ручний sync лише пришвидшує). Це також пояснює «Already up to date» — VPS уже підтягнув.
2. **Потенційне подвоєння order-флоу:** `order-pipeline` (merged) І окремі `orders-watcher`+`order-router` мають розклади. Якщо всі три `.timer` активні — подвійний fetch/forward (той самий race, який merge мав закрити). Треба перевірити `systemctl is-enabled/is-active` для orders-watcher.timer/order-router.timer.

## Крок 2 (за рішенням власника) — git-керування
Щоб git став джерелом істини для цих юнітів (і зникли сталі), треба внести їх у корінь репо БАЙТ-У-БАЙТ (інакше наступний `deploy_systemd_units` затре робочий). Робити по підсистемах, з байт-звіркою. Поки — цей інвентар лише для видимості.
