# -*- coding: utf-8 -*-
"""site_rejected_baskets.py — ОБЛІК кошиків сайту, які сервер відхиляє fail-closed (22.10.2026, прохання Консультанта).

НАВІЩО: фронт обіцяє «кілька посилок» і пускає оформлення, а `site_order_api` відхиляє змішаний кошик Toysi+RoyalToys і rt-позицію з
накладеним платежем — ПІСЛЯ того, як покупець ввів ПІБ/телефон/адресу. Раніше OrderError летів без обліку, тож невідомо: один випадок на місяць
чи щоденна втрата. Рішення «будувати поділ замовлень між постачальниками чи лишити “оформіть окремо”» має спиратись на ЦЕ число, а не на здогад.

ЩО ЛОГУЄМО (БЕЗ персональних даних): дата-час, вид відхилення, кількість рядків і штук, сума кошика, скільки рядків rt-/Toysi. Ні ПІБ, ні телефону,
ні адреси, ні email, ні назв товарів, ні IP. Це СПРОБИ (запити, що дійшли до сервера), а не унікальні покупці: повторна спроба того ж покупця
рахується окремо — для рішення важливий порядок величини.

best-effort: збій запису НІКОЛИ не змінює відповідь покупцю (виняток ковтається).
Файл — JSONL поряд з кодом (gitignored; на VPS /opt/plutustoys), перевизначається SITE_REJECTED_BASKETS_FILE.
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

KIND_MIXED = "mixed_toysi_rt"      # змішаний кошик Toysi + RoyalToys
KIND_RT_COD = "rt_cod"             # rt-позиція + накладений (або невідомий) спосіб оплати
KIND_BLOCKED = "mixed_blocked_cart"   # змішаний кошик, заблокований ще В КОШИКУ (22.10, Консультант): до сервера /api/order такий кошик більше не доходить, тож фронт повідомляє про нього окремо (POST /api/basket_blocked)
KIND_TITLES = {KIND_MIXED: "змішаний Toysi+RT", KIND_RT_COD: "RT + накладений", KIND_BLOCKED: "змішаний, заблоковано в кошику"}


MAX_FILE_BYTES = int(os.environ.get("SITE_REJECTED_BASKETS_MAX_BYTES", "5000000"))   # ≈ 35 тис. подій (≈140 Б на рядок); звичайний потік — одиниці на добу


def _file() -> Path:
    return Path(os.environ.get("SITE_REJECTED_BASKETS_FILE") or (Path(__file__).resolve().parent / "site_rejected_baskets.jsonl"))


def log_rejected(kind: str, items) -> None:
    """Дописати одну подію. `items` — нормалізовані позиції кошика ({toysi_code, qty, price}). Ніколи не кидає виняток."""
    try:
        items = [i for i in (items or []) if isinstance(i, dict)]
        n_rt = sum(1 for i in items if str(i.get("toysi_code") or "").lower().startswith("rt-"))
        n_zb = sum(1 for i in items if str(i.get("toysi_code") or "").lower().startswith("zb-"))
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "n_lines": len(items),
            "qty": sum(int(i.get("qty") or 0) for i in items),
            "total": round(sum(float(i.get("price") or 0) * int(i.get("qty") or 0) for i in items), 2),
            "n_rt": n_rt,
            "n_toysi": len(items) - n_rt - n_zb,
        }
        if n_zb:
            rec["n_zb"] = n_zb
        path = _file()
        if path.exists() and path.stat().st_size >= MAX_FILE_BYTES:   # захист від роздування (ротації нема, load() читає файл повністю): понад ліміт нові події не пишемо, відповідь покупцю не змінюється
            print(f"[site_rejected_baskets] файл ≥ {MAX_FILE_BYTES} Б — подію не записано (ротація/архів — вручну)", file=sys.stderr)
            return
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 — облік не має ламати прийом замовлень
        print(f"[site_rejected_baskets] не записано ({type(e).__name__})", file=sys.stderr)


def load(since: datetime = None) -> list:
    out = []
    try:
        with open(_file(), encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                    ts = datetime.fromisoformat(r["ts"])
                except (ValueError, KeyError, TypeError):
                    continue
                if since is None or ts >= since:
                    out.append((ts, r))
    except OSError:
        pass
    return out


def summary(now: datetime = None) -> dict:
    """{kind: {'7d': n, '30d': n, 'total7': сума ₴ за 7 діб, 'last': ISO або None}} для обох видів + 'since' (перша подія у файлі)."""
    now = now or datetime.now()
    d7, d30 = now - timedelta(days=7), now - timedelta(days=30)
    res = {k: {"7d": 0, "30d": 0, "total7": 0.0, "last": None} for k in KIND_TITLES}
    rows = load()
    for ts, r in rows:
        s = res.get(r.get("kind"))
        if s is None:
            continue
        if ts >= d30:
            s["30d"] += 1
        if ts >= d7:
            s["7d"] += 1
            s["total7"] += float(r.get("total") or 0)
        if s["last"] is None or ts.isoformat() > s["last"]:
            s["last"] = ts.isoformat(timespec="seconds")
    res["since"] = min((ts for ts, _ in rows), default=None)
    return res


def report_line(now: datetime = None) -> str:
    """Рядок для щоденного звіту. Завжди повертає текст (навіть при нулях — щоб «0» було видно, а не мовчання)."""
    s = summary(now)
    parts = []
    for kind, title in KIND_TITLES.items():
        k = s[kind]
        part = f"{title}: {k['7d']} за 7 діб ({k['30d']} за 30)"
        if k["7d"]:
            part += f", на {k['total7']:.0f} грн"
        parts.append(part)
    since = s["since"].strftime("%d.%m") if s.get("since") else "обліку ще нема подій"
    return ("\n\n🛒 Відхилені кошики сайту (спроби, без персональних даних): " + "; ".join(parts) +
            f". Облік веде site_rejected_baskets (перша подія: {since}).")
