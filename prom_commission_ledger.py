"""prom_commission_ledger.py — комісія Prom по кожному замовленню → кандидати графи 9 КОДВ.

Задача власника 2026-08-29: «треба збирати комісію Prom по кожному замовленню». Методологія
НЕ вигадана — вона повністю задокументована в `КОДВ_норми_довідник.md` §3 (звірено живо 12/12
замовлень, 100% збіг з кабінетом Prom, запис журналу "(21)"). Цей скрипт її автоматизує.

ДЖЕРЕЛО (Prom Orders API, `GET /orders/list`, Authorization: Bearer PROM_API_KEY):
  Комісія Prom у графу 9 = сума до 3 компонентів (усе прямо в об'єкті замовлення):
  1. order.cpa_commission.amount — база «Комісія за замовлення». ЗАВЖДИ. (is_refunded — прапорець
     повернення: віддаємо його бухгалтеру, методологічне рішення «чинна чи ні» — за роллю КОДВ.)
  2. order.payment_data.rpay_parts.commission_amount — надбавка «Оплатити частинами»
     (лише коли payment_option.name містить «частинами/частями»).
  3. «Дешева доставка»: якщо order.ps_promotion.name містить «Дешева/Дешевая доставка» —
     10 грн (ціна 200-700) або 30 грн (>700); API прапорець дає, суму рахуємо порогом самі.
  НЕ включаємо «Комісію за післяплату» (COD) — її платить ОТРИМУВАЧ, не наша витрата.
  Комісія переказу NovaPay — ОКРЕМО (з реєстру NovaPay, інший скрипт); тут лише комісія Prom.

ВИХІД: кандидати у документи_КОДВ/YYYY-MM/Prom/ (як Rozetka/EVA-леджери). Книгу НЕ пише —
графу 9 пише лише роль «агент-бухгалтер» (податковий документ). Крос-звірка з книгою READ-ONLY.

ТОКЕН: потрібен PROM_API_KEY зі scope «замовлення». Поточний локальний ключ може бути лише
products-only (тоді /orders дає 401) — скрипт це ловить і М'ЯКО виходить (код 0, ланцюг не валить),
з чіткою вимогою поставити order-scope токен. Живий фетч дзеркалить перевірений orders_watcher.py.
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

BASE_DIR = Path(__file__).parent
COWORK_DIR = Path(os.environ.get(
    "PLUTUS_COWORK_DIR", r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
KODV_XLSX = COWORK_DIR / "KODV_PlutusToys_2026.xlsx"
CURSOR_FILE = BASE_DIR / ".local_secrets" / "prom_commission_cursor.json"

PROM_API_KEY = os.environ.get("PROM_API_KEY", "")
PROM_API_URL = "https://my.prom.ua/api/v1"
REQUEST_TIMEOUT = 30
LOOKBACK_DAYS = int(os.environ.get("PROM_COMMISSION_LOOKBACK_DAYS", "30"))
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

# Пороги «Дешевої доставки» (§3 довідника): продавець платить з балансу Prom.
CHEAP_DELIVERY_LOW = 10.0    # ціна замовлення 200-700 грн
CHEAP_DELIVERY_HIGH = 30.0   # ціна замовлення > 700 грн


def _log(msg: str) -> None:
    print(f"[PromCommission] {msg}")


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        sys.path.insert(0, str(BASE_DIR))
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[PromCommission] Telegram не надіслано: {e}", file=sys.stderr)


def _load_cursor() -> dict:
    try:
        return json.loads(CURSOR_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cursor(processed_ids: list) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(
        json.dumps({"processed_ids": sorted(set(processed_ids)),
                    "updated_at": datetime.now().isoformat(timespec="seconds")},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


class PromAuthError(Exception):
    """Токен без scope замовлень (401) чи інша відмова авторизації."""


def fetch_prom_orders() -> list:
    """GET /orders/list за LOOKBACK_DAYS, пагінація через last_id (дзеркало orders_watcher).
    Кидає PromAuthError на 401 (products-only токен)."""
    date_from = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    orders, last_id = [], None
    while True:
        params = {"date_from": date_from, "limit": 100}
        if last_id is not None:
            params["last_id"] = last_id
        r = requests.get(f"{PROM_API_URL}/orders/list",
                         headers={"Authorization": f"Bearer {PROM_API_KEY}"},
                         params=params, timeout=REQUEST_TIMEOUT)
        if r.status_code == 401:
            raise PromAuthError("401 Not Authenticated — токен без scope «замовлення»")
        r.raise_for_status()
        page = (r.json() or {}).get("orders", [])
        if not page:
            break
        orders.extend(page)
        if len(page) < 100:
            break
        last_id = page[-1]["id"]
    return orders


def _to_float(v) -> float:
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else 0.0
    except (ValueError, TypeError):
        return 0.0


def _commission_components(o: dict) -> dict:
    """3 компоненти комісії Prom за §3 довідника. Повертає розкладку + суму."""
    cpa = o.get("cpa_commission") or {}
    base = _to_float(cpa.get("amount"))
    is_refunded = bool(cpa.get("is_refunded"))

    parts = 0.0
    pay_opt = ((o.get("payment_option") or {}).get("name") or "").lower()
    if "частин" in pay_opt or "частям" in pay_opt:
        rpay = ((o.get("payment_data") or {}).get("rpay_parts") or {})
        parts = _to_float(rpay.get("commission_amount"))

    cheap = 0.0
    promo = ((o.get("ps_promotion") or {}).get("name") or "").lower()
    if "дешев" in promo and "доставк" in promo:
        price = _to_float(o.get("price"))
        if price > 700:
            cheap = CHEAP_DELIVERY_HIGH
        elif price >= 200:
            cheap = CHEAP_DELIVERY_LOW
    total = round(base + parts + cheap, 2)
    return {"base": round(base, 2), "is_refunded": is_refunded, "parts": round(parts, 2),
            "cheap_delivery": cheap, "total": total}


def _lookup_book_row(order_id: str) -> dict:
    """READ-ONLY крос-звірка: шукає «№<order_id>» у графі 5 (колонка E) книги КОДВ.
    Повертає {row, current_i9, e_text} або {}. Книгу НЕ пише (правило власника)."""
    if not KODV_XLSX.exists():
        return {}
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(KODV_XLSX), data_only=True, read_only=True)
        ws = wb["КОДВ"]
        needle = f"№{order_id}"
        for row in ws.iter_rows(min_row=7):
            e = row[4].value if len(row) > 4 else None
            if e and needle in str(e):
                return {"row": row[4].row,
                        "current_i9": row[8].value if len(row) > 8 else None,
                        "e_text": str(e)}
    except Exception as e:  # noqa: BLE001 — крос-звірка не критична
        print(f"[PromCommission] крос-звірка з книгою не вдалась (не критично): {e}", file=sys.stderr)
    return {}


def collect() -> tuple:
    cursor = _load_cursor()
    processed = set(cursor.get("processed_ids", []))
    is_first_run = "processed_ids" not in cursor

    orders = fetch_prom_orders()
    all_ids = [str(o.get("id")) for o in orders if o.get("id") is not None]

    if is_first_run:
        _log(f"Перший запуск — базова лінія: {len(all_ids)} замовлень за {LOOKBACK_DAYS} дн "
             f"вважаю вже опрацьованими (історія в книзі), кандидатів не шукаю.")
        return [], all_ids

    candidates = []
    for o in orders:
        oid = str(o.get("id"))
        if oid in processed:
            continue
        comp = _commission_components(o)
        if comp["base"] <= 0:
            # База «Комісія за замовлення» присутня ЗАВЖДИ (§3 довідника). base<=0 = Prom ще НЕ
            # проставив комісію (перші хвилини після створення замовлення) — не кандидат і НЕ
            # фіксуємо в курсорі, щоб узяти на наступному прогоні, коли база з'явиться. Гейт саме
            # по base (не по total): інакше замовлення з уже проставленою «дешевою доставкою», але
            # ще нульовою базою, закурсорилось би з base=0 і базу вже не підхопило б (ниточка аудиту).
            continue
        book = _lookup_book_row(oid)
        candidates.append({
            "order_id": oid,
            "price": _to_float(o.get("price")),
            "status": o.get("status"),
            "payment": ((o.get("payment_option") or {}).get("name")),
            "commission_base": comp["base"],
            "commission_is_refunded": comp["is_refunded"],
            "commission_parts": comp["parts"],
            "cheap_delivery": comp["cheap_delivery"],
            "commission_total": comp["total"],
            "book_row": book.get("row"),
            "book_current_i9": book.get("current_i9"),
            "book_e_text": book.get("e_text"),
        })
        processed.add(oid)
    return candidates, sorted(processed)


def _write_report(candidates: list) -> Path:
    today = datetime.now()
    month_dir = COWORK_DIR / "документи_КОДВ" / today.strftime("%Y-%m") / "Prom"
    month_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{today.strftime('%Y-%m-%d')}_prom_komisiya_kandydaty"

    (month_dir / f"{stem}.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# Prom — комісія по замовленнях (графа 9), автоматично, {today.strftime('%Y-%m-%d %H:%M')}",
             "",
             "Джерело: Prom Orders API `GET /orders/list` → `cpa_commission.amount` (база) + «Оплатити",
             "частинами» + «Дешева доставка» (§3 `КОДВ_норми_довідник.md`, звірено 12/12). Це КАНДИДАТИ",
             "графи 9 — крос-звірка з книгою READ-ONLY, книгу НЕ змінено. «Післяплату» не включаємо",
             "(платить покупець); комісію переказу NovaPay додає окремий облік реєстрів.",
             "⚠️ `refunded=так` — Prom повернув комісію: рішення «чинна для графи 9 чи ні» — за КОДВ.",
             "",
             "| Замовлення | Ціна | Оплата | База | Частинами | Дешева дост. | Разом графа9 | refunded | Рядок книги |",
             "|---|---|---|---|---|---|---|---|---|"]
    for c in candidates:
        row = c["book_row"] if c["book_row"] else "❗ НЕ в книзі"
        lines.append(f"| №{c['order_id']} | {c['price']} | {c['payment'] or '—'} | {c['commission_base']} | "
                     f"{c['commission_parts']} | {c['cheap_delivery']} | {c['commission_total']} | "
                     f"{'так' if c['commission_is_refunded'] else 'ні'} | {row} |")
    (month_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return month_dir / f"{stem}.md"


def main() -> None:
    if not PROM_API_KEY:
        _log("PROM_API_KEY не задано — пропускаю (вихід 0).")
        return
    dry_run = "--dry-run" in sys.argv
    try:
        candidates, processed_ids = collect()
    except PromAuthError as e:
        # 401 = поточний PROM_API_KEY без scope «замовлення» (відома «ще не налаштовано»). М'який
        # вихід (0) + лог, БЕЗ щоденного Telegram-спаму — ланцюг задач не валимо. Активується
        # автоматично, щойно в .env з'явиться order-scope токен.
        _log(f"{e} — потрібен order-scope PROM_API_KEY у .env (поточний лише products). "
             f"Пропускаю (вихід 0), активується сам після додавання токена.")
        return
    except (requests.exceptions.RequestException, OSError) as e:
        _notify(f"🚨 prom_commission_ledger: помилка збору комісії Prom: {e}")
        _log(f"помилка: {e}")
        sys.exit(1)

    if not candidates:
        if not dry_run:
            _save_cursor(processed_ids)
        _log("Нових замовлень з комісією немає (або перший запуск — базова лінія).")
        return

    if dry_run:
        _log(f"[dry-run] БУЛО Б {len(candidates)} кандидатів комісії; курсор не рухаю, файли не пишу.")
        for c in candidates:
            _log(f"  [dry-run] №{c['order_id']}: разом {c['commission_total']} "
                 f"(база {c['commission_base']}, частинами {c['commission_parts']}, "
                 f"дешева {c['cheap_delivery']}, refunded={c['commission_is_refunded']})")
        return

    path = _write_report(candidates)
    _save_cursor(processed_ids)
    _log(f"ГОТОВО: {len(candidates)} кандидатів комісії → {path}")


if __name__ == "__main__":
    main()
