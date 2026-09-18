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
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COWORK_DIR = Path(os.environ.get(
    "PLUTUS_COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
REGISTRY_PATH = COWORK_DIR / "документи_КОДВ" / "_vidkryti_kandydaty.json"
REPORT_PATH = COWORK_DIR / "документи_КОДВ" / "_vidkryti_kandydaty.md"


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
            still_open.append(full_key)
        else:
            # Новий АБО раніше "resolved", але знову спливає непокритим — відкриваємо знову
            # (не довіряємо старому resolved-статусу сліпо, якщо джерело зараз каже "не в книзі").
            reg[full_key] = {
                "source": source,
                "key": c["key"],
                "summary": c.get("summary", ""),
                "sum": c.get("sum"),
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


def write_open_report(path: Path = None, out_path: Path = None) -> Path:
    """Формує `_vidkryti_kandydaty.md` — усі "open" записи реєстру, найстарші (найдовше висять)
    вгорі. Можна викликати з будь-якого kandydaty-скрипта наприкінці прогону — читає ввесь
    реєстр, не лише "свій" source."""
    reg = _load_registry(path)
    out_path = out_path or REPORT_PATH
    today = date.today()
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
        "(поки що: Checkbox). Кандидат зникає звідси, лише коли джерело САМЕ підтвердить, що він",
        "більше не unresolved (пройшов власну звірку з книгою) — не за курсором джерела.",
        "",
        "| Днів висить | Джерело | Ключ | Сума | Дата | Опис |",
        "|---|---|---|---|---|---|",
    ]
    for age_days, full_key, entry in open_entries:
        age_str = str(age_days) if age_days is not None and age_days >= 0 else "?"
        lines.append(
            f"| {age_str} | {entry.get('source', '?')} | {entry.get('key', '?')} | "
            f"{entry.get('sum', '?')} | {entry.get('date', '?')} | {entry.get('summary', '')} |"
        )
    if not open_entries:
        lines.append("| — | — | — | — | — | Немає відкритих кандидатів. |")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


if __name__ == "__main__":
    p = write_open_report()
    reg = _load_registry()
    n_open = sum(1 for e in reg.values() if e.get("status") == "open")
    print(f"[kandydaty_registry] Звіт сформовано: {p} ({n_open} відкритих кандидатів)")
