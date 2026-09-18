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
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import checkbox_client as cb
import kandydaty_registry

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


def fetch_receipts() -> tuple:
    """GET /receipts/search (desc) → (список валідних фіскальних чеків (DONE, не тестові), truncated).
    `truncated=True` — сторінка заповнена вщент (можуть бути старіші чеки поза вибіркою).
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
    truncated = len(results) >= FETCH_LIMIT
    if truncated:
        # Сторінка заповнена вщент — між прогонами могло з'явитись >FETCH_LIMIT чеків, і найстаріші
        # «нові» випали б за межу вибірки, а курсор стрибнув би повз них (латентна втрата). Каса
        # низькооборотна, тож малоймовірно, але сигналимо, щоб не пройшло тихо. `truncated` також
        # НЕ дає main() закривати "open"-кандидатів у kandydaty_registry цим прогоном (аудит,
        # 2026-09-18) — інакше кандидат старший за межу сторінки випав би зі списку "unresolved" і
        # хибно позначився б "resolved", хоча насправді просто не потрапив у вибірку.
        _log(f"⚠️ отримано {len(results)} чеків = ліміт сторінки {FETCH_LIMIT}: можливо є ще старіші "
             f"нові чеки поза вибіркою — за потреби додати пагінацію по meta.offset.")
        _notify(f"⚠️ checkbox_registry_sync: сторінка чеків заповнена ({FETCH_LIMIT}) — перевір, чи "
                f"не втрачено старіші нові чеки; можливо потрібна пагінація.")

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
    return receipts, truncated


def _coerce_date(value):
    """openpyxl віддає datetime/date для дат книги, зрідка рядок. Приводимо до date або None
    (None — дата не розпізнана, НЕ вважається збігом ні з чим)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value[:10]).date()
        except ValueError:
            return None
    return None


def _book_date_sum_index() -> dict:
    """READ-ONLY: {сума_доходу(грн, 2 знаки) → [дата рядка, ...]} з графи 1(дата)/графа 2(сума)
    книги. Книгу НЕ пише.

    АУДИТ Д2 (незалежний аудитор, КОДВ_журнал «ДОПОВНЕННЯ 5», 2026-09-18): раніше індекс брав
    ЛИШЕ суму — рядок з правильною сумою й ХИБНОЮ датою (напр. 194,00 ₴ від 10.09, а в книзі
    стояло 30.08 — 11 днів різниці, через межу місяця) читався як «збігів: 1» → «уже внесено».
    Автоматика не просто пропустила помилку — вона ВИДАЛА підтвердження хибному рядку. Дата
    тепер обов'язкова частина звірки (див. _match_book нижче): «сума збігається, дата ні» —
    окремий, видимий сигнал, не тихе «ОК»."""
    index: dict = {}
    if not KODV_XLSX.exists():
        return index
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(KODV_XLSX), data_only=True, read_only=True)
        try:
            ws = wb["КОДВ"]
            for row in ws.iter_rows(min_row=7):
                if len(row) < 2:
                    continue
                a, b = row[0].value, row[1].value      # графа 1 — дата, графа 2 — сума доходу
                if not isinstance(b, (int, float)) or not b:
                    continue
                key = round(float(b), 2)
                index.setdefault(key, []).append(_coerce_date(a))
        finally:
            wb.close()  # read_only-книга тримає файловий дескриптор відкритим, поки не закрити явно
    except Exception as e:  # noqa: BLE001 — хінт не критичний
        print(f"[CheckboxSync] книжковий хінт не побудовано (не критично): {e}", file=sys.stderr)
    return index


_KYIV_TZ = ZoneInfo("Europe/Kyiv")


def _match_book(by_sum: dict, sum_uah: float, created_at_utc: str) -> dict:
    """Порівнює чек з книгою за (сума, дата ±1 день, Київ). Повертає
    {"exact": N, "sum_only": N, "kyiv_date": "YYYY-MM-DD"|None}.

    Чому ±1 день, не точний збіг: касовий чек і рядок книги можуть різнитись на добу через
    момент фіксації (вечірній чек проти ранкового запису) — це нормальна похибка, не помилка.
    Різниця, БІЛЬША за 1 день (як інцидент 11 днів через межу місяця) — уже НЕ похибка.

    Чому через zoneinfo, не фіксований timedelta(hours=3): Checkbox `created_at` — UTC (перевірено
    живо), а Київ EEST=UTC+3 лише з 29.03 по 25.10; решту року EET=UTC+2 (аудит 2026-09-18 —
    попередня версія мала захардкоджений +3, що стало б систематичною похибкою на годину для
    кожного вечірнього чека з 26.10). `ZoneInfo` рахує правильний зсув на кожну конкретну дату."""
    dates = by_sum.get(sum_uah, [])
    try:
        kyiv_date = (datetime.fromisoformat(created_at_utc)
                     .replace(tzinfo=timezone.utc).astimezone(_KYIV_TZ)).date()
    except ValueError:
        return {"exact": 0, "sum_only": len(dates), "kyiv_date": None}
    exact = sum(1 for d in dates if d is not None and abs((d - kyiv_date).days) <= 1)
    return {"exact": exact, "sum_only": len(dates) - exact, "kyiv_date": kyiv_date.isoformat()}


def collect() -> tuple:
    """Повертає (new_for_report, max_serial, is_baseline, all_matched, window_truncated).
    `new_for_report` — лише СЕРІАЛ-нові (стара поведінка, для щоденного .md/.json звіту).
    `all_matched` — УСІ отримані чеки (останні FETCH_LIMIT, незалежно від курсора джерела) зі
    звіркою з книгою — для kandydaty_registry (аудит Д1: реєстр НЕ довіряє курсору джерела,
    бо саме курсор губив кандидатів, яких не встигли внести). На базовій лінії — порожньо
    (перший запуск свідомо НЕ трактує всю історію як «нове», той самий принцип поширюється на
    реєстр — не заводимо сотні історичних чеків як «щойно відкриті кандидати»).
    `window_truncated` — True, якщо fetch_receipts() отримав рівно FETCH_LIMIT чеків (сторінка
    могла не показати ВСІ фактично актуальні чеки). main() тоді НЕ закриває "open"-кандидатів
    у kandydaty_registry цим прогоном (аудит 2026-09-18, Д1/Д4-рецидив) — інакше кандидат
    старший за межу сторінки випав би зі списку unresolved і хибно позначився б "resolved"."""
    cursor = _load_cursor()
    last_serial = cursor.get("last_serial")
    receipts, truncated = fetch_receipts()
    if not receipts:
        # last_serial уже задано, а цей прогін просто не отримав жодного валідного чека
        # (фільтр status/is_test тощо) — це НЕ базова лінія, курсор рухати нема куди.
        return [], last_serial, last_serial is None, [], truncated
    max_serial = max(r["serial"] for r in receipts)

    if last_serial is None:
        # Базова лінія: історія вже в книзі — не дампимо як «нове».
        _log(f"Перший запуск — базова лінія за серіалом ≤{max_serial}, кандидатів не шукаю.")
        return [], max_serial, True, [], truncated

    book_idx = _book_date_sum_index()
    for r in receipts:
        m = _match_book(book_idx, r["sum_uah"], r["created_at"])
        r["book_exact_matches"] = m["exact"]
        r["book_sum_only_matches"] = m["sum_only"]
        r["book_same_sum_rows"] = m["exact"] + m["sum_only"]  # зворотна сумісність зі старим полем
        r["kyiv_date"] = m["kyiv_date"]

    new = [r for r in receipts if r["serial"] > last_serial]
    new.sort(key=lambda r: r["serial"])
    return new, max(max_serial, last_serial), False, receipts, truncated


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
             "«Точний збіг»: рядків книги з ТАКОЮ Ж сумою й датою ±1 день (Київ) — 0 = майже",
             "напевно ще не в книзі. «Лише сума»: сума збігається, АЛЕ дата ні (⚠️ перевірити",
             "уважно — саме такий рядок хибно виглядав «уже внесеним», аудит 2026-09-18, Д2).",
             "",
             "| Серіал | Дата (Київ) | Дата (UTC) | Сума, грн | Тип | Оплата | Фіскальний код | Точний збіг | ⚠️ Лише сума (дата не збіглась) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for c in candidates:
        warn = "⚠️" if c.get("book_sum_only_matches", 0) > 0 else ""
        lines.append(f"| {c['serial']} | {c.get('kyiv_date', '?')} | {c['created_at']} | {c['sum_uah']} | {c['type']} | "
                     f"{c['pay_label']} | {c['fiscal_code']} | {c.get('book_exact_matches', '?')} | "
                     f"{warn} {c.get('book_sum_only_matches', 0)} |")
    (month_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return month_dir / f"{stem}.md"


def main() -> None:
    if not (cb.CHECKBOX_API_KEY and cb.CHECKBOX_CASHIER_PIN):
        _log("CHECKBOX_API_KEY/CHECKBOX_CASHIER_PIN не задані — збирач пропущено (вихід 0, ланцюг не валю).")
        return
    dry_run = "--dry-run" in sys.argv
    try:
        candidates, new_serial, is_baseline, all_matched, window_truncated = collect()
    except (cb.CheckboxAPIError, OSError) as e:
        _notify(f"🚨 checkbox_registry_sync: помилка збору чеків Checkbox: {e}")
        _log(f"помилка: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 — несподівана помилка не має падати ТИХО без сповіщення
        _notify(f"🚨 checkbox_registry_sync: несподівана помилка: {e}")
        _log(f"несподівана помилка: {e}")
        sys.exit(1)

    if is_baseline:
        if not dry_run and new_serial is not None:
            _save_cursor(new_serial)
        _log("Базова лінія встановлена — нових кандидатів нема.")
        return

    # РЕЄСТР ВІДКРИТИХ КАНДИДАТІВ (аудит Д1+Д4, 2026-09-18): незалежно від курсора джерела
    # (last_serial) — синхронізуємо ВСІ щойно отримані чеки, чия сума не має точного збігу в
    # книзі (book_exact_matches==0), як "ще не в книзі". Це той самий клас чеків, що раніше
    # губився назавжди після першого показу: тепер вони лишаються "open" у реєстрі, доки книга
    # не покаже точний збіг — незалежно від того, чи курсор джерела вже пройшов повз них.
    # dry-run НЕ чіпає реєстр (як і курсор) — узгоджено з рештою скрипта.
    #
    # `resolve=not window_truncated` (аудит 2026-09-18, рецидив Д1/Д4): якщо сторінка API
    # заповнена вщент, `all_matched` НЕ показує гарантовано ВСІ актуальні чеки — старий "open"
    # кандидат, що випав за межу сторінки, виглядав би "відсутній у current" і хибно закрився б
    # "resolved", хоча насправді просто не потрапив у вибірку цього прогону. Тому при truncated
    # реєстр лише ВІДКРИВАЄ нових/оновлює still_open, але НІКОГО не закриває цим прогоном.
    if not dry_run and all_matched:
        unresolved = [
            {
                "key": str(r["serial"]),
                "summary": f"{r['sum_uah']} грн {r['pay_label']} {r.get('kyiv_date') or r['created_at']}",
                "sum": r["sum_uah"],
                "date": r.get("kyiv_date") or r["created_at"],
            }
            for r in all_matched if r.get("book_exact_matches", 0) == 0
        ]
        sync_result = kandydaty_registry.sync_open_candidates(
            "checkbox", unresolved, resolve=not window_truncated)
        if sync_result["newly_opened"] or sync_result["resolved"]:
            _log(f"Реєстр відкритих кандидатів: +{len(sync_result['newly_opened'])} нових, "
                 f"-{len(sync_result['resolved'])} закритих, {len(sync_result['still_open'])} досі відкриті.")
        if window_truncated:
            _log("⚠️ сторінка чеків заповнена — закриття кандидатів у реєстрі пропущено цим "
                 "прогоном (щоб не закрити хибно того, хто просто випав за межу вибірки).")
        kandydaty_registry.write_open_report()

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
                 f"{c['created_at']} (точний збіг: {c.get('book_exact_matches', '?')}, "
                 f"лише сума: {c.get('book_sum_only_matches', '?')})")
        return

    path = _write_report(candidates)
    _save_cursor(new_serial)
    _log(f"ГОТОВО: {len(candidates)} нових чеків → {path}")


if __name__ == "__main__":
    main()
