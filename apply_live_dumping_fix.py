"""apply_live_dumping_fix.py — ручний, разовий контролер: живе прибирання
демпінгу з вітрини Prom, ЧЕРЕЗ apply_price(), не чекаючи звичайного
репрайсера.

НАВІЩО (пряме прохання власниці, 2026-07-26, її словами — див.
spec_2026-07-26_manual_live_price_controller.md, Security audit:
rozetka_agent): "Треба з вітрини прибрати демпінгові ціни, треба
виставити наші 6000 товарів на рівень конкурент - 3 грн, не чекаючи
репрайсера, ця операція контролера буде використовуватися вручну." І
пізніше, прямим текстом: "все що на вітрині і в фіді повинні мати
адекватні та конкурентні ціни — це головне в торгівлі, і мені все одно
як ви це зробите."

ІСТОРІЯ: перша версія (apply_cached_scan_prices.py, PR #171) писала
результат ЛИШЕ у price_state — ніколи не потрапляло на вітрину (Prom
імпортує лише prom_feed_top.xml, для SKU поза топ-N override не
читається). Друга версія почала РЕАЛЬНО викликати apply_price(), але
без синхронізації з feed-data (виправлено, PR #172) і без фільтра
"чи SKU взагалі колись створювався в Prom" для самостійного локального
запуску (виправлено, PR #173 — root-cause масових "Продукт не найден").
Ця версія додає ТРЕТІЙ бакет кандидатів, якого бракувало: SKU з
історичним price_state, але без даних нічного скану (живий приклад,
що виявив прогалину: SKU 292858 — сам "герой" початкового розслідування
демпінгу, випав з топ-N, не покритий нічним сканом, і 3 доби лишався з
ціною, порахованою ДО фіксу формули PR #168, бо жодне з двох перших
джерел цього скрипта його не бачило).

ОХОПЛЕННЯ — ТРИ джерела, усі реально ЖИВІ на Prom зараз:
1. top_catalog (select_top_items(), поточний топ-N) — "наші 6000
   товарів" власниці, з кешованим конкурентом нічного скану.
2. SKU ПОЗА топ-N, але з даними нічного скану
   (_rotated_out_scan_candidates()) — теж кешований конкурент.
3. SKU ПОЗА топ-N, БЕЗ даних нічного скану, але з історичним
   price_state-записом (_rotated_out_needing_live_lookup()) — тут
   кешованих даних конкурента взагалі немає, тож ЦЕЙ бакет проходить
   ПОВНИЙ живий пайплайн (find_best_competitor() + decide_action(),
   включно з можливим delist із presence-перевіркою) — той самий
   пайплайн, що й топ-970 у звичайному репрайсері. Повільніше (живий
   пошук на кожен SKU), але це єдиний спосіб дати цим SKU ХОЧ ЯКУСЬ
   конкурентну ціну — без цього бакету вони взагалі ніколи не
   потрапляють у жоден з трьох сценаріїв прогону.

Джерела 1-2 — decide_price_for_platform() БЕЗ ЗМІН (канонічна формула
власниці, PR #168), НІКОЛИ delist (кешовані дані нічного скану
недостатньо надійні для живого delist без verify_competitor_really_
available()). Джерело 3 МОЖЕ робити delist — так само, як і звичайний
репрайсер для цього самого бакету — але ЛИШЕ після живої presence-
перевірки конкурента (buyBox виняток не потрібен, той самий гейт, що
вже є в prom_competitor_pricer.py::main()).

СИНХРОНІЗАЦІЯ З feed-data (PR #172, пряме зауваження власниці —
"звичайно він повинен бути синхронізован, інакше нащо все це"):
apply_price()/delist() міняють ЖИВИЙ стан у Prom НЕГАЙНО (прямі API-
виклики), але цей скрипт запускається ЛОКАЛЬНО. Без синхронізації
generate_prom_feed_top.py (CI, читає price_state/_delisted_since з
гілки feed-data) продовжував би генерувати фід зі СТАРОГО стану,
ризикуючи відкотити щойно застосовані зміни через наступний Prom-
авто-реімпорт. _sync_price_state_to_feed_data() публікує НАШІ зміни
(лише ті pid, яких цей прогін реально торкнувся — і ціни, і delist-
позначки) напряму в feed-data через тимчасовий git worktree, злиті
ПОВЕРХ найсвіжішого стану звідти, звичайний (не force) push із
ретраями. Викликається періодично (SAVE_EVERY) і в кінці прогону.

Так само на СТАРТІ прогону price_state читається НЕ з локального
диска (може бути тижнями застарілим — саме так топ-N ранжування
пропускало вже позначені _delisted_since ghost-SKU), а фетчиться
свіжим з feed-data, з фолбеком на локальний файл лише якщо мережа
недоступна.

БЕЗПЕКА для потенційно тисяч живих викликів за один прогін:
- Часовий бюджет (MAX_RUNTIME_SECONDS, той самий патерн, що й PR #169).
- Резюмований прогрес (PROGRESS_FILE) — SKU, вже оброблені в ЦЬОМУ
  sweep'і (незалежно від успіху/помилки), окремо від price_state —
  повторний ручний запуск ПРОДОВЖУЄ, не починає заново.
- Троттлінг: APPLY_THROTTLE_SECONDS між live apply_price()/delist()-
  викликами; SEARCH_DELAY (той самий, що й у prom_competitor_pricer.py)
  між живими пошуковими запитами для джерела 3.
- SKU на непідтвердженій комісії (PROM_COMMISSION_DEFAULT) виключені з
  живого apply_price()/delist() — ціна все одно йде у price_state для
  фіда, потребує ручного перегляду.

Запуск:
    python apply_live_dumping_fix.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

from parser import fetch_toysi_catalog, assert_catalog_size_sane, CatalogSizeError
from competitor_pricing import load_prom_price_state, save_prom_price_state, real_toysi_cost
from generate_prom_feed import fetch_russian_text
from generate_prom_feed_top import select_top_items, load_scan_state
from prom_competitor_pricer import (
    SEARCH_DELAY,
    LIVE_LOOKUP_EXTRA_BATCH_LIMIT,
    MAX_DELIST_PER_RUN,
    CIRCUIT_BREAKER_MAX_DELIST_FRACTION,
    find_best_competitor,
    decide_action,
    verify_competitor_really_available,
    _rotated_out_scan_candidates,
    _rotated_out_needing_live_lookup,
    _decide_from_scan_entry,
    _category_commission_is_default,
    _load_prom_category_cache,
    _load_own_product_links_cache,
)
from prom_api_client import PromEditError, apply_price, delist
from telegram_notify import send_telegram_message

SAVE_EVERY = 200
APPLY_THROTTLE_SECONDS = 0.2  # консервативна пауза між живими apply_price()/delist()-викликами, немає задокументованого офіційного ліміту Prom API
MAX_RUNTIME_SECONDS = 5 * 3600

PROGRESS_FILE = Path(__file__).parent / "live_dumping_fix_progress.json"

FEED_BRANCH = "feed-data"
PRICE_STATE_FILENAME = "prom_competitor_price_state.json"
FEED_DATA_SYNC_RETRIES = 3

_RUN_DEADLINE: float | None = None


def _time_budget_exceeded() -> bool:
    return _RUN_DEADLINE is not None and time.monotonic() >= _RUN_DEADLINE


def _load_progress() -> dict:
    if not PROGRESS_FILE.exists():
        return {}
    try:
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_progress(progress: dict) -> None:
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")


def _fetch_fresh_price_state() -> dict:
    """Фетчить price_state НАПРЯМУ з feed-data (git show, без worktree —
    лише читання) — той самий стан, який бачить CI, а НЕ можливо
    тижнями застарілий локальний файл на цій машині. Локальний
    load_prom_price_state() — лише фолбек, якщо мережа/git недоступні."""
    try:
        subprocess.run(
            ["git", "fetch", "origin", FEED_BRANCH],
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        raw = subprocess.run(
            ["git", "show", f"origin/{FEED_BRANCH}:{PRICE_STATE_FILENAME}"],
            check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout
        print(f"[LiveDumpingFix] price_state фетчено свіжим з {FEED_BRANCH}.")
        return json.loads(raw)
    except (subprocess.CalledProcessError, ValueError) as e:
        print(f"[LiveDumpingFix] Не вдалось фетчити price_state з {FEED_BRANCH} ({e}) — "
              "падаю на локальний файл (може бути застарілим).", file=sys.stderr)
        return load_prom_price_state()


def _sync_price_state_to_feed_data(pending: dict, pending_delisted: dict, pending_undelisted: set) -> bool:
    """Публікує ЛИШЕ pending (ціни, {pid: entry}), pending_delisted
    ({pid: timestamp}, нові позначки) і pending_undelisted (set pid,
    зняті позначки — SKU знову конкурентний) у гілку feed-data — злиті
    поверх НАЙСВІЖІШОГО стану звідти, через одноразовий git worktree,
    звичайний (не force) push. Повертає True при успіху (усі три
    структури можна очистити), False — щоб викликач лишив їх
    накопиченими і спробував ще раз пізніше."""
    if not pending and not pending_delisted and not pending_undelisted:
        return True
    for attempt in range(1, FEED_DATA_SYNC_RETRIES + 1):
        tmp_dir = tempfile.mkdtemp(prefix="feed_data_sync_")
        try:
            subprocess.run(
                ["git", "fetch", "origin", FEED_BRANCH],
                check=True, capture_output=True, text=True, encoding="utf-8",
            )
            subprocess.run(
                ["git", "worktree", "add", "--detach", "--force", tmp_dir, f"origin/{FEED_BRANCH}"],
                check=True, capture_output=True, text=True, encoding="utf-8",
            )
            state_file = Path(tmp_dir) / PRICE_STATE_FILENAME
            remote_state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
            remote_state.update(pending)
            if pending_delisted or pending_undelisted:
                remote_delisted = remote_state.setdefault("_delisted_since", {})
                remote_delisted.update(pending_delisted)
                for pid in pending_undelisted:
                    remote_delisted.pop(pid, None)
            # Публікуємо РЕДАГОВАНУ копію (без cost/margin_pct/_meta.last_avg_margin_pct) — feed-data
            # публічна (витік 2026-08-10). remote_state лишається повним для локального вжитку;
            # redact_price_state не мутує вхід. Читачі беруть лише _delisted_since+price/competitor_price.
            from price_state_redact import redact_price_state
            state_file.write_text(json.dumps(redact_price_state(remote_state), ensure_ascii=False, indent=1), encoding="utf-8")

            subprocess.run(["git", "add", PRICE_STATE_FILENAME], cwd=tmp_dir, check=True,
                            capture_output=True, text=True, encoding="utf-8")
            diff_check = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=tmp_dir)
            if diff_check.returncode == 0:
                return True  # remote вже містить ідентичні значення (напр. попередня спроба вже пройшла)
            subprocess.run(
                ["git", "commit", "-q", "-m",
                 f"apply_live_dumping_fix.py: sync {len(pending)} price + {len(pending_delisted)} delisted "
                 f"+ {len(pending_undelisted)} undelisted"],
                cwd=tmp_dir, check=True, capture_output=True, text=True, encoding="utf-8",
            )
            subprocess.run(
                ["git", "push", "origin", f"HEAD:{FEED_BRANCH}"],
                cwd=tmp_dir, check=True, capture_output=True, text=True, encoding="utf-8",
            )
            print(f"[LiveDumpingFix] Синхронізовано з {FEED_BRANCH}: {len(pending)} цін, "
                  f"{len(pending_delisted)} delisted, {len(pending_undelisted)} undelisted.")
            return True
        except subprocess.CalledProcessError as e:
            print(f"[LiveDumpingFix] Спроба синхронізації {attempt}/{FEED_DATA_SYNC_RETRIES} з {FEED_BRANCH} "
                  f"не вдалась (ймовірно, паралельний запис — CI чи інший прогін): {e.stderr}", file=sys.stderr)
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", tmp_dir], capture_output=True, text=True)
            shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"[LiveDumpingFix] УВАГА: синхронізація з {FEED_BRANCH} не вдалась після "
          f"{FEED_DATA_SYNC_RETRIES} спроб — {len(pending)} цін/{len(pending_delisted)} delisted/"
          f"{len(pending_undelisted)} undelisted залишаються лише локально, спробую знову на "
          "наступному циклі збереження.", file=sys.stderr)
    return False


def main() -> None:
    global _RUN_DEADLINE
    _RUN_DEADLINE = time.monotonic() + MAX_RUNTIME_SECONDS

    price_state = _fetch_fresh_price_state()

    print("[LiveDumpingFix] Завантажую каталог Toysi...")
    toysi_catalog = fetch_toysi_catalog()
    try:
        assert_catalog_size_sane(toysi_catalog)
    except CatalogSizeError as e:
        print(f"[LiveDumpingFix] {e}", file=sys.stderr)
        sys.exit(1)

    top_catalog = select_top_items(toysi_catalog)
    scan_state = load_scan_state()
    prom_category_cache = _load_prom_category_cache()
    own_product_links = _load_own_product_links_cache()
    print("[LiveDumpingFix] Завантажую російськомовні назви (для живого пошуку джерела 3)...")
    russian_text = fetch_russian_text()

    # prom_products_raw_cache.json — короткоживучий (TTL 1г) кеш, який у
    # звичайному CI будує ОКРЕМИЙ, РАНІШИЙ крок ТОГО Ж прогону
    # (generate_google_feed.py). Цей скрипт — самостійний, разовий
    # локальний запуск БЕЗ такого попереднього кроку, тож кеш ЗАВЖДИ
    # відсутній/застарілий тут — тихий фолбек "не фільтруємо" означав би:
    # жодного фільтра "чи це взагалі колись створювалось у Prom" для
    # rotated_out-бюкетів, апробуючи apply_price() на потенційно тисячах
    # SKU, яких у Prom ніколи не існувало (живо підтверджено 2026-07-26).
    # Якщо кешу немає — робимо ЖИВИЙ повний фетч (fetch_prom_products())
    # замість мовчазного вимкнення фільтра.
    live_prom_ids = None
    try:
        from generate_google_feed import load_prom_products_cache
        _live = load_prom_products_cache()
        if _live is not None:
            live_prom_ids = set(_live.keys())
    except Exception:
        live_prom_ids = None
    if live_prom_ids is None:
        print("[LiveDumpingFix] Кеш живих товарів Prom відсутній/застарів — роблю живий фетч "
              "(може зайняти кілька хвилин, це самостійний запуск без окремого попереднього кроку)...")
        from prom_catalog_sync import fetch_prom_products
        live_prom_ids = set(fetch_prom_products().keys())
        print(f"[LiveDumpingFix] Живих товарів у Prom: {len(live_prom_ids)}.")

    # Джерело 1: топ-N ("наші 6000 товарів") — лише ті, для яких уже є
    # дані конкурента в кеші нічного скану.
    top_with_scan_data = {pid: item for pid, item in top_catalog.items() if pid in scan_state}

    # Джерело 2: живі на Prom, поза топ-N, з даними скану.
    rotated_out = _rotated_out_scan_candidates(top_catalog, toysi_catalog, scan_state, live_prom_ids)

    # Джерело 3: живі на Prom, поза топ-N, БЕЗ даних скану, але з
    # історичним price_state-записом — потребує повного живого пайплайна
    # (find_best_competitor + decide_action, включно з можливим delist).
    # ОБМЕЖЕНО LIVE_LOOKUP_EXTRA_BATCH_LIMIT (знахідка аудиту,
    # code_report_2026-07-26_pt10.md) — той самий ліміт, що вже є в
    # звичайному репрайсері для цього самого класу кандидатів; без нього
    # немає жодної стелі на розмір бакету, який (на відміну від джерел
    # 1-2) МОЖЕ робити живий delist. Резюмованість прогресу означає, що
    # решта цього бакету дочекається наступного запуску скрипта.
    live_lookup_extra_all = _rotated_out_needing_live_lookup(top_catalog, toysi_catalog, scan_state, price_state, live_prom_ids)
    live_lookup_extra = dict(list(live_lookup_extra_all.items())[:LIVE_LOOKUP_EXTRA_BATCH_LIMIT])
    live_lookup_pids = set(live_lookup_extra.keys())

    candidates = dict(top_with_scan_data)
    candidates.update(rotated_out)  # немає перетину: rotated_out навмисно виключає top_catalog
    candidates.update(live_lookup_extra)  # теж без перетину (виключає top_catalog І scan_state)
    print(f"[LiveDumpingFix] Кандидатів усього: {len(candidates)} "
          f"(топ-N з даними скану: {len(top_with_scan_data)}, поза топ-N живі (скан): {len(rotated_out)}, "
          f"поза топ-N живий пошук: {len(live_lookup_extra)} з {len(live_lookup_extra_all)} доступних "
          f"— обмежено LIVE_LOOKUP_EXTRA_BATCH_LIMIT={LIVE_LOOKUP_EXTRA_BATCH_LIMIT}).")

    progress = _load_progress()
    done_pids = set(progress.get("done_pids") or [])
    remaining = {pid: item for pid, item in candidates.items() if pid not in done_pids}
    if not remaining:
        print("[LiveDumpingFix] Попередній sweep повністю покрив цю множину кандидатів — починаю новий.")
        progress = {"started_at": datetime.now().isoformat(), "done_pids": []}
        done_pids = set()
        remaining = dict(candidates)
    else:
        print(f"[LiveDumpingFix] Продовжую попередній sweep (початок {progress.get('started_at')}): "
              f"{len(done_pids)} вже оброблено, {len(remaining)} залишилось.")

    applied_count = 0
    default_commission_count = 0
    error_count = 0
    confirmed_delist_count = 0
    price_protected_for_delist_count = 0  # ціна піднята до безпечної межі для SKU, що ЧЕКАЮТЬ на delist (не входить у applied_count)
    category_counts = {"undercut": 0, "floor": 0, "no_competitor": 0}
    time_budget_hit = False
    pending_sync: dict = {}  # {pid: entry} з ЦЬОГО прогону, ще не опубліковані в feed-data
    pending_delisted: dict = {}  # {pid: timestamp} з ЦЬОГО прогону, ще не опубліковані в feed-data
    pending_undelisted: set = set()  # pid, зняті позначки _delisted_since цього прогону (SKU знову конкурентний)
    delisted_since = price_state.setdefault("_delisted_since", {})

    # ДОДАНО (знахідка аудиту, code_report_2026-07-26_pt10.md): звичайний
    # репрайсер для ЦЬОГО САМОГО класу кандидатів (live_lookup_extra) має
    # АГРЕГАТНИЙ захист (MAX_DELIST_PER_RUN + evaluate_circuit_breaker()),
    # перевірений ПЕРЕД тим, як реально почати видаляти — тут його не
    # було зовсім, попри те, що це ПЕРШЕ місце в цьому скрипті, де
    # з'являється живий delist(). Тому delist для джерела 3 не
    # виконується одразу — рішення накопичуються тут, і РЕАЛЬНЕ
    # видалення відбувається лише ПІСЛЯ основного циклу, якщо ні
    # абсолютна кількість, ні частка не перевищують той самий поріг, що
    # й у prom_competitor_pricer.py.
    to_delist_3: list[str] = []
    source3_decided_count = 0

    for pid, item in remaining.items():
        if _time_budget_exceeded():
            print(f"[LiveDumpingFix] Часовий бюджет ({MAX_RUNTIME_SECONDS/3600:.0f}г) вичерпано — "
                  f"зупиняю достроково ({applied_count} застосовано з {len(remaining)} цього прогону).")
            time_budget_hit = True
            break

        try:
            cost = real_toysi_cost(item)  # свіжа собівартість з ЖИВОГО каталогу, не зі збереженої в скані
        except (TypeError, ValueError):
            done_pids.add(pid)
            continue
        if cost <= 0:
            done_pids.add(pid)
            continue

        category_name = item.get("category_name")
        prom_category_id = (prom_category_cache.get(pid) or {}).get("category_id")
        now_iso = datetime.now().isoformat()

        if pid in live_lookup_pids:
            # Джерело 3: жодних кешованих даних конкурента — повний живий
            # пайплайн, той самий, що й основний цикл prom_competitor_pricer.py.
            name_ukr = (item.get("name") or "").strip()
            name_rus = (russian_text.get(pid, {}) or {}).get("name") or name_ukr
            own_link = own_product_links.get(pid)
            competitor = find_best_competitor(name_rus, cost, own_link, item.get("pictures"))
            time.sleep(SEARCH_DELAY)
            decision = decide_action(cost, competitor, category_name, name_ukr, prom_category_id, item.get("pictures"))
            if decision["action"] == "delist" and decision["competitor"].get("source") != "buybox":
                if not verify_competitor_really_available(decision["competitor"]):
                    decision["action"] = "adjust"
                time.sleep(SEARCH_DELAY)
        else:
            # Джерела 1-2: кешований конкурент з нічного скану, ніколи delist.
            scan_entry = scan_state.get(pid, {})
            decision = _decide_from_scan_entry(cost, category_name, prom_category_id, scan_entry, item.get("pictures"))

        if _category_commission_is_default(category_name, prom_category_id):
            # Той самий гейт, що й у звичайному репрайсері: комісія не
            # підтверджена реальною категорією Prom -> НЕ auto-apply,
            # ціна все одно йде у price_state для фіда/ручного перегляду.
            default_commission_count += 1
            entry = {
                "price": decision["price"], "timestamp": now_iso, "competitor_key": None,
                "category": decision["category"], "competitor_price": decision["competitor_price"],
                "cost": cost, "margin_pct": decision["margin_pct"],
            }
            price_state[pid] = entry
            pending_sync[pid] = entry
            done_pids.add(pid)
            continue

        if pid in live_lookup_pids:
            source3_decided_count += 1

        # ДОДАНО (2026-07-27, пряме зауваження власниці — "нехай вона
        # більше показувала би та чекала на видалення, алеж не
        # демпінгувала би і не була би для нас збитковою"): ціна
        # піднімається до безпечної межі ЗАВЖДИ, НЕЗАЛЕЖНО від того, чи
        # рішення "adjust", чи "delist" — decision["price"] в обох
        # випадках уже включає net_revenue>=cost запобіжник. Раніше для
        # "delist" ціна взагалі не чіпалась, тож поки видалення чекало
        # черги/агрегатної перевірки (чи взагалі не траплялось), SKU
        # лишався зі СТАРОЮ, потенційно збитковою ціною — живий приклад,
        # що виявив це: SKU 292858/293138.
        try:
            apply_price(pid, decision["price"])
            entry = {
                "price": decision["price"], "timestamp": now_iso, "competitor_key": None,
                "category": decision["category"], "competitor_price": decision["competitor_price"],
                "cost": cost, "margin_pct": decision["margin_pct"],
            }
            price_state[pid] = entry
            pending_sync[pid] = entry
            if decision["action"] == "delist":
                price_protected_for_delist_count += 1
            else:
                applied_count += 1
                category_counts[decision["category"]] = category_counts.get(decision["category"], 0) + 1
            # ДОДАНО (знахідка аудиту, pt10): той самий універсальний
            # патерн, що й у звичайному репрайсері (рядки 1653/2315
            # поточної версії prom_competitor_pricer.py) — якщо SKU
            # раніше було підтверджено видаленим, а тепер знову
            # конкурентний ("adjust"), знімаємо позначку, інакше
            # select_top_items() назавжди виключав би товар, який
            # РЕАЛЬНО повернувся до конкурентності. Універсально для
            # ВСІХ трьох джерел, не лише третього — _delisted_since
            # не перевіряється при побудові rotated_out (джерело 2),
            # тож застаріла позначка теоретично могла торкнутись і його.
            # ВИПРАВЛЕНО (знахідка аудиту pt2, 2026-07-27): об'єднання
            # adjust-/delist-шляхів у цей спільний try-блок (piagoditi
            # ціну перед delist) лишило .pop() БЕЗ гейту на
            # decision["action"] — знімало позначку "видалено" і для
            # СЬОГОДНІШНЬОГО рішення "delist" теж, хоча SKU не став
            # конкурентним. select_top_items() після цього вважав би такий
            # SKU знову придатним для топ-N навіть після реального
            # видалення з Prom — саме той клас бага, заради якого
            # _delisted_since взагалі існує (PR #95).
            if decision["action"] != "delist" and delisted_since.pop(pid, None) is not None:
                pending_undelisted.add(pid)
            time.sleep(APPLY_THROTTLE_SECONDS)
        except (requests.exceptions.RequestException, PromEditError) as e:
            error_count += 1
            print(f"  - {pid}: помилка зміни ціни — {e}", file=sys.stderr)

        if decision["action"] == "delist":
            # Джерело 3 ЛИШЕ (джерела 1-2 ніколи не приймають це рішення) —
            # ціна вже піднята вище; САМЕ видалення накопичуємо для
            # агрегатної перевірки ПІСЛЯ основного циклу (див. коментар
            # біля to_delist_3 вище).
            to_delist_3.append(pid)

        done_pids.add(pid)  # позначаємо оброблений НЕЗАЛЕЖНО від успіху -- постійна помилка на одному SKU не має блокувати прогрес

        if len(done_pids) % SAVE_EVERY == 0:
            save_prom_price_state(price_state)
            progress["done_pids"] = sorted(done_pids)
            _save_progress(progress)
            if _sync_price_state_to_feed_data(pending_sync, pending_delisted, pending_undelisted):
                pending_sync = {}
                pending_delisted = {}
                pending_undelisted = set()
            print(f"[LiveDumpingFix] {len(done_pids)}/{len(candidates)} оброблено "
                  f"(застосовано {applied_count}, видалено {confirmed_delist_count}, помилок {error_count})...")

    # Агрегатна перевірка ПЕРЕД тим, як реально виконати НАКОПИЧЕНІ
    # delist-рішення джерела 3 (той самий принцип, що й hard_cap_tripped/
    # evaluate_circuit_breaker() у prom_competitor_pricer.py, знахідка
    # аудиту pt10). Якщо аномально багато — НЕ видаляємо жодного цього
    # прогону і повертаємо ці pid у "не оброблені" (наступний запуск
    # переоцінить їх заново, можливо аномалія на той час минула).
    delist_fraction = (len(to_delist_3) / source3_decided_count) if source3_decided_count else 0.0
    hard_cap_tripped = len(to_delist_3) > MAX_DELIST_PER_RUN
    fraction_tripped = delist_fraction > CIRCUIT_BREAKER_MAX_DELIST_FRACTION
    if to_delist_3 and (hard_cap_tripped or fraction_tripped):
        reason = (
            f"{len(to_delist_3)} > MAX_DELIST_PER_RUN ({MAX_DELIST_PER_RUN})" if hard_cap_tripped else
            f"{delist_fraction:.0%} > {CIRCUIT_BREAKER_MAX_DELIST_FRACTION:.0%} від джерела 3 ({source3_decided_count} рішень)"
        )
        print(f"[LiveDumpingFix] 🚨 Circuit breaker: delist для джерела 3 ЗАБЛОКОВАНО ({reason}) — "
              f"жодного з {len(to_delist_3)} не видалено цього прогону.", file=sys.stderr)
        send_telegram_message(
            f"🚨 apply_live_dumping_fix.py: delist ЗАБЛОКОВАНО circuit breaker'ом ({reason}). "
            f"Перевір вручну — {len(to_delist_3)} SKU лишились live_lookup_extra до наступного запуску."
        )
        done_pids -= set(to_delist_3)  # переоцінити заново наступного разу, не "загублено назавжди"
    else:
        for pid in to_delist_3:
            try:
                delist(pid)
                now_iso = datetime.now().isoformat()
                delisted_since[pid] = now_iso
                pending_delisted[pid] = now_iso
                confirmed_delist_count += 1
                time.sleep(APPLY_THROTTLE_SECONDS)
            except (requests.exceptions.RequestException, PromEditError) as e:
                error_count += 1
                print(f"  - {pid}: помилка видалення — {e}", file=sys.stderr)

    progress["done_pids"] = sorted(done_pids)
    _save_progress(progress)
    save_prom_price_state(price_state)
    if _sync_price_state_to_feed_data(pending_sync, pending_delisted, pending_undelisted):
        pending_sync = {}
        pending_delisted = {}
        pending_undelisted = set()

    remaining_after = len(candidates) - len(done_pids)
    unsynced = len(pending_sync) + len(pending_delisted) + len(pending_undelisted)
    print(f"[LiveDumpingFix] Готово. Застосовано живо: {applied_count}, видалено: {confirmed_delist_count}, "
          f"ціна піднята до безпечної межі перед очікуваним видаленням: {price_protected_for_delist_count}, "
          f"на непідтвердженій комісії (лише фід) — {default_commission_count}, помилок — {error_count}. "
          f"Залишилось у sweep'і: {remaining_after}."
          + (f" УВАГА: {unsynced} SKU не вдалось синхронізувати з {FEED_BRANCH}." if unsynced else ""))

    digest = (
        f"🏷 apply_live_dumping_fix.py: застосовано живо — {applied_count} "
        f"(підрізано конкурента — {category_counts.get('undercut', 0)}, "
        f"піднято до floor — {category_counts.get('floor', 0)}), видалено — {confirmed_delist_count} "
        f"(з них ціна заздалегідь піднята до безпечної межі — {price_protected_for_delist_count}), "
        f"на непідтвердженій комісії (лише фід) — {default_commission_count}, помилок — {error_count}."
        + (f"\n\n⏱ Часовий бюджет вичерпано — залишилось {remaining_after} SKU, "
           "запусти скрипт ще раз для продовження цього sweep'у." if time_budget_hit else
           "\n\n✅ Sweep повністю завершено.")
        + (f"\n\n⚠ {unsynced} записів НЕ синхронізовано з {FEED_BRANCH} (буде повторна спроба "
           "на наступному запуску)." if unsynced else "")
    )
    send_telegram_message(digest)


if __name__ == "__main__":
    main()
