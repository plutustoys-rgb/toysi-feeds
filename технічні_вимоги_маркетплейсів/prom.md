# Prom.ua — API замовлень (Orders) — довідник

Джерело: `https://public-api.docs.prom.ua/` (живий Swagger, звірено 2026-09-25) +
`orders_watcher.py` (наш конвертер). Публічне API prom.ua, версія 1.0 (закритий бета-тест).
Сервер: `https://my.prom.ua/api/v1`.

## Order schema (GET /orders/list, GET /orders/{id}) — delivery-поля дослівно

```json
"delivery_option": {"id": 0, "name": "string", "shipping_service": "string"},
"delivery_provider_data": {
  "provider": "nova_poshta",
  "type": "W2W",
  "sender_warehouse_id": "string",
  "recipient_warehouse_id": "string",
  "declaration_number": "string",
  "unified_status": "string"
},
"delivery_address": "string",
"delivery_cost": 0,
```

**`delivery_address` — гола вільнотекстова адреса.** Жодного окремого поля область/район у
статичній схемі немає. `orders_watcher.py:297` бере це поле напряму в `np_branch` без обробки
(на відміну від EVA/Rozetka, де є власні білдери `_eva_delivery_address`/`_rozetka_delivery_address`).

## ✅ ПІДТВЕРДЖЕНО ЖИВО (2026-09-25): структурний NP-реф ІСНУЄ, ми його НЕ читаємо

`delivery_provider_data.recipient_warehouse_id` — НЕ порожній рядок-заглушка в схемі: живий
запит до `/orders/list` (9 реальних NP-замовлень за 30 днів) повернув РЕАЛЬНІ NP UUID-рефи в
КОЖНОМУ з них, плюс вкладений об'єкт `recipient_address`:

```json
"recipient_address": {
  "city_id": "db5c88f5-391c-11dd-90d9-001a92567626",
  "city_name": "м. Львів (Львівська обл.)",
  "city_katottg": "UA4606025...",
  "warehouse_id": "3fb321ca-5682-11ed-9eb1-d4f5ef0df2b8",
  "street_id": null, "street_name": null, "building_number": null, "apartment_number": null
}
```

`city_id`/`warehouse_id` тут — той самий формат NP CityRef/WarehouseRef, що вже й так
резолвиться нашим `nova_poshta.warehouse_by_ref()` для EVA/Rozetka. **Наш код (`orders_watcher.py`,
`_convert_prom_order`) це поле НІКОЛИ не читає** — лише `delivery_provider_data.provider` (для
визначення carrier). `_convert_prom_order` не протягує ні `np_city_ref`, ні `np_ref_id` для Prom.

## 🟡 ВІДКРИТЕ ПИТАННЯ (не документне — часове): коли саме `recipient_address` з'являється

Це НЕ питання «чи є поле в API» (є, підтверджено вище) — а «чи доступне воно ДО того, як МИ
приймаємо рішення про маршрутизацію (форвард у Toysi)». Гіпотеза: `recipient_address` може
заповнюватись ЛИШЕ ПІСЛЯ того, як ми самі пушимо `declaration_number` назад у Prom
(`order_status_tracker.py:353-409`, `attach_prom_declaration_id` → `/delivery/save_declaration_id`)
— тобто Prom might resolve/back-fill recipient_address за НАШИМ ЖЕ ТТН через власну звірку з NP,
а не з вибору клієнта на чекауті. Якщо так — поле НЕПРИДАТНЕ для маршрутизації (з'являється вже
ПІСЛЯ того, як маршрут визначено).

**Живий тест (2026-09-25):** 9/9 NP-замовлень за 30 днів мали `declaration_number` ОДНОЧАСНО з
`recipient_address` — 0 «чистих» прикладів без `declaration_number`, щоб перевірити гіпотезу
напряму. Наш конвеєр форвардить+пушить ТТН достатньо швидко (цикл order-pipeline ~15 хв), що
«чисте» вікно для перевірки природним семплюванням практично не трапляється — ретроспективна
вибірка тут структурно НЕ вирішить питання (Консультант, 2026-09-25: «дані можуть мовчати роками»).

**Рішення (Консультант, 2026-09-25 — краще за лист): прогін уперед, не ретроспективна вибірка.**
Ретроспективна вибірка НЕ відповість на «коли саме» — історія зберігає кінцевий стан, не момент.
Замість листа `api@prom.ua` (винесено власнику як окреме зовнішнє повідомлення, не критичний шлях) —
`orders_watcher.py::_convert_prom_order` тепер логує (stderr) `recipient_address`-присутність для
КОЖНОГО нового NP-замовлення Prom ПРИ ПЕРШОМУ отриманні, ДО будь-якого нашого запису (insert_order/
push declaration_number). Наступне ж реальне замовлення дасть остаточну відповідь: присутнє на
першому погляді → поле раннє, придатне для маршрутизації, читаємо; відсутнє → заднім числом,
непридатне, питання закрито назавжди з доказом.

## Наслідок для наскрізного аудиту order→Toysi (2026-09-25)

Поки питання відкрите — Prom-адреса лишається текстовою (`delivery_address` напряму в `np_branch`,
без змін), той самий метод, що й до аудиту. `shipping_city`-фікс (область/район у самому полі,
PR #594) ПРАЦЮЄ для Prom так само, як і для інших площадок — ЛИШЕ КОЛИ `delivery_address`
сам містить область у форматі, який `parse_np_branch` розпізнає (доведено на замовленні №414634349:
Prom додає `(Xобл.)`, коли назва міста збігається з назвою області). Чи Prom робить так само для
двох однойменних СІЛ у різних областях (точний клас інциденту EVA 8-081747967) — **не перевірено**.

## Related
[[np-warehouse-routing-direct]], технічні_вимоги_маркетплейсів/toysi.md
