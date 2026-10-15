# -*- coding: utf-8 -*-
"""site_sale.py — ПОСТІЙНА «Знижка −15%» власного сайту (рішення власника 14.10: «червона, варіант а… автоматизація», «не мінялась 7 днів», запас маржі 25 ₴; 15.10: «акції повинні бути постійно… товарів на 2 сторінки» → варіант 1, щоденна ротація).
Ціна на картці = ціна, за якою продаємо (index.json pr); акція підміняє її на термін партії.

ЩО РОБИТЬ МОДУЛЬ (чисті функції + два CLI):
  • `python site_sale.py history` — після кожного ребілду сайту фіксує «з якої дати ціна товару не змінюється» (site_price_history.json, приватний runtime-файл поруч з кодом). Лише факти:
    ціна товару змінилась → since = зараз; не змінилась → лишається. Урізаний/порожній index.json історію НЕ чіпає.
  • `python site_sale.py plan` — ЩОДНЯ о 18:00 обирає нову ПАРТІЮ з N_ITEMS=6 товарів на 8 діб (від 00:00 завтра, Київ) і дописує її в site/sale_week.json (без собівартості й маржі): одночасно діє ≈6×8 = 48 товарів (2 сторінки по 24).
    Кандидат: є на сайті, в наявності ≥3, не заборонений/не уцінка/не UNO, є фото, ціна НЕ мінялась ≥7 днів (стара ціна має бути РЕАЛЬНОЮ), після знижки маржа ≥25 ₴ з урахуванням еквайрингу 2,8% і витрат 17,81 ₴ на замовлення.
    Ранг: спершу товари з попитом (site/demand_featured.json від SEO), потім з більшим залишком, далі детермінований «жереб» за датою партії; товар, що вже діє у будь-якій партії файлу чи був у 8 останніх партіях, не повторюється.
  Нова ціна = ceil(стара × 0,85) цілими гривнями (без копійок у чеку/LiqPay/накладеному).
КОНТРАКТ site/sale_week.json: {"weeks":[{"week":"2026-10-16","pct":15,"from":"…+03:00","until":"…+03:00","items":{"<id>":{"old":int,"new":int}}}, …]} — усі партії, що ще не закінчились (діючі + завтрашня; прострочені прибираються); поле "week" = дата старту партії.
Споживач build_site об'єднує всі діючі партії (ціна pr=new, op=old, кінець знижки — свій у кожного товару, лише коли old == поточна ціна), фіди додають sale_price + дати партії. Споживач сам перевіряє вікно: файл може лишатись на диску й після терміну.
Без звернень до мережі в чистих функціях; `plan` тягне каталог Toysi (parser.fetch_toysi_catalog) раз на добу.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SITE_DIR = Path(os.environ.get("SITE_DIR", str(HERE / "site")))
INDEX_FILE = SITE_DIR / "index.json"
STOCK_FILE = SITE_DIR / "stock.json"
SALE_FILE = SITE_DIR / "sale_week.json"
DEMAND_FILE = SITE_DIR / "demand_featured.json"
HISTORY_FILE = Path(os.environ.get("SITE_PRICE_HISTORY_FILE", str(HERE / "site_price_history.json")))
PICKS_FILE = Path(os.environ.get("SITE_SALE_PICKS_FILE", str(HERE / "site_sale_picks.json")))

PCT = 15                      # відсоток знижки (рішення власника, варіант «а»)
RESERVE_UAH = 25              # запас маржі з одиниці після знижки (політика, власник погодив 14.10)
STABLE_DAYS = 7               # ціна не мінялась щонайменше стільки днів (рішення власника 14.10)
N_ITEMS = 6                   # нових товарів у щоденній партії; партія діє WINDOW_DAYS діб → одночасно 6×8 = 48 товарів (2 сторінки каталогу по 24; рішення власника 15.10 «товарів на 2 сторінки»)
WINDOW_DAYS = 8               # вікно партії: від 00:00 завтра до 23:59:59 на 8-й день (build_site приймає вікно <8 діб)
MIN_STOCK = 3                 # залишок Toysi не менше (щоб тижнева акція не вичерпалась за годину)


class KyivTZ(tzinfo):
    """Europe/Kyiv без залежності від tzdata (Windows): EET +2 / EEST +3, перехід в останню неділю березня й жовтня о 01:00 UTC."""
    def _last_sunday(self, year, month):
        d = date(year, month, 31)
        while d.weekday() != 6:
            d -= timedelta(days=1)
        return d

    def _is_dst_utc(self, utc_naive):
        y = utc_naive.year
        start = datetime(y, 3, self._last_sunday(y, 3).day, 1, 0)
        end = datetime(y, 10, self._last_sunday(y, 10).day, 1, 0)
        return start <= utc_naive < end

    def utcoffset(self, dt):
        if dt is None:
            return timedelta(hours=2)
        # dt — місцевий «настінний» час; шукаємо офсет, що узгоджується з UTC-межами DST
        for off in (timedelta(hours=3), timedelta(hours=2)):
            if self._is_dst_utc(dt.replace(tzinfo=None) - off) == (off == timedelta(hours=3)):
                return off
        return timedelta(hours=2)

    def dst(self, dt):
        return timedelta(hours=1) if self.utcoffset(dt) == timedelta(hours=3) else timedelta(0)

    def tzname(self, dt):
        return "EEST" if self.dst(dt) else "EET"


KYIV = KyivTZ()


def sale_ceil(price: int) -> int:
    """ceil(price × (100−PCT)/100) цілочисельно (без плаваючої крапки)."""
    return -(-int(price) * (100 - PCT) // 100)


def next_batch_window(now: datetime) -> tuple[datetime, datetime, str]:
    """Вікно щоденної партії: ЗАВТРА 00:00 … 23:59:59 на WINDOW_DAYS-й день (Київ; now — aware). Мітка = дата старту (YYYY-MM-DD) — унікальна для партії."""
    local = now.astimezone(KYIV)
    d0 = (local + timedelta(days=1)).date()
    start = datetime(d0.year, d0.month, d0.day, 0, 0, 0, tzinfo=KYIV)
    last = d0 + timedelta(days=WINDOW_DAYS - 1)
    end = datetime(last.year, last.month, last.day, 23, 59, 59, tzinfo=KYIV)
    return start, end, d0.isoformat()


# ───────────────────────── історія цін ─────────────────────────
def _atomic_write(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def load_history(path: Path | None = None) -> dict:
    try:
        d = json.loads(Path(path or HISTORY_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def update_price_history(index: list, now: datetime, path: Path | None = None) -> bool:
    """Фіксує ціни з index.json: нова ціна товару → since=now; та сама → лишаємо. Порожній індекс або такий, що втратив понад половину відомих товарів (урізана збірка), історію НЕ змінює. True — записано."""
    path = Path(path or HISTORY_FILE)
    if not isinstance(index, list) or not index:
        return False
    cur = {}
    for it in index:
        try:
            cur[str(it["id"])] = int(it["pr"])
        except (KeyError, TypeError, ValueError):
            continue
    if not cur:
        return False
    hist = load_history(path)
    if hist and len(cur) < len(hist) * 0.5:
        print(f"[site_sale] індекс ({len(cur)}) вдвічі менший за історію ({len(hist)}) — схоже на урізану збірку, історію не оновлено", file=sys.stderr)
        return False
    iso = now.replace(microsecond=0).isoformat()
    new = {}
    for pid, pr in cur.items():
        rec = hist.get(pid)
        new[pid] = rec if isinstance(rec, dict) and rec.get("price") == pr and rec.get("since") else {"price": pr, "since": iso}
    _atomic_write(path, new)
    return True


def is_stable(rec, price: int, now: datetime, days: int = STABLE_DAYS) -> bool:
    if not isinstance(rec, dict) or rec.get("price") != price:
        return False
    try:
        since = datetime.fromisoformat(rec["since"])
    except (KeyError, ValueError, TypeError):
        return False
    if since.tzinfo is None:
        since = since.replace(tzinfo=now.tzinfo)
    return now - since >= timedelta(days=days)


# ───────────────────────── відбір ─────────────────────────
def _contribution(new: int, cost: float) -> float:
    """Внесок після знижки до копійок: ТОЙ САМИЙ вираз і порядок операцій, що в build_site.apply_sale (new − cost − new×2,8% − 17,81, round 2): похибка float не має відкидати «рівно 25,00»; фід, планувальник і сайт обов'язково збігаються (тест звіряє з build_site)."""
    import competitor_pricing as cp
    return round(new - cost - new * cp.PAYMENT_COMMISSION.get("site", 0.0) - cp.SITE_FULFILL_PER_ORDER, 2)


def margin_after(price: int, cost: float) -> float:
    return _contribution(sale_ceil(price), cost)


def _ok_item(item: dict) -> bool:
    from forbidden_products import is_forbidden
    from generate_prom_feed import is_clearance_item
    from trademark_filter import is_uno_trademark_blocked
    if is_forbidden(item):
        return False
    if is_clearance_item(item.get("name") or "", item.get("category_name") or "", str(item.get("category_id") or "")):
        return False
    if is_uno_trademark_blocked(item.get("name") or "", item.get("vendor") or ""):
        return False
    from generate_google_feed import is_weapon_replica      # ті самі правила торгівлі Meta/Google, що й у фідах: репліка зброї на вітрину акції не потрапляє
    if is_weapon_replica(str(item.get("id") or ""), item.get("name") or ""):
        return False
    return True


def candidates(index: list, catalog: dict, history: dict, now: datetime) -> list:
    """[{id, old, new, stock, margin}] придатні кандидати (див. правила в докстрінгу модуля)."""
    import competitor_pricing as cp
    out = []
    for it in index:
        pid = str(it.get("id"))
        try:
            price = int(it["pr"])
        except (KeyError, TypeError, ValueError):
            continue
        item = catalog.get(pid)
        if not item or not it.get("p"):
            continue
        try:
            stock = float(item.get("stock") or 0)
        except (TypeError, ValueError):
            continue
        if stock < MIN_STOCK or not _ok_item(item):
            continue
        if not is_stable(history.get(pid), price, now):
            continue
        try:
            cost = cp.real_toysi_cost(item)
        except (TypeError, ValueError):
            continue
        if not cost or cost <= 0:
            continue
        m = margin_after(price, cost)
        if m < RESERVE_UAH:
            continue
        out.append({"id": pid, "old": price, "new": sale_ceil(price), "stock": stock, "margin": round(m, 2)})
    return out


def pick(cands: list, demand_ids: set, last_ids: set, week_label: str, n: int = N_ITEMS) -> list:
    """Детермінований добір: спершу попит (SEO), потім більший залишок, потім «жереб» від номера тижня; минулотижневі виключені."""
    import hashlib
    pool = [c for c in cands if c["id"] not in last_ids]
    def key(c):
        h = hashlib.sha256(f"{week_label}:{c['id']}".encode()).hexdigest()
        return (0 if c["id"] in demand_ids else 1, 0 if c["stock"] >= 10 else 1, h)
    return sorted(pool, key=key)[:n]


def _load_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def plan(now: datetime, index: list, catalog: dict, history: dict, demand_ids: set, last_ids: set) -> tuple[dict | None, dict]:
    """(sale_dict або None, звіт). None, лише якщо не обрано жодного товару; менше N_ITEMS — партія публікується, а cmd_plan шле алерт. last_ids — товари, що вже діють/діяли протягом вікна (не повторюються)."""
    start, end, week = next_batch_window(now)
    cands = candidates(index, catalog, history, now)
    chosen = pick(cands, demand_ids, last_ids, week)
    rep = {"week": week, "candidates": len(cands), "chosen": len(chosen), "want": N_ITEMS}
    if not chosen:
        return None, rep
    sale = {"week": week, "pct": PCT, "from": start.isoformat(), "until": end.isoformat(), "created_at": now.replace(microsecond=0).isoformat(),
            "items": {c["id"]: {"old": c["old"], "new": c["new"]} for c in chosen}}
    rep["margins"] = {c["id"]: c["margin"] for c in chosen}
    return sale, rep


def _fmt_dt(iso: str, colon: bool = False) -> str:
    """ISO → формат sale_price_effective_date: Google 2026-10-26T00:00+0200 (за специфікацією Google); Meta/Bing в документації дають офсет З ДВОКРАПКОЮ (colon=True): 2026-10-26T00:00+02:00."""
    out = datetime.fromisoformat(iso).strftime("%Y-%m-%dT%H:%M%z")
    return out[:-2] + ":" + out[-2:] if colon else out


def load_feed_sale(now: datetime | None = None, path: Path | None = None) -> dict:
    """{id: {"old": int, "new": int, "dates": "from/until"}} для фідів: тижні файлу акції ПОТОЧНИЙ і МАЙБУТНІЙ (until ще не минув; див. _read_weeks). Нема файлу, він зіпсований, прострочений або без потрібних полів → {} (фід без акції, нічого не ламається).
    Фід, згенерований ДО старту акції, вже містить майбутню sale_price з датами: Google вмикає й вимикає знижку точно за датою, без щілини між ребілдом сайту й фідом. Якщо id є в кількох тижнях — береться найближчий."""
    now = now or datetime.now(KYIV)
    out = {}
    try:
        for w in sorted(_read_weeks(Path(path or SALE_FILE), now), key=lambda x: x.get("from", "")):
            start, until = datetime.fromisoformat(w["from"]), datetime.fromisoformat(w["until"])
            if start.tzinfo is None or until.tzinfo is None:
                continue
            dates = f"{_fmt_dt(w['from'])}/{_fmt_dt(w['until'])}"
            dates_c = f"{_fmt_dt(w['from'], True)}/{_fmt_dt(w['until'], True)}"
            for pid, v in (w.get("items") or {}).items():
                try:
                    old, new = int(v["old"]), int(v["new"])
                except (KeyError, TypeError, ValueError):
                    continue
                if 0 < new < old and str(pid) not in out:
                    out[str(pid)] = {"old": old, "new": new, "dates": dates, "dates_colon": dates_c}
        return out
    except Exception:  # noqa: BLE001
        return {}


def add_sale_tags(entry, ns: str, it: dict, colon: bool = False) -> None:
    """Дописує g:sale_price і g:sale_price_effective_date в <item>, якщо позиція має акцію (спільне для Google/Meta/Bing)."""
    import xml.etree.ElementTree as ET
    dates = it.get("sale_dates_colon") if colon else it.get("sale_dates")
    if it.get("sale_price") and dates:
        ET.SubElement(entry, f"{{{ns}}}sale_price").text = it["sale_price"]
        ET.SubElement(entry, f"{{{ns}}}sale_price_effective_date").text = dates


def _read_weeks(path: Path, now: datetime) -> list:
    """Тижні з файлу акції, що ще НЕ закінчились (until > now). Підтримує й плоский (старий) формат одного тижня. Зіпсований/відсутній файл → []."""
    d = _load_json(path, {})
    weeks = d.get("weeks") if isinstance(d, dict) and isinstance(d.get("weeks"), list) else ([d] if isinstance(d, dict) and d.get("items") else [])
    out = []
    for w in weeks:
        try:
            if isinstance(w, dict) and datetime.fromisoformat(w["until"]) > now and w.get("items"):
                out.append(w)
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _alert(key: str, text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=24 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[site_sale] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


def _history_age_days(hist: dict, now: datetime) -> float:
    """Вік НАЙСТАРІШОГО запису історії, днів (0, якщо історії нема): поки він <7, жодна ціна не може бути «стабільною 7 днів»."""
    oldest = None
    for rec in hist.values():
        try:
            d = datetime.fromisoformat(rec["since"])
        except (KeyError, ValueError, TypeError):
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=now.tzinfo)
        oldest = d if oldest is None or d < oldest else oldest
    return 0.0 if oldest is None else (now - oldest).total_seconds() / 86400


def sale_still_eligible(old: int, new: int, cost: float) -> bool:
    """Той самий критерій, що застосовує build_site при кожній збірці: new == ceil(old×0,85) і внесок після знижки (new − cost − 2,8%·new − 17,81) ≥ RESERVE_UAH. Фіди рекламують sale_price лише для позицій, які пройшли б цю перевірку."""
    import competitor_pricing as cp
    return new == sale_ceil(old) and _contribution(new, cost) >= RESERVE_UAH


def check_applied(now: datetime, index: list, path: Path | None = None) -> tuple[int, int]:
    """(позицій у чинному тижні, застосованих на сайті). «Застосована» = у index.json є поле op (build_site ставить op=стара ціна лише для акційних). Якщо чинний тиждень є, а застосовано <3 (або менше за кількість позицій) —
    алерт site_sale_not_applied: ловить і збій понеділкової збірки (index без op), і масове випадання позицій. Нема чинного тижня → (0, 0), без алерту."""
    ids = set()
    for w in _read_weeks(Path(path or SALE_FILE), now):
        try:
            if datetime.fromisoformat(w["from"]) <= now < datetime.fromisoformat(w["until"]):
                ids |= {str(k) for k in (w.get("items") or {})}
        except (KeyError, ValueError, TypeError):
            continue
    if not ids:
        return 0, 0
    applied = sum(1 for it in index if isinstance(it, dict) and str(it.get("id")) in ids and it.get("op"))
    if applied < min(3, len(ids)):
        _alert("site_sale_not_applied", f"⚠️ site_sale: «Знижка тижня» чинна за розкладом ({len(ids)} позицій у файлі), але на сайті (index.json) застосовано лише {applied} — збірка сайту не підхопила акцію (збій ребілду чи масове випадання позицій). Перевірити site-rebuild.")
    return len(ids), applied


def cmd_history(now: datetime | None = None) -> int:
    now = now or datetime.now(KYIV)
    index = _load_json(INDEX_FILE, [])
    ok = update_price_history(index, now)
    print(f"[site_sale] історія цін: {'оновлено' if ok else 'НЕ оновлено'} ({len(index) if isinstance(index, list) else 0} товарів в індексі)")
    try:
        total, applied = check_applied(now, index if isinstance(index, list) else [])
        if total:
            print(f"[site_sale] акція чинна: у файлі {total}, на сайті застосовано {applied}")
    except Exception as e:  # noqa: BLE001 — перевірка допоміжна: не валить оновлення історії
        print(f"[site_sale] check_applied: {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
    return 0 if ok else 1


def cmd_plan(now: datetime | None = None, catalog: dict | None = None) -> int:
    now = now or datetime.now(KYIV)
    index = _load_json(INDEX_FILE, [])
    if not isinstance(index, list) or not index:
        _alert("site_sale_no_index", "⚠️ site_sale: index.json порожній/нечитабельний — набір «Знижки тижня» НЕ складено.")
        return 1
    if catalog is None:
        import parser as tp
        catalog = tp.fetch_toysi_catalog()
    if not catalog:
        _alert("site_sale_no_catalog", "⚠️ site_sale: каталог Toysi порожній/недоступний — набір «Знижки тижня» НЕ складено.")
        return 1
    demand = _load_json(DEMAND_FILE, {})
    demand_ids = {str(x) for x in (demand.get("ids") if isinstance(demand, dict) else []) or []}
    picks = _load_json(PICKS_FILE, [])
    if not isinstance(picks, list):
        picks = []
    _w = next_batch_window(now)[2]
    picks = [p for p in picks if isinstance(p, dict) and p.get("week") != _w]       # повторний запуск ТОГО САМОГО тижня не виключає власний щойно записаний набір і не дублює запис
    last_ids = {str(i) for p in picks[-WINDOW_DAYS:] for i in (p.get("ids") or [])}          # діють/діяли протягом вікна (8 останніх щоденних партій)
    for w in (x for x in _read_weeks(SALE_FILE, now) if x.get("week") != _w):                  # і все, що вже стоїть у файлі й не закінчилось (інакше товар у двох партіях одночасно)
        last_ids |= {str(i) for i in (w.get("items") or {})}
    hist = load_history()
    if not hist and HISTORY_FILE.exists() and HISTORY_FILE.stat().st_size > 2:
        _alert("site_sale_history_lost", "⚠️ site_sale: файл історії цін існує, але нечитабельний/порожній — відлік «ціна не мінялась 7 днів» піде з нуля, «Знижку тижня» не складено. Перевірити site_price_history.json.")
        return 1
    if _history_age_days(hist, now) < STABLE_DAYS:
        print(f"[site_sale] історія цін накопичується менше {STABLE_DAYS} днів — набір ще неможливий (без алерту); перший можливий тиждень — коли найстаріший запис сягне {STABLE_DAYS} днів")
        return 1
    sale, rep = plan(now, index, catalog, hist, demand_ids, last_ids)
    print(f"[site_sale] партія {rep['week']}: придатних кандидатів {rep['candidates']}, обрано {rep['chosen']} (треба {rep['want']})")
    if sale is None:
        _alert("site_sale_few", f"⚠️ site_sale: на партію {rep['week']} не знайдено жодного нового придатного товару (ціна не мінялась ≥{STABLE_DAYS} дн., маржа після −{PCT}% ≥{RESERVE_UAH} ₴, не в діючих партіях) — партію не опубліковано.")
        return 1
    if rep["chosen"] < rep["want"]:
        _alert("site_sale_short", f"⚠️ site_sale: партія {rep['week']} неповна: {rep['chosen']} з {rep['want']} (нових придатних лишилось мало: {rep['candidates']} у пулі, частина вже в діючих партіях).")
    picks.append({"week": sale["week"], "ids": list(sale["items"]), "margins": rep.get("margins", {})})
    _atomic_write(PICKS_FILE, picks[-30:])                    # спершу приватний журнал (збій другого запису не губить виключення «минулого тижня»)
    # файл акції тримає всі партії, що ще не закінчились (до ~9: діючі + завтрашня); прострочені прибираються
    kept = [w for w in _read_weeks(SALE_FILE, now) if w.get("week") != sale["week"]]
    _atomic_write(SALE_FILE, {"weeks": kept + [sale]})
    print(f"[site_sale] записано {SALE_FILE}: партій у файлі {len(kept) + 1}; нова {sale['week']}: {len(sale['items'])} товарів, {sale['from']} … {sale['until']}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "history":
        sys.exit(cmd_history())
    if cmd == "plan":
        sys.exit(cmd_plan())
    print("використання: python site_sale.py history | plan", file=sys.stderr)
    sys.exit(2)
