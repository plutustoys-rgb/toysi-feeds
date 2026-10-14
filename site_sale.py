# -*- coding: utf-8 -*-
"""site_sale.py — «Знижка тижня −15%» власного сайту (рішення власника 14.10.2026: «червона, варіант а… автоматизація щоб товар змінювався і перераховувався», «не мінялась 7 днів»,
запас маржі 25 ₴ — «підтримую усе»). Ціна на картці = ціна, за якою продаємо (index.json pr); акція просто підміняє її на тиждень.

ЩО РОБИТЬ МОДУЛЬ (чисті функції + два CLI):
  • `python site_sale.py history` — після кожного ребілду сайту фіксує «з якої дати ціна товару не змінюється» (site_price_history.json, приватний runtime-файл поруч з кодом). Лише факти:
    ціна товару змінилась → since = зараз; не змінилась → лишається. Урізаний/порожній index.json історію НЕ чіпає.
  • `python site_sale.py plan` — щонеділі ввечері обирає набір на ПОНЕДІЛОК–НЕДІЛЮ (Київ) і пише site/sale_week.json (без собівартості й маржі). Кандидат: є на сайті, в наявності ≥3, не заборонений/не уцінка/не UNO,
    є фото, ціна НЕ мінялась ≥7 днів (стара ціна має бути РЕАЛЬНОЮ), після знижки маржа ≥25 ₴ з урахуванням еквайрингу 2,8% і витрат 17,81 ₴ на замовлення. Ранг: спершу товари з попитом (site/demand_featured.json від SEO),
    потім з більшим залишком, далі детермінований «жереб» за номером тижня; товари минулого тижня не повторюються.
  Нова ціна = ceil(стара × 0,85) цілими гривнями (без копійок у чеку/LiqPay/накладеному).
КОНТРАКТ site/sale_week.json: {"week":"2026-W44","pct":15,"from":"…+02:00","until":"…+02:00","items":{"<id>":{"old":int,"new":int}}} — читають build_site (ціна pr=new, op=old у вікні from…until) і фіди (sale_price + дати).
Без звернень до мережі в чистих функціях; `plan` тягне каталог Toysi (parser.fetch_toysi_catalog) раз на тиждень.
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
N_ITEMS = 10                  # у наборі 8–12 товарів (дизайн Shopify-code); беремо 10
N_MIN = 8                     # менше придатних кандидатів — набір не публікуємо, алерт
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


def next_week_window(now: datetime) -> tuple[datetime, datetime, str]:
    """Вікно ПОНЕДІЛОК 00:00 … НЕДІЛЯ 23:59:59 (Київ) найближчого тижня, що починається після `now` (now — aware). Мітка ISO-тижня."""
    local = now.astimezone(KYIV)
    days = (7 - local.weekday()) % 7 or 7                      # понеділок строго після сьогодні
    mon = (local + timedelta(days=days)).date()
    start = datetime(mon.year, mon.month, mon.day, 0, 0, 0, tzinfo=KYIV)
    end = datetime(mon.year, mon.month, mon.day, 23, 59, 59, tzinfo=KYIV) + timedelta(days=6)
    iso = mon.isocalendar()
    return start, end, f"{iso[0]}-W{iso[1]:02d}"


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
def margin_after(price: int, cost: float) -> float:
    import competitor_pricing as cp
    new = sale_ceil(price)
    return new * (1 - cp.PAYMENT_COMMISSION["site"]) - cost - cp.SITE_FULFILL_PER_ORDER


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
    """(sale_dict або None, звіт). None, якщо придатних менше N_MIN."""
    start, end, week = next_week_window(now)
    cands = candidates(index, catalog, history, now)
    chosen = pick(cands, demand_ids, last_ids, week)
    rep = {"week": week, "candidates": len(cands), "chosen": len(chosen), "min": N_MIN}
    if len(chosen) < N_MIN:
        return None, rep
    sale = {"week": week, "pct": PCT, "from": start.isoformat(), "until": end.isoformat(), "created_at": now.replace(microsecond=0).isoformat(),
            "items": {c["id"]: {"old": c["old"], "new": c["new"]} for c in chosen}}
    rep["margins"] = {c["id"]: c["margin"] for c in chosen}
    return sale, rep


def _fmt_dt(iso: str) -> str:
    """ISO → формат Google/Meta/Bing для sale_price_effective_date: 2026-10-26T00:00+0200."""
    d = datetime.fromisoformat(iso)
    return d.strftime("%Y-%m-%dT%H:%M%z")


def load_feed_sale(now: datetime | None = None, path: Path | None = None) -> dict:
    """{id: {"old": int, "new": int, "dates": "from/until"}} для фідів: файл акції ПОТОЧНОГО чи МАЙБУТНЬОГО тижня (until ще не минув). Нема файлу, він зіпсований, прострочений або без потрібних полів → {} (фід без акції, нічого не ламається).
    Фід, згенерований ДО старту акції, вже містить майбутню sale_price з датами: Google вмикає й вимикає знижку точно за датою, без щілини між ребілдом сайту й фідом."""
    now = now or datetime.now(KYIV)
    try:
        d = json.loads(Path(path or SALE_FILE).read_text(encoding="utf-8-sig"))
        until = datetime.fromisoformat(d["until"])
        start = datetime.fromisoformat(d["from"])
        if until.tzinfo is None or start.tzinfo is None or until <= now:
            return {}
        dates = f"{_fmt_dt(d['from'])}/{_fmt_dt(d['until'])}"
        out = {}
        for pid, v in (d.get("items") or {}).items():
            old, new = int(v["old"]), int(v["new"])
            if 0 < new < old:
                out[str(pid)] = {"old": old, "new": new, "dates": dates}
        return out
    except Exception:  # noqa: BLE001
        return {}


def add_sale_tags(entry, ns: str, it: dict) -> None:
    """Дописує g:sale_price і g:sale_price_effective_date в <item>, якщо позиція має акцію (спільне для Google/Meta/Bing)."""
    import xml.etree.ElementTree as ET
    if it.get("sale_price") and it.get("sale_dates"):
        ET.SubElement(entry, f"{{{ns}}}sale_price").text = it["sale_price"]
        ET.SubElement(entry, f"{{{ns}}}sale_price_effective_date").text = it["sale_dates"]


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


def cmd_history(now: datetime | None = None) -> int:
    now = now or datetime.now(KYIV)
    index = _load_json(INDEX_FILE, [])
    ok = update_price_history(index, now)
    print(f"[site_sale] історія цін: {'оновлено' if ok else 'НЕ оновлено'} ({len(index) if isinstance(index, list) else 0} товарів в індексі)")
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
    last_ids = set(picks[-1]["ids"]) if isinstance(picks, list) and picks and isinstance(picks[-1], dict) else set()
    hist = load_history()
    if _history_age_days(hist, now) < STABLE_DAYS:
        print(f"[site_sale] історія цін накопичується менше {STABLE_DAYS} днів — набір ще неможливий (без алерту); перший можливий тиждень — коли найстаріший запис сягне {STABLE_DAYS} днів")
        return 1
    sale, rep = plan(now, index, catalog, hist, demand_ids, last_ids)
    print(f"[site_sale] тиждень {rep['week']}: придатних кандидатів {rep['candidates']}, обрано {rep['chosen']} (мінімум {rep['min']})")
    if sale is None:
        _alert("site_sale_few", f"⚠️ site_sale: на {rep['week']} придатних товарів лише {rep['candidates']} (потрібно ≥{N_MIN}: ціна не мінялась ≥{STABLE_DAYS} дн., маржа після −{PCT}% ≥{RESERVE_UAH} ₴) — «Знижку тижня» не опубліковано.")
        return 1
    _atomic_write(SALE_FILE, sale)
    if not isinstance(picks, list):
        picks = []
    picks.append({"week": sale["week"], "ids": list(sale["items"]), "margins": rep.get("margins", {})})
    _atomic_write(PICKS_FILE, picks[-12:])
    print(f"[site_sale] записано {SALE_FILE}: {len(sale['items'])} товарів, {sale['from']} … {sale['until']}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "history":
        sys.exit(cmd_history())
    if cmd == "plan":
        sys.exit(cmd_plan())
    print("використання: python site_sale.py history | plan", file=sys.stderr)
    sys.exit(2)
