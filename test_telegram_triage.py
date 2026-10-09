#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_telegram_triage.py — сито Telegram (telegram_triage.py + хук у telegram_notify.send_telegram_message).

Інваріанти:
  1. Класифікація на ДОСЛІВНИХ першіх рядках з реальних журналів (VPS+десктоп, 02–09.10.2026): інформаційні → mute, повторювані збої → throttle.
  2. ВАЖЛИВЕ НЕ ЗАГЛУШУЄТЬСЯ: замовлення/фіскалізація/КОДВ/«Toysi не підтверджує»/нові збої невідомого класу → send.
  3. Те саме повідомлення з ІНШОГО джерела не заглушується (правило прив'язане до джерела).
  4. send_telegram_message: mute → HTTP не викликається, повертає True, журнал отримує позначку; throttle → перший іде, повтор у вікні ні, після вікна знову;
     вікно стартує лише від доставленого (збій Telegram не «з'їдає» перше повідомлення).
Самодостатній: мережа/журнал/стан — підмінені тимчасовими файлами.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

import telegram_notify as tn
import telegram_triage as tt

_FAILS = []


def _check(name, got, exp):
    ok = got == exp
    print(f"[{'OK ' if ok else 'FAIL'}] {name}: got={got!r} exp={exp!r}")
    if not ok:
        _FAILS.append(name)


# ── 1–3. Класифікація ────────────────────────────────────────────────────────
CASES = [
    ("service_watchdog.py", "🚀 Watchdog PlutusToys: автодеплой — master підтягнуто", "mute"),
    ("service_watchdog.py", "🚀 Watchdog PlutusToys: фід-пайплайн VPS запущено", "mute"),
    ("catalog_size_tracker.py", "📊 Наповненість вітрини — увага:\nПрогноз…", "mute"),
    ("prom_competitor_pricer.py", "💰 prom_competitor_pricer.py --apply: скориговано цін — 12 (ЗАБЛОКОВАНО 3)", "mute"),
    ("prom_competitor_pricer.py", "ℹ️ prom_competitor_pricer.py --apply: delist КАПІРОВАНО на 40", "mute"),
    ("full_catalog_competitor_scan.py", "🌙 Нічний скан каталогу (2026-10-08, початкове покриття): просканировано 5", "mute"),
    ("social_auto_poster.py", "📣 Соцпостинг: 1 пост(ів) на ig. Помилок: 0.", "mute"),
    ("eva_cabinet_scraper.py", "🟣 EVA повний імпорт 2026-10-08 (APPLY): подано (чекбокс модерації True).", "mute"),
    ("prom_competitor_pricer.py", "🚨 prom_competitor_pricer.py --apply: коригування ЦІНИ ЗУПИНЕНО circuit breaker", "throttle"),
    ("eva_cabinet_scraper.py", "🚨 eva_cabinet_scraper keepalive: сесія протухла/збій (сесію не прийнято)", "throttle"),
    ("eva_cabinet_scraper.py", "🚨 eva повний імпорт не вдався: сесію не прийнято — редірект", "throttle"),
    ("allo_cabinet_scraper.py", "🚨 allo автозіставлення не вдалось: сесію не прийнято — редірект", "throttle"),
    ("allo_cabinet_scraper.py", "🚨 allo подача на модерацію не вдалась: сесію не прийнято", "throttle"),
    ("prom_notifications_scraper.py", "🚨 prom_notifications_scraper keepalive: сесія протухла/збій", "throttle"),
    ("rozetka_price_monitor.py", "⚠️ rozetka_price_monitor: тимчасовий таймаут (Page.goto: Timeout 45000ms)", "throttle"),
    ("system_map_driftcheck.py", "🗺️ SYSTEM_MAP дрейф (linux):\n…", "throttle"),
    # НЕ заглушувати:
    ("service_watchdog.py", "🚨 Watchdog PlutusToys: замовлення передане, але Toysi не підтверджує", "send"),
    ("service_watchdog.py", "🚨 Watchdog PlutusToys: сервіс(и) не відповідають", "send"),
    ("order_status_tracker.py", "🚨 Чек Checkbox не створено для замовлення prom_1", "send"),
    ("order_pipeline.py", "🚨 order_pipeline: токен протух", "send"),
    ("kandydaty_registry.py", "🔴 КОДВ: 3 відкритів кандидати висить довше 5 дн", "send"),
    ("prom_convergence_monitor.py", "⚠️ Prom-конвергенція: available ВПАВ 3000→2900 (-100) за день.", "send"),
    ("social_auto_poster.py", "📣 Соцпостинг: 1 пост(ів) на fb. Помилок: 2.", "send"),
    ("daily_report.py", "📋 Щоденний звіт PlutusToys — 09.10.2026 09:00", "send"),
    ("kodv_mail_archiver.py", "🚨 kodv_mail_archiver: помилка архівації первинки КОДВ", "send"),
    # те саме тіло, інше джерело — правило не спрацьовує
    ("some_new_script.py", "🚀 Watchdog PlutusToys: автодеплой", "send"),
    ("order_router.py", "🚨 allo автозіставлення не вдалось", "send"),
]
for src, txt, exp in CASES:
    _check(f"classify {src} | {txt[:48]}", tt.classify(src, txt)[1], exp)

# ── 4. Хук у send_telegram_message ───────────────────────────────────────────
tmp = Path(tempfile.mkdtemp())
tn.ALERTS_LOG_FILE = tmp / "telegram_alerts.md"
tn.ALERT_THROTTLE_FILE = tmp / ".alert_throttle.json"
tn.TELEGRAM_BOT_TOKEN, tn.TELEGRAM_CHAT_ID = "t", "c"

posts = []
_ok = {"v": True}


class _Resp:
    def __init__(self, ok):
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise tn.requests.exceptions.ConnectionError("down")

    def json(self):
        return {"ok": True}


def _fake_post(url, json=None, timeout=None):
    posts.append(json["text"])
    return _Resp(_ok["v"])


tn.requests.post = _fake_post


def _as(src, text):
    old = sys.argv
    sys.argv = [src]
    try:
        return tn.send_telegram_message(text)
    finally:
        sys.argv = old


r = _as("catalog_size_tracker.py", "📊 Наповненість вітрини — увага: x")
_check("4a. mute: повертає True", r, True)
_check("4b. mute: HTTP не викликався", len(posts), 0)
log = tn.ALERTS_LOG_FILE.read_text(encoding="utf-8")
_check("4c. mute: у журналі позначка правила", "[не надіслано в Telegram: правило catalog_size_info]" in log, True)
_check("4d. mute: заголовок журналу без змін (для telegram_digest)", "— catalog_size_tracker.py\n" in log, True)

_as("kandydaty_registry.py", "🔴 КОДВ: важливе")
_check("4e. send: непридушене йде одразу", len(posts), 1)

n0 = len(posts)
_as("allo_cabinet_scraper.py", "🚨 allo автозіставлення не вдалось: сесію не прийнято")
_check("4f. throttle: перше йде", len(posts), n0 + 1)
_as("allo_cabinet_scraper.py", "🚨 allo подача на модерацію не вдалась: сесію не прийнято")
_check("4g. throttle: повтор (інший текст, те саме правило) у вікні не йде", len(posts), n0 + 1)
st = json.loads(tn.ALERT_THROTTLE_FILE.read_text(encoding="utf-8"))
st["triage:allo_session"] = time.time() - 25 * 3600
tn.ALERT_THROTTLE_FILE.write_text(json.dumps(st), encoding="utf-8")
_as("allo_cabinet_scraper.py", "🚨 allo автозіставлення не вдалось: сесію не прийнято")
_check("4h. throttle: після 24 год знову йде", len(posts), n0 + 2)

# збій Telegram не «з'їдає» перше повідомлення
tn.ALERT_THROTTLE_FILE.write_text("{}", encoding="utf-8")
_ok["v"] = False
n1 = len(posts)
r = _as("eva_cabinet_scraper.py", "🚨 eva_cabinet_scraper keepalive: сесія протухла/збій")
_check("4i. збій Telegram → False, вікно не стартувало", (r, "triage:eva_session" in json.loads(tn.ALERT_THROTTLE_FILE.read_text(encoding="utf-8"))), (False, False))
_ok["v"] = True
_as("eva_cabinet_scraper.py", "🚨 eva_cabinet_scraper keepalive: сесія протухла/збій")
_check("4j. наступна спроба йде (вікно не стартувало від збою)", len(posts), n1 + 2)

print()
if _FAILS:
    print("FAILED:", _FAILS)
    sys.exit(1)
print("ALL OK")
