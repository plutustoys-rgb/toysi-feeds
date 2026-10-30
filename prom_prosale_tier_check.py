#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prom_prosale_tier_check.py — звірка PROSALE_TIER у коді з ЖИВИМ режимом кампанії «Товари з комісією за замовлення» у кабінеті Prom.

НАВІЩО (30.10.2026, власник: «переходимо на економ»; SEO просила цей запобіжник ще у серпні): `competitor_pricing.PROSALE_TIER` множить комісію Prom
(econom ×0,5) у флорі/ціні. Якщо кабінет НЕ на цьому режимі (рівень «топ-продавець» втрачено → Prom сам повернув «Звичайний»; кампанію вимкнено; перемкнули вручну),
код рахує флор із половиною реальної комісії → ціни нижчі за беззбиткові, і ми дізнаємось з падіння маржі. 09.09.2026 саме це й сталося (стартовий «Економ» згорів).
Цей скрипт читає режим живцем і піднімає тривогу з ПРИЧИНОЮ («кабінет каже X, код каже Y, флор помиляється»).

ЯК ЧИТАЄМО: сторінка my.prom.ua/cms/prosale, рядок кампанії «Товари з комісією за замовлення» має 5 кнопок із data-qaid: disable=Вимкнено, lessSales=Економ,
enable=Звичайний, moreSales=Більше продажів, turbo=Турбо. Активна — та, чий набір CSS-класів відрізняється від решти чотирьох (хеші класів Prom міняє, тож за іменем клас не шукаємо).
Не вдалось прочитати однозначно (не рівно 1 відмінна кнопка, нема рядка, сесія протухла) = ТРИВОГА «не можу перевірити», а не мовчазне «ок».
ТРИВОГА = запис [Автомат → Код] угорі EXECUTOR_CHANNEL.md (раз на добу на тему) + код виходу 1 (щоденний аудит додає крок у failedSteps) + Telegram, якщо не AUDIT_NO_TELEGRAM=1.
READ-ONLY (лише навігація і читання; нічого не клікаємо). Кабінетна сесія: .local_secrets/prom_cabinet_state.json (лише десктоп).
`python prom_prosale_tier_check.py` → exit 0 (збіг), 1 (розбіжність/не прочитано). `--no-notify` — без запису в канал і Telegram.
"""
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
STATE_FILE = Path(os.environ.get("PROM_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "prom_cabinet_state.json")))
URL = "https://my.prom.ua/cms/prosale"
CAMPAIGN_TITLE = "Товари з комісією за замовлення"
QAID_TO_TIER = {"disable": "off", "lessSales": "econom", "enable": "standard", "moreSales": "more_sales", "turbo": "turbo"}
NAV_TIMEOUT_MS = 60000
DEFAULT_REPORT_DIR = r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\reports"
CHANNEL_FILE = Path(os.environ.get("PROSALE_TIER_CHANNEL_FILE") or (Path(os.environ.get("AUDIT_REPORT_DIR") or DEFAULT_REPORT_DIR).parent / "EXECUTOR_CHANNEL.md"))
MARK = "[Автомат → Код] ProSale:"


def parse_active_mode(buttons):
    """buttons: [(data-qaid, className), …] одного рядка кампанії. Повертає tier або None, якщо однозначно не визначити
    (активною вважаємо єдину кнопку, чий набір класів не збігається з набором більшості)."""
    sets = [(q, frozenset((c or "").split())) for q, c in buttons if q in QAID_TO_TIER]
    if len(sets) != 5:
        return None
    common, n = Counter(s for _q, s in sets).most_common(1)[0]
    if n != 4:
        return None
    odd = [q for q, s in sets if s != common]
    return QAID_TO_TIER[odd[0]] if len(odd) == 1 else None


def read_live_tier():
    """Живий режим кампанії «за замовлення» → (tier|None, пояснення)."""
    from playwright.sync_api import sync_playwright
    if not STATE_FILE.exists():
        return None, f"нема кабінетної сесії ({STATE_FILE.name}) — `prom_notifications_scraper.py --login`"
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            ctx = b.new_context(storage_state=str(STATE_FILE))
            pg = ctx.new_page()
            pg.goto(URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            try:
                pg.wait_for_selector(f"text={CAMPAIGN_TITLE}", timeout=NAV_TIMEOUT_MS)
                pg.wait_for_selector("[data-qaid='lessSales']", timeout=NAV_TIMEOUT_MS)
            except Exception:
                return None, "рядок кампанії не з'явився (сесія протухла чи сторінка змінилась)"
            rows = pg.evaluate("""(title) => {
                const link = [...document.querySelectorAll('a')].find(a => a.textContent.trim() === title);
                if (!link) return null;
                let row = link;
                for (let i = 0; i < 10 && row; i++) { if (row.querySelectorAll('button[data-qaid]').length >= 5) break; row = row.parentElement; }
                if (!row) return null;
                return [...row.querySelectorAll('button[data-qaid]')].map(b => [b.getAttribute('data-qaid'), String(b.className)]);
            }""", CAMPAIGN_TITLE)
        finally:
            b.close()
    if not rows:
        return None, "рядок кампанії або її кнопки режимів не знайдено в DOM"
    tier = parse_active_mode(rows)
    return (tier, "") if tier else (None, f"активний режим однозначно не визначено ({len(rows)} кнопок)")


def post_to_channel(title: str, body: str, now: datetime, channel: Path | None = None) -> bool:
    """Запис [Автомат → Код] угорі EXECUTOR_CHANNEL.md (вставка ПЕРЕД першим рядком, що ПОЧИНАЄТЬСЯ з «## »; атомарно). Не частіше ніж раз на добу на тему.
    Недоступний канал → False без винятку."""
    channel = Path(channel or CHANNEL_FILE)
    try:
        with open(channel, encoding="utf-8", newline="") as f:
            s = f.read()
    except OSError:
        return False
    head = f"## {MARK} {title} {now:%Y-%m-%d}"
    if re.search(r"(?m)^" + re.escape(head), s):
        return False
    nl = "\r\n" if "\r\n" in s else "\n"
    entry = f"{head}\n{body}\n\n".replace("\n", nl)
    m = re.search(r"(?m)^## ", s)
    new = (s[:m.start()] + entry + s[m.start():]) if m else (s + nl + entry)
    try:
        tmp = channel.with_name(channel.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        os.replace(tmp, channel)
    except OSError:
        return False
    return True


def _notify(title: str, msg: str) -> None:
    """Канал Коду (основний шлях; щоденний аудит ганяється з AUDIT_NO_TELEGRAM=1) + Telegram, якщо не вимкнено."""
    if not post_to_channel(title, msg, datetime.now()):
        print("[ProSaleTier] запис у канал не зроблено (вже є за сьогодні або канал недоступний)", file=sys.stderr)
    if os.environ.get("AUDIT_NO_TELEGRAM") == "1":
        return
    try:
        sys.path.insert(0, str(BASE_DIR))
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001
        print(f"[ProSaleTier] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def main() -> int:
    sys.path.insert(0, str(BASE_DIR))
    import competitor_pricing as cp
    code_tier = cp.PROSALE_TIER
    try:
        live, why = read_live_tier()
    except Exception as e:  # noqa: BLE001
        live, why = None, f"{type(e).__name__}: {str(e)[:120]}"
    if live is None:
        msg = (f"🚨 ProSale-режим: не зміг прочитати кабінет — {why}. Код рахує за PROSALE_TIER={code_tier}; "
               f"звірити режим «Товари з комісією за замовлення» у my.prom.ua/cms/prosale вручну.")
        print(f"[ProSaleTier] {msg}", file=sys.stderr)
        if "--no-notify" not in sys.argv:
            _notify("не можу прочитати режим кабінету", msg)
        return 1
    if live == code_tier:
        print(f"[ProSaleTier] ок: кабінет = код = {live}")
        return 0
    mult = cp.PROSALE_TIER_MULTIPLIER
    if live in mult and code_tier in mult:
        effect = f"множник комісії в коді ×{mult[code_tier]}, у кабінеті ×{mult[live]}"
        if mult[code_tier] < mult[live]:
            effect += " — код недооцінює комісію, флор занижений, ціни можуть бути збитковими"
        else:
            effect += " — код переоцінює комісію, флор завищений, ціни дорожчі за потрібне"
    else:
        effect = "режим поза таблицею множників"
    msg = (f"🚨 ProSale-режим розійшовся: кабінет каже «{live}», код каже «{code_tier}»: {effect}. "
           f"Привести PROSALE_TIER у competitor_pricing.py до кабінету (або кабінет до коду).")
    print(f"[ProSaleTier] {msg}", file=sys.stderr)
    if "--no-notify" not in sys.argv:
        _notify(f"розбіжність: кабінет {live}, код {code_tier}", msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
