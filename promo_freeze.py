"""promo_freeze.py — заморозка ціни SKU на час акції Prom (напр. «Сезонні знижки»).

НАВІЩО (2026-08-26, наказ власника — вхід у «Сезонні знижки» Prom): акція вимагає, щоб
БАЗОВА ціна товару НЕ мінялась протягом вікна. Дослівно з довідки Prom: «якщо ціна товару
зміниться, цей товар перестане брати участь… ми фіксуємо ціну товару — при імпорті ціна та
знижка оновлюватись не будуть». Наш репрайсер міняє ціни щопрогону → без заморозки SKU
вилетить з акції на першому ж прогоні. Тут — реєстр {pid: заморожена_ціна + вікно}: у вікні
фід тримає заморожену ціну (через `price_overrides`, тим самим floor-гардом, що й репрайсерні
override), після `until` — авто-розморозка (репрайсер повертається сам, без ручного кроку).

Реєстр `promo_freeze.json`: `{"pid": {"price": float, "until": "YYYY-MM-DD", "note": str}}`.
`price` — знімок ціни в момент enrollment (`enroll_skus`). ВАЖЛИВО (маржа > участі в акції):
floor-гард у фіді лишається — якщо собівартість Toysi зросла й заморожена ціна впала нижче
беззбиткового floor, фід підніме ціну до floor (SKU вилетить з акції, але НЕ опублікується
в збиток). Тобто заморозка ніколи не публікує нижче собівартості.
"""
import json
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROMO_FREEZE_FILE = Path(__file__).parent / "promo_freeze.json"


def _load_raw() -> dict:
    """Сирий реєстр. Відсутній/битий файл → порожньо (заморозок нема, не валимо фід)."""
    try:
        d = json.loads(PROMO_FREEZE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load_active_freeze(today: date = None) -> dict:
    """{pid(str): заморожена_ціна(float)} для АКТИВНИХ (today <= until) записів.
    Прострочені (today > until) — авто-розморозка (не повертаємо). Биті записи / службові
    ключі (`_note`) — тихо пропускаємо."""
    today = today or date.today()
    out = {}
    for pid, e in _load_raw().items():
        if not isinstance(e, dict):
            continue
        try:
            until = date.fromisoformat(str(e.get("until"))[:10])
            price = float(e.get("price"))
        except (ValueError, TypeError):
            continue
        # опційне "from": дозволяє enroll НАПЕРЕД (запис лежить, але заморозка вмикається
        # лише з дати старту акції — до неї репрайсер оптимізує SKU вільно). Битий from →
        # не морозимо (безпечний дефолт: краще не заморозити, ніж заморозити не в той час).
        frm = e.get("from")
        if frm:
            try:
                if today < date.fromisoformat(str(frm)[:10]):
                    continue
            except (ValueError, TypeError):
                continue
        if price > 0 and today <= until:
            out[str(pid)] = price
    return out


def enroll_skus(prices: dict, until: str, from_date: str = None, note: str = "") -> int:
    """Записати/оновити заморозки. prices={pid: ціна-знімок}, until='YYYY-MM-DD',
    from_date (опц.)='YYYY-MM-DD' — заморозка вмикається лише з цієї дати (enroll наперед).
    Валідує дати. Повертає к-сть заморожених (ціна>0)."""
    date.fromisoformat(until)  # валідація (кине ValueError, якщо криво)
    if from_date:
        date.fromisoformat(from_date)
    reg = _load_raw()
    added = 0
    for pid, price in prices.items():
        p = round(float(price), 2)
        if p <= 0:
            continue
        entry = {"price": p, "until": until, "note": note}
        if from_date:
            entry["from"] = from_date
        reg[str(pid)] = entry
        added += 1
    PROMO_FREEZE_FILE.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    return added
