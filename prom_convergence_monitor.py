"""prom_convergence_monitor.py — щоденний монітор конвергенції каталогу Prom.

НАВІЩО (2026-08-17): після переходу на СТІЙКИЙ membership (select_top_items, PR #293)
каталог Prom має ПОСТУПОВО зійтися до ~6000 наявних (Prom доганяє створення на
стабільній цілі). Це відповідальний процес на кілька днів — власник не має перевіряти
його вручну. Монітор робить це сам: тягне ПОВНИЙ каталог через кабінет
(prom_cabinet_catalog.fetch_full_catalog — CMS, обхід капу ~499 публічного API), веде
історію available/OOS, і шле Telegram-алерт ЛИШЕ коли щось НЕ ТАК.

САМОДІАГНОСТИКА (правило власника: алерт сам каже ЧОМУ, не змушує розслідувати):
кожен алерт несе розклад — available зараз, дельта від минулого, OOS, скільки днів
пласко, і найімовірнішу причину. Мовчить, поки available нормально росте до цілі
(щоб не шуміти) і поки не досягнуто збіжності.

Три стани, що дають алерт:
  1. РЕГРЕС — available впав відносно попереднього виміру понад поріг (щось гаситься:
     масові delisting-и / впав фід / зайвий catalog_sync). Розклад: наскільки, чи виріс OOS.
  2. ЗАСТІЙ — available НЕ росте STALL_DAYS днів поспіль, хоч ще не досяг цілі (Prom
     не створює: денний ліміт / фід не відпрацьовує на VPS / стійкий відбір не активний).
  3. СЕСІЯ — CMS-фетч впав (сесія протухла) → треба `prom_notifications_scraper.py --login`.

ЛОКАЛЬНИЙ (кабінетна сесія живе лише тут, не на VPS). Запуск раз на день по таймеру
Windows (прихований, run_hidden.vbs). Історія — .local_secrets/prom_convergence_history.json
(не в git: account-specific, перебудовується; жодних фінданих — лише лічильники каталогу).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
HISTORY_FILE = BASE_DIR / ".local_secrets" / "prom_convergence_history.json"

TARGET = 6000                 # ціль наявних (SELECT_COUNT)
CONVERGED_AT = int(TARGET * 0.95)   # 5700 — вважаємо збіжність досягнутою, далі мовчимо
GROWTH_MIN = 30               # приріст available/день менший за це = «пласко»
STALL_DAYS = 3                # стільки пласких днів поспіль (нижче цілі) = ЗАСТІЙ
REGRESSION_DROP = 60          # падіння available відносно минулого разу понад це = РЕГРЕС


def _notify(msg: str) -> None:
    try:
        sys.path.insert(0, str(BASE_DIR))
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:
        print(f"[Converge] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _load_history() -> list:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_history(hist: list) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    # тримаємо останні 60 вимірів — історія не росте безмежно
    HISTORY_FILE.write_text(json.dumps(hist[-60:], ensure_ascii=False), encoding="utf-8")


def _measure() -> dict:
    """Один вимір каталогу через кабінет. Кидає PromCabinetError при протухлій сесії."""
    from prom_cabinet_catalog import fetch_full_catalog
    cat = fetch_full_catalog()
    avail = sum(1 for v in cat.values()
                if str(v.get("presence")) in ("available", "avail", "True", "1"))
    return {"at": datetime.now().isoformat(timespec="minutes"),
            "total": len(cat), "available": avail, "oos": len(cat) - avail}


def _is_stalled(hist: list) -> bool:
    """available не виріс на GROWTH_MIN у ЖОДНОМУ з останніх STALL_DAYS переходів
    і досі нижче цілі. Потрібно STALL_DAYS+1 вимірів, щоб судити."""
    if len(hist) < STALL_DAYS + 1:
        return False
    recent = hist[-(STALL_DAYS + 1):]
    if recent[-1]["available"] >= CONVERGED_AT:
        return False
    deltas = [recent[i + 1]["available"] - recent[i]["available"] for i in range(len(recent) - 1)]
    return all(d < GROWTH_MIN for d in deltas)


def main() -> None:
    hist = _load_history()
    prev = hist[-1] if hist else None

    # 1. СЕСІЯ — фетч впав.
    try:
        cur = _measure()
    except Exception as e:  # PromCabinetError чи інше
        msg = (f"🚨 Prom-конвергенція: не зміг зчитати каталог — {e}\n"
               f"ПРИЧИНА: найімовірніше протухла кабінетна сесія Prom. "
               f"Перелогінься: `python prom_notifications_scraper.py --login`.")
        print(f"[Converge] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)

    hist.append(cur)
    _save_history(hist)

    delta = (cur["available"] - prev["available"]) if prev else None
    line = (f"[Converge] available={cur['available']}/{TARGET}, OOS={cur['oos']}, "
            f"total={cur['total']}" + (f", Δ={delta:+d} від минулого" if delta is not None else " (перший вимір)"))
    print(line)

    # Збіжність досягнута — тихий успіх (без алерту, щоб не шуміти).
    if cur["available"] >= CONVERGED_AT:
        print(f"[Converge] Збіжність досягнута ({cur['available']} ≥ {CONVERGED_AT}). Монітор мовчить.")
        return

    # 2. РЕГРЕС — available помітно впав.
    if delta is not None and delta <= -REGRESSION_DROP:
        oos_delta = cur["oos"] - prev["oos"]
        msg = (f"⚠️ Prom-конвергенція: available ВПАВ {prev['available']}→{cur['available']} "
               f"({delta:+d}) за день.\nПРИЧИНА-розклад: OOS {oos_delta:+d} (тепер {cur['oos']}), "
               f"total {cur['total']-prev['total']:+d}. Ймовірно масові delisting-и / фід не "
               f"відпрацював / зайва деактивація catalog_sync. Перевір останні прогони репрайсера "
               f"й catalog_sync у Telegram + `git log` на VPS.")
        print(f"[Converge] {msg}", file=sys.stderr)
        _notify(msg)
        return

    # 3. ЗАСТІЙ — не росте кілька днів, а ще не досяг цілі.
    if _is_stalled(hist):
        window = hist[-(STALL_DAYS + 1):]
        span = f"{window[0]['available']}→{window[-1]['available']}"
        msg = (f"⚠️ Prom-конвергенція ЗАСТОПОРИЛАСЬ: available {span} за {STALL_DAYS} дні "
               f"(ціль {TARGET}, зараз недобір ~{TARGET-cur['available']}).\n"
               f"ПРИЧИНА (перевір по черзі): (1) фід НЕ відпрацьовує на VPS — глянь останній "
               f"'Автодеплой'/генерацію фіду в Telegram; (2) впертись у денний ліміт створення "
               f"Prom (тоді росте повільно — це норма, чекай); (3) стійкий відбір не активний — "
               f"звір, що generate_prom_feed_top на VPS має load_feed_membership.")
        print(f"[Converge] {msg}", file=sys.stderr)
        _notify(msg)
        return

    # Нормальний ріст — тихо (не шуміти).
    print(f"[Converge] Ріст у нормі (Δ={delta}), ще не збіжність — монітор мовчить.")


if __name__ == "__main__":
    main()
