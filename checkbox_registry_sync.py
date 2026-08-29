"""checkbox_registry_sync.py — збирач нових фіскальних чеків Checkbox → кандидати КОДВ.

Задача власника 2026-08-29 (канал КОДВ): механічні КОДВ-рутини — детермінованими скриптами,
не LLM-сесіями. Це друга з 5 битих Windows-задач (PlutusToys-ChecboxRegistrySync).

ЩО РОБИТЬ: тягне через офіційний Checkbox API (api.checkbox.in.ua/api/v1, GET /receipts/search —
підтверджено живим викликом 2026-08-29) фіскальні чеки нашої каси, і НОВІ (з часу останнього
курсора) виписує як КАНДИДАТІВ доходу у документи_КОДВ/YYYY-MM/Checkbox/. Бухгалтер звіряє з
книгою (сума+дата) і сам вирішує визнання доходу — скрипт КНИГУ НЕ ПИШЕ.

ЧОМУ ПРОСТО СПИСОК, А НЕ АВТО-ЗІСТАВЛЕННЯ З КНИГОЮ:
  чеки Checkbox НЕ несуть номер замовлення (`order_id=None` — перевірено живо), а книга звіряє
  дохід за номером замовлення платформи («Prom.ua №…», «Rozetka №…» у графі 5). Тож автоматично
  прив'язати чек до рядка книги детерміновано НЕ можна — це робить бухгалтер за сумою+датою+типом.
  Скрипт дає легкий хінт «скільки рядків графи 2 книги мають таку саму суму» (0 = майже напевно ще
  не в книзі), але рішення — за роллю бухгалтер (як кандидати Rozetka/EVA-леджерів).

Курсор — за серіалом чека (послідовний int). Перший запуск = базова лінія (не дампимо всю
історію як «нове» — вона вже в книзі), далі лише serial > останнього.

Креди: CHECKBOX_API_KEY + CHECKBOX_CASHIER_PIN (ті самі, що create_receipt). Без них — м'який вихід.
READ-ONLY по касі: лише GET /receipts/search і авторизація касира; зміну НЕ відкриваємо, чеків НЕ
створюємо.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

import checkbox_client as cb

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
COWORK_DIR = Path(os.environ.get(
    "PLUTUS_COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
KODV_XLSX = COWORK_DIR / "KODV_PlutusToys_2026.xlsx"
CURSOR_FILE = BASE_DIR / ".local_secrets" / "checkbox_registry_cursor.json"
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

# Скільки останніх чеків тягнути (desc). API-максимум сторінки = 100 (limit=200 → 422, перевірено
# живо). Каса низькооборотна (усього 39 чеків станом на 2026-08-29) — 100 покриває з запасом.
# Якщо колись обіг перевищить 100 між прогонами — додати пагінацію по meta.offset.
FETCH_LIMIT = min(int(os.environ.get("CHECKBOX_FETCH_LIMIT", "100")), 100)


def _log(msg: str) -> None:
    print(f"[CheckboxSync] {msg}")


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        sys.path.insert(0, str(BASE_DIR))
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[CheckboxSync] Telegram не надіслано: {e}", file=sys.stderr)


def _load_cursor() -> dict:
    try:
        return json.loads(CURSOR_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cursor(last_serial: int) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(
        json.dumps({"last_serial": last_serial, "updated_at": datetime.now().isoformat(timespec="seconds")},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def fetch_receipts() -> list:
    """GET /receipts/search (desc) → список валідних фіскальних чеків (DONE, не тестові).
    READ-ONLY: лише авторизація касира + GET. Зміну не відкриваємо, чеків не створюємо."""
    token = cb._authenticate_cashier()
    headers = {"X-License-Key": cb.CHECKBOX_API_KEY, "Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(f"{cb.CHECKBOX_API_URL}/receipts/search",
                            headers=headers, params={"limit": FETCH_LIMIT, "desc": "true"},
                            timeout=cb.REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise cb.CheckboxAPIError(f"помилка з'єднання (GET /receipts/search): {e}") from e
    try:
        results = (resp.json() or {}).get("results") or []
    except ValueError:
        raise cb.CheckboxAPIError(f"невалідна відповідь (не JSON) /receipts/search: {resp.text[:300]}")

    receipts = []
    for it in results:
        if it.get("status") != "DONE" or it.get("is_test") is True:
            continue
        serial = it.get("serial")
        if serial is None:
            continue
        pays = it.get("payments") or []
        pay_type = pays[0].get("type") if pays else None          # CASH / CASHLESS
        pay_label = (pays[0].get("label") if pays else None) or pay_type or "?"
        receipts.append({
            "serial": int(serial),
            "fiscal_code": it.get("fiscal_code"),
            "sum_uah": round((it.get("total_sum") or 0) / 100, 2),
            "type": it.get("type"),                                # SELL / RETURN
            "pay_type": pay_type,
            "pay_label": pay_label,
            "created_at": (it.get("created_at") or "")[:19],
        })
    return receipts


def _book_amount_index() -> dict:
    """READ-ONLY: {сума_доходу(грн, 2 знаки) → к-сть рядків графи 2 книги з такою сумою}.
    Легкий хінт для бухгалтера (0 = чек майже напевно ще не в книзі). Книгу НЕ пише."""
    index: dict = {}
    if not KODV_XLSX.exists():
        return index
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(KODV_XLSX), data_only=True, read_only=True)
        ws = wb["КОДВ"]
        for row in ws.iter_rows(min_row=7):
            b = row[1].value if len(row) > 1 else None            # графа 2 — сума доходу
            if isinstance(b, (int, float)) and b:
                key = round(float(b), 2)
                index[key] = index.get(key, 0) + 1
    except Exception as e:  # noqa: BLE001 — хінт не критичний
        print(f"[CheckboxSync] книжковий хінт не побудовано (не критично): {e}", file=sys.stderr)
    return index


def collect() -> tuple:
    cursor = _load_cursor()
    last_serial = cursor.get("last_serial")
    receipts = fetch_receipts()
    if not receipts:
        return [], last_serial, True
    max_serial = max(r["serial"] for r in receipts)

    if last_serial is None:
        # Базова лінія: історія вже в книзі — не дампимо як «нове».
        _log(f"Перший запуск — базова лінія за серіалом ≤{max_serial}, кандидатів не шукаю.")
        return [], max_serial, True

    book_idx = _book_amount_index()
    new = [r for r in receipts if r["serial"] > last_serial]
    new.sort(key=lambda r: r["serial"])
    for r in new:
        r["book_same_sum_rows"] = book_idx.get(r["sum_uah"], 0)
    return new, max(max_serial, last_serial), False


def _write_report(candidates: list) -> Path:
    today = datetime.now()
    month_dir = COWORK_DIR / "документи_КОДВ" / today.strftime("%Y-%m") / "Checkbox"
    month_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{today.strftime('%Y-%m-%d')}_checkbox_cheky_kandydaty"

    (month_dir / f"{stem}.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# Checkbox — нові фіскальні чеки, знайдені автоматично, {today.strftime('%Y-%m-%d %H:%M')}",
             "",
             "Джерело: Checkbox API `GET /receipts/search` (наша каса). Це КАНДИДАТИ доходу —",
             "звірити з книгою за сумою+датою+типом оплати перед записом. Книгу НЕ змінено.",
             "«Збігів суми в графі 2»: скільки рядків доходу книги вже мають таку суму (0 = майже",
             "напевно ще не в книзі; ≥1 = можливо вже внесено, перевірити щоб не задвоїти).",
             "",
             "| Серіал | Дата (UTC) | Сума, грн | Тип | Оплата | Фіскальний код | Збігів суми в графі 2 |",
             "|---|---|---|---|---|---|---|"]
    for c in candidates:
        lines.append(f"| {c['serial']} | {c['created_at']} | {c['sum_uah']} | {c['type']} | "
                     f"{c['pay_label']} | {c['fiscal_code']} | {c['book_same_sum_rows']} |")
    (month_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return month_dir / f"{stem}.md"


def main() -> None:
    if not (cb.CHECKBOX_API_KEY and cb.CHECKBOX_CASHIER_PIN):
        _log("CHECKBOX_API_KEY/CHECKBOX_CASHIER_PIN не задані — збирач пропущено (вихід 0, ланцюг не валю).")
        return
    dry_run = "--dry-run" in sys.argv
    try:
        candidates, new_serial, is_baseline = collect()
    except (cb.CheckboxAPIError, OSError) as e:
        _notify(f"🚨 checkbox_registry_sync: помилка збору чеків Checkbox: {e}")
        _log(f"помилка: {e}")
        sys.exit(1)

    if is_baseline:
        if not dry_run and new_serial is not None:
            _save_cursor(new_serial)
        _log("Базова лінія встановлена — нових кандидатів нема." if not candidates else "")
        return

    if not candidates:
        if not dry_run:
            _save_cursor(new_serial)
        _log("Нових чеків з часу останнього запуску нема.")
        return

    if dry_run:
        _log(f"[dry-run] БУЛО Б виписано {len(candidates)} кандидатів (серіали "
             f"{candidates[0]['serial']}–{candidates[-1]['serial']}); курсор не рухаю, файли не пишу.")
        for c in candidates:
            _log(f"  [dry-run] чек {c['serial']}: {c['sum_uah']} грн {c['pay_label']} "
                 f"{c['created_at']} (збігів суми в книзі: {c['book_same_sum_rows']})")
        return

    path = _write_report(candidates)
    _save_cursor(new_serial)
    _log(f"ГОТОВО: {len(candidates)} нових чеків → {path}")


if __name__ == "__main__":
    main()
