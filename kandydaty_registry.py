"""kandydaty_registry.py — реєстр ВІДКРИТИХ кандидатів КОДВ (документи_КОДВ/_vidkryti_kandydaty.json).

НАВІЩО (незалежний аудитор, КОДВ_журнал.md «ДОПОВНЕННЯ 5», 2026-09-18, Д1+Д4): курсор кожного
kandydaty-скрипта (checkbox_registry_sync.py й аналоги) означає «що НОВОГО з'явилось у ДЖЕРЕЛІ» —
тому кандидат, якого людина не встигла перенести в книгу до наступного прогону, зникав із
кожного наступного звіту НАЗАВЖДИ (корінь найбільшої знайденої діри: чеки 58-63 висіли
кандидатами з 10.09, курсор пішов далі, жодна рутина не нагадала про них 6 днів — знайшов їх
лише разовий ручний аудит). Цей реєстр — ДРУГИЙ, незалежний курсор: не «що нового в джерелі»,
а «що ще НЕ В КНИЗІ» — незалежно від того, коли кандидат уперше з'явився.

ЯК ІНТЕГРУВАТИ В ІСНУЮЧИЙ KANDYDATY-СКРИПТ (мінімально, без переписування його логіки):
кожен прогін, що визначив, чи кожен його кандидат ВЖЕ звірений з книгою (свій хінт —
book_same_sum_rows / «Поточне I9 → Пропоноване» тощо), викликає ОДИН раз
`sync_open_candidates(source, currently_unresolved)`, де `currently_unresolved` — ті кандидати,
які СЬОГОДНІ, за власною логікою звірки скрипта, досі НЕ знайдені в книзі (а не лише «нові з
часу останнього курсора джерела» — джерело-курсор і реєстр-курсор навмисно незалежні).

Реєстр:
  (а) додає в "open" кандидатів, яких він бачить уперше (`first_seen` = сьогодні);
  (б) кандидатів, які були "open" раніше, але сьогодні СКРИПТ їх більше не вважає
      unresolved (тобто пройшли ЙОГО власну звірку з книгою) — позначає "resolved"
      (`resolved_at` = сьогодні). Реєстр НЕ вирішує ЧОМУ (внесено людиною чи відхилено) —
      це лишається на совісті бухгалтера, реєстр лише фіксує факт;
  (в) кандидатів, які й сьогодні unresolved — лишає "open", вік = сьогодні − first_seen.
Resolved-записи НЕ видаляються (аудиторський слід), лише не потрапляють у щоденний звіт.

`write_open_report()` — окремий крок (можна викликати з БУДЬ-ЯКОГО kandydaty-скрипта в кінці
його прогону, ідемпотентно — читає ввесь реєстр, не залежить від того, хто саме її викликав):
формує `документи_КОДВ/_vidkryti_kandydaty.md` — усі "open" записи, найстарші вгорі.
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from telegram_notify import send_throttled_alert

_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COWORK_DIR = Path(os.environ.get(
    "PLUTUS_COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
REGISTRY_PATH = COWORK_DIR / "документи_КОДВ" / "_vidkryti_kandydaty.json"
REPORT_PATH = COWORK_DIR / "документи_КОДВ" / "_vidkryti_kandydaty.md"
# Визнані винятки (Аудитор, КОДВ_CHANNEL.md, 2026-09-28, п.3): "щоб чеки 1,3,5,8 не висіли
# шумом і не маскували нові" — кандидати, чию затримку власник/бухгалтер уже пояснили,
# виключаються зі stale-алерту (acknowledge()), лишаючись "open" у самому звіті (прозорість —
# видно, що досі не в книзі, просто не сигналить повторно).
ACK_PATH = COWORK_DIR / "документи_КОДВ" / "_vidkryti_kandydaty_ack.json"
DEFAULT_STALE_DAYS = 2


def _load_registry(path: Path = None) -> dict:
    path = path or REGISTRY_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_registry(reg: dict, path: Path = None) -> None:
    path = path or REGISTRY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def compact_summary(parts: list, limit: int = 160) -> str:
    """Склеює короткі фрагменти через « | », НІКОЛИ не ріжучи посеред фрагмента. Раніше `note[:120]` обрізав опис
    посеред числа («винагорода НП 0.92» → «0.»; запит бухгалтера 2026-10-05, рядки 146–147 книги лишились без винагороди
    NovaPay). Тепер: що не влізло в `limit` — відкидається ЦІЛИМИ фрагментами з кінця; числові ставити першими.
    Додатково комісія/винагорода зберігається ЧИСЛОМ у полі `fee` запису (див. sync_open_candidates)."""
    out = []
    total = 0
    for p in parts:
        if p is None:
            continue
        p = str(p).strip()
        if not p:
            continue
        add = len(p) + (3 if out else 0)
        if out and total + add > limit:
            break
        out.append(p)
        total += add
    return " | ".join(out)


def sync_open_candidates(source: str, current: list, path: Path = None, resolve: bool = True) -> dict:
    """`current` — список dict {"key": str, "summary": str, "sum": float, "date": str},
    кандидати джерела `source`, які САМЕ ЗАРАЗ, за логікою джерела-скрипта, ще НЕ в книзі.
    `key` — стабільний ідентифікатор у межах джерела (напр. serial чека, order_id).

    `resolve=False` — цей прогін НЕ закриває жодного "open"-запису, навіть якщо його немає в
    `current` (лише відкриває нові/оновлює still_open). Використовувати, коли викликач НЕ
    впевнений, що `current` — це справді ПОВНИЙ список усіх актуальних unresolved-кандидатів
    джерела за цей прогін (напр. відповідь API обрізана лімітом сторінки) — інакше кандидат,
    що просто випав за межу вибірки, хибно позначився б "resolved" (той самий клас бага, що
    цей реєстр і покликаний закрити, див. докстрінг модуля).

    Повертає {"newly_opened": [...], "still_open": [...], "resolved": [...]} (ключі).
    Ідемпотентно: повторний виклик з тим самим `current` не змінює вже-"open" записів
    (окрім оновлення `last_seen`)."""
    reg = _load_registry(path)
    today = date.today().isoformat()
    current_keys = set()
    newly_opened, still_open = [], []

    for c in current:
        full_key = f"{source}:{c['key']}"
        current_keys.add(full_key)
        existing = reg.get(full_key)
        if existing and existing.get("status") == "open":
            existing["last_seen"] = today
            existing["summary"] = c.get("summary", existing.get("summary", ""))
            if c.get("fee") is not None:
                existing["fee"] = c.get("fee")
            still_open.append(full_key)
        else:
            # Новий АБО раніше "resolved", але знову спливає непокритим — відкриваємо знову
            # (не довіряємо старому resolved-статусу сліпо, якщо джерело зараз каже "не в книзі").
            reg[full_key] = {
                "source": source,
                "key": c["key"],
                "summary": c.get("summary", ""),
                "sum": c.get("sum"),
                "fee": c.get("fee"),
                "date": c.get("date"),
                "status": "open",
                "first_seen": (existing or {}).get("first_seen", today),
                "last_seen": today,
            }
            newly_opened.append(full_key)

    # Закриваємо те, що БУЛО "open" для цього source, але сьогодні джерело його більше не
    # пропонує (пройшло власну звірку джерела з книгою) — ЛИШЕ якщо викликач підтверджує, що
    # `current` цього разу повний (resolve=True за замовчуванням; False — напр. обрізана
    # сторінка API, див. докстрінг вище).
    resolved = []
    if resolve:
        for full_key, entry in reg.items():
            if entry.get("source") == source and entry.get("status") == "open" and full_key not in current_keys:
                entry["status"] = "resolved"
                entry["resolved_at"] = today
                resolved.append(full_key)

    _save_registry(reg, path)
    return {"newly_opened": newly_opened, "still_open": still_open, "resolved": resolved}


def write_open_report(path: Path = None, out_path: Path = None, ack_path: Path = None) -> Path:
    """Формує `_vidkryti_kandydaty.md` — усі "open" записи реєстру, найстарші (найдовше висять)
    вгорі. Можна викликати з будь-якого kandydaty-скрипта наприкінці прогону — читає ввесь
    реєстр, не лише "свій" source. Визнані винятки (acknowledge()) позначені окремо — досі
    видно, що не в книзі, але не рахуються в stale-алерт (див. check_stale_candidates)."""
    reg = _load_registry(path)
    ack = _load_ack(ack_path)
    out_path = out_path or REPORT_PATH
    today = date.today()
    sources = sorted({e.get("source", "?") for e in reg.values()}) or ["ще жодного"]
    open_entries = []
    for full_key, entry in reg.items():
        if entry.get("status") != "open":
            continue
        try:
            first_seen = date.fromisoformat(entry.get("first_seen", ""))
            age_days = (today - first_seen).days
        except ValueError:
            age_days = None
        open_entries.append((age_days if age_days is not None else -1, full_key, entry))
    open_entries.sort(key=lambda t: t[0], reverse=True)

    lines = [
        f"# Відкриті кандидати КОДВ — ще не в книзі, {today.isoformat()}",
        "",
        "Автоматично зведено з усіх kandydaty-джерел, які інтегровані з `kandydaty_registry.py`",
        f"(зараз: {', '.join(sources)}). Кандидат зникає звідси, лише коли джерело САМЕ",
        "підтвердить, що він більше не unresolved (пройшов власну звірку з книгою) — не за",
        "курсором джерела.",
        "",
        "| Днів висить | Джерело | Ключ | Сума | Комісія | Дата | Опис | Визнаний виняток |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for age_days, full_key, entry in open_entries:
        age_str = str(age_days) if age_days is not None and age_days >= 0 else "?"
        ack_entry = ack.get(full_key)
        ack_str = f"✅ {ack_entry['reason']}" if ack_entry else ""
        lines.append(
            f"| {age_str} | {entry.get('source', '?')} | {entry.get('key', '?')} | "
            f"{entry.get('sum', '?')} | {entry.get('fee') if entry.get('fee') is not None else ''} | "
            f"{entry.get('date', '?')} | {entry.get('summary', '')} | {ack_str} |"
        )
    if not open_entries:
        lines.append("| — | — | — | — | — | — | Немає відкритих кандидатів. | |")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def _load_ack(path: Path = None) -> dict:
    path = path or ACK_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def acknowledge(full_key: str, reason: str, path: Path = None) -> None:
    """Позначає ВІДКРИТОГО кандидата визнаним винятком (Аудитор, КОДВ_CHANNEL.md, 2026-09-28,
    п.3 — «чеки 1,3,5,8 не мають висіти шумом і маскувати нові»): власник/бухгалтер уже знає
    причину затримки. Виключається зі stale-алерту (check_stale_candidates/send_stale_alert),
    лишається "open" у самому звіті — прозорість, не приховування факту, що досі не в книзі."""
    path = path or ACK_PATH
    ack = _load_ack(path)
    ack[full_key] = {"reason": reason, "acknowledged_at": date.today().isoformat()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ack, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def check_stale_candidates(
    max_age_days: int = DEFAULT_STALE_DAYS, path: Path = None, ack_path: Path = None,
) -> list:
    """Відкриті кандидати, що висять довше `max_age_days` і НЕ визнані винятком (acknowledge()).
    Повертає [(age_days, full_key, entry), ...], найстаріші спершу — порожній список, якщо
    нема нічого стривоженого. Сама НЕ шле алерт — окремо send_stale_alert()."""
    reg = _load_registry(path)
    ack = _load_ack(ack_path)
    today = date.today()
    stale = []
    for full_key, entry in reg.items():
        if entry.get("status") != "open" or full_key in ack:
            continue
        try:
            first_seen = date.fromisoformat(entry.get("first_seen", ""))
        except (ValueError, TypeError):
            continue
        age_days = (today - first_seen).days
        if age_days >= max_age_days:
            stale.append((age_days, full_key, entry))
    stale.sort(key=lambda t: t[0], reverse=True)
    return stale


def send_stale_alert(
    max_age_days: int = DEFAULT_STALE_DAYS, path: Path = None, ack_path: Path = None,
) -> bool:
    """«Книга стоїть» — сигнал, якого не було (аудит 2026-09-28, п.3: `source_freshness.py`
    дивиться лише свіжість ДЖЕРЕЛА, не вік НЕВНЕСЕНОГО кандидата — джерело може оновлюватись
    щодня, а кандидати все одно накопичуватись, якщо writer/бухгалтер не встигають). Якщо є
    непідтверджені відкриті кандидати старші `max_age_days` — ОДИН throttled Telegram-алерт
    (раз на добу, поки стан триває, `telegram_notify.send_throttled_alert`) з переліком.
    Повертає True, лише якщо алерт реально пішов цього разу (поза вікном тиші throttle) —
    False і коли стріляти нема чого, коли throttle-вікно ще не минуло, і коли AUDIT_NO_TELEGRAM=1
    (аудит 2026-09-29: ручний діагностичний запуск `stale-check` дав РЕАЛЬНИЙ алерт — цей
    модуль, на відміну від УСІХ інших kandydaty-скриптів проєкту, не мав власного _NO_TELEGRAM-
    гейту для тихого ручного тестування)."""
    stale = check_stale_candidates(max_age_days, path, ack_path)
    if not stale:
        return False
    if _NO_TELEGRAM:
        return False
    plural = "ів" if len(stale) != 1 else ""
    lines = [f"🔴 КОДВ: {len(stale)} відкрит{plural} кандидат{'' if len(stale) == 1 else 'и'} "
             f"висить{'ь' if len(stale) == 1 else ''} довше {max_age_days} дн (книга не встигає):"]
    for age_days, full_key, entry in stale[:10]:
        summary = (entry.get("summary") or "")[:60]
        lines.append(f"  • {entry.get('source', '?')}:{entry.get('key', '?')} — {age_days} дн, "
                     f"{entry.get('sum', '?')} — {summary}")
    if len(stale) > 10:
        lines.append(f"  ...і ще {len(stale) - 10}")
    lines.append("Визнаний виняток: kandydaty_registry.acknowledge(full_key, 'причина').")
    return send_throttled_alert("kodv_stale_candidates", "\n".join(lines), cooldown_sec=24 * 3600)


_NUMBER_TOKEN_RE = re.compile(r"(?<![\d,.])\d+(?:[.,]\d+)?(?![\d])")
# Слова ПЕРЕД числом, що означають «це ЩЕ НЕ внесено» (аудит 2026-09-29, живий приклад
# з рядка книги: примітка «бракує 10,20» — саме число кандидата, але в НЕГАТИВНОМУ сенсі).
# Вікно пошуку — 25 символів ПЕРЕД числом (достатньо для «бракує »/«не вистачає » тощо,
# замалий, щоб зачепити попереднє, непов'язане речення).
_NEGATIVE_CONTEXT_WORDS = ("бракує", "не вистачає", "недостає", "залишилось внести",
                           "потрібно ще", "мінус", "відсутньо", "не внесено", "ще не")
_NEGATIVE_CONTEXT_WINDOW = 25


def amount_applied_in_text(text: str, amount) -> bool:
    """Чи згадує вільний текст (типово Графа 5 книги) конкретну суму — спільний критерій
    «внесено» для kandydaty-джерел, що звіряються з книгою за текстом, не структурними даними
    (Аудитор, КОДВ_CHANNEL.md, 2026-09-28, п.1: "відкритий, доки розклад i9 у графі 5 рядка
    цього замовлення не містить суму"). Книга пише суми КОМОЮ ("47,18"), джерела рахують
    крапкою (float).

    ЧИСЛОВЕ порівняння, НЕ текстовий substring (аудит 2026-09-29, живий приклад: книга іноді
    пише «10.2» замість «10.20», рядок 118 «стало 10.2 (+10.2)» — текстовий пошук «10.20»
    цього не зловив би, хибне «не внесено»). Витягуємо кожне число з тексту (з тією самою
    межовою перевіркою, що й раніше — «10,20» НЕ збігається всередині «110,20»), парсимо у
    float, порівнюємо ЧИСЛА (round до копійки), не рядки — «10.2» і «10.20» тепер РІВНІ.

    НЕГАТИВНИЙ КОНТЕКСТ (аудит 2026-09-29, живий приклад: примітка «бракує 10,20» містить
    те саме число кандидата, але означає ПРОТИЛЕЖНЕ — досі НЕ внесено): якщо безпосередньо
    ПЕРЕД числом (у межах _NEGATIVE_CONTEXT_WINDOW символів) стоїть слово з
    _NEGATIVE_CONTEXT_WORDS — цей збіг НЕ рахується. Прагматичний захист (конкретний
    словник, не NLP) — краще пропустити межовий випадок, ніж хибно закрити невнесений факт."""
    if not text or amount is None:
        return False
    try:
        target = round(float(amount), 2)
    except (TypeError, ValueError):
        return False
    text_lower = text.lower()
    for m in _NUMBER_TOKEN_RE.finditer(text):
        raw = m.group(0).replace(",", ".")
        try:
            val = round(float(raw), 2)
        except ValueError:
            continue
        if val != target:
            continue
        before = text_lower[max(0, m.start() - _NEGATIVE_CONTEXT_WINDOW):m.start()]
        if any(neg in before for neg in _NEGATIVE_CONTEXT_WORDS):
            continue
        return True
    return False


def resolve_open_candidates_by_text(source: str, lookup_text_fn, path: Path = None) -> dict:
    """Для КОЖНОГО відкритого кандидата цього source — викликає `lookup_text_fn(key)` (типово
    Графа 5 відповідного рядка книги, READ-ONLY), закриває ("resolved"), якщо
    amount_applied_in_text() підтверджує суму кандидата (entry["sum"]) у цьому тексті.
    НЕЗАЛЕЖНО від того, чи джерело САМЕ бачить цього кандидата зараз (на відміну від
    sync_open_candidates(resolve=True), яка орієнтується на `current` — тут звірка йде проти
    ЖИВОЇ книги для ВСІХ відкритих, навіть тих, що вже випали з вікна джерела) — саме це
    закриває клас бага "губить факти назавжди" (курсор джерела рухається незалежно).

    `lookup_text_fn` — відповідальність ВИКЛИКАЧА перехопити мережеві/файлові збої (best-effort,
    тут лише порівняння тексту); виняток із lookup_text_fn НЕ ловиться навмисно — викликач
    (kandydaty-скрипт) сам вирішує, чи one order lookup, що впав, має зупинити весь прогін."""
    reg = _load_registry(path)
    today = date.today().isoformat()
    resolved = []
    for full_key, entry in reg.items():
        if entry.get("source") != source or entry.get("status") != "open":
            continue
        text = lookup_text_fn(entry.get("key", "")) or ""
        if amount_applied_in_text(text, entry.get("sum")):
            entry["status"] = "resolved"
            entry["resolved_at"] = today
            entry["resolved_reason"] = "сума знайдена в тексті книги"
            resolved.append(full_key)
    if resolved:
        _save_registry(reg, path)
    return {"resolved": resolved}


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")

    p_report = sub.add_parser("report", help="сформувати _vidkryti_kandydaty.md (за замовчуванням)")

    p_stale = sub.add_parser("stale-check", help="перевірити й надіслати throttled-алерт «книга стоїть»")
    p_stale.add_argument("--max-age-days", type=int, default=DEFAULT_STALE_DAYS)

    p_ack = sub.add_parser("ack", help="визнати відкритого кандидата винятком (не сигналити)")
    p_ack.add_argument("full_key", help='напр. "checkbox:58"')
    p_ack.add_argument("reason")

    args = parser.parse_args()

    if args.cmd == "ack":
        acknowledge(args.full_key, args.reason)
        print(f"[kandydaty_registry] Визнано винятком: {args.full_key} — {args.reason}")
        return 0

    if args.cmd == "stale-check":
        stale = check_stale_candidates(args.max_age_days)
        sent = send_stale_alert(args.max_age_days)
        print(f"[kandydaty_registry] Стривожених кандидатів: {len(stale)}"
              + (f" (алерт надіслано)" if sent else " (алерт у вікні тиші/нема чого слати)"))
        return 0

    p = write_open_report()
    reg = _load_registry()
    n_open = sum(1 for e in reg.values() if e.get("status") == "open")
    print(f"[kandydaty_registry] Звіт сформовано: {p} ({n_open} відкритих кандидатів)")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
