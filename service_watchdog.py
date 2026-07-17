import json
import os
import re
import subprocess
import sys
from datetime import datetime

import order_router
from orders_db import get_connection, get_active_toysi_orders, get_orders_ready_to_forward
from telegram_notify import send_telegram_message
from toysi_order_submit import fetch_order_statuses, ToysiAPIError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

"""
Watchdog для orders_watcher.py/bank_check.py: якщо systemd-сервіс не мав
жодного успішного завершення ("Finished ...", яке journald логує лише при
status=0/SUCCESS — при падінні логується "Failed with result...") довше,
ніж 2x очікуваний інтервал таймера — це рання ознака, що воркер завис/впав,
і без цього ніхто не помітить, поки не гляне логи вручну.

Сповіщає в Telegram лише на ЗМІНУ стану (OK -> ALARM і назад), а не на
кожній перевірці — інакше при тривалому падінні прийшов би окремий алерт
щоразу, коли запускається сам watchdog.

Друга перевірка (check_toysi_reconciliation) — інша категорія проблем:
не "сервіс впав", а "сервіс відзвітував про успіх, але дані по факту хибні".
Реальний випадок (замовлення №414634349, 2026-07-08): order_router.py
логував "Передано Toysi" з response_code=1 (справжній, валідний успіх за
даними Toysi), і orders_watcher.py/order_router.py обидва завершувались
статусом 0/SUCCESS — journald-перевірка вище нічого б не показала. Але
через баг (test_mode=True за замовчуванням у продакшн-виклику) Toysi
реально НІКОЛИ не створював замовлення. Ця перевірка звіряє нещодавно
передані Toysi замовлення з їхнім реальним станом через order_status API —
незалежно від того, що наш власний код вважає "успіхом".
"""

# Назва сервісу -> поріг у хвилинах (2x очікуваний інтервал відповідного таймера).
# ВИПРАВЛЕНО (2026-07-16): orders-watcher/order-router злиті в
# order-pipeline.service (один послідовний процес, pt8/pt9 — усуває
# гонку двох незалежних таймерів) — інакше цей чек продовжував би
# ALARM-ити на два сервіси, які більше ніколи не запускаються.
# ВИПРАВЛЕНО (2026-07-16): bank_check.py теж злито в order-pipeline
# (та сама вразливість, що й orders_watcher/order_router — писало
# payment_confirmed, яке order_router читає) — bank-check.timer
# ретировано, більше не окремий запис тут.
# ДОДАНО (2026-07-17, P0-7): order-status-tracker.service досі був
# ПОВНІСТЮ поза цим списком — це вхідні двері TTN/статусу доставки для
# КОЖНОГО замовлення (checkbox-фіскалізація, Rozetka TTN push-back), і
# якщо він мовчки зависне/впаде, ніхто про це не дізнається інакше, ніж
# вручну перевіривши логи. bank_check.py/orders_watcher.py НЕ додаються
# окремо — вони більше не самостійні сервіси (злиті в order-pipeline вище).
MONITORED_SERVICES = {
    "order-pipeline": 30,          # таймер кожні 15 хв (fetch+save+confirm+forward одним процесом)
    "prom-chat-bot": 15,           # таймер кожні 5 хв
    "order-status-tracker": 60,    # таймер кожні 30 хв
}

LOOKBACK = "3 days ago"  # достатньо, щоб знайти останній успіх навіть після тривалого падіння

# Скільки часу замовлення може лишатись непідтвердженим у Toysi (status=0,
# order_is_paid=0, без ТТН, place_count=0) до алерту. Власник орієнтовно
# назвав "1-2 години" — беремо верхню межу з запасом, оскільки навіть
# реальне замовлення якийсь час лишається в статусі 0 до обробки менеджером.
TOYSI_RECONCILE_THRESHOLD_MINUTES = 120

# ВИПРАВЛЕНО (2026-07-16, safety-net після третього поспіль випадку
# недоходження замовлення вчасно — 415858222/вузький фільтр status=
# pending, 100445626/норма Toysi, 416114712/гонка таймерів, pt8/pt9):
# незалежна від причини перевірка. order_pipeline.py тепер виконує
# fetch->save->forward одним послідовним процесом (структурний фікс
# гонки), але ЦЯ перевірка навмисно НЕ довіряє тому, що основний
# конвеєр справді відпрацював — вона сама рахує "готове до пересилки"
# (get_orders_ready_to_forward(), той самий критерій, що й
# order_router.py) і вік замовлення, незалежно від того, чи то стара
# гонка таймерів, збій Toysi API, чи будь-яка майбутня причина, якої
# ще не було. 25 хв — трохи більше за один цикл order_pipeline.py
# (~15 хв), щоб не спрацьовувати на нормальний ритм, але досить туго,
# щоб реально застрягле замовлення не чекало годинами непоміченим.
STALE_ORDER_THRESHOLD_MINUTES = 25

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchdog_state.json")

# journalctl -o short-iso віддає зсув часового поясу без двокрапки (+0300),
# а datetime.fromisoformat() приймає такий формат лише з Python 3.11+.
# Нормалізуємо самі, щоб парсинг не залежав від версії Python середовища,
# де це виконується.
_TZ_OFFSET_RE = re.compile(r"([+-]\d{2})(\d{2})$")


class WatchdogCheckError(Exception):
    """Сам watchdog не зміг перевірити стан сервісу (journalctl недоступний,
    немає прав, таймаут тощо) — це НЕ те саме, що "сервіс не звітував про
    успіх": тут ми просто не знаємо, і замовчувати цю різницю означало б
    ризикувати або хибним ALARM, або (гірше) тихим "все ОК", коли насправді
    watchdog сам не працює."""


def _parse_journal_timestamp(timestamp_str: str) -> datetime:
    normalized = _TZ_OFFSET_RE.sub(r"\1:\2", timestamp_str)
    return datetime.fromisoformat(normalized)


def get_last_success_time(service: str):
    """Час останнього рядка "Finished <service>.service" у journald. Повертає
    timezone-aware datetime, або None, якщо успішних завершень не знайдено
    за LOOKBACK. Піднімає WatchdogCheckError, якщо сам виклик journalctl
    не вдався — це відрізняється від "успіхів дійсно немає"."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", f"{service}.service", "--since", LOOKBACK, "-o", "short-iso", "--no-pager"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as e:
        raise WatchdogCheckError(f"не вдалося викликати journalctl: {e}") from e

    if result.returncode != 0:
        raise WatchdogCheckError(
            f"journalctl завершився з кодом {result.returncode}: {result.stderr.strip()[:200]}"
        )

    marker = f"Finished {service}.service"
    last_success = None
    for line in result.stdout.splitlines():
        if marker in line:
            timestamp_str = line.split(" ", 1)[0]
            try:
                last_success = _parse_journal_timestamp(timestamp_str)
            except ValueError:
                continue
    return last_success


def _load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def check_services() -> None:
    now = datetime.now().astimezone()
    state = _load_state()
    new_alarms = []
    recoveries = []

    for service, threshold_minutes in MONITORED_SERVICES.items():
        was_alarming = state.get(service, False)

        try:
            last_success = get_last_success_time(service)
        except WatchdogCheckError as e:
            # Не можемо підтвердити ні здоров'я, ні падіння сервісу — сам
            # watchdog зламався. Позначаємо як ALARM (безпечніше помилково
            # насторожити, ніж мовчки пропустити реальну проблему), але з
            # чітко іншим формулюванням, щоб було видно: це не сервіс упав,
            # а сам watchdog не може перевірити.
            state[service] = True
            print(f"[watchdog] {service}: ERROR — {e}", file=sys.stderr)
            if not was_alarming:
                new_alarms.append(f"⚠️ {service}: watchdog не зміг перевірити стан — {e}")
            continue

        if last_success is None:
            is_alarming = True
            detail = f"жодного успішного запуску не знайдено за {LOOKBACK}"
        else:
            elapsed_minutes = (now - last_success).total_seconds() / 60
            is_alarming = elapsed_minutes > threshold_minutes
            detail = (
                f"останній успіх {last_success.strftime('%d.%m.%Y %H:%M')} "
                f"({elapsed_minutes:.0f} хв тому, поріг {threshold_minutes} хв)"
            )

        state[service] = is_alarming
        status_word = "ALARM" if is_alarming else "OK"
        print(f"[watchdog] {service}: {status_word} — {detail}")

        if is_alarming and not was_alarming:
            new_alarms.append(f"⛔ {service}: {detail} — можливо, завис/впав")
        elif not is_alarming and was_alarming:
            recoveries.append(f"✅ {service}: знову працює ({detail})")

    _save_state(state)

    if new_alarms:
        message = "🚨 Watchdog PlutusToys: сервіс(и) не відповідають\n\n" + "\n\n".join(new_alarms)
        print(message)
        if not send_telegram_message(message):
            print("[watchdog] Не вдалося надіслати алерт у Telegram (див. вище)", file=sys.stderr)
    if recoveries:
        message = "✅ Watchdog PlutusToys: відновлено\n\n" + "\n\n".join(recoveries)
        print(message)
        if not send_telegram_message(message):
            print("[watchdog] Не вдалося надіслати повідомлення про відновлення в Telegram", file=sys.stderr)


def _order_confirmed_in_toysi(info: dict) -> bool:
    """Чи є в статусі Toysi ознака, що замовлення дійсно існує й
    опрацьовується — а не назавжди "підвішене". Саме так виглядало тестове
    замовлення №414634349: status=0, order_is_paid=0, TTN="", place_count=0
    без кінця, бо воно було відправлене через api_mode=test і ніколи реально
    не створювалось у Toysi."""
    status = int(info.get("status", 0) or 0)
    if status != 0:
        return True
    if int(info.get("order_is_paid", 0) or 0):
        return True
    if info.get("TTN"):
        return True
    if int(info.get("place_count", 0) or 0):
        return True
    return False


def check_toysi_reconciliation() -> None:
    """Звіряє нещодавно передані Toysi замовлення (forwarded_to_toysi_at
    заповнено, доставка ще не термінальна) з їхнім реальним станом через
    order_status API — незалежно від того, що наш власний код вважав
    "успіхом" при передачі. Алармує, якщо замовлення старше
    TOYSI_RECONCILE_THRESHOLD_MINUTES і досі не показує жодної ознаки
    реального опрацювання (див. _order_confirmed_in_toysi)."""
    now = datetime.now()
    state = _load_state()
    reconcile_state = state.get("toysi_reconcile", {})

    with get_connection() as conn:
        active_orders = get_active_toysi_orders(conn)

    candidates = []
    for order in active_orders:
        try:
            forwarded_at = datetime.fromisoformat(order["forwarded_to_toysi_at"])
        except (TypeError, ValueError):
            continue
        age_minutes = (now - forwarded_at).total_seconds() / 60
        if age_minutes >= TOYSI_RECONCILE_THRESHOLD_MINUTES:
            candidates.append((order, age_minutes))

    if not candidates:
        print("[watchdog] Звірка з Toysi: немає замовлень, старших за поріг, для перевірки")
        return

    try:
        statuses = fetch_order_statuses([str(o["toysi_order_id"]) for o, _ in candidates])
    except (RuntimeError, ToysiAPIError) as e:
        # ToysiAPIError = сам запит не вдався (мережа/невалідна відповідь/фатальна
        # помилка API) — НЕ те саме, що "жодне із замовлень не знайдено в Toysi".
        # Лише лог, без new_alarms/Telegram: інакше короткочасний мережевий блип
        # виглядав би так само, як реальний повтор бага test_mode (усі активні
        # замовлення одразу потрапили б у "не знайдено в Toysi" — саме той
        # крайовий випадок, який знайшло незалежне рев'ю PR #10).
        print(f"[watchdog] Звірка з Toysi: не вдалося перевірити — {e}", file=sys.stderr)
        return

    still_unconfirmed = {}
    new_alarms = []
    recoveries = []

    for order, age_minutes in candidates:
        internal_id = order["internal_order_id"]
        toysi_id = str(order["toysi_order_id"])
        info = statuses.get(toysi_id)
        was_alarming = reconcile_state.get(internal_id, False)
        confirmed = info is not None and _order_confirmed_in_toysi(info)

        if confirmed:
            print(f"[watchdog] Звірка з Toysi: OK — {internal_id} (Toysi #{toysi_id}) підтверджено")
            if was_alarming:
                recoveries.append(f"✅ {internal_id} (Toysi #{toysi_id}): тепер підтверджено в Toysi")
            continue

        still_unconfirmed[internal_id] = True
        reason = "не знайдено в Toysi" if info is None else "status=0, без оплати/ТТН/місць"
        detail = f"{internal_id} (Toysi #{toysi_id}): непідтверджено {age_minutes:.0f} хв ({reason})"
        print(f"[watchdog] Звірка з Toysi: ALARM — {detail}")
        if not was_alarming:
            new_alarms.append(f"⛔ {detail}")

    state["toysi_reconcile"] = still_unconfirmed
    _save_state(state)

    if new_alarms:
        message = (
            "🚨 Watchdog PlutusToys: замовлення передане, але Toysi не підтверджує\n\n"
            + "\n\n".join(new_alarms)
            + "\n\nПеревір вручну — можливо, замовлення реально не створено "
              "(як №414634349 через баг test_mode)."
        )
        print(message)
        if not send_telegram_message(message):
            print("[watchdog] Не вдалося надіслати алерт про звірку в Telegram", file=sys.stderr)
    if recoveries:
        message = "✅ Watchdog PlutusToys: звірка з Toysi відновлена\n\n" + "\n\n".join(recoveries)
        print(message)
        if not send_telegram_message(message):
            print("[watchdog] Не вдалося надіслати повідомлення про відновлення звірки в Telegram", file=sys.stderr)


def check_unforwarded_orders() -> None:
    """Safety-net, незалежний від причини застрягання (див. коментар
    біля STALE_ORDER_THRESHOLD_MINUTES вище). Рахує "готове до
    пересилки" тим самим критерієм, що й order_router.py
    (get_orders_ready_to_forward) — не довіряє основному конвеєру на
    слово. Якщо знаходить застрягле замовлення — одразу намагається
    підхопити його сама (route_order()), і сигналить лише якщо навіть
    ця спроба не допомогла."""
    now = datetime.now()
    state = _load_state()
    stale_state = state.get("stale_orders", {})

    with get_connection() as conn:
        candidates = get_orders_ready_to_forward(conn)
        stale = []
        for order in candidates:
            try:
                created_at = datetime.fromisoformat(order["created_at"])
            except (TypeError, ValueError):
                continue
            age_minutes = (now - created_at).total_seconds() / 60
            if age_minutes >= STALE_ORDER_THRESHOLD_MINUTES:
                stale.append((order, age_minutes))

        if not stale:
            print("[watchdog] Застряглі непередані замовлення: немає")
            recoveries = [f"✅ {internal_id}: більше не застрягле" for internal_id in stale_state]
            state["stale_orders"] = {}
            _save_state(state)
            if recoveries:
                message = "✅ Watchdog PlutusToys: застряглі замовлення підхоплено\n\n" + "\n\n".join(recoveries)
                print(message)
                if not send_telegram_message(message):
                    print("[watchdog] Не вдалося надіслати повідомлення про відновлення застряглих замовлень у Telegram", file=sys.stderr)
            return

        for order, age_minutes in stale:
            internal_id = order["internal_order_id"]
            print(f"[watchdog] Застрягле замовлення: {internal_id} ({age_minutes:.0f} хв, "
                  f"поріг {STALE_ORDER_THRESHOLD_MINUTES} хв) — намагаюсь підхопити зараз")
            try:
                order_router.route_order(conn, order)
            except Exception as e:
                print(f"[watchdog] Спроба підхопити {internal_id} впала: {e}", file=sys.stderr)

        # Перевіряємо результат ПІСЛЯ спроб окремим свіжим запитом — не
        # довіряємо припущенню, що route_order() точно спрацював, якщо
        # не впав винятком (напр. ukrposhta-гілка може тихо return без
        # позначення forwarded, якщо створення відправлення не вдалось).
        still_unforwarded_ids = {o["internal_order_id"] for o in get_orders_ready_to_forward(conn)}

    new_alarms = []
    recoveries = []
    still_stuck = {}
    for order, age_minutes in stale:
        internal_id = order["internal_order_id"]
        was_alarming = internal_id in stale_state
        if internal_id in still_unforwarded_ids:
            still_stuck[internal_id] = True
            detail = f"{internal_id}: застрягле {age_minutes:.0f} хв, автопідхоплення НЕ вдалось"
            print(f"[watchdog] ALARM — {detail}")
            if not was_alarming:
                new_alarms.append(f"⛔ {detail}")
        else:
            print(f"[watchdog] {internal_id}: підхоплено автоматично зараз")
            if was_alarming:
                recoveries.append(f"✅ {internal_id}: підхоплено автоматично")

    for internal_id in stale_state:
        if internal_id not in still_stuck and internal_id not in {o["internal_order_id"] for o, _ in stale}:
            recoveries.append(f"✅ {internal_id}: більше не застрягле")

    state["stale_orders"] = still_stuck
    _save_state(state)

    if new_alarms:
        message = (
            "🚨 Watchdog PlutusToys: замовлення застрягло, автопідхоплення не вдалось\n\n"
            + "\n\n".join(new_alarms)
            + f"\n\nПоріг: {STALE_ORDER_THRESHOLD_MINUTES} хв. Перевір вручну — можливо, "
              "проблема з даними замовлення чи Toysi API недоступний."
        )
        print(message)
        if not send_telegram_message(message):
            print("[watchdog] Не вдалося надіслати алерт про застрягле замовлення в Telegram", file=sys.stderr)
    if recoveries:
        message = "✅ Watchdog PlutusToys: застряглі замовлення підхоплено\n\n" + "\n\n".join(recoveries)
        print(message)
        if not send_telegram_message(message):
            print("[watchdog] Не вдалося надіслати повідомлення про відновлення застряглих замовлень у Telegram", file=sys.stderr)


if __name__ == "__main__":
    check_services()
    check_toysi_reconciliation()
    check_unforwarded_orders()
