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
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
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


MAX_LOOKBACK_WINDOWS = 20  # ~4.8 років при 89-денних вікнах — з великим запасом на історію каси
MAX_429_RETRIES = 5        # той самий патерн, що prom_catalog_sync.py::_get_with_retry
RETRY_BACKOFF_BASE_SECONDS = 2.0


def _get_receipts_page(headers: dict, params: dict) -> dict:
    """GET /receipts/search з retry+backoff на 429 — ЖИВО зловлено (2026-09-22, тестування
    цього самого фікса): повне сканування MAX_LOOKBACK_WINDOWS вікон дає значно більше
    запитів поспіль, ніж стара версія (1 запит), і продакшн-прогін раз на добу міг би
    так само вперся у rate-limit без цього. Той самий патерн, що prom_catalog_sync.py."""
    for attempt in range(MAX_429_RETRIES + 1):
        try:
            resp = requests.get(f"{cb.CHECKBOX_API_URL}/receipts/search", headers=headers,
                                 params=params, timeout=cb.REQUEST_TIMEOUT)
        except requests.exceptions.RequestException as e:
            raise cb.CheckboxAPIError(f"помилка з'єднання (GET /receipts/search): {e}") from e
        if resp.status_code != 429 or attempt == MAX_429_RETRIES:
            resp.raise_for_status()
            try:
                return resp.json() or {}
            except ValueError:
                raise cb.CheckboxAPIError(f"невалідна відповідь (не JSON) /receipts/search: {resp.text[:300]}")
        retry_after = resp.headers.get("Retry-After")
        wait = float(retry_after) if retry_after and retry_after.isdigit() else RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
        _log(f"⚠️ 429 Too Many Requests — чекаю {wait:.0f}с (спроба {attempt + 1}/{MAX_429_RETRIES})...")
        time.sleep(wait)
    raise AssertionError("unreachable")  # цикл завжди повертає чи кидає на attempt == MAX_429_RETRIES


def _fetch_window(headers: dict, from_dt: datetime, to_dt: datetime | None) -> list:
    """Одне 90-денне вікно, повна пагінація по offset до вичерпання СЕРЕДИНИ вікна."""
    page_results = []
    offset = 0
    while True:
        params = {"limit": FETCH_LIMIT, "desc": "true", "from_date": from_dt.isoformat(), "offset": offset}
        if to_dt is not None:
            params["to_date"] = to_dt.isoformat()
        data = _get_receipts_page(headers, params)
        page = data.get("results") or []
        page_results.extend(page)
        if len(page) < FETCH_LIMIT:
            break
        offset += FETCH_LIMIT
    return page_results


def fetch_receipts() -> tuple:
    """GET /receipts/search (desc) → (список валідних фіскальних чеків (DONE, не тестові), truncated).
    READ-ONLY: лише авторизація касира + GET. Зміну не відкриваємо, чеків не створюємо.

    ВИПРАВЛЕНО (аудит КОДВ-автоматики, 2026-09-22, знахідка (1) — черга 1, втрата даних
    щодня): раніше запит не передавав `from_date` взагалі, і `truncated = len(results) >=
    FETCH_LIMIT` (82 ≥ 100 = False) НІКОЛИ не спрацьовував, попри те, що ендпоінт БЕЗ
    from_date мовчки обрізає найстаріші чеки — живо звірено: без from_date вибірка дає
    серіали 8…89, а з `from_date=2026-07-01` та сама вибірка дає серіали 1…12. Чек серіал 1
    (39,00 ₴, 08.07.2026) жодна звірка не бачила за весь час існування книги через це.

    ⚠️ Пропозиція аудиту "from_date = початок року" НЕ спрацювала на живому виклику
    (2026-09-22, я сама перевірила, не повірила на слово — `check-docs-recall-before-
    building`/`kodv-verify-audit-suggestions-before-applying`): API повертає
    `400 date.wrong_interval — "Період пошуку чеків не може перевищувати 90 днів"`.
    Це реальне, задокументоване в OpenAPI-спеці (api.checkbox.in.ua/api/openapi.json,
    /receipts/search, from_date/to_date) обмеження, якого сам аудит не перевіряв.

    Реальний фікс — ЛАНЦЮЖОК 89-денних вікон НАЗАД у часі (89, не 90 — запас на дрейф
    часових поясів/секунд), кожне з повною пагінацією по offset усередині.

    ВИПРАВЛЕНО (аудит PR #584, живий вердикт НЕ ЧИСТО): раніше зупинявся на ПЕРШОМУ
    порожньому вікні — хибне припущення для низькооборотної каси: одне порожнє вікно
    (сезонне затишшя) ≠ початок історії, а СТАРІШІ вікна за ним могли мати реальні
    чеки. Це відтворило б РІВНО той клас бага, заради якого писався весь PR — і гірше:
    `truncated` НЕ став би True в цьому сценарії, тож `resolve=not window_truncated`
    (main(), нижче) мовчки позначив би старий "open"-кандидат "resolved", хоча книга
    його так і не отримала (той самий клас, що вже одного разу зламав звірку —
    `kandydaty_registry.py` докстрінг). Тепер СКАНУЄМО УСІ MAX_LOOKBACK_WINDOWS
    (~4.8 років) незалежно від порожніх вікон по дорозі — порожнє вікно нічого не
    зупиняє, просто не додає записів. Дорожче (до 20 запитів замість 1-2), але
    прогін раз на добу (`PlutusToys-ChecboxRegistrySync`, 08:25) — прийнятна ціна за
    відсутність "мовчки закрив старого кандидата"."""
    token = cb._authenticate_cashier()
    headers = {"X-License-Key": cb.CHECKBOX_API_KEY, "Authorization": f"Bearer {token}"}

    all_results = []
    to_dt = datetime.now(ZoneInfo("Europe/Kyiv"))
    for _ in range(MAX_LOOKBACK_WINDOWS):
        from_dt = to_dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=89)
        window = _fetch_window(headers, from_dt, to_dt)
        all_results.extend(window)
        to_dt = from_dt

    # `truncated` тепер означає буквально "дійшли до MAX_LOOKBACK_WINDOWS, не знаємо, чи
    # там справді кінець історії" — консервативний сигнал (завжди True технічно, бо цикл
    # завжди виконує повні MAX_LOOKBACK_WINDOWS ітерацій зараз). Лишаю прапорець на
    # майбутнє (якщо колись повернуть ранню зупинку з надійнішим сигналом кінця історії),
    # а зараз main()/collect() отримують False, бо повне сканування вже й так гарантує
    # повноту в межах ~4.8 років — жодного "open"-кандидата це не закриє хибно.
    truncated = False

    receipts = []
    for it in all_results:
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


_BOOK_CHECKBOX_SERIAL_RE = re.compile(r"[Cc]heckbox\s*(?:сер[іi]ал|№)\s*(\d+)", re.IGNORECASE)


def _book_date_sum_index() -> dict:
    """READ-ONLY: {сума_доходу(грн, 2 знаки) → [(дата рядка, серіал_чека_або_None), ...]} з графи
    1(дата)/графа 2(сума)/графа 5(опис, звідки серіал чека Checkbox, якщо згаданий). Книгу НЕ пише.

    АУДИТ Д2 (незалежний аудитор, КОДВ_журнал «ДОПОВНЕННЯ 5», 2026-09-18): раніше індекс брав
    ЛИШЕ суму — рядок з правильною сумою й ХИБНОЮ датою (напр. 194,00 ₴ від 10.09, а в книзі
    стояло 30.08 — 11 днів різниці, через межу місяця) читався як «збігів: 1» → «уже внесено».
    Автоматика не просто пропустила помилку — вона ВИДАЛА підтвердження хибному рядку. Дата
    тепер обов'язкова частина звірки (див. _match_book нижче): «сума збігається, дата ні» —
    окремий, видимий сигнал, не тихе «ОК».

    БАГ (4) черги 2 (аудит КОДВ-автоматики, 2026-09-22): суми+дата БЕЗ прив'язки до серіала — чек
    88 (78,00₴, 20.09, КАРТКА) отримав «Точний збіг 1» на рядок 113, який ЗА ТЕКСТОМ графи 5 сам
    прив'язаний до серіала 84 (19.09, ГОТІВКА, інший покупець), а не до 88. Живо перевірено
    (2026-09-23): книга ЯВНО пише серіал у графі 5 («чек Checkbox серіал 84 від 19.09.2026» /
    «чек Checkbox №35 від…») для 29 з 34 рядків, що згадують Checkbox — тож серіал є чим звіряти
    напряму, не лише сумою±датою. Рядки без явного серіала (лише дата) лишаються на старій сумі+
    дата-логіці — там нема з чим звіряти жорсткіше, і саме там ризику подвійного заявлення нема
    (немає ЧУЖОГО серіала, що міг би хибно застовпити рядок)."""
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
                e = row[4].value if len(row) > 4 else None
                serial_m = _BOOK_CHECKBOX_SERIAL_RE.search(e) if isinstance(e, str) else None
                row_serial = int(serial_m.group(1)) if serial_m else None
                key = round(float(b), 2)
                index.setdefault(key, []).append((_coerce_date(a), row_serial))
        finally:
            wb.close()  # read_only-книга тримає файловий дескриптор відкритим, поки не закрити явно
    except Exception as e:  # noqa: BLE001 — хінт не критичний
        print(f"[CheckboxSync] книжковий хінт не побудовано (не критично): {e}", file=sys.stderr)
    return index


_KYIV_TZ = ZoneInfo("Europe/Kyiv")


def _match_book(by_sum: dict, sum_uah: float, created_at_utc: str, serial: int | None = None) -> dict:
    """Порівнює чек з книгою за (сума, дата ±1 день, Київ), З пріоритетом серіала, коли рядок
    книги його явно називає. Повертає {"exact": N, "sum_only": N, "kyiv_date": "YYYY-MM-DD"|None}.

    Чому ±1 день, не точний збіг: касовий чек і рядок книги можуть різнитись на добу через
    момент фіксації (вечірній чек проти ранкового запису) — це нормальна похибка, не помилка.
    Різниця, БІЛЬША за 1 день (як інцидент 11 днів через межу місяця) — уже НЕ похибка.

    Чому через zoneinfo, не фіксований timedelta(hours=3): Checkbox `created_at` — UTC (перевірено
    живо), а Київ EEST=UTC+3 лише з 29.03 по 25.10; решту року EET=UTC+2 (аудит 2026-09-18 —
    попередня версія мала захардкоджений +3, що стало б систематичною похибкою на годину для
    кожного вечірнього чека з 26.10). `ZoneInfo` рахує правильний зсув на кожну конкретну дату.

    БАГ (4) черги 2, фікс (2026-09-23): рядок книги, що ЯВНО називає ІНШИЙ серіал у графі 5
    (`row_serial is not None and row_serial != serial`), більше НЕ рахується ні exact, ні
    sum_only для цього чека — він уже застовпив чужий рядок, показувати його як «можливо цей»
    активно вводить в оману (саме так серіал 88 «збігся» з рядком серіала 84). Рядок БЕЗ явного
    серіала (`row_serial is None`) лишається на старій сумі+дата-логіці — звіряти з чим саме
    нема."""
    rows = by_sum.get(sum_uah, [])
    try:
        kyiv_date = (datetime.fromisoformat(created_at_utc)
                     .replace(tzinfo=timezone.utc).astimezone(_KYIV_TZ)).date()
    except ValueError:
        kyiv_date = None
    exact = 0
    sum_only = 0
    for d, row_serial in rows:
        if row_serial is not None and row_serial != serial:
            continue  # застовплено чужим серіалом — не кандидат для ЦЬОГО чека
        if kyiv_date is None:
            sum_only += 1
            continue
        if d is not None and abs((d - kyiv_date).days) <= 1:
            exact += 1
        else:
            sum_only += 1
    return {"exact": exact, "sum_only": sum_only,
            "kyiv_date": kyiv_date.isoformat() if kyiv_date else None}


def _pair_match_unmatched(receipts: list, book_idx: dict) -> None:
    """Черга 2, баг (5) (аудит КОДВ-автоматики 2026-09-22): книга об'єднує КІЛЬКА чеків
    Checkbox одного дня/типу оплати в ОДИН рядок (§2 брифу) — індивідуальна звірка (сума+дата
    ОДНОГО чека) такий рядок ніколи не знайде, бо сума рядка = сума ДВОХ чеків разом. 17 чеків
    на 3 759,50 ₴ хибно йшли в `_vidkryti_kandydaty` як «не в книзі», хоча БУЛИ там — просто
    парами.

    Живо звірено (2026-09-23, реальний виклик Checkbox API, не гіпотеза): усі 8 пар з аудиту
    підтверджені день-в-день — 11(630.00)+12(204.28)=834.28→р.23; 28(140.17)+29(538.00)=678.17
    →р.50; 33(383.10)+37(142.30)=525.40→р.56; 34(114.17)+35(174.67)=288.84→р.55;
    42(316.00)+43(121.00)=437.00→р.67; 44(190.00)+45(135.00)=325.00→р.68;
    46(132.24)+47(101.60)=233.84→р.63; 53(190.08)+54(125.89)=315.97→р.66. Усі 8 пар — РІВНО
    2 чеки, той самий календарний день (Київ) і той самий тип оплати (CASH/CASHLESS) —
    відображає, як бухгалтер РЕАЛЬНО об'єднує рядок (§2 брифу: один прихід за день+тип оплати).

    Тому МІНІМАЛЬНИЙ, звужений фікс — не повний subset-sum (комбінаторний ризик хибних
    збігів на більших групах), а РІВНО пари в межах (kyiv_date, pay_type): для кожного
    ІНДИВІДУАЛЬНО незматченого чека (`book_exact_matches==0`) шукаємо ОДИН такий самий
    незматчений чек з тієї самої групи, чия сума В ПАРІ дає суму рядка книги
    (дата ±1 день, як і одиночна звірка; рядок із ЯВНО іншим checkbox-серіалом у графі 5 —
    виключений, той самий принцип, що бага (4)). Жадібний, відсортований за серіалом прохід:
    перша валідна пара забирає рядок книги (локальний `consumed`, щоб та сама сума+дата не
    використалась двічі різними парами за один прогін) — детерміновано, відтворювано.

    НЕ мутує книгу і НЕ закриває кандидата остаточно сам — лише виставляє
    `book_exact_matches=1` (через parity з `_match_book`, той самий поріг `==0`, що main()
    вже читає для `_vidkryti_kandydaty`) і `paired_with_serial` для прозорості в звіті. Пара,
    для якої НЕ знайдено рядка — лишається окремо в `_vidkryti_kandydaty`, як і раніше
    (безпечний дефолт: не знайшли — не ховаємо).

    ЗАЛИШКОВИЙ РИЗИК (незалежний аудит PR #588, п.5, не блокер): `consumed` тут захищає
    ЛИШЕ пари-проти-пар усередині цієї функції — рядок книги, який УЖЕ пояснює один
    ІНДИВІДУАЛЬНИЙ чек через `_match_book()` (звичайна звірка, не парна), НЕ виключений
    з пошуку тут. Теоретично: рядок 500₴/05.09 legit пояснює чек A (500₴, індивідуальний
    exact-збіг), а чеки B(300)+C(200) того самого дня/типу оплати випадково СУМУЮТЬ у ту
    саму суму — пара B+C хибно "закриється" тим самим рядком, що вже belongs A. Для
    низькооборотної каси (~90 чеків/2 міс, копійкові суми) ймовірність точного числового
    збігу низька, і всі 8 живих пар з аудиту незалежно звірені проти реальної книги — але
    якщо колись побачите підозріло "закриту" пару поруч із індивідуально-заматченим чеком
    ТІЄЇ САМОЇ суми — це той самий клас, звіряти вручну."""
    # ЖИВИЙ ФІКС (2026-09-23, реальний тест на живих даних): спершу вимагав ще й
    # `book_sum_only_matches==0` для допуску в пару — пара 42+43 (437,00₴→р.67) через це НЕ
    # зматчилась: чек 43 (121,00₴) МАВ sum_only=1 через ЗБІГ суми з НІЯК НЕ пов'язаним рядком 20
    # (121,00₴, 30.07, зовсім інша дата) — випадковий збіг суми деінде НЕ означає, що чек 43
    # уже "пояснений". `book_exact_matches==0` — єдина правильна умова: чек досі "не в книзі",
    # незалежно від того, скільки випадкових sum_only-збігів він зібрав по дорозі.
    consumed: dict = {}  # (сума, дата_рядка) → True, щоб той самий рядок не забрала друга пара
    unmatched = [r for r in receipts
                 if r.get("book_exact_matches", 0) == 0 and r.get("kyiv_date")]
    unmatched.sort(key=lambda r: r["serial"])
    paired_serials: set = set()
    for i, r1 in enumerate(unmatched):
        if r1["serial"] in paired_serials:
            continue
        for r2 in unmatched[i + 1:]:
            if r2["serial"] in paired_serials:
                continue
            if r2["kyiv_date"] != r1["kyiv_date"] or r2["pay_type"] != r1["pay_type"]:
                continue
            pair_sum = round(r1["sum_uah"] + r2["sum_uah"], 2)
            rows = book_idx.get(pair_sum, [])
            r1_date = date.fromisoformat(r1["kyiv_date"])
            for row_date, row_serial in rows:
                if row_serial is not None:
                    continue  # рядок явно прив'язаний до ОДНОГО checkbox-серіала — не парний випадок
                if (pair_sum, row_date) in consumed:
                    continue
                if row_date is not None and abs((row_date - r1_date).days) <= 1:
                    consumed[(pair_sum, row_date)] = True
                    paired_serials.add(r1["serial"])
                    paired_serials.add(r2["serial"])
                    for rc, partner in ((r1, r2), (r2, r1)):
                        rc["book_exact_matches"] = 1
                        rc["paired_with_serial"] = partner["serial"]
                    _log(f"⚠️ парний збіг: чеки {r1['serial']}+{r2['serial']} "
                         f"({r1['sum_uah']}+{r2['sum_uah']}={pair_sum}) = рядок книги "
                         f"{row_date.isoformat()} — обидва виключено з «не в книзі»")
                    break
            if r1["serial"] in paired_serials:
                break


def collect() -> tuple:
    """Повертає (new_for_report, max_serial, is_baseline, all_matched, window_truncated).
    `new_for_report` — лише СЕРІАЛ-нові (стара поведінка, для щоденного .md/.json звіту).
    `all_matched` — УСІ отримані чеки (останні FETCH_LIMIT, незалежно від курсора джерела) зі
    звіркою з книгою — для kandydaty_registry (аудит Д1: реєстр НЕ довіряє курсору джерела,
    бо саме курсор губив кандидатів, яких не встигли внести). На базовій лінії — порожньо
    (перший запуск свідомо НЕ трактує всю історію як «нове», той самий принцип поширюється на
    реєстр — не заводимо сотні історичних чеків як «щойно відкриті кандидати»).
    `window_truncated` — ОНОВЛЕНО (PR #584, повна пагінація ланцюжком 89-денних вікон
    замінила одну сторінку без дати): зараз завжди False, бо fetch_receipts() сканує
    ВЕСЬ MAX_LOOKBACK_WINDOWS діапазон (~4.8 років) кожного прогону, без ранньої
    зупинки — повнота гарантована структурно, не флагом. Прапорець і механізм
    `resolve=not window_truncated` нижче лишені на місці (аудит 2026-09-18, Д1/Д4-
    рецидив: інакше кандидат, якого джерело не показало, хибно позначився б
    "resolved") — на випадок, якщо колись повернуть часткове сканування."""
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
        m = _match_book(book_idx, r["sum_uah"], r["created_at"], serial=r["serial"])
        r["book_exact_matches"] = m["exact"]
        r["book_sum_only_matches"] = m["sum_only"]
        r["book_same_sum_rows"] = m["exact"] + m["sum_only"]  # зворотна сумісність зі старим полем
        r["kyiv_date"] = m["kyiv_date"]
    _pair_match_unmatched(receipts, book_idx)  # баг (5) черги 2 — об'єднані рядки книги

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
