# -*- coding: utf-8 -*-
"""forbidden_watch.py — детектор-ДОПОВІДАЧ «клинкова зброя» (рішення Консультанта 2026-10-07). НІЧОГО не забороняє сам.

ЧОМУ доповідач, а не правило: ключ за словом (ніж/меч/сокира/…) має precision ≈ 8% (5 справжніх із 62 кандидатів; решта — канцелярські ножі,
баскетбольні щити, літаки-«мечі», бульбашки-«меч», Funko «з мечем»). Автозаборона знімала б товар із шести каналів. Тому: нові кандидати за ЦІЛИМ
словом, яких людина ще не переглядала, → ОДИН Telegram-алерт на pid; людина вирішує: додає pid у `FORBIDDEN_PRODUCT_IDS` (заборона) або в
`forbidden_products_reviewed.json` (перегляд: verdict, дата, хто — інакше через півроку не відрізнити «вирішено» від «заглушено»).

ЗАПУСК: щоденно з `catalog_health_monitor.py` (VPS, systemd-таймер catalog-health-monitor) — `alert_new_candidates(catalog)`. Результат також пишеться в
`catalog_health_history.jsonl` (поле `blade_unreviewed`). Ручний запуск: `python forbidden_watch.py` (друкує кандидатів, нічого не шле).
Ключ — ЦІЛІ ТОКЕНИ (не підрядок): «ножиці», «ніжки», «ніжний», «ножиця» НЕ збігаються; «ніж» як сполучник («більше ніж») і канцелярія/кухня/ліплення відсіюються.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import forbidden_products as fp

BASE = Path(__file__).resolve().parent
REVIEWED_FILE = Path(os.environ.get("FORBIDDEN_REVIEWED_FILE", "") or (BASE / "forbidden_products_reviewed.json"))
MAX_IN_ALERT = 15
STATE_FILE = Path(os.environ.get("FORBIDDEN_WATCH_STATE", "") or (BASE / "forbidden_watch_state.json"))

# Цілі форми (укр + рос). НЕ підрядки.
_FORMS = {
    "ніж": {"ніж", "ножа", "ножу", "ножем", "ножі", "ножів", "ножами", "ножах", "ножик", "ножика", "ножики", "нож", "ножи", "ножом"},
    "кинджал": {"кинджал", "кинджала", "кинджали", "кинджалів", "кинжал", "кинжала", "кинжалы", "кинджалик", "кинджалика"},
    "викидуха": {"викидуха", "викидуху", "викидухи", "викидух", "выкидуха", "выкидной"},
    "кунай": {"кунай", "куная", "куной", "kunai"},
    "катана": {"катана", "катану", "катани", "катан", "катаны"},
    "сокира": {"сокира", "сокиру", "сокири", "сокир", "сокирою", "топор", "топоры", "топора", "сокирка", "сокирку", "сокирки", "топірець", "топірця", "топорик"},
    "меч": {"меч", "меча", "мечі", "мечів", "мечем", "мечу", "мечи", "мечей"},
    "шабля": {"шабля", "шаблю", "шаблі", "шабель", "сабля", "сабли", "шаблею", "шпага", "шпагу", "шпаги", "нунчаки"},
    "щит": {"щит", "щита", "щити", "щитів", "щиту", "щиты"},
}
_FORM2KEY = {f: k for k, forms in _FORMS.items() for f in forms}

# Автоматично відсіюються (не зброя): канцелярія/кухня/ліплення — інструмент творчості, не клинок.
_NEG_PHRASES = [r"канцеляр", r"для пластилін", r"для пластилин", r"для тіста", r"для теста", r"для ліплення", r"для лепки", r"ножиц", r"кухонн"]
_NEG_CATEGORIES = {"ножиці та канцелярські ножі"}
_CONJ = re.compile(r"(більш\w*|менш\w*|дорожч\w*|краще|більше|менше|раніше|пізніше|швидше)\s+ніж", re.I)


def tokens(text: str) -> list:
    return re.findall(r"[a-zа-яіїєґё0-9'’ʼ]+", (text or "").lower())


def matched_keys(name: str) -> list:
    """Ключові слова зброї, знайдені як ЦІЛІ токени в назві, з автоматичними відсіюваннями; порожньо = не кандидат."""
    low = (name or "").lower()
    keys = {_FORM2KEY[t] for t in tokens(name) if t in _FORM2KEY}
    # канцелярія/кухня/ліплення глушить ЛИШЕ «ніж» (аудит #636 R3): «Кухонний ніж, меч» лишається кандидатом через «меч»
    if any(re.search(p, low) for p in _NEG_PHRASES):
        keys.discard("ніж")
    if keys == {"ніж"} and _CONJ.search(low):      # «ніж» лише як сполучник
        return []
    return sorted(keys)


def candidates(catalog: dict) -> list:
    """Кандидати за словом серед ще НЕ заборонених позицій: [{pid, name, category, stock, keys}]."""
    out = []
    for pid, it in (catalog or {}).items():
        if fp.is_forbidden(it):
            continue
        if (it.get("category_name") or "").strip().lower() in _NEG_CATEGORIES:
            continue
        keys = matched_keys(it.get("name"))
        if keys:
            out.append({"pid": str(pid), "name": it.get("name") or "", "category": it.get("category_name") or "",
                        "stock": int(it.get("stock") or 0), "keys": keys})
    return out


def load_reviewed(path: Path = None) -> dict:
    try:
        d = json.loads(Path(path or REVIEWED_FILE).read_text(encoding="utf-8"))
        return d.get("items", {}) if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def unreviewed(catalog: dict, reviewed: dict = None) -> list:
    rv = load_reviewed() if reviewed is None else reviewed
    return [c for c in candidates(catalog) if c["pid"] not in rv]


def _load_state(path: Path) -> set:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _save_state(path: Path, pids: set) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(sorted(pids), f)
    os.replace(tmp, path)


def alert_new_candidates(catalog: dict, send=None, state_path: Path = None, reviewed: dict = None) -> list:
    """Шле ОДИН Telegram-алерт на кожен новий нерозглянутий pid (стан у forbidden_watch_state.json — не повторює щодня).
    Повертає ВСІ поточні нерозглянуті (для запису в історію монітора). Алерт самодіагностичний: що знайдено і що робити."""
    sp = Path(state_path or STATE_FILE)
    pending = unreviewed(catalog, reviewed)
    seen = _load_state(sp)
    fresh = [c for c in pending if c["pid"] not in seen]
    if fresh:
        shown = fresh[:MAX_IN_ALERT]
        lines = [f"• {c['pid']} [{c['category']}] {c['name'][:80]} (склад {c['stock']}; слова: {', '.join(c['keys'])})" for c in shown]
        more = f"\n…і ще {len(fresh) - len(shown)} — прийдуть наступним алертом" if len(fresh) > len(shown) else ""
        warn = ""
        if reviewed is None and not load_reviewed():
            warn = "\n⚠️ forbidden_products_reviewed.json відсутній/порожній/нечитний — тому кандидатів стільки; перевір, що файл задеплоєно."
            print("[forbidden_watch] УВАГА: forbidden_products_reviewed.json не прочитано.", file=sys.stderr)
        text = (f"🔪 Нові кандидати «клинкова зброя» за словом у назві ({len(fresh)}) — потрібен ПЕРЕГЛЯД людиною, автозаборони НЕМАЄ:\n"
                + "\n".join(lines) + more + warn +
                "\n\nЩо робити: прибрати з усіх вітрин → додати pid у FORBIDDEN_PRODUCT_IDS (forbidden_products.py); "
                "лишити → додати pid у forbidden_products_reviewed.json (verdict, дата, хто). Поки не вирішено — алерт не повториться, "
                "але pid лишається в `blade_unreviewed` щоденного монітора.")
        try:
            ok = (send or _telegram)(text)
        except Exception as e:  # noqa: BLE001 — алерт best-effort, монітор не падає
            print(f"[forbidden_watch] Telegram не надіслано: {e}", file=sys.stderr)
            return pending                    # стан не оновлюємо — спробуємо знову наступного дня
        if ok is False:                       # send_telegram_message НЕ кидає виняток, а повертає False (аудит #636, блокер)
            print("[forbidden_watch] Telegram повернув False (токен/мережа/ok:false) — стан не оновлено, повторимо наступного дня.", file=sys.stderr)
            return pending
        _save_state(sp, seen | {c["pid"] for c in shown})    # у стан лише ПОКАЗАНІ в алерті pid: решта прийде наступним
    return pending


def _telegram(text: str):
    if os.environ.get("AUDIT_NO_TELEGRAM") == "1":
        return None                           # тестовий режим: не збій і не відправка
    from telegram_notify import send_telegram_message
    return send_telegram_message(text)


if __name__ == "__main__":
    from parser import fetch_toysi_catalog
    cat = fetch_toysi_catalog()
    cands = candidates(cat)
    new = unreviewed(cat)
    print(f"кандидатів за словом (поза забороненими): {len(cands)}; нерозглянутих: {len(new)}")
    for c in new:
        print(f"  {c['pid']} [{c['category']}] {c['name'][:80]} (склад {c['stock']}; {', '.join(c['keys'])})")
