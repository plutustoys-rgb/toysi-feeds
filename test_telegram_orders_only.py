#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_telegram_orders_only.py — режим «лише надходження замовлень» + редакція секретів у Telegram (рішення власника 12.10.2026:
«видали усе, залиш лише інформацію о надходженні замовлень»; у чат потрапив сирий cookie кабінету Prom із тексту виключення Playwright).
Інваріанти: (1) у Telegram іде ТІЛЬКИ «🛒 Нове замовлення …»; усе інше не йде, але лишається в журналі з позначкою; (2) секрети (Cookie/Authorization
заголовки, auth=/token=/api_key=…, Bearer) прибираються і з відправки, і з журналу; (3) notify_new_order без ПІБ/телефону/адреси й не кидає виняток;
(4) TELEGRAM_ORDERS_ONLY=0 повертає старе сито. Мережа/журнал замокані. `python test_telegram_orders_only.py` → exit 0/1."""
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import telegram_notify as tn
import telegram_triage as tt

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


tmp = Path(tempfile.mkdtemp())
tn.ALERTS_LOG_FILE = tmp / "telegram_alerts.md"
tn.ALERT_THROTTLE_FILE = tmp / ".alert_throttle.json"
tn.TELEGRAM_BOT_TOKEN, tn.TELEGRAM_CHAT_ID = "T", "C"
posts = []


class _Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"ok": True}


tn.requests.post = lambda url, json=None, timeout=0: (posts.append(json["text"]) or _Resp())
os.environ.pop("TELEGRAM_ORDERS_ONLY", None)   # дефолт = увімкнено


def reset():
    posts.clear()
    if tn.ALERTS_LOG_FILE.exists():
        tn.ALERTS_LOG_FILE.unlink()


def log():
    return tn.ALERTS_LOG_FILE.read_text(encoding="utf-8") if tn.ALERTS_LOG_FILE.exists() else ""


NL = chr(10)
QQ = chr(34)
COOKIE_ALERT = ("🚨 Prom-конвергенція: не зміг зчитати каталог — timeout кабінету: Page.goto: Timeout"+NL+"Call log:"+NL+
                "  - navigating to "+QQ+"https://my.prom.ua/cms/product"+QQ+NL+
                "    - cookie: auth=36057fb4e8112a04edf113ab28923b9e31d9d0ae; evoauth=w36e0f1778ef74d669378e9690fe03436; "
                "cid=2004375262283108217787700328005096496; client_id=c-KC4HdKHM1rlM1duBQ4hQDHFK"+NL+
                "    - user-agent: Mozilla/5.0"+NL+"ПРИЧИНА: найімовірніше протухла кабінетна сесія Prom.")

# ── (1) лише замовлення
reset()
chk("надходження замовлення → ЙДЕ в Telegram", tn.send_telegram_message("🛒 Нове замовлення · prom №123 · 250 грн · 2 поз.") is True and len(posts) == 1)
reset()
for txt in ("🚨 eva_cabinet_scraper keepalive: сесія протухла", "⚠️ orders_watcher: замовлення 1 НЕ оброблено", "🛑 site_T1: замовлення сайту НЕ передано в Toysi",
            "💳 LiqPay: site_T1 має статус wait_accept", "🔴 Чек Checkbox НЕ видано для prom_1", "📊 звіт", "щось невідоме"):
    r = tn.send_telegram_message(txt)
    chk(f"НЕ замовлення → у Telegram не йде, повертає True: {txt[:32]}", r is True and not posts)
chk("усе заглушене лишилось у журналі з позначкою режиму", log().count("[не надіслано в Telegram: режим «лише замовлення»]") == 7)

# ── (2) секрети
reset()
tn.send_telegram_message(COOKIE_ALERT)
lg = log()
chk("cookie-алерт: не йде в Telegram", not posts)
chk("cookie-алерт: у журналі НЕМАЄ значень auth/evoauth/cid", "36057fb4" not in lg and "w36e0f1778" not in lg and "2004375262283" not in lg)
chk("cookie-алерт: у журналі лишилась суть (причина, підказка)", "ПРИЧИНА" in lg and "[прибрано]" in lg)
reset()
tn.send_telegram_message("🛒 Нове замовлення · site №T1 · 100 грн · 1 поз. token=abc123secret Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
chk("секрет усередині дозволеного повідомлення прибирається і з відправки", posts and "abc123secret" not in posts[0] and "abcdefghijklmnop" not in posts[0])
red = tn._redact
chk("redact: api_key=…, password=…, csrf_token=…", all(v not in red(f"x {k}={v} y") for k, v in (("api_key", "SEKRET1"), ("password", "hunter2"), ("csrf_token", "01adae092c"))))
chk("redact: Set-Cookie і Authorization-рядки з продовженням", "AAAA" not in red("Set-Cookie: sid=AAAA; Path=/\n  BBBB\nok") and "BBBB" not in red("Set-Cookie: sid=AAAA; Path=/\n  BBBB\nok") and red("ok\nnext").endswith("next"))
chk("redact: звичайний текст не чіпається", red("Замовлення prom_414634349: сума 39,00 грн; ТТН 20450000000000") == "Замовлення prom_414634349: сума 39,00 грн; ТТН 20450000000000")
chk("redact: порожнє/None", red("") == "" and red(None) is None)

# ── (3) notify_new_order
reset()
tn.notify_new_order("rozetka", 905840122, 108.0, 3, "cod")
chk("notify_new_order: формат, сума, к-ть позицій, оплата", posts == ["🛒 Нове замовлення · rozetka №905840122 · 108 грн · 3 поз. · накладений платіж"])
reset()
tn.notify_new_order("eva", "8-1", "н/д", 1, "")
chk("notify_new_order: нечислова сума не валить", len(posts) == 1 and "сума невідома" in posts[0])
tn.requests.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("мережа лягла"))
try:
    tn.notify_new_order("prom", 1, 10, 1, "cod")
    chk("notify_new_order не кидає виняток при збої відправки", True)
except Exception:
    chk("notify_new_order не кидає виняток при збої відправки", False)
tn.requests.post = lambda url, json=None, timeout=0: (posts.append(json["text"]) or _Resp())

# ── (4) вимкнення режиму → старе сито
os.environ["TELEGRAM_ORDERS_ONLY"] = "0"
reset()
tn.send_telegram_message("щось невідоме")
chk("TELEGRAM_ORDERS_ONLY=0: невідоме йде як раніше (старе сито)", len(posts) == 1)
os.environ.pop("TELEGRAM_ORDERS_ONLY", None)
chk("is_order_arrival: тільки початок «🛒 Нове замовлення»", tt.is_order_arrival("🛒 Нове замовлення · x") and not tt.is_order_arrival("текст 🛒 Нове замовлення") and not tt.is_order_arrival(""))

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
