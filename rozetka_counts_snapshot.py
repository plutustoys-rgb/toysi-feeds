# -*- coding: utf-8 -*-
"""rozetka_counts_snapshot.py — щоденний знімок лічильників кабінету Rozetka + виявлення застою черги модерації (лише читання; рішення власника 16.10.2026: «додавай необхідні правила, перевіряй результат»).

ЧОМУ: 16.10 у кабінеті було «на модерації» 896 і «очікують підтвердження» 844, а історії цих чисел не було: «ростуть три доби без руху» (ЩО_ДИВИТИСЬ_І_КОЛИ_ТРИВОГА.md §3.2) нічим не вимірювалось. EVA показала, що без щоденного числа дірка лежить тижнями.
ЩО РОБИТЬ: сесія кабінету (як rozetka_price_monitor) → GET /v1/goods/counts → одне число на кожен розділ; пише ОДИН рядок на добу в rozetka_counts_history.jsonl (повторний запуск того ж дня перезаписує рядок дня). Правила (факти, не вгадування):
  • ЗАСТІЙ: черга = «на модерації» + «очікують підтвердження» > 0 і за останні STALL_DAYS (3) доби вона НЕ зменшувалась (кожен знімок ≥ попереднього, найстаріший у вікні ≥3 доби від найновішого) → запис [Автомат → Код] у EXECUTOR_CHANNEL.md (раз на добу);
  • ПОМИЛКИ: «з помилками» або «невалідні» > 0 → запис одразу (у норму вони 0);
  • рядок-звіт щоразу друкується (динаміка проти попереднього знімка).
Без Telegram власнику. Збій/протухла сесія — повідомлення й код 0 (сигнал про сесію дає rozetka_price_monitor)."""
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
DEFAULT_REPORT_DIR = r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\reports"
REPORT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or DEFAULT_REPORT_DIR)
HISTORY_FILE = REPORT_DIR / "rozetka_counts_history.jsonl"
CHANNEL_FILE = Path(os.environ.get("RZ_COUNTS_CHANNEL_FILE") or (REPORT_DIR.parent / "EXECUTOR_CHANNEL.md"))
STALL_DAYS = 3
RECENT_DAYS = 3      # один запис на тему не частіше ніж раз на стільки днів
MARK = "[Автомат → Код] Rozetka:"


def extract(counts: dict) -> dict:
    """Числа з відповіді /v1/goods/counts (структура content → розділ → tabs); відсутній розділ → 0."""
    c = (counts or {}).get("content") or {}

    def tab(sec, name):
        try:
            return int(((c.get(sec) or {}).get("tabs") or {}).get(name) or 0)
        except (TypeError, ValueError):
            return 0

    def tot(sec):
        try:
            return int((c.get(sec) or {}).get("sum") or 0)
        except (TypeError, ValueError):
            return 0

    return {"all": tot("all_items"), "active": tot("active_items"), "on_moderation": tot("on_moderation_items"),
            "waiting_approve": tot("items_waiting_approve"), "new": tot("new_items"),
            "hidden": tab("inactive_items", "items_hidden"), "errors": tab("inactive_items", "items_errors"),
            "not_valid": tab("inactive_items", "not_valid"), "not_available": tab("inactive_items", "not_available"),
            "stop_labels": tot("items_blocked_by_stop_label")}


def queue(snap: dict) -> int:
    return int(snap.get("on_moderation", 0)) + int(snap.get("waiting_approve", 0))


def load_history(path: Path | None = None) -> list:
    out = []
    try:
        for line in Path(path or HISTORY_FILE).read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                if isinstance(d, dict) and d.get("date"):
                    date.fromisoformat(str(d["date"]))
                    out.append(d)
            except ValueError:
                continue
    except OSError:
        return []
    return sorted(out, key=lambda d: d["date"])


def record(snap: dict, today: date, path: Path | None = None) -> list:
    """Рядок дня (перезапис, якщо вже є) → повертає історію."""
    path = Path(path or HISTORY_FILE)
    hist = [d for d in load_history(path) if d["date"] != today.isoformat()]
    hist.append({"date": today.isoformat(), **snap})
    hist.sort(key=lambda d: d["date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in hist[-90:]) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return hist[-90:]


def stalled(hist: list, days: int = STALL_DAYS) -> bool:
    """Черга >0 і за ≥days діб НЕ зменшувалась: у вікні знімків від (останній − days діб) кожен знімок ≥ попереднього, а найстаріший знімок вікна відстоїть від найновішого не менш ніж на days діб."""
    if not hist or queue(hist[-1]) <= 0:
        return False
    last = date.fromisoformat(hist[-1]["date"])
    older = [i for i, d in enumerate(hist) if (last - date.fromisoformat(d["date"])).days >= days]
    if not older:
        return False                      # історія коротша за days діб
    window = hist[older[-1]:]              # якір = НАЙНОВІШИЙ знімок, що відстоїть щонайменше days діб (пропуски днів не ховають застій)
    return all(queue(b) >= queue(a) for a, b in zip(window, window[1:]))


def post_to_channel(title: str, body: str, now: datetime, channel: Path | None = None) -> bool:
    """Запис [Автомат → Код] угорі спільного каналу. Не частіше ніж раз на RECENT_DAYS днів на тему (поки стан триває, канал не засмічується щодня); вставка ПЕРЕД першим рядком, що ПОЧИНАЄТЬСЯ з «## » (не всередині блок-цитати «> ## …»); атомарний запис; недоступний/заблокований канал → False без винятку."""
    channel = Path(channel or CHANNEL_FILE)
    try:
        with open(channel, encoding="utf-8", newline="") as _f:
            s = _f.read()
    except OSError:
        return False
    for m in re.finditer(r"(?m)^## " + re.escape(MARK) + " " + re.escape(title) + r" (\d{4}-\d{2}-\d{2})", s):
        try:
            if (now.date() - date.fromisoformat(m.group(1))).days < RECENT_DAYS:
                return False
        except ValueError:
            continue
    head = f"## {MARK} {title} {now:%Y-%m-%d}"
    nl = "\r\n" if "\r\n" in s else "\n"
    entry = f"{head}\n{body}\n\n".replace("\n", nl)
    m = re.search(r"(?m)^## ", s)
    new = (s[:m.start()] + entry + s[m.start():]) if m else (s + nl + entry)
    try:
        tmp = channel.with_name(channel.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as _f:
            _f.write(new)
        os.replace(tmp, channel)
    except OSError:
        return False
    return True


def active_drop(hist: list) -> tuple | None:
    """ФАКТ: «Активні» у кабінеті впали проти попереднього знімка БІЛЬШЕ, ніж виросли «немає в наявності» + «сховані» (інакше це склад Toysi / коливання схованих). Без порогів. Повертає (було, стало, виросло_not_available) або None. Рішення власника 18.10: ряд активних мовчав 5 тижнів (None з 12.09)."""
    if len(hist) < 2:
        return None
    prev, cur = hist[-2], hist[-1]
    pa, ca = prev.get("active"), cur.get("active")
    if not isinstance(pa, int) or not isinstance(ca, int) or ca >= pa:
        return None
    # Пояснюють падіння лише склад (not_available↑) і «сховані» (hidden скаче туди-сюди щодня: 17.10 80→181). Перелив у модерацію/очікування/помилки — НЕ пояснення (саме це ловимо).
    explained = max(0, (cur.get("not_available") or 0) - (prev.get("not_available") or 0)) + max(0, (cur.get("hidden") or 0) - (prev.get("hidden") or 0))
    if pa - ca > explained:
        return pa, ca, explained
    return None


def evaluate(hist: list, now: datetime, channel: Path | None = None) -> list:
    """Застосовує правила до історії; повертає список спрацювань (назви тем). Чиста щодо кабінету."""
    cur = hist[-1]
    fired = []
    if stalled(hist):
        first = next(d for d in hist if (date.fromisoformat(cur["date"]) - date.fromisoformat(d["date"])).days <= STALL_DAYS)
        body = (f"Черга модерації Rozetka не зменшується {STALL_DAYS}+ доби: «на модерації» {cur['on_moderation']} + «очікують підтвердження» {cur['waiting_approve']} = {queue(cur)} "
                f"(знімок {first['date']}: {queue(first)}). Активні {cur['active']}, сховані {cur['hidden']}, з помилками {cur['errors']}. "
                f"Дія — Коду: звірити, які картки чекають і чи не пов'язано зі зміною фото/назв у фіді; Rozetka сама не повідомляє термін.")
        if post_to_channel("застій черги модерації", body, now, channel):
            fired.append("застій")
        else:
            fired.append("застій (запис за сьогодні вже є)")
    drop = active_drop(hist)
    if drop:
        body = (f"«Активні» у кабінеті Rozetka впали: {drop[0]} → {drop[1]} (−{drop[0] - drop[1]}), а «немає в наявності» + «сховані» виросли лише на {drop[2]}, тобто це не склад. "
                f"Знімки: {hist[-2]['date']} → {hist[-1]['date']}; на модерації {cur['on_moderation']}, очікують {cur['waiting_approve']}, сховані {cur['hidden']}, з помилками {cur['errors']}. "
                f"Дія — Коду: звірити, що змінилось у фіді/правилах (стоп-бренди, заборонені категорії, ціни) між цими датами.")
        # дата знімка в темі: інше падіння того ж 3-добового вікна не затулене попереднім записом (аудит #843)
        if post_to_channel(f"активні впали ({hist[-1]['date']})", body, now, channel):
            fired.append("активні впали")
        else:
            fired.append("активні впали (запис за сьогодні вже є)")
    if cur["errors"] > 0 or cur["not_valid"] > 0:
        body = (f"У кабінеті Rozetka з'явились товари в розділах, що в нормі порожні: «з помилками» {cur['errors']}, «невалідні» {cur['not_valid']}. "
                f"Дія — Коду: відкрити розділ, прочитати причину, звірити з фідом.")
        if post_to_channel("помилки/невалідні > 0", body, now, channel):
            fired.append("помилки")
        else:
            fired.append("помилки (запис за сьогодні вже є)")
    return fired


def main() -> int:
    now = datetime.now()
    try:
        from playwright.sync_api import sync_playwright
        import rozetka_price_monitor as m
        if not m.STATE_FILE.exists():
            print(f"[rz_counts] нема сесії {m.STATE_FILE.name}: знімок пропущено")
            return 0
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            ctx = b.new_context(storage_state=str(m.STATE_FILE))
            page = ctx.new_page()
            cap = {}
            m._open_cabinet(page, cap)
            counts = m._api_get(page, "/v1/goods/counts", cap["auth"])
            b.close()
    except Exception as e:  # noqa: BLE001 — сесія протухла/мережа: без паніки, сигнал про сесію дає rozetka_price_monitor
        print(f"[rz_counts] знімок не отримано: {type(e).__name__}: {str(e)[:150]}", file=sys.stderr)
        return 0
    cont = (counts or {}).get("content") if isinstance(counts, dict) else None
    if not isinstance(cont, dict) or counts.get("success") is False or "on_moderation_items" not in cont or "items_waiting_approve" not in cont:
        print("[rz_counts] відповідь кабінету неповна (нема on_moderation_items/items_waiting_approve): знімок не записано", file=sys.stderr)
        return 0
    snap = extract(counts)
    if not snap["all"] and not snap["active"]:
        print("[rz_counts] кабінет віддав нулі: знімок не записано (щоб не отруїти історію)", file=sys.stderr)
        return 0
    prev = [d for d in load_history() if d["date"] != now.date().isoformat()]
    hist = record(snap, now.date())
    delta = f" (учора {queue(prev[-1])}, Δ {queue(snap) - queue(prev[-1]):+d})" if prev else " (перший знімок)"
    print(f"[rz_counts] {now:%Y-%m-%d}: черга {queue(snap)} = на модерації {snap['on_moderation']} + очікують {snap['waiting_approve']}{delta}; активні {snap['active']}, сховані {snap['hidden']}, помилки {snap['errors']}, невалідні {snap['not_valid']}")
    fired = evaluate(hist, now)
    if fired:
        print("[rz_counts] правила спрацювали:", ", ".join(fired))
    return 0


if __name__ == "__main__":
    sys.exit(main())
