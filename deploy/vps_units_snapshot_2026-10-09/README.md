# Знімок systemd-юнітів VPS (2026-10-09) — ДОКУМЕНТАЦІЯ, не деплой

Тут лежать **36 файлів** (18 пар `.service` + `.timer`) — дослівна копія з `/etc/systemd/system/` на VPS,
знята Code-Agent (`cat`, read-only, 2026-10-09 ~17:20). Файли прочитано повністю: секретів нема
(лише шляхи, описи, розклади, `Environment=PATH=...`).

## Навіщо
До цього знімка ці 18 юнітів існували **лише на сервері** — у git їхніх файлів не було (`git ls-files`
порожній). Серед них `order-pipeline` (обробка замовлень), `feed-pipeline`, `prom-*`, `service-watchdog`,
`vps-code-sync`. Якби сервер загинув, відновлювати їх було б нема з чого.

## ЦЕ НЕ РОЗГОРТАЄТЬСЯ АВТОМАТИЧНО
`deploy_systemd_units.sh` (його викликає `vps_code_sync.sh` кожні 15 хв) читає **лише** `./*.service ./*.timer`
з КОРЕНЯ репо. Ці файли свідомо лежать у підпапці: якби їх покласти в корінь, `vps-code-sync` почав би
ними керувати — копіювати, робити `enable --now` і знімати їх із сервера при видаленні з репо.

## Стан таймерів на VPS (2026-10-09 ~17:30, Code-Agent, read-only `systemctl is-enabled` / `is-active`)
- **enabled + active (12):** daily-report, deadline-reminder, eva-feed, feed-pipeline, full-catalog-scan, order-pipeline,
  order-status-tracker, prom-catalog-auditor, prom-catalog-sync, prom-chat-bot, service-watchdog, vps-code-sync.
- **disabled + inactive (6):** `bank-check` (bank_check іде всередині order-pipeline), `order-router` і `orders-watcher`
  (злиті в order-pipeline, pt8), `prom-competitor-pricer` (репрайсер запускає feed-pipeline, `run_feed_pipeline_vps.sh:83`),
  **`eva-catalog-auditor` і `novapay-statement` — чи вимкнено навмисно, НЕ з'ясовано** (питання власнику/Аудитору/КОДВ).
Файли вимкнених юнітів лишаються в `/etc/systemd/system/` — вони тут для документації все одно.

## Що НЕ входить у знімок
- юніти з кореня репо (авто-деплой): `catalog-health-monitor`, `link-cache-validator`, `meta-feed-coverage-monitor`,
  `np-warehouse-sync`, `prom-review-requester`, `promo-margin-guard`, `social-*`, `system-map-driftcheck`;
- юніти сайту в `deploy/` (`site-order-api`, `site-rebuild.*`) — ручна активація;
- `webuzo*.service` (хостинг-панель, не наше).

## Обмеження (читай перед використанням)
1. **Знімок на дату.** Сервер міг змінитись; істина — `systemctl cat <unit>` на VPS.
2. **Файли не показують стан** (enabled/active) — його зафіксовано вище на дату знімка й у SYSTEM_MAP §2Б.
3. **`vps-code-sync.service/.timer`** у дампі мають порожній рядок між кожним рядком. Збережено як у дампі;
   чи так виглядають файли на сервері, чи це артефакт копіювання — невідомо.
4. Описи в деяких юнітах застарілі (напр. `full-catalog-scan` згадує `scan-state-data` — ту гілку вилучено,
   публікатор тепер no-op, див. CLAUDE.md).

## Відновлення (орієнтовно, НЕ перевірялось)
Скопіювати потрібні файли в `/etc/systemd/system/`, `systemctl daemon-reload`, `systemctl enable --now <name>.timer`.
Перед цим звірити зі SYSTEM_MAP §2Б, які юніти мають діяти.
