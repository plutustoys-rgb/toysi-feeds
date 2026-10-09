# -*- coding: utf-8 -*-
"""vitrine_funnel.py — щоденна воронка Prom-вітрини від початку до кінця (машинна версія; людську/кабінетну веде Аудитор вітрин → аудит_вітрин/ВОРОНКА_PROM.md).

ЧОМУ (власник 09.10.2026, «чому через 3 місяці не можемо наповнити конкурентними товарами Prom»): за три місяці вітрина тричі ламалась без сигналу, бо міряли розмір ФІДУ, а не
результат: пул придатних 8 261 → 1 052 (17.08–08.10, наш же «храповик» delist), 08–09.10 вимкнена кампанія «за замовлення» → усі 3 463 з 3 468 «Недоступен» (кнопки покупки нема),
0 замовлень з 24.09. Тут міряємо ЩО БАЧИТЬ ПОКУПЕЦЬ:
  1. кабінет (повний каталог через CMS): усього / «В наявності» / в кампанії (`in_running_cpa` — без цього кнопки «Купити» нема, довідка Prom: «Якщо товари не додані до кампаній, їх не можна придбати»);
  2. ПУБЛІЧНІ сторінки (вибірка): schema.org InStock + кнопка `buy_now_btn`;
  3. наш фід: кількість `<offer>` у feed-data/feeds/prom_feed_top.xml проти кабінету.
Рядок → reports/vitrine_funnel.csv. Пороги → ОДИН Telegram-алерт на добу (send_throttled_alert; сито telegram_triage пропускає — нове джерело/текст «Воронка Prom»).
Викликається best-effort наприкінці prom_cabinet_catalog.summary() (задача PlutusToys_PromCatalogHistory, щодня); окремо: `python vitrine_funnel.py` (знімає кабінет сам).
READ-ONLY: лише GET.
"""
import csv
import random
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
CSV_FILE = BASE_DIR / "reports" / "vitrine_funnel.csv"
FEED_URL = "https://raw.githubusercontent.com/plutustoys-rgb/toysi-feeds/feed-data/feeds/prom_feed_top.xml"
SAMPLE_N = 30
UA = {"User-Agent": "Mozilla/5.0"}
COLUMNS = ["at", "total", "avail", "not_avail", "in_cpa", "in_cpa_pct", "buy_ok", "buy_n", "buy_err", "buy_pct", "feed_offers", "feed_delta_pct", "alerts"]

# Пороги (узгоджені з завданням Аудитора, EXECUTOR_CHANNEL 09.10.2026)
BUY_MIN_PCT = 90.0           # частка вибіркових публічних сторінок InStock + кнопка
CPA_MIN_PCT = 80.0           # частка «В наявності», що в кампанії
# Фід БІЛЬШИЙ за кабінет — це черга створення нових позицій на Prom (лаг 12–15 год після повернення/додавання), тому поріг широкий (+40%: імпорт не створює);
# фід МЕНШИЙ за кабінет — позиції лишаються на Prom без фіду (застарілі ціни/залишки), поріг вузький (−10%).
FEED_OVER_MAX_PCT = 40.0
FEED_UNDER_MAX_PCT = 10.0
AVAIL_DROP_MAX_PCT = 15.0    # падіння «В наявності» за добу


def page_buyable(url: str):
    """(InStock?, є кнопка?) для публічної сторінки; (None, None) — збій мережі (у вибірку не рахується)."""
    h = None
    for attempt in range(2):   # один повтор лише на мережевий збій/таймаут; 404 (свіжа картка ще не публічна) повторювати марно
        try:
            h = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25).read().decode("utf-8", "ignore")
            break
        except urllib.error.HTTPError:
            return None, None
        except Exception:  # noqa: BLE001 — мережевий збій ≠ «недоступний»
            time.sleep(1.5)
    if h is None:
        return None, None
    m = re.search(r'"availability":"http://schema.org/(\w+)"', h)
    if not m:
        return None, None   # схеми нема (зміна розмітки/https/блок) — це НЕ «недоступний»; рахується в buy_err (аудит #648)
    return m.group(1) == "InStock", ('data-qaid="buy_now_btn"' in h)


def feed_offer_count():
    try:
        x = urllib.request.urlopen(urllib.request.Request(FEED_URL, headers=UA), timeout=60).read().decode("utf-8", "ignore")
        return len(re.findall(r"<offer id=", x))
    except Exception:  # noqa: BLE001
        return None


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


def _prev_row():
    try:
        rows = list(csv.DictReader(open(CSV_FILE, encoding="utf-8")))
        r = rows[-1]
        return {"avail": int(r["avail"])}
    except Exception:  # noqa: BLE001
        return None


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
    problems = evaluate(row, _prev_row())
    row["alerts"] = " | ".join(problems)
    print(f"[Funnel] усього {row['total']} · в наявності {row['avail']} · в кампанії {in_cpa} ({row['in_cpa_pct']:.0f}%) · "
          f"кнопка {buy_ok}/{len(res)} (без відповіді {row['buy_err']}) · фід {feed}")
    # АЛЕРТ ПЕРЕД записом CSV: заблокований/пошкоджений файл не повинен ковтати сигнал (аудит #648 Д3)
    if problems and alert:
        try:
            from telegram_notify import send_throttled_alert
            send_throttled_alert("vitrine_funnel", "🔴 Воронка Prom-вітрини:\n• " + "\n• ".join(problems)
                                 + "\nПокупець бачить не те, що в фіді. Деталі: reports/vitrine_funnel.csv", cooldown_sec=24 * 3600)
        except Exception as e:  # noqa: BLE001
            print(f"[Funnel] алерт не надіслано: {e}", file=sys.stderr)
    try:
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
