"""prom_pushed_ledger.py — журнал external_id, які МИ КОЛИ-НЕБУДЬ штовхали в Prom.

Навіщо (проблема «невидимої групи», Phase 3 2026-08-11):
Публічний Prom API (/groups/list, який бачить fetch_prom_products()) стабільно НЕ
повертає частину груп цього акаунта — історично видно лише ~2402 з ~5836 товарів.
Товар випадає з топ-6000 (нульовий залишок / витіснений кращою маржею), але лишається
«Опубліковано» в невидимій групі, займаючи місце тарифу. Автоматичний prom_catalog_sync
його не бачить (find_stale працює лише по видимому зрізу), тож OOS-мотлох накопичувався,
і каталог не тримався на 6000 наявних. Раніше чистилось ЛИШЕ вручну (clear_invisible_oos.py,
через сесію кабінету /cms/product/list?presence=not_avail).

Цей журнал робить чистку автоматичною БЕЗ сесії кабінету: товар опинився в Prom тому, що
колись був у prom_feed_top.xml. Записуючи КОЖЕН external_id, який ми штовхаємо (generate_
prom_feed_top.py), маємо повний набір «наших» лістингів. prom_catalog_sync потім бере
кандидатів = журнал − поточний_топ − видимий_зріз, звіряє КОЖНОГО НАЖИВО через
/by_external_id (структурно НЕ залежить від /groups/list — задача #47/#64), і застарілі
(живі 200 + поза топ-6000) деактивує тим самим безпечним delist() + _delisted_since.

Формат: JSON-список external_id (рядки). Самоочищення: prom_catalog_sync прибирає з журналу
кожен id, підтверджено відсутній (404) чи вже видалений (status=deleted) — журнал не росте
надгробками. Суто локальний VPS-стан (як full_catalog_scan_state.json) — НЕ для git,
account-specific, перебудовується з фідів; жодних фінансових даних (лише id товарів).
"""
import json
import sys
from pathlib import Path

LEDGER_FILE = Path(__file__).parent / "prom_pushed_ledger.json"


def load_ledger() -> set:
    """Множина external_id (рядки), які ми коли-небудь штовхали в Prom. Відсутній/
    пошкоджений файл — порожня множина (журнал наповниться з наступного фіду), не помилка."""
    try:
        data = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(x) for x in data} if isinstance(data, list) else set()


def _save_ledger(ids: set) -> None:
    try:
        LEDGER_FILE.write_text(json.dumps(sorted(ids), ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        print(f"[ledger] Не вдалось зберегти {LEDGER_FILE.name} ({e})", file=sys.stderr)


def record_pushed(external_ids) -> int:
    """Додати щойно штовхнуті external_id до журналу (об'єднання, накопичувально).
    Викликається після кожної генерації prom_feed_top.xml. Повертає новий розмір журналу.
    Наявні id не дублюються; вже присутні drop-out'и лишаються (вони — майбутні кандидати
    на звірку, бо їх НЕМА в поточному топ-6000)."""
    ledger = load_ledger()
    before = len(ledger)
    ledger.update(str(x) for x in external_ids)
    if len(ledger) != before:
        _save_ledger(ledger)
    return len(ledger)


def prune(external_ids) -> int:
    """Прибрати external_id (підтверджено відсутні в Prom або вже видалені) з журналу —
    самоочищення, щоб журнал не ріс надгробками й кандидатський пул лишався обмеженим.
    Повертає кількість реально прибраних."""
    to_remove = {str(x) for x in external_ids}
    if not to_remove:
        return 0
    ledger = load_ledger()
    removed = ledger & to_remove
    if removed:
        _save_ledger(ledger - to_remove)
    return len(removed)
