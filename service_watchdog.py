import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata

import requests

import order_router
from orders_db import get_connection, get_active_toysi_orders, get_orders_ready_to_forward
from parser import fetch_toysi_catalog
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

# ВЕРХНЯ межа вікна звірки (2026-08-11, хибний алерт «Toysi не підтверджує»):
# order_status Toysi має ретенцію 40 днів — fetch_order_statuses() повертає застарілі
# замовлення як ВІДСУТНІ в результаті (response_code 503 «Замовлення застаріло (>40 днів)»,
# toysi_order_submit.py:56, docstring fetch_order_statuses). Без цієї межі замовлення, що
# лишилось нетермінальним у нашій БД понад 40 днів (order_status_tracker не довів
# delivery_status до термінального), потрапляло у звірку, отримувало info=None і хибно
# алертило «не знайдено в Toysi» — попри те, що воно могло успішно пройти ще на 1-й день і
# лише тепер випасти з ретенції. Справжній фантом test_mode, який ця перевірка ловить,
# проявляється за хвилини/години (info=None вже з ~120 хв), а не через 40+ днів — тож понад
# ретенцію звірка беззмістовна, і такі замовлення пропускаємо (не алертимо).
# Межу беремо з ~2-денним запасом НИЖЧЕ 40 днів (38): наш forwarded_to_toysi_at і власний
# годинник ретенції Toysi можуть трохи розходитись, тож зупиняємось звіряти ЩЕ до краю
# вікна, щоб не зловити хибний «не знайдено» на замовленні, яке Toysi ось-ось випустить з
# ретенції (той самий підхід «межа із запасом», що й пороги каталогу в prom_catalog_sync).
TOYSI_RECONCILE_MAX_AGE_MINUTES = 38 * 24 * 60  # 38 днів = 40-денна ретенція Toysi мінус ~2 дні запасу

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

# ІСТОРІЯ (2026-07-17, P0-1 — 4-й підтверджений випадок дрейфу VPS↔master:
# order_status_tracker.py/checkbox_client.py/orders_db.py та ще 5 файлів
# змержено в master і тижнями не задеплоєно, знайдено лише ручним SHA256-
# порівнянням): тоді /opt/plutustoys деплоївся ручним scp, тож check_deploy_drift()
# звіряла кожен .py-файл на диску проти origin/master через
# raw.githubusercontent.com. ЗАМІНЕНО (2026-07-27, vps-git-autodeploy): VPS
# тепер справжній git-клон з автопулом (vps_code_sync.sh, vps-code-sync.timer),
# тож цей клас розбіжності структурно неможливий — див. check_autodeploy_status()
# нижче, яка натомість читає стан САМОГО автопулу.
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/master/"

VPS_CODE_SYNC_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vps_code_sync_state.json")
# 2x очікуваний інтервал vps-code-sync.timer (OnUnitActiveSec=15min) — та сама
# логіка порогу "2x інтервал таймера", що й MONITORED_SERVICES вище.
AUTODEPLOY_STALE_THRESHOLD_MINUTES = 30

# ВИПРАВЛЕНО (знахідка аудиту pt3, 2026-07-27): "ДОСІ не вдається"-нагадування
# нижче спочатку не мало жодного throttle — при затяжному non-fast-forward
# (watchdog на ~10-хвилинному циклі) це слало б Telegram-алерт що ~10 хв,
# доки не виправлять вручну. Той самий 24-годинний throttle, що був у
# колишній check_deploy_drift() (DEPLOY_DRIFT_CHECK_INTERVAL_HOURS,
# прибрано разом з рештою SHA256-звірки) — нагадувати варто, щоб тривала
# невдача не загубилась непоміченою (P0-1, 2026-07-18), але не щоцикл.
AUTODEPLOY_REMINDER_INTERVAL_HOURS = 24

# ДОДАНО (2026-07-21, findings_log.md — "dependency-drift-not-code-drift"):
# check_deploy_drift() вище хешує ТЕКСТ .py-файлів — новий `import imagehash`
# у щойно задеплоєному коді він би виявив (файл змінився -> хеш не збігається
# з master), АЛЕ факт, що сам ПАКЕТ imagehash фізично не встановлений у venv,
# для нього невидимий взагалі: requirements.txt — не .py-файл, і, навіть якби
# й був, збіг хешів requirements.txt нічого не каже про те, що реально
# встановлено в venv ПІСЛЯ його редагування (pip install — окрема, ручна дія,
# яку легко забути після мержу нової залежності). Живий інцидент (2026-07-21,
# PR #113): Pillow/ImageHash додані в requirements.txt, деплой .py-файлів
# пройшов би success/checksum-зелений, але нічний скан впав би на ПЕРШОМУ ж
# `import imagehash` — знайдено вручну під час деплою, не автоматично.
# Той самий 24-годинний цикл, що й check_deploy_drift() (не свій, частіший —
# дрейф залежностей за визначенням теж змінюється не швидше, ніж хтось
# руками відредагує requirements.txt).
DEPENDENCY_DRIFT_CHECK_INTERVAL_HOURS = 24

# ІСТОРІЯ (2026-07-23, живий інцидент — власниця помітила падіння каталогу
# Prom до 473/1000 через скріншот): GitHub Actions `schedule`-подія (cron
# "0 */4 * * *" в update-feeds.yml) сама по собі НЕ гарантована — GitHub
# офіційно документує, що scheduled-запуски можуть затримуватись чи
# пропускатись під навантаженням платформи. check_feed_pipeline_schedule()
# моніторила історію запусків update-feeds.yml через публічний GitHub API.
# ЗАМІНЕНО (2026-07-27, знахідка аудиту pt6, Фаза 2 VPS-міграції): весь
# фід-пайплайн (генерація+репрайсер) переїхав на VPS
# (run_feed_pipeline_vps.sh, feed-pipeline.timer), а schedule: в
# update-feeds.yml закоментовано — стара перевірка або хибно алармила б
# постійно (GH Actions більше не оновлюється регулярно), або мовчала б
# про реальний стан VPS-пайплайну. check_feed_pipeline_vps_status()
# нижче читає локальний feed_pipeline_state.json (пише
# feed_pipeline_report.py, викликається з run_feed_pipeline_vps.sh) —
# той самий патерн, що й check_autodeploy_status()/vps_code_sync_state.json.
FEED_PIPELINE_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feed_pipeline_state.json")
# 2x очікуваний інтервал feed-pipeline.timer (OnUnitActiveSec=6h за
# runbook) — та сама логіка порогу "2x інтервал таймера", що й
# MONITORED_SERVICES/AUTODEPLOY_STALE_THRESHOLD_MINUTES вище.
FEED_PIPELINE_STALE_THRESHOLD_HOURS = 12
# Той самий принцип, що й AUTODEPLOY_REMINDER_INTERVAL_HOURS — нагадувати
# про триваючу невдачу/degraded-стан варто, щоб не загубити непоміченим
# (P0-1, 2026-07-18), але не на кожному циклі watchdog.
FEED_PIPELINE_REMINDER_INTERVAL_HOURS = 24

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


def _load_vps_code_sync_state() -> dict:
    if not os.path.exists(VPS_CODE_SYNC_STATE_FILE):
        return {}
    try:
        with open(VPS_CODE_SYNC_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def check_autodeploy_status() -> None:
    """ЗАМІНЮЄ колишню check_deploy_drift() (SHA256-звірку .py-файлів проти
    origin/master через raw.githubusercontent.com) — після переведення
    /opt/plutustoys на справжній git-клон з автопулом (vps-code-sync.timer,
    vps_code_sync.sh, 2026-07-27) цей клас розбіжності структурно
    неможливий: VPS або точно на origin/master, або явно провалив спробу
    (non-fast-forward), і про це вже пише vps_code_sync_state.json —
    звірка файл-за-файлом через мережу більше не потрібна.

    Три незалежні сигнали з того самого стану:
    1. `last_status == "failed"` — автопул не зміг дотягнутись: одноразовий
       алерт на нову невдачу й на відновлення (як у check_services()), АЛЕ,
       на відміну від check_services(), ще й окреме "ДОСІ не вдається"
       нагадування, поки невдача триває — гейтнуте AUTODEPLOY_REMINDER_
       INTERVAL_HOURS (24 год), інакше на ~10-хвилинному циклі watchdog це
       був би Telegram-спам щоцикл (знахідка аудиту pt3).
    2. `last_run` старіший за 2x інтервал таймера
       (AUTODEPLOY_STALE_THRESHOLD_MINUTES) — сам таймер/сервіс на VPS
       міг зупинитись, і без цього ніхто б не помітив.
    3. `last_success_commit` змінився відносно останнього разу, коли ми
       про це сповіщали — одноразове "підтягнуто X" (не сплять на самому
       факті успіху щоцикл, лише на ЗМІНУ)."""
    state = _load_state()
    sync_state = _load_vps_code_sync_state()
    if not sync_state:
        return  # vps-code-sync.sh ще не запускався жодного разу на цій машині

    try:
        last_run = datetime.fromisoformat(sync_state["last_run"])
    except (KeyError, ValueError):
        return
    if last_run.tzinfo is None:
        last_run = last_run.astimezone()
    now = datetime.now(last_run.tzinfo)

    elapsed_minutes = (now - last_run).total_seconds() / 60
    is_stale = elapsed_minutes > AUTODEPLOY_STALE_THRESHOLD_MINUTES
    is_failing = sync_state.get("last_status") == "failed"

    was_stale = state.get("autodeploy_stale", False)
    was_failing = state.get("autodeploy_failing", False)
    last_alerted_commit = state.get("autodeploy_last_alerted_commit")
    last_reminder = state.get("autodeploy_failing_last_reminder")

    messages = []

    if is_stale and not was_stale:
        messages.append(
            f"⛔ Автодеплой: vps-code-sync.timer не звітував {elapsed_minutes:.0f} хв "
            f"(поріг {AUTODEPLOY_STALE_THRESHOLD_MINUTES}) — сам таймер міг зупинитись на VPS"
        )
    elif not is_stale and was_stale:
        messages.append("✅ Автодеплой: vps-code-sync.timer знову звітує вчасно")

    if is_failing and not was_failing:
        messages.append(f"⛔ Автодеплой не вдався: {sync_state.get('reason', 'причина невідома')}")
        state["autodeploy_failing_last_reminder"] = now.isoformat()
    elif is_failing and was_failing:
        reminder_due = True
        if last_reminder:
            try:
                reminder_due = (now - datetime.fromisoformat(last_reminder)).total_seconds() / 3600 \
                    >= AUTODEPLOY_REMINDER_INTERVAL_HOURS
            except ValueError:
                pass
        if reminder_due:
            messages.append(f"⏰ Автодеплой ДОСІ не вдається: {sync_state.get('reason', 'причина невідома')}")
            state["autodeploy_failing_last_reminder"] = now.isoformat()
    elif not is_failing and was_failing:
        messages.append("✅ Автодеплой: відновлено, VPS знову синхронізовано з master")
        state.pop("autodeploy_failing_last_reminder", None)

    current_success_commit = sync_state.get("last_success_commit")
    if not is_failing and current_success_commit and current_success_commit != last_alerted_commit \
            and last_alerted_commit is not None:
        changed = sync_state.get("changed_files") or []
        detail = f" ({len(changed)} файл(ів) змінено)" if changed else " (без змін коду)"
        when = last_run.astimezone().strftime("%d.%m.%Y %H:%M")
        messages.append(f"✅ Автодеплой: підтягнуто commit {current_success_commit[:8]} о {when}{detail}")

    state["autodeploy_stale"] = is_stale
    state["autodeploy_failing"] = is_failing
    if current_success_commit:
        state["autodeploy_last_alerted_commit"] = current_success_commit
    _save_state(state)

    if not messages:
        if is_failing:
            # Невдача триває, але нагадування притлумлене (AUTODEPLOY_REMINDER_
            # INTERVAL_HOURS ще не минув) — консольний лог не повинен вдавати
            # "OK", інакше журнал journalctl вводить в оману так само, як
            # раніше вводило б Telegram-повідомлення без throttle.
            print(f"[watchdog] Автодеплой: досі не вдається ({sync_state.get('reason', 'причина невідома')}), "
                  "нагадування притлумлене")
        else:
            print(f"[watchdog] Автодеплой: OK, commit {sync_state.get('last_success_commit', '?')[:8]}")
        return

    print("[watchdog] " + " | ".join(messages))
    message = "🚀 Watchdog PlutusToys: автодеплой\n\n" + "\n\n".join(messages)
    if not send_telegram_message(message):
        print("[watchdog] Не вдалося надіслати алерт про автодеплой у Telegram", file=sys.stderr)


def _normalize_pkg_name(name: str) -> str:
    """PEP 503-подібна нормалізація ("Pillow" == "pillow" == "PILLOW",
    "python-dotenv" == "python_dotenv") — щоб порівняння requirements.txt
    (як пише людина) проти назв реально встановлених дистрибутивів
    (як їх повертає importlib.metadata) не хибно алармило на різницю
    в регістрі/розділювачі, якої нема насправді."""
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _required_package_names(requirements_text: str) -> set:
    """Парсить requirements.txt у множину нормалізованих назв пакетів,
    ігноруючи версійні специфікатори (>=, ==, тощо), коментарі, порожні
    рядки. Навмисно НЕ звіряє версії — мета лише "пакет фізично
    встановлений", не "встановлена версія рівно та, що в файлі" (друге
    складніше й крихкіше: pip може підтягнути сумісну новішу версію, це
    не помилка)."""
    names = set()
    for line in requirements_text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)", line)
        if match:
            names.add(_normalize_pkg_name(match.group(1)))
    return names


def check_dependency_drift() -> None:
    """ДОДАНО (2026-07-21, findings_log.md — "dependency-drift-not-code-drift",
    живий інцидент під час деплою PR #113): check_deploy_drift() вище
    звіряє ТЕКСТ .py-файлів, але не бачить, чи пакети, перелічені в
    requirements.txt, РЕАЛЬНО встановлені у venv, де виконується цей же
    процес. Живий приклад: `Pillow`/`ImageHash` додані в requirements.txt
    разом із кодом, що їх імпортує (`prom_competitor_pricer.py`) — деплой
    .py-файлів пройшов би checksum-зеленим, але `import imagehash` на
    першому ж рядку впав би з ModuleNotFoundError, і весь нічний скан
    пропав би без жодного алерту, доки хтось не поглянув би в journalctl.

    Механізм: тягне requirements.txt із live origin/master (той самий
    GITHUB_RAW_BASE, що й check_deploy_drift), парсить назви пакетів,
    звіряє проти `importlib.metadata.distributions()` ПОТОЧНОГО
    інтерпретатора (той самий venv, у якому виконується сам watchdog —
    жодного subprocess/pip-виклику не потрібно, встановлені дистрибутиви
    видно напряму з процесу, що вже запущений усередині цього venv).
    Той самий цикл "щоденний алерт, повторюється, доки не виправлено, +
    повідомлення про відновлення", що й check_deploy_drift() — навмисна
    симетрія, той самий клас проблеми (VPS відстав від того, що вимагає
    master), лише інший ШАР (залежності, не код)."""
    state = _load_state()
    last_check = state.get("dependency_drift_last_check")
    now = datetime.now()
    if last_check:
        try:
            elapsed_hours = (now - datetime.fromisoformat(last_check)).total_seconds() / 3600
            if elapsed_hours < DEPENDENCY_DRIFT_CHECK_INTERVAL_HOURS:
                return
        except ValueError:
            pass

    try:
        response = requests.get(GITHUB_RAW_BASE + "requirements.txt", timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"[watchdog] Звірка залежностей: не вдалось завантажити requirements.txt з GitHub ({e})",
              file=sys.stderr)
        return

    required = _required_package_names(response.text)
    installed = {_normalize_pkg_name(dist.metadata["Name"]) for dist in importlib_metadata.distributions()
                 if dist.metadata.get("Name")}

    missing_now = sorted(required - installed)
    drift_state = set(state.get("dependency_drift", []))

    new_alarms = [f"⛔ {name}: у requirements.txt є, у venv НЕ встановлено" for name in missing_now if name not in drift_state]
    reminders = [f"⏰ {name}: ДОСІ не встановлено (розбіжність триває)" for name in missing_now if name in drift_state]
    recoveries = [f"✅ {name}: тепер встановлено" for name in drift_state if name not in missing_now]

    state["dependency_drift"] = missing_now
    state["dependency_drift_last_check"] = now.isoformat()
    _save_state(state)

    if not new_alarms and not reminders and not recoveries:
        print(f"[watchdog] Звірка залежностей: {len(missing_now)} пакет(ів) відсутні (без змін)")

    if new_alarms or reminders:
        message = (
            "🚨 Watchdog PlutusToys: requirements.txt вимагає пакети, яких немає у venv на VPS\n\n"
            + "\n\n".join(new_alarms + reminders)
            + "\n\nКод, що їх імпортує, впаде з ModuleNotFoundError при першому ж запуску — "
              "постав вручну (venv/bin/pip install -r requirements.txt). "
              "Це нагадування буде повторюватись щодня, доки не буде встановлено."
        )
        print(message)
        if not send_telegram_message(message):
            print("[watchdog] Не вдалося надіслати алерт про дрейф залежностей у Telegram", file=sys.stderr)
    if recoveries:
        message = "✅ Watchdog PlutusToys: дрейф залежностей усунено\n\n" + "\n\n".join(recoveries)
        print(message)
        if not send_telegram_message(message):
            print("[watchdog] Не вдалося надіслати повідомлення про усунення дрейфу залежностей у Telegram", file=sys.stderr)


def _load_feed_pipeline_state() -> dict:
    if not os.path.exists(FEED_PIPELINE_STATE_FILE):
        return {}
    try:
        with open(FEED_PIPELINE_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def check_feed_pipeline_vps_status() -> None:
    """ЗАМІНЮЄ check_feed_pipeline_schedule() (моніторила update-feeds.yml
    через GitHub API) — після переносу фід-пайплайну на VPS
    (run_feed_pipeline_vps.sh, feed-pipeline.timer, Фаза 2, 2026-07-27)
    читає локальний feed_pipeline_state.json (пише feed_pipeline_report.py
    після КОЖНОГО прогону) — той самий патерн, що й
    check_autodeploy_status()/vps_code_sync_state.json.

    Три сигнали:
    1. `last_status == "failed"` — публікація пропущена цього прогону
       (фіди відсутні/порожні навіть після фолбеку) — new/recovery
       одразу, "ДОСІ" — гейтнуто FEED_PIPELINE_REMINDER_INTERVAL_HOURS
       (той самий throttle-принцип, що й check_autodeploy_status(),
       знахідка аудиту pt3/pt4 звідти).
    2. `last_status == "degraded"` — репрайсер чи generate_prom_feed_top.py
       провалились, але фолбек дозволив публікацію (не критично, але
       варто знати) — той самий new/repeat(throttled)/recovery цикл,
       окремим формулюванням.
    3. `last_run` старіший за FEED_PIPELINE_STALE_THRESHOLD_HOURS — сам
       таймер/сервіс на VPS міг зупинитись, і без цього ніхто б не
       помітив."""
    state = _load_state()
    pipeline_state = _load_feed_pipeline_state()
    if not pipeline_state:
        return  # feed-pipeline.timer ще не запускався жодного разу на цій машині

    try:
        last_run = datetime.fromisoformat(pipeline_state["last_run"])
    except (KeyError, ValueError):
        return
    if last_run.tzinfo is None:
        last_run = last_run.astimezone()
    now = datetime.now(last_run.tzinfo)

    elapsed_hours = (now - last_run).total_seconds() / 3600
    is_stale = elapsed_hours > FEED_PIPELINE_STALE_THRESHOLD_HOURS
    last_status = pipeline_state.get("last_status")
    is_failing = last_status == "failed"
    is_degraded = last_status == "degraded"
    reason = pipeline_state.get("reason") or "причина невідома"

    was_stale = state.get("feed_pipeline_stale", False)
    was_failing = state.get("feed_pipeline_failing", False)
    was_degraded = state.get("feed_pipeline_degraded", False)
    last_reminder = state.get("feed_pipeline_last_reminder")

    messages = []

    if is_stale and not was_stale:
        messages.append(
            f"⛔ Фід-пайплайн VPS: feed-pipeline.timer не звітував {elapsed_hours:.1f} год "
            f"(поріг {FEED_PIPELINE_STALE_THRESHOLD_HOURS}) — сам таймер міг зупинитись на VPS"
        )
    elif not is_stale and was_stale:
        messages.append("✅ Фід-пайплайн VPS: feed-pipeline.timer знову звітує вчасно")

    reminder_due = True
    if last_reminder:
        try:
            reminder_due = (now - datetime.fromisoformat(last_reminder)).total_seconds() / 3600 \
                >= FEED_PIPELINE_REMINDER_INTERVAL_HOURS
        except ValueError:
            pass

    if is_failing and not was_failing:
        messages.append(f"⛔ Фід-пайплайн VPS: публікація ПРОПУЩЕНА — {reason}")
        state["feed_pipeline_last_reminder"] = now.isoformat()
    elif is_failing and was_failing:
        if reminder_due:
            messages.append(f"⏰ Фід-пайплайн VPS: публікація ДОСІ пропускається — {reason}")
            state["feed_pipeline_last_reminder"] = now.isoformat()
    elif not is_failing and was_failing:
        messages.append("✅ Фід-пайплайн VPS: публікація відновлена")
        state.pop("feed_pipeline_last_reminder", None)
    elif is_degraded and not was_degraded:
        messages.append(f"⚠️ Фід-пайплайн VPS: прогін degraded (фолбек спрацював) — {reason}")
        state["feed_pipeline_last_reminder"] = now.isoformat()
    elif is_degraded and was_degraded:
        if reminder_due:
            messages.append(f"⏰ Фід-пайплайн VPS: ДОСІ degraded — {reason}")
            state["feed_pipeline_last_reminder"] = now.isoformat()
    elif not is_degraded and was_degraded:
        messages.append("✅ Фід-пайплайн VPS: більше не degraded")
        state.pop("feed_pipeline_last_reminder", None)

    state["feed_pipeline_stale"] = is_stale
    state["feed_pipeline_failing"] = is_failing
    state["feed_pipeline_degraded"] = is_degraded
    _save_state(state)

    if not messages:
        print(f"[watchdog] Фід-пайплайн VPS: {last_status or '?'}, {elapsed_hours:.1f} год тому")
        return

    print("[watchdog] " + " | ".join(messages))
    message = "🚀 Watchdog PlutusToys: фід-пайплайн VPS\n\n" + "\n\n".join(messages)
    if not send_telegram_message(message):
        print("[watchdog] Не вдалося надіслати алерт про фід-пайплайн VPS у Telegram", file=sys.stderr)


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
    aged_out = 0
    for order in active_orders:
        try:
            forwarded_at = datetime.fromisoformat(order["forwarded_to_toysi_at"])
        except (TypeError, ValueError):
            continue
        age_minutes = (now - forwarded_at).total_seconds() / 60
        if age_minutes < TOYSI_RECONCILE_THRESHOLD_MINUTES:
            continue
        if age_minutes > TOYSI_RECONCILE_MAX_AGE_MINUTES:
            # Понад 40-денну ретенцію Toysi order_status: info=None означало б «застаріло»,
            # а не «не створено» — звірка беззмістовна, «не знайдено»-алерт був би хибним
            # (див. TOYSI_RECONCILE_MAX_AGE_MINUTES). Такі замовлення природно випадають і
            # з reconcile-стану нижче (still_unconfirmed перебудовується лише з кандидатів).
            aged_out += 1
            continue
        candidates.append((order, age_minutes))

    if aged_out:
        print(f"[watchdog] Звірка з Toysi: пропущено {aged_out} замовлень старших за 40-денну "
              f"ретенцію Toysi (order_status їх уже не віддає — звірка беззмістовна, не алертимо).")

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

        # Один живий фетч каталогу Toysi (P0-6) на весь цей прогін, не на
        # кожне застрягле замовлення окремо — інакше кожне ЩЕ ОДНЕ застрягле
        # замовлення в тому самому інциденті додавало б ще один ~70МБ
        # запит, сповільнюючи саме той safety-net, що має рятувати систему
        # під час проблеми (той самий підхід, що й route_pending_orders()).
        toysi_catalog = fetch_toysi_catalog()
        for order, age_minutes in stale:
            internal_id = order["internal_order_id"]
            print(f"[watchdog] Застрягле замовлення: {internal_id} ({age_minutes:.0f} хв, "
                  f"поріг {STALE_ORDER_THRESHOLD_MINUTES} хв) — намагаюсь підхопити зараз")
            try:
                order_router.route_order(conn, order, toysi_catalog=toysi_catalog)
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
    check_autodeploy_status()
    check_dependency_drift()
    check_feed_pipeline_vps_status()
