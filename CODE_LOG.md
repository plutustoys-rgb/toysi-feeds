
## 2026-08-30 — Виправлення: запит на скасування шлеться в ЧАТ Toysi (юзербот), не власнику

Власник вказав на мою помилку: тікет скасування я поставив на `send_telegram_message` (алерти власнику з ручним пересиланням), заявивши «каналу до Toysi нема». ХИБНО — `telegram_userbot_client.send_marking(text, to_toysi=...)` шле прямо в чат менеджера Toysi (Chechetenko), той самий канал, що RZ-Delivery маркування. Переписав `order_status_tracker._maybe_ticket_rozetka_cancelled`: тепер `send_marking` у чат Toysi з тим самим гейтом `MARKING_TEST_MODE` ('1' дефолт → номер власника; '0' → реальний Toysi), мітка ЛИШЕ на успіху (ретрай при UserbotError), + FYI власнику. Тест 6/6 (додано: гейт to_toysi у тест/бойовому, збій→ретрай). Довідник rozetka.md виправлено. Поставив собі memory-флаг проти вигадування архітектури без grep.

## 2026-08-30 — Rozetka: обробка скасування покупцем (pre-forward гейт + пост-forward тікет адміну)

Власник помітив на скріні кабінету Rozetka-замовлення 904664322 «Скасовано покупцем», яке ВЖЕ пішло в
Toysi. З'ясував у коді: (1) Rozetka НЕ мала pre-forward гейта проти скасування — Prom/EVA мали
(`_check_prom/eva_not_cancelled`), Rozetka ні, хоча докстрінг Prom-гейта прямо позначав це як TODO «коли
Rozetka стане активною»; (2) Toysi API взагалі не має методу скасування (лише order_create+order_status).
Власник: «якщо не можна відмінити треба автоматом відправляти у чат адміну тікет». Зробив гілкою
`rozetka-cancel-ticket`:

- **rozetka_client.py:** `ROZETKA_CANCELLED_STATUSES` = {13,17,18,28,29,32–45,49} — скасувальні ORDER-коди
  (звірено довідник rozetka.md/apidoc, 45=«Скасовано покупцем»). Навмисно БЕЗ 11/12/19 (пост-відправкові,
  їх ловить Toysi-трекер як returned).
- **order_router.py `_check_rozetka_not_cancelled`** (дзеркало Prom/EVA) — живий `get_order_status()` ПЕРЕД
  форвардом; скасувальний код → стоп + Telegram + `status='rozetka_cancelled_before_forward'` (виключено з
  `get_orders_ready_to_forward`). Fail-open (APIError/None → форвард дозволено).
- **order_status_tracker.py `_maybe_ticket_rozetka_cancelled`** — пост-форвард: замовлення вже в Toysi +
  скасоване на Rozetka → 🎫 ТІКЕТ адміну в Telegram (Toysi# + клієнт + «напиши менеджеру Toysi скасувати»),
  ідемпотентно (`rozetka_cancel_ticket_sent_at`). Додав `init_db()` у tracker's __main__ (standalone-запуск
  раніше за order_router не впаде «no such column»).
- **orders_db.py:** колонка `rozetka_cancel_ticket_sent_at` (SCHEMA + `_ensure_column` + `mark_*`),
  виключення `rozetka_cancelled_before_forward` у `get_orders_ready_to_forward`.
- Синтетичний тест (temp orders.db): 4/4 — pre-forward стоп+виключення, fail-open (6/APIError), пост-forward
  тікет+ідемпотентність, не-скасоване (61) без тікета. Довідник rozetka.md оновлено.

Замовлення 904664322 конкретно: після деплою tracker сам згенерує тікет (воно forwarded + статус 45), поки
активне. Ручна дія власника по ньому — написати Toysi скасувати, поки не відвантажили.

## 2026-08-29/30 — КОДВ-автоматизація: 5 битих Task Scheduler-задач + 2 прогалини комісій закрито

Власник передав задачу бухгалтера (видалив spawn_task-сесію): механічні КОДВ-рутини — детермінованими
скриптами, не LLM. Виявив: попередня сесія зареєструвала 5 Windows-задач під .ps1, яких не створила →
усі падали щодня (0xFFFF0000). Закрив усі 5 + 2 прогалини комісій. Усі збирачі пишуть КАНДИДАТІВ у
документи_КОДВ/, книгу НЕ пишуть (лише роль бухгалтер), крос-звірка книги READ-ONLY, кожен аудитований:

- **#426 kodv_mail_archiver.py** (→ novapay_registry_archiver.ps1) — IMAP read-only (BODY.PEEK, без Seen)
  реєстри NovaPay + акти НоваПошта → NovaPay/НоваПошта. Покрив 2 рутини. Бэклог: 10+7.
- **#427 checkbox_registry_sync.py** — Checkbox API /receipts/search → чеки-кандидати доходу + хінт
  «збігів суми в графі 2». Бэклог: 18 (8 нових ~1722₴).
- **#428 prom_commission_ledger.py** — Prom Orders API cpa_commission.amount + «частинами» + «дешева
  доставка» (§3 довідника, факт не оцінка) → графа 9. Токен order-scope у .env працює. Бэклог 606.81₴.
- **#429 graph6_daily.py** (→ graph6_daily.ps1) — собівартість реалізованих («Відвантажене») з кабінету
  Toysi «Історія замовлень» → графа 6. Сума ВКЛЮЧАЄ Збірку (§303, не донараховувати). Бэклог 14 (~2743₴).
- **#431 eva_commission_ledger.py** — фактична комісія з картки seller.eva.ua/merchant/orders/{id}
  («Сума комісії» Всього) замість 15%-оцінки (прогалина бухгалтера). Бэклог 10 (398.89₴, 8 не в книзі).
- **#430 KODVDailyCheck прибрано** — редундантна (Prom-дохід-детектор перекрито крос-звіркою #428).
- **#432 PrivatDailySync прибрано** — структурно нездійсненна: Privat Автоклієнт API 401 (платний тариф),
  лише вручну Приват24. bank_check.py готовий на випадок тарифу.

Уроки (у пам'ять): читати recall+довідники ПЕРЕД побудовою (не reverse-engineer наживо); дубль-варта
recall-guard тепер additionalContext (агенту), не ask (власнику); методологія комісій — у §3/§EVA/§Графа6
довідника, звірено живо. Prom-токен «протухає» від простою й оживає від використання (не products-only).
