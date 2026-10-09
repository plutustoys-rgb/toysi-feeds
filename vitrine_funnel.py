# -*- coding: utf-8 -*-
"""vitrine_funnel.py — щоденна воронка Prom-вітрини від початку до кінця (машинна версія; людську/кабінетну веде Аудитор вітрин → аудит_вітрин/ВОРОНКА_PROM.md).

ЧОМУ (власник 09.10.2026, «чому через 3 місяці не можемо наповнити конкурентними товарами Prom»): за три місяці вітрина тричі ламалась без сигналу, бо міряли розмір ФІДУ, а не
результат: пул придатних 8 261 → 1 052 (17.08–08.10, наш же «храповик» delist), 08–09.10 вимкнена кампанія «за замовлення» → усі 3 463 з 3 468 «Недоступен» (кнопки покупки нема),
0 замовлень з 24.09. Тут міряємо ЩО БАЧИТЬ ПОКУПЕЦЬ:
  1. кабінет (повний каталог через CMS): усього / «В наявності» / в кампанії (`in_running_cpa` — без цього кнопки «Купити» нема, довідка Prom: «Якщо товари не додані до кампаній, їх не можна придбати»);
  2. ПУБЛІЧНІ сторінки (вибірка): schema.org InStock + кнопка `buy_now_btn`;
  3. наш фід: кількість `<offer>` у feed-data/feeds/prom_feed_top.xml проти кабінету;
  4. ЗАМОВЛЕННЯ Prom (API `GET /orders/list`, останні 60 діб): дата останнього, діб розриву, скільки за 14 діб; ≥14 діб тиші = алерт раз на 7 діб (мовчання теж сигнал);
     перше замовлення після розриву ≥14 діб = окреме повідомлення (єдина справжня перевірка всього ланцюга);
  5. ДОЛЯ ПОВЕРНЕНИХ позицій (запит Консультанта 09.10): з публічного (редагованого) price_state у feed-data беремо `_readded_at`/`_readd_failed`; рахуємо, скільки повернених
     >24 год / >72 год тому вже в кабінеті (створені), скільки з вибірки публічних сторінок з кнопкою, скільки 404, скільки відмов. Якщо з повернених >72 год у кабінеті <50% —
     приріст вітрини «паперовий» (картки не доходять до публікації).
Бракує даних — колонка друкує «нема даних» і причину (orders_note/ret_note), а не порожнє місце.
Рядок → reports/vitrine_funnel.csv. Пороги → Telegram (send_throttled_alert; сито telegram_triage пропускає — нові тексти «Воронка Prom»/«Prom: перше замовлення»).
Викликається best-effort наприкінці prom_cabinet_catalog.summary() (задача PlutusToys_PromCatalogHistory, щодня); окремо: `python vitrine_funnel.py` (знімає кабінет сам).
READ-ONLY: лише GET.
"""
import csv
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
CSV_FILE = BASE_DIR / "reports" / "vitrine_funnel.csv"
FEED_URL = "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/prom_feed_top.xml"
STATE_URL = "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/prom_competitor_price_state.json"   # публічний, РЕДАГОВАНИЙ (без cost/margin)
PROM_API_URL = "https://my.prom.ua/api/v1"
SAMPLE_N = 30
UA = {"User-Agent": "Mozilla/5.0"}
COLUMNS = ["at", "total", "avail", "not_avail", "in_cpa", "in_cpa_pct", "buy_ok", "buy_n", "buy_err", "buy_pct", "feed_offers", "feed_delta_pct",
           "last_order_at", "days_since_order", "orders_14d", "orders_note",
           "ret_total", "ret_in_cab", "ret72_total", "ret72_in_cab", "ret_failed", "ret_pub_buy", "ret_pub_404", "ret_pub_n", "ret_note",
           "alerts"]

# Пороги (узгоджені з завданням Аудитора, EXECUTOR_CHANNEL 09.10.2026)
BUY_MIN_PCT = 90.0           # частка вибіркових публічних сторінок InStock + кнопка
CPA_MIN_PCT = 80.0           # частка «В наявності», що в кампанії
# Фід БІЛЬШИЙ за кабінет — це черга створення нових позицій на Prom (лаг 12–15 год після повернення/додавання), тому поріг широкий (+40%: імпорт не створює);
# фід МЕНШИЙ за кабінет — позиції лишаються на Prom без фіду (застарілі ціни/залишки), поріг вузький (−10%).
FEED_OVER_MAX_PCT = 40.0
FEED_UNDER_MAX_PCT = 10.0
AVAIL_DROP_MAX_PCT = 15.0    # падіння «В наявності» за добу
NO_ORDERS_DAYS = 14          # діб без замовлення Prom → алерт (раз на 7 діб)
ORDERS_LOOKBACK_DAYS = 60
RET_MIN_HOURS = 24           # «повернена» рахується в долі, лише коли минуло ≥ стільки годин (лаг створення на Prom 12–15 год)
RET_CHECK_HOURS = 72         # поріг «мали б уже бути створені» (грація повернення READD_GRACE_HOURS = 72)
RET_IN_CAB_MIN_PCT = 50.0    # частка повернених >72 год тому, що мають бути в кабінеті
RET_PUB_MIN_PCT = 70.0       # частка вибірки повернених, що публічні з кнопкою
RET_SAMPLE_N = 15


def _fetch_html(url: str):
    """(html, None) успіх; (None, 404) — HTTP 404 (свіжа картка ще не публічна, повторювати марно); (None, 'err') — інший HTTP-код або мережевий збій після одного повтору."""
    for attempt in range(2):   # один повтор лише на мережевий збій/таймаут
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25).read().decode("utf-8", "ignore"), None
        except urllib.error.HTTPError as e:
            return None, (404 if e.code == 404 else "err")
        except Exception:  # noqa: BLE001 — мережевий збій ≠ «недоступний»
            if attempt == 0:
                time.sleep(1.5)
    return None, "err"


def page_buyable(url: str):
    """(InStock?, є кнопка?) для публічної сторінки; (None, None) — збій мережі/HTTP/нема схеми (у вибірку не рахується)."""
    h, _code = _fetch_html(url)
    if h is None:
        return None, None
    m = re.search(r'"availability":"http://schema.org/(\w+)"', h)
    if not m:
        return None, None   # схеми нема (зміна розмітки/https/блок) — це НЕ «недоступний»; рахується в buy_err (аудит #648)
    return m.group(1) == "InStock", ('data-qaid="buy_now_btn"' in h)


def page_state(url: str) -> str:
    """'buy' (InStock + кнопка) | 'nobuy' (сторінка є, але не купити) | '404' | 'err'."""
    h, code = _fetch_html(url)
    if h is None:
        return "404" if code == 404 else "err"
    m = re.search(r'"availability":"http://schema.org/(\w+)"', h)
    if not m:
        return "err"
    return "buy" if (m.group(1) == "InStock" and 'data-qaid="buy_now_btn"' in h) else "nobuy"


def feed_offer_count():
    try:
        x = urllib.request.urlopen(urllib.request.Request(FEED_URL, headers=UA), timeout=60).read().decode("utf-8", "ignore")
        return len(re.findall(r"<offer id=", x))
    except Exception:  # noqa: BLE001
        return None


def last_orders(now: datetime = None) -> dict:
    """Замовлення Prom за ORDERS_LOOKBACK_DAYS через API (токен PROM_API_KEY): дата останнього, діб розриву, скільки за 14 діб.
    Немає токена/401/мережа → поля порожні, orders_note каже чому («нема даних: …»)."""
    now = now or datetime.now()
    out = {"last_order_at": "", "days_since_order": None, "orders_14d": None, "orders_note": ""}
    try:   # задача виконується з теки основної копії; .env може бути ще не завантажений (telegram_notify вантажить його лише при імпорті пізніше)
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except Exception:  # noqa: BLE001
        pass
    key = os.environ.get("PROM_API_KEY", "")
    if not key:
        out["orders_note"] = "нема даних: PROM_API_KEY не заданий"
        return out
    try:
        import requests
        date_from = (now - timedelta(days=ORDERS_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
        dates, last_id = [], None
        while True:
            params = {"date_from": date_from, "limit": 100}
            if last_id is not None:
                params["last_id"] = last_id
            r = requests.get(f"{PROM_API_URL}/orders/list", headers={"Authorization": f"Bearer {key}"}, params=params, timeout=30)
            if r.status_code == 401:
                out["orders_note"] = "нема даних: 401 (токен без scope «замовлення»)"
                return out
            r.raise_for_status()
            page = (r.json() or {}).get("orders", [])
            if not page:
                break
            dates.extend(str(o.get("date_created") or "")[:19] for o in page)
            if len(page) < 100:
                break
            last_id = page[-1]["id"]
    except Exception as e:  # noqa: BLE001
        out["orders_note"] = f"нема даних: {type(e).__name__}: {str(e)[:80]}"
        return out
    parsed = []
    for d in dates:
        try:
            parsed.append(datetime.fromisoformat(d))
        except ValueError:
            continue
    out["orders_14d"] = sum(1 for d in parsed if d >= now - timedelta(days=14))
    if parsed:
        last = max(parsed)
        out["last_order_at"] = last.strftime("%Y-%m-%d %H:%M")
        out["days_since_order"] = (now - last).days
    else:
        out["days_since_order"] = ORDERS_LOOKBACK_DAYS   # жодного за період — «щонайменше N діб»
        out["orders_note"] = f"жодного замовлення за {ORDERS_LOOKBACK_DAYS} діб"
    return out


def returned_fate(cat: dict, now: datetime = None, sample_n: int = RET_SAMPLE_N) -> dict:
    """Доля повернених позицій (дані `_readded_at`/`_readd_failed` з публічного price_state): у кабінеті / публічні з кнопкою / 404 / відмови."""
    now = now or datetime.now()
    out = {"ret_total": None, "ret_in_cab": None, "ret72_total": None, "ret72_in_cab": None, "ret_failed": None,
           "ret_pub_buy": None, "ret_pub_404": None, "ret_pub_n": None, "ret_note": ""}
    try:
        st = json.loads(urllib.request.urlopen(urllib.request.Request(STATE_URL, headers=UA), timeout=60).read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        out["ret_note"] = f"нема даних: price_state недоступний ({type(e).__name__})"
        return out
    ra = st.get("_readded_at") or {}
    if not ra:
        out["ret_note"] = "нема даних: у price_state немає _readded_at"
        return out

    def older(hours):
        cut = (now - timedelta(hours=hours)).isoformat()
        return [p for p, t in ra.items() if t <= cut]
    r24, r72 = older(RET_MIN_HOURS), older(RET_CHECK_HOURS)
    out.update({"ret_total": len(r24), "ret_in_cab": sum(1 for p in r24 if p in cat),
                "ret72_total": len(r72), "ret72_in_cab": sum(1 for p in r72 if p in cat),
                "ret_failed": len(st.get("_readd_failed") or {})})
    in_cab = [p for p in r24 if p in cat and cat[p].get("view_catalog_url")]
    smp = random.sample(in_cab, min(sample_n, len(in_cab))) if in_cab else []
    with ThreadPoolExecutor(3) as ex:
        states = list(ex.map(lambda p: page_state(cat[p]["view_catalog_url"]), smp))
    out["ret_pub_n"] = len(smp)
    out["ret_pub_buy"] = sum(1 for s in states if s == "buy")
    out["ret_pub_404"] = sum(1 for s in states if s == "404")
    if not smp:
        out["ret_note"] = "нема даних: серед повернених >24 год нема жодної в кабінеті для вибірки сторінок"
    return out


def evaluate(row: dict, prev: dict | None) -> list:
    """Пороги → список текстів-порушень (порожньо = норма). Чиста функція, тестується офлайн."""
    out = []
    if row["total"] and not row["avail"]:
        out.append(f"у кабінеті {row['total']} позицій, але «В наявності» = 0 — вітрина порожня для покупця (алерт щодоби, поки так)")
    # Сторінки не читаються (404 / блок / інша розмітка): половина вибірки і більше випала — порушення, а не тиша (аудит #648 Д2).
    # Свіжостворені позиції 1–2 доби дають 404 на публічній сторінці (лаг Prom) — тому поріг 50%, не 0.
    if row.get("buy_err") and row.get("sample_n") and row["buy_err"] >= row["sample_n"] * 0.5:
        out.append(f"публічні сторінки не читаються: {row['buy_err']} з {row['sample_n']} вибірки без відповіді/схеми — кнопку покупки не перевірено")
    if row.get("buy_n") and row["buy_pct"] < BUY_MIN_PCT:
        out.append(f"кнопка покупки на публічних сторінках лише у {row['buy_pct']:.0f}% вибірки ({row['buy_ok']}/{row['buy_n']}) < {BUY_MIN_PCT:.0f}%")
    if row["avail"] and row["in_cpa_pct"] < CPA_MIN_PCT:
        out.append(f"у кампанії лише {row['in_cpa_pct']:.0f}% позицій «В наявності» ({row['in_cpa']}/{row['avail']}) < {CPA_MIN_PCT:.0f}% — без кампанії купити не можна")
    d = row.get("feed_delta_pct")
    if d is not None and (d > FEED_OVER_MAX_PCT or d < -FEED_UNDER_MAX_PCT):
        out.append(f"фід {row['feed_offers']} проти кабінету {row['avail']} (розбіжність {d:+.0f}%: "
                   + ("імпорт не створює нові позиції)" if d > 0 else "позиції на Prom лишились без фіду)"))
    if prev and prev.get("avail") and row["avail"] < prev["avail"] * (1 - AVAIL_DROP_MAX_PCT / 100):
        out.append(f"«В наявності» впало {prev['avail']} → {row['avail']} (>{AVAIL_DROP_MAX_PCT:.0f}% за добу)")
    return out


def evaluate_returns(row: dict) -> list:
    """Повернені позиції не доходять до публікації → приріст вітрини «паперовий». Бракує даних (None) — порушення не вигадуємо."""
    out = []
    t72, c72 = row.get("ret72_total"), row.get("ret72_in_cab")
    if t72 and t72 >= 100 and c72 is not None and 100.0 * c72 / t72 < RET_IN_CAB_MIN_PCT:
        out.append(f"з {t72} повернених >{RET_CHECK_HOURS} год тому у кабінеті лише {c72} ({100.0 * c72 / t72:.0f}% < {RET_IN_CAB_MIN_PCT:.0f}%) — картки не створюються, приріст вітрини паперовий")
    n, b = row.get("ret_pub_n"), row.get("ret_pub_buy")
    if n and n >= 10 and b is not None and 100.0 * b / n < RET_PUB_MIN_PCT:
        out.append(f"з вибірки повернених у кабінеті публічні з кнопкою лише {b}/{n} (404: {row.get('ret_pub_404')}) < {RET_PUB_MIN_PCT:.0f}%")
    return out


def evaluate_orders(row: dict, prev: dict | None):
    """(тиша_замовлень: список, подія_першого_замовлення: список). None → не вигадуємо."""
    silence, event = [], []
    d = row.get("days_since_order")
    if d is not None and d >= NO_ORDERS_DAYS:
        silence.append(f"на Prom {d} діб без замовлення (останнє: {row.get('last_order_at') or 'за ' + str(ORDERS_LOOKBACK_DAYS) + ' діб жодного'}), за 14 діб: {row.get('orders_14d')}")
    pd = (prev or {}).get("days_since_order")
    if d is not None and pd is not None and pd >= NO_ORDERS_DAYS and d < pd and d < NO_ORDERS_DAYS:
        event.append(f"Prom: ПЕРШЕ замовлення після розриву {pd} діб (останнє: {row.get('last_order_at')}). Ланцюг кампанія → вітрина → покупка працює.")
    return silence, event


def _prev_row():
    try:
        rows = list(csv.DictReader(open(CSV_FILE, encoding="utf-8")))
        r = rows[-1]
        dso = r.get("days_since_order")
        return {"avail": int(r["avail"]), "days_since_order": int(dso) if dso not in (None, "") else None}
    except Exception:  # noqa: BLE001
        return None


def _rotate_csv_if_header_changed():
    """Колонки змінилися (нові рядки воронки) — старий файл відкладаємо, щоб заголовок не розійшовся з даними."""
    try:
        if CSV_FILE.exists():
            first = CSV_FILE.read_text(encoding="utf-8").splitlines()[0]
            if first != ",".join(COLUMNS):
                CSV_FILE.rename(CSV_FILE.with_name(f"vitrine_funnel.{datetime.now():%Y%m%d%H%M%S}.old.csv"))
    except OSError as e:
        print(f"[Funnel] ротація CSV не вдалась: {e}", file=sys.stderr)


def _send(key, text, cooldown_sec):
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=cooldown_sec)
    except Exception as e:  # noqa: BLE001
        print(f"[Funnel] алерт {key} не надіслано: {e}", file=sys.stderr)


def record(cat: dict, sample_n: int = SAMPLE_N, alert: bool = True) -> dict:
    """cat — {sku: {...}} з prom_cabinet_catalog.fetch_full_catalog(). Дописує рядок у CSV, шле алерт при порушенні."""
    items = list(cat.values())
    avail_items = [v for v in items if v.get("presence") == "avail"]
    in_cpa = sum(1 for v in avail_items if v.get("in_running_cpa"))
    sample = random.sample(avail_items, min(sample_n, len(avail_items))) if avail_items else []
    with ThreadPoolExecutor(3) as ex:   # 8 потоків під навантаженням (інший скан з тієї ж IP) давали 42 з 60 таймаутів → хибне «не читаються»
        res = [r for r in ex.map(lambda v: page_buyable(v["view_catalog_url"]), sample) if r[0] is not None]
    buy_ok = sum(1 for instock, btn in res if instock and btn)
    feed = feed_offer_count()
    row = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(items), "avail": len(avail_items), "not_avail": len(items) - len(avail_items),
        "in_cpa": in_cpa, "in_cpa_pct": (100.0 * in_cpa / len(avail_items)) if avail_items else 0.0,
        "buy_ok": buy_ok, "buy_n": len(res), "buy_pct": (100.0 * buy_ok / len(res)) if res else 0.0,
        "sample_n": len(sample), "buy_err": len(sample) - len(res),
        "feed_offers": feed,
        "feed_delta_pct": (100.0 * (feed - len(avail_items)) / len(avail_items)) if (feed is not None and avail_items) else None,
    }
    row.update(last_orders())
    row.update(returned_fate(cat))
    prev = _prev_row()
    problems = evaluate(row, prev)
    ret_problems = evaluate_returns(row)
    silence, first_order = evaluate_orders(row, prev)
    row["alerts"] = " | ".join(problems + ret_problems + silence)
    print(f"[Funnel] усього {row['total']} · в наявності {row['avail']} · в кампанії {in_cpa} ({row['in_cpa_pct']:.0f}%) · "
          f"кнопка {buy_ok}/{len(res)} (без відповіді {row['buy_err']}) · фід {feed} · останнє замовлення: {row['last_order_at'] or row['orders_note'] or '—'} "
          f"({row['days_since_order']} діб) · повернені >24 год {row['ret_total']} (у кабінеті {row['ret_in_cab']}; публічні з кнопкою {row['ret_pub_buy']}/{row['ret_pub_n']}, 404: {row['ret_pub_404']}) {row['ret_note']}")
    # АЛЕРТИ ПЕРЕД записом CSV: заблокований/пошкоджений файл не повинен ковтати сигнал (аудит #648 Д3)
    if alert:
        if problems:
            _send("vitrine_funnel", "🔴 Воронка Prom-вітрини:\n• " + "\n• ".join(problems)
                  + "\nПокупець бачить не те, що в фіді. Деталі: reports/vitrine_funnel.csv", 24 * 3600)
        if ret_problems:
            _send("vitrine_returns", "🔴 Воронка Prom: повернені позиції\n• " + "\n• ".join(ret_problems), 24 * 3600)
        if silence:
            _send("vitrine_no_orders", "🔴 Воронка Prom: тиша замовлень\n• " + "\n• ".join(silence) + "\n(повторюється раз на 7 діб, поки так)", 7 * 24 * 3600)
        if first_order:
            _send("vitrine_first_order", "🟢 " + first_order[0], 3 * 24 * 3600)
    try:
        _rotate_csv_if_header_changed()
        CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
        new = not CSV_FILE.exists()
        with open(CSV_FILE, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow({k: (round(v, 1) if isinstance(v, float) else v) for k, v in row.items()})
    except OSError as e:
        print(f"[Funnel] CSV не записано: {e}", file=sys.stderr)
    return row


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from prom_cabinet_catalog import fetch_full_catalog
    record(fetch_full_catalog())
