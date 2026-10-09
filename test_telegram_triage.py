#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_telegram_triage.py — сито Telegram (telegram_triage.py + хук у telegram_notify.send_telegram_message).

Інваріанти:
  1. Класифікація: інформаційні → mute, повторювані збої СЕСІЇ/таймауту → throttle.
  2. ВАЖЛИВЕ НЕ ЗАГЛУШУЄТЬСЯ (аудит #643 B1–B3, M2, M3): збої автодеплою/фід-пайплайна watchdog (⛔/⏰/⚠️, «ПРОПУЩЕНА», «не звітував»),
     алерти catalog_size_tracker (🛑/📉), звіт прайсера з помилками/ЗАБЛОКОВАНО/масовим видаленням/виключенням через комісію, імпорт EVA без модерації,
     не-сесійні збої ALLO/EVA, замовлення/фіскалізація/КОДВ, невідомі джерела.
  3. Правило прив'язане до джерела; порожній argv ("?") → send.
  4. send_telegram_message: mute → HTTP не викликається, повертає True, журнал: перший рядок тіла = текст, позначка ПІСЛЯ (telegram_digest класифікує за
     першим рядком); throttle → перший іде, повтор у вікні ні, після вікна знову; вікно стартує лише від доставленого.
Самодостатній: мережа/журнал/стан — підмінені тимчасовими файлами.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

import telegram_notify as tn
import telegram_triage as tt

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_FAILS = []


def _check(name, got, exp):
    ok = got == exp
    print(f"[{'OK ' if ok else 'FAIL'}] {name}: got={got!r} exp={exp!r}")
    if not ok:
        _FAILS.append(name)


WD = "🚀 Watchdog PlutusToys: автодеплой\n\n"
WF = "🚀 Watchdog PlutusToys: фід-пайплайн VPS\n\n"
PR = "💰 prom_competitor_pricer.py --apply: "

CASES = [
    # --- mute: лише справді інформаційне ---
    ("service_watchdog.py", WD + "✅ Автодеплой: підтягнуто commit abcd1234 о 09.10.2026 09:00 (3 файл(ів) змінено)", "mute"),
    ("prom_competitor_pricer.py", PR + "скориговано цін — 12, видалено як неконкурентні — 3 товарів. Помилок: 0.", "mute"),
    ("prom_competitor_pricer.py", PR + "скориговано цін — 12, видалено як неконкурентні — 40 товарів (кап 40, відкладено 5 на наступний прогін). Помилок: 0.", "mute"),
    ("full_catalog_competitor_scan.py", "🌙 Нічний скан каталогу (2026-10-08, початкове покриття): просканировано 5", "mute"),
    ("social_auto_poster.py", "📣 Соцпостинг: 1 пост(ів) на ig. Помилок: 0.", "mute"),
    ("eva_cabinet_scraper.py", "🟣 EVA повний імпорт 2026-10-08 (APPLY): подано (чекбокс модерації True).", "mute"),
    # --- throttle: повторювані збої сесії/таймауту ---
    ("prom_competitor_pricer.py", "🚨 prom_competitor_pricer.py --apply: коригування ЦІНИ ЗУПИНЕНО circuit breaker", "throttle"),
    ("prom_competitor_pricer.py", "ℹ️ prom_competitor_pricer.py --apply: delist КАПІРОВАНО на 40 (кандидатів 900)", "throttle"),
    ("eva_cabinet_scraper.py", "🚨 eva_cabinet_scraper keepalive: сесія протухла/збій (сесію не прийнято — редірект)", "throttle"),
    ("eva_cabinet_scraper.py", "🚨 eva_cabinet_scraper keepalive: сесія протухла/збій (Page.goto: Timeout 30000ms exceeded)", "throttle"),
    ("eva_cabinet_scraper.py", "🚨 eva повний імпорт не вдався: сесію не прийнято — редірект на https://x", "throttle"),
    ("allo_cabinet_scraper.py", "🚨 allo автозіставлення не вдалось: сесію не прийнято — редірект на https://x", "throttle"),
    ("allo_cabinet_scraper.py", "🚨 allo подача на модерацію не вдалась: сесію не прийнято", "throttle"),
    ("prom_notifications_scraper.py", "🚨 prom_notifications_scraper keepalive: сесія протухла/збій", "throttle"),
    ("rozetka_price_monitor.py", "⚠️ rozetka_price_monitor: тимчасовий таймаут (Page.goto: Timeout 45000ms)", "throttle"),
    ("system_map_driftcheck.py", "🗺️ SYSTEM_MAP дрейф (linux):\n…", "throttle"),
    # --- throttle: той самий СТАН, що повторюється щопрогону ---
    ("prom_competitor_pricer.py", PR + "скориговано цін — 0 (ЗАБЛОКОВАНО circuit breaker), видалено як неконкурентні — 25 товарів. Помилок: 2.", "send"),
    ("prom_competitor_pricer.py", PR + "скориговано цін — 0, видалено як неконкурентні — 0 товарів, виключено через непідтверджену комісію — 5. Помилок: 0.", "throttle"),
    ("catalog_size_tracker.py", "📊 Наповненість вітрини — увага:\n🔻 PROM: 2749 товарів на вітрині — нижче підлогового порога 3000 (ціль 6000).\n\nПоточно: PROM: 2749", "throttle"),
    ("service_watchdog.py", "🚨 Watchdog PlutusToys: замовлення передане, але Toysi не підтверджує\n\n⛔ eva_8-082151972 (Toysi #100453005): непідтверджено 669 хв", "throttle"),
    ("service_watchdog.py", "✅ Watchdog PlutusToys: звірка з Toysi відновлена\n\n✅ eva_8-082151972 (Toysi #100453005): тепер підтверджено в Toysi", "throttle"),
    # --- send: усе важливе ---
    # аудит #643 F1: реальний суфікс ALLO/EVA «Якщо сесія протухла — --login» є в КОЖНОМУ збої; ознака сесії шукається ДО нього
    ("allo_cabinet_scraper.py", "🚨 allo автозіставлення не вдалось: кнопку не знайдено. Якщо сесія протухла — `python allo_cabinet_scraper.py --login`.", "send"),
    ("eva_cabinet_scraper.py", "🚨 eva повний імпорт не вдався: кнопка не знайдена. Якщо сесія протухла — `python eva_cabinet_scraper.py --login`.", "send"),
    ("allo_cabinet_scraper.py", "🚨 allo автозіставлення не вдалось: сесію не прийнято — редірект. Якщо сесія протухла — `--login`.", "throttle"),
    # F2: масове зняття з вітрини й помилки API йдуть завжди, навіть із ЗАБЛОКОВАНО / комісією
    ("prom_competitor_pricer.py", PR + "скориговано цін — 12, видалено як неконкурентні — 300 товарів, виключено через непідтверджену комісію — 4. Помилок: 9.", "send"),
    ("prom_competitor_pricer.py", PR + "скориговано цін — 0 (ЗАБЛОКОВАНО circuit breaker), видалено як неконкурентні — 249 товарів, виключено через непідтверджену комісію — 4. Помилок: 0.", "send"),
    ("prom_competitor_pricer.py", PR + "скориговано цін — 0 (ЗАБЛОКОВАНО circuit breaker), видалено як неконкурентні — 5 товарів. Помилок: 12.", "send"),
    # F3: allow-list — текст збою watchdog БЕЗ жодного з очікуваних маркерів не мутиться
    ("service_watchdog.py", WD + "Автодеплой завершився з помилкою: код 2", "send"),
    ("service_watchdog.py", WD + "❌ Автодеплой провалено", "send"),
    ("service_watchdog.py", WF + "✅ Фід-пайплайн VPS: публікація відновлена", "send"),
    ("service_watchdog.py", WD + "✅ Автодеплой: підтягнуто commit abcd1234 о 09:00\n\n⛔ Автодеплой не вдався: x", "send"),
    # 🔻 + 🛑/📉 в одному повідомленні — це не «просто нижче порога»
    ("catalog_size_tracker.py", "📊 Наповненість вітрини — увага:\n🔻 PROM: 2749 нижче порога 3000\n📉 вітрина впала 3000 → 2749 (−8%)", "send"),
    ("service_watchdog.py", WD + "⛔ Автодеплой не вдався: git pull конфлікт", "send"),
    ("service_watchdog.py", WD + "⛔ Автодеплой: vps-code-sync.timer не звітував 40 хв (поріг 30)", "send"),
    ("service_watchdog.py", WD + "⏰ Автодеплой ДОСІ не вдається: причина", "send"),
    ("service_watchdog.py", WD + "✅ Автодеплой: відновлено, VPS знову синхронізовано з master", "send"),
    ("service_watchdog.py", WD + "✅ Автодеплой: підтягнуто commit abcd1234 о 09:00\n\n⛔ Автодеплой не вдався: x", "send"),
    ("service_watchdog.py", WF + "⛔ фід-пайплайн: публікація ПРОПУЩЕНА — фід порожній", "send"),
    ("service_watchdog.py", WF + "⚠️ фід-пайплайн degraded", "send"),
    ("service_watchdog.py", WF + "⛔ feed-pipeline.timer не звітував 9 год", "send"),
    ("service_watchdog.py", "🚨 Watchdog PlutusToys: замовлення передане, але Toysi не підтверджує", "send"),
    ("service_watchdog.py", "🚨 Watchdog PlutusToys: сервіс(и) не відповідають", "send"),
    ("catalog_size_tracker.py", "📊 Наповненість вітрини — увага:\n🛑 фід ПОРОЖНІЙ/ВІДСУТНІЙ — вітрина зникла!", "send"),
    ("catalog_size_tracker.py", "📊 Наповненість вітрини — увага:\n📉 вітрина впала 3000 → 2000 (−33%)", "send"),
    ("prom_competitor_pricer.py", PR + "скориговано цін — 12, видалено як неконкурентні — 3 товарів. Помилок: 2.", "send"),
    ("prom_competitor_pricer.py", PR + "скориговано цін — 0 (ЗАБЛОКОВАНО circuit breaker), видалено як неконкурентні — 3 товарів. Помилок: 0.", "throttle"),
    ("prom_competitor_pricer.py", PR + "скориговано цін — 12, видалено як неконкурентні — 250 товарів. Помилок: 0.", "send"),
    ("prom_competitor_pricer.py", PR + "скориговано цін — 12, видалено як неконкурентні — 3 товарів, виключено через непідтверджену комісію — 7. Помилок: 0.", "throttle"),
    ("eva_cabinet_scraper.py", "🟣 EVA повний імпорт 2026-10-08 (APPLY): подано (чекбокс модерації False).", "send"),
    ("eva_cabinet_scraper.py", "🚨 eva повний імпорт не вдався: кнопку «Імпортувати» не знайдено", "send"),
    ("allo_cabinet_scraper.py", "🚨 allo автозіставлення не вдалось: кнопку не знайдено (змінилась верстка)", "send"),
    ("order_status_tracker.py", "🚨 Чек Checkbox не створено для замовлення prom_1", "send"),
    ("order_pipeline.py", "🚨 order_pipeline: токен протух", "send"),
    ("kandydaty_registry.py", "🔴 КОДВ: 3 відкритів кандидати висить довше 5 дн", "send"),
    ("prom_convergence_monitor.py", "⚠️ Prom-конвергенція: available ВПАВ 3000→2900 (-100) за день.", "send"),
    ("social_auto_poster.py", "📣 Соцпостинг: 1 пост(ів) на fb. Помилок: 2.", "send"),
    ("social_auto_poster.py", "⚠️ Соцпостинг: токен FB протух", "send"),
    ("daily_report.py", "📋 Щоденний звіт PlutusToys — 09.10.2026 09:00", "send"),
    ("kodv_mail_archiver.py", "🚨 kodv_mail_archiver: помилка архівації первинки КОДВ", "send"),
    # те саме тіло, інше джерело / порожній argv
    ("some_new_script.py", WD + "✅ Автодеплой: підтягнуто commit abcd1234", "send"),
    ("order_router.py", "🚨 allo автозіставлення не вдалось: сесію не прийнято", "send"),
    ("?", "🚨 allo автозіставлення не вдалось: сесію не прийнято", "send"),
]
for src, txt, exp in CASES:
    _check(f"classify {src} | {txt[:60]!r}", tt.classify(src, txt)[1], exp)

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


r = _as("social_auto_poster.py", "📣 Соцпостинг: 1 пост(ів) на ig. Помилок: 0.")
_check("4a. mute: повертає True", r, True)
_check("4b. mute: HTTP не викликався", len(posts), 0)
log = tn.ALERTS_LOG_FILE.read_text(encoding="utf-8")
_check("4c. mute: у журналі позначка правила", "[не надіслано в Telegram: правило social_post_ok]" in log, True)
_check("4d. mute: заголовок журналу без змін (для telegram_digest)", "— social_auto_poster.py\n" in log, True)
_check("4d2. mute: перший рядок тіла — текст, не позначка (digest класифікує за ним)",
       log.split("— social_auto_poster.py\n\n", 1)[1].startswith("📣 Соцпостинг"), True)

_as("kandydaty_registry.py", "🔴 КОДВ: важливе")
_check("4e. send: непридушене йде одразу", len(posts), 1)

n0 = len(posts)
_as("allo_cabinet_scraper.py", "🚨 allo автозіставлення не вдалось: сесію не прийнято")
_check("4f. throttle: перше йде", len(posts), n0 + 1)
_as("allo_cabinet_scraper.py", "🚨 allo подача на модерацію не вдалась: сесію не прийнято")
_check("4g. throttle: повтор (інший текст, те саме правило) у вікні не йде", len(posts), n0 + 1)
_as("allo_cabinet_scraper.py", "🚨 allo автозіставлення не вдалось: кнопку не знайдено")
_check("4g2. не-сесійний збій ALLO у вікні сесійного йде одразу", len(posts), n0 + 2)
st = json.loads(tn.ALERT_THROTTLE_FILE.read_text(encoding="utf-8"))
st["triage:allo_session"] = time.time() - 25 * 3600
tn.ALERT_THROTTLE_FILE.write_text(json.dumps(st), encoding="utf-8")
_as("allo_cabinet_scraper.py", "🚨 allo автозіставлення не вдалось: сесію не прийнято")
_check("4h. throttle: після 24 год знову йде", len(posts), n0 + 3)

# збій Telegram не «з'їдає» перше повідомлення
tn.ALERT_THROTTLE_FILE.write_text("{}", encoding="utf-8")
_ok["v"] = False
n1 = len(posts)
r = _as("eva_cabinet_scraper.py", "🚨 eva_cabinet_scraper keepalive: сесія протухла/збій")
_check("4i. збій Telegram → False, вікно не стартувало",
       (r, "triage:eva_session" in json.loads(tn.ALERT_THROTTLE_FILE.read_text(encoding="utf-8"))), (False, False))
_ok["v"] = True
_as("eva_cabinet_scraper.py", "🚨 eva_cabinet_scraper keepalive: сесія протухла/збій")
_check("4j. наступна спроба йде (вікно не стартувало від збою)", len(posts), n1 + 2)


# --- ключі: інший майданчик / інше замовлення / без id ---
_FL = "📊 Наповненість вітрини — увага:\n"
_prom = tt.classify("catalog_size_tracker.py", _FL + "🔻 PROM: 2749 нижче порога 3000")
_eva = tt.classify("catalog_size_tracker.py", _FL + "🔻 EVA: 0 нижче порога 4000")
_prom2 = tt.classify("catalog_size_tracker.py", _FL + "🔻 PROM: 2700 нижче порога 3000")
_check("5a. floor: PROM і EVA — різні ключі", _prom[0] != _eva[0], True)
_check("5b. floor: той самий майданчик — той самий ключ", _prom[0], _prom2[0])
_UC = "🚨 Watchdog PlutusToys: замовлення передане, але Toysi не підтверджує\n\n"
_o1 = tt.classify("service_watchdog.py", _UC + "⛔ eva_8-1 (Toysi #1): 10 хв")
_o2 = tt.classify("service_watchdog.py", _UC + "⛔ prom_2 (Toysi #2): 10 хв")
_o1b = tt.classify("service_watchdog.py", _UC + "⛔ eva_8-1 (Toysi #1): 700 хв")
_check("5c. watchdog: інше замовлення — інший ключ", _o1[0] != _o2[0], True)
_check("5d. watchdog: те саме замовлення — той самий ключ", _o1[0], _o1b[0])
_many = ["⛔ eva_%d (Toysi #1)" % i for i in range(30)]
_m1 = tt.classify("service_watchdog.py", _UC + "\n".join(_many))
_m2 = tt.classify("service_watchdog.py", _UC + "\n".join(_many + ["⛔ eva_99 (Toysi #1)"]))
_check("5e. масовий збій: новий id у великому наборі — інший ключ (sha1, не обрізання)", _m1[0] != _m2[0], True)

# --- F5: старі triage-ключі чистяться ---
tn.ALERT_THROTTLE_FILE.write_text(
    json.dumps({"triage:old": time.time() - 8 * 24 * 3600, "triage:fresh": time.time(), "other": 1}), encoding="utf-8")
tn._triage_mark_sent("x")
_st = json.loads(tn.ALERT_THROTTLE_FILE.read_text(encoding="utf-8"))
_check("5f. stale triage-ключ (>7 діб) видалено, свіжий і чужий лишились",
       (("triage:old" in _st), ("triage:fresh" in _st), ("other" in _st), ("triage:x" in _st)), (False, True, True, True))

print()
if _FAILS:
    print("FAILED:", _FAILS)
    sys.exit(1)
print("ALL OK")
