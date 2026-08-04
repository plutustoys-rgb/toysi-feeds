"""
eva_catalog_auditor.py — щоденний аудит EVA-каталогу (STATUS 2026-07-31, пряме
прохання власника: «аудитори для кожного з майданчиків»). Дзеркало патерну
prom_catalog_auditor.py, але з EVA-специфічними перевірками, ЩО ВИВОДЯТЬСЯ ЛИШЕ
з опублікованого фіда + каталогу Toysi (без крихкого скрейпінгу кабінету —
кабінет-залежні перевірки, як статус мапінгу категорій/модерації, — окремим
заходом через prom_cabinet_scraper-патерн, коли знадобиться).

Перевірки (усі feed-derivable, надійні, автоматичні):
1. Збиткові ціни — EVA-ціна ×1.45 БЕЗ floor (свідоме рішення власника), тож дешеві
   товари продаються нижче собівартості+комісії. Рахуємо, СКІЛЬКИ активних товарів
   дають net_revenue < cost і сумарну експозицію (найважливіше — гроші).
2. Дублікати name_ua — EVA авто-модерація відхиляє однакові назви; фід дедупить
   (_dedup_key), але аудит підтверджує, що жоден дубль не просочився.
3. Здоров'я деактивації OOS — скільки товарів у фіді available="false" (PR #209):
   тренд «скільки вітрини зараз недоступно».
4. Наповненість — активних vs м'яка ціль (перетин із catalog_size_tracker — тут
   лише як рядок звіту, без дубльованого алерту).

Звіт — reports/eva_catalog_audit_YYYY-MM-DD.md + Telegram-підсумок. Read-only,
жодних дій запису (той самий принцип, що prom_catalog_auditor).
"""
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from competitor_pricing import real_toysi_cost, compute_total_commission
from parser import fetch_toysi_catalog
from telegram_notify import send_telegram_message

BASE_DIR = Path(__file__).parent
FEED_FILE = BASE_DIR / "feeds" / "eva_feed.xml"
# Куди писати звіт. За замовч. reports/ (VPS). AUDIT_REPORT_DIR перевизначає —
# локальний прогін (Task Scheduler) пише ПРЯМО у спільну Cowork-папку, щоб звіт
# читався без SSH (рішення власника 2026-08-04, «без телеграма, у папку»).
REPORT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (BASE_DIR / "reports"))
# AUDIT_NO_TELEGRAM=1 — не слати Telegram (локальна доставка у папку замість Telegram).
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

EVA_TARGET_SOFT = 8000          # м'яка ціль наповненості (як у catalog_size_tracker)
LOSS_EXAMPLES = 10              # скільки прикладів збиткових показати у звіті

# group(1) = атрибути відкриваючого тегу (id/available живуть тут), group(2) = тіло
_OFFER_RE = re.compile(r"<offer\b([^>]*)>(.*?)</offer>", re.DOTALL)
_ID_RE = re.compile(r'\bid="(\d+)"')
_AVAIL_RE = re.compile(r'\bavailable\s*=\s*"([^"]*)"')
_PRICE_RE = re.compile(r"<price>([\d.]+)</price>")
_NAME_RE = re.compile(r"<name_ua>(.*?)</name_ua>", re.DOTALL)


def parse_feed(path: Path) -> list:
    """Повертає список {id, price, name_ua, available} з eva_feed.xml. Відсутній/
    порожній фід → [] (сам факт «фіда нема» ловить catalog_size_tracker, не тут)."""
    try:
        xml = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    offers = []
    for m in _OFFER_RE.finditer(xml):
        attrs, body = m.group(1), m.group(2)
        id_m = _ID_RE.search(attrs)
        if not id_m:
            continue
        avail_m = _AVAIL_RE.search(attrs)
        price_m = _PRICE_RE.search(body)
        name_m = _NAME_RE.search(body)
        offers.append({
            "id": id_m.group(1),
            "price": float(price_m.group(1)) if price_m else None,
            "name_ua": (name_m.group(1).strip() if name_m else ""),
            "available": (avail_m.group(1) if avail_m else "true"),
        })
    return offers


def check_loss_making(offers: list, catalog: dict) -> dict:
    """Активні товари, де чиста виручка (ціна − комісія EVA) < собівартості Toysi.
    ×1.45 без floor → дешеві товари збиткові. Повертає {count, checked, total_loss,
    examples}."""
    commission = compute_total_commission("eva")  # EVA_COMMISSION_TOYS + оплата (0.15)
    losers, total_loss, checked = [], 0.0, 0
    for o in offers:
        if o["available"] != "true" or o["price"] is None:
            continue
        item = catalog.get(o["id"])
        if item is None:
            continue
        try:
            cost = real_toysi_cost(item)
        except (ValueError, TypeError):
            continue
        if cost <= 0:
            continue
        checked += 1
        net = o["price"] * (1 - commission)
        if net < cost:
            loss = round(cost - net, 2)
            total_loss += loss
            losers.append((o["id"], o["name_ua"][:40], o["price"], round(cost, 2), loss))
    losers.sort(key=lambda x: -x[4])  # найбільший збиток першим
    return {"count": len(losers), "checked": checked, "total_loss": round(total_loss, 2),
            "examples": losers[:LOSS_EXAMPLES]}


def check_duplicate_names(offers: list) -> list:
    """Дублікати name_ua серед АКТИВНИХ (EVA авто-модерація відхиляє однакові назви)."""
    counts = Counter(o["name_ua"] for o in offers if o["available"] == "true" and o["name_ua"])
    return [(name, n) for name, n in counts.most_common() if n > 1]


def check_deactivation(offers: list) -> dict:
    active = sum(1 for o in offers if o["available"] == "true")
    deactivated = sum(1 for o in offers if o["available"] == "false")
    return {"active": active, "deactivated": deactivated}


def build_report(today: str, loss: dict, dups: list, deact: dict) -> str:
    lines = [f"# Аудит EVA-каталогу — {today}\n"]
    lines.append(f"**Наповненість вітрини:** {deact['active']} активних"
                 + (f" | {deact['deactivated']} деактивовано (OOS, available=false)" if deact['deactivated'] else "")
                 + (f" ⚠️ нижче м'якої цілі {EVA_TARGET_SOFT}" if deact['active'] < EVA_TARGET_SOFT else "") + "\n")

    lines.append("## 1. Збиткові ціни (×1.45 без floor)")
    if loss["count"]:
        pct = loss["count"] * 100 // max(1, loss["checked"])
        lines.append(f"🔴 **{loss['count']} із {loss['checked']}** активних ({pct}%) продаються В ЗБИТОК "
                     f"(net_revenue < собівартість). Сумарна експозиція збитку: **{loss['total_loss']} грн**.")
        lines.append("\nНайбільші (id | назва | ціна | собівартість | збиток/шт):")
        for oid, name, price, cost, l in loss["examples"]:
            lines.append(f"- `{oid}` {name} — {price} грн, cost {cost}, **−{l} грн**")
        lines.append("\n_Це наслідок прямої націнки ×1.45 без floor (свідоме рішення власника). "
                     "Аудит лише підсвічує масштаб — рішення про floor/виключення дешевих за власником._")
    else:
        lines.append("✅ Збиткових активних товарів не знайдено.")

    lines.append("\n## 2. Дублікати name_ua")
    if dups:
        lines.append(f"🟠 {len(dups)} назв повторюються (EVA авто-модерація може відхилити):")
        for name, n in dups[:15]:
            lines.append(f"- «{name[:50]}» × {n}")
    else:
        lines.append("✅ Дублікатів назв немає (дедуп фіда працює).")

    lines.append(f"\n## 3. Деактивація OOS\n{deact['deactivated']} товарів у фіді з available=\"false\" "
                 "(розпродані/випали, autoimport переводить у «Неактивні»).")
    return "\n".join(lines)


def build_telegram_summary(today: str, loss: dict, dups: list, deact: dict) -> str:
    parts = [f"🔍 Аудит EVA-каталогу {today}:"]
    parts.append(f"вітрина {deact['active']}"
                 + (f" (−{deact['deactivated']} деактив.)" if deact['deactivated'] else ""))
    if loss["count"]:
        parts.append(f"🔴 збиткових: {loss['count']}/{loss['checked']} (експозиція {loss['total_loss']} грн)")
    else:
        parts.append("✅ збиткових нема")
    if dups:
        parts.append(f"🟠 дублів назв: {len(dups)}")
    return "\n".join(parts)


def main() -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    offers = parse_feed(FEED_FILE)
    if not offers:
        print("[EVA-audit] eva_feed.xml порожній/відсутній — аудит пропущено (розмір ловить catalog_size_tracker).",
              file=sys.stderr)
        return

    print(f"[EVA-audit] Завантажую каталог Toysi для перевірки собівартості...")
    catalog = fetch_toysi_catalog()

    loss = check_loss_making(offers, catalog)
    dups = check_duplicate_names(offers)
    deact = check_deactivation(offers)

    report = build_report(today, loss, dups, deact)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"eva_catalog_audit_{today}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"[EVA-audit] Звіт: {out_path}")
    print(f"[EVA-audit] Активних {deact['active']} | збиткових {loss['count']}/{loss['checked']} | дублів {len(dups)}")

    if not _NO_TELEGRAM:
        send_telegram_message(build_telegram_summary(today, loss, dups, deact))


if __name__ == "__main__":
    main()
