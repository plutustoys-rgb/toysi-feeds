# -*- coding: utf-8 -*-
"""telegram_triage.py — що з автоматичних повідомлень реально іде у Telegram власника (рішення 09.10.2026).

ПРОБЛЕМА (власник: «ти зробив з Telegram смітник, купа спаму»; виміряно за 02–09.10 по reports/telegram_alerts.md: VPS 207 + десктоп 185 ≈ 55 повідомлень на
добу). Основні джерела: нескінченні «сесія протухла» EVA/ALLO (≈135 однакових), інформаційні «автодеплой ✅»/чисті звіти прайсера/«нічний скан»/«соцпостинг: 0 помилок» (≈100), усе — без дії для власника.

РІШЕННЯ — центральне сито в `telegram_notify.send_telegram_message` (єдина точка, через яку йде кожне повідомлення):
  • MUTE     — чисто інформаційне повідомлення: у Telegram НЕ йде, але лишається в `reports/telegram_alerts.md` (журнал для агентів/дайджесту) з позначкою.
  • THROTTLE — повторюваний збій: у Telegram іде ПЕРШЕ повідомлення, далі не частіше ніж раз на COOLDOWN (24 год) на правило.
  • усе інше — як і раніше, одразу (замовлення, гроші, фіскалізація, КОДВ, нові класи збоїв: невідоме = показати, не ховати).
Правила — ЛИШЕ явний список нижче. Нове джерело спаму = новий рядок тут, а не «вимкнути алерти». Повернення значення True для заглушених — свідоме: виклик
«доставку» не вимагав, а виклики з ретраєм (`service_watchdog`) інакше зациклились би на повторах.
"""
import re

COOLDOWN_SEC = 24 * 60 * 60

# (id, regex джерела (sys.argv[0] basename), regex ПЕРШОГО рядка/усього тексту, дія)
_FAIL = r"(?!.*(⛔|⏰|⚠️|🚨|🛑|🔻|📉|не вдав|ПРОПУЩЕН|degraded|не звітував|ДОСІ|відновлено|знову звітує))"
RULES = [
    # --- інформаційні, дії від власника не потребують (СУВОРО: будь-яка ознака збою → send) ---
    # ✅-повідомлення watchdog («підтягнуто commit …»). Шапка однакова і для збою — збій відсікає _FAIL (аудит #643 B1).
    ("watchdog_autodeploy", r"service_watchdog", r"^🚀 Watchdog PlutusToys: (автодеплой|фід-пайплайн)" + _FAIL, "mute"),
    # лише «чистий» звіт прайсера: цін скориговано N (не ЗАБЛОКОВАНО), видалено <100, без виключень через комісію, Помилок: 0 (аудит #643 B3)
    ("pricer_apply_report", r"prom_competitor_pricer",
     r"^💰 prom_competitor_pricer\.py --apply: скориговано цін — \d+, видалено як неконкурентні — \d{1,2} товарів"
     r"(?: \(кап[^)]*\))?\. Помилок: 0\.\s*$", "mute"),
    ("catalog_scan_nightly", r"full_catalog_competitor_scan", r"^🌙 Нічний скан каталогу", "mute"),
    ("social_post_ok", r"social_auto_poster", r"^📣 Соцпостинг: [^\n]*Помилок: 0\.\s*$", "mute"),
    ("eva_import_ok", r"eva_cabinet_scraper", r"^🟣 EVA повний імпорт [^\n]*подано \(чекбокс модерації True\)", "mute"),
    # --- повторювані збої: першу появу показати, далі раз на добу (лише ПРО СЕСІЮ/таймаут — інші причини йдуть одразу, аудит #643 M2) ---
    ("pricer_circuit", r"prom_competitor_pricer", r"^🚨 prom_competitor_pricer\.py --apply: коригування ЦІНИ ЗУПИНЕНО", "throttle"),
    ("pricer_delist_cap", r"prom_competitor_pricer", r"^ℹ️ prom_competitor_pricer\.py --apply: delist КАПІРОВАНО", "throttle"),
    ("eva_session", r"eva_cabinet_scraper",
     r"^🚨 (eva_cabinet_scraper keepalive|eva повний імпорт не вдався)[^\n]*(сесі[юя] не прийнято|протухла|Timeout)", "throttle"),
    ("allo_session", r"allo_cabinet_scraper", r"^🚨 allo [^\n]*(сесі[юя] не прийнято|протухла)", "throttle"),
    ("prom_session", r"prom_notifications_scraper|prom_cabinet", r"^🚨 prom_\w+ keepalive", "throttle"),
    ("rozetka_price_timeout", r"rozetka_price_monitor", r"^⚠️ rozetka_price_monitor: тимчасовий таймаут", "throttle"),
    # той самий стан повторюється щопрогону (виміряно 02–09.10: 15+15 однакових звітів прайсера, 27 «нижче підлогового порога», 20+18 про ОДНЕ замовлення)
    ("pricer_blocked_report", r"prom_competitor_pricer", r"^💰 prom_competitor_pricer\.py --apply: скориговано цін — 0 \(ЗАБЛОКОВАНО", "throttle"),
    ("pricer_commission_skipped", r"prom_competitor_pricer", r"^💰 prom_competitor_pricer\.py --apply: [^\n]*виключено через непідтверджену комісію", "throttle"),
    ("catalog_floor", r"catalog_size_tracker", r"^📊 Наповненість вітрини — увага:\s*\n🔻[^🛑📉]*$", "throttle"),
    # ключ — id замовлень у тексті: НОВЕ замовлення = новий ключ = іде одразу; те саме — раз на добу
    ("watchdog_toysi_unconfirmed", r"service_watchdog", r"^🚨 Watchdog PlutusToys: замовлення передане, але Toysi не підтверджує", "throttle"),
    ("watchdog_toysi_recovered", r"service_watchdog", r"^✅ Watchdog PlutusToys: звірка з Toysi відновлена", "throttle"),
    ("sysmap_drift", r"system_map_driftcheck", r"^🗺️ SYSTEM_MAP дрейф", "throttle"),
]
_COMPILED = [(i, re.compile(s), re.compile(t, re.S), a) for i, s, t, a in RULES]


_KEYED = {"watchdog_toysi_unconfirmed", "watchdog_toysi_recovered"}
_ORDER_ID_RE = re.compile(r"\b(?:prom|rozetka|eva|allo|site)_[\w-]+")


def classify(source: str, text: str):
    """→ (rule_id, action) або (None, 'send'). Збіг по джерелу І по початку тексту.
    Для правил з ключем (_KEYED) rule_id несе набір id замовлень з тексту: інше замовлення = інший ключ вікна тиші."""
    src = source or ""
    txt = (text or "").lstrip()
    for rule_id, src_re, text_re, action in _COMPILED:
        if src_re.search(src) and text_re.search(txt):
            if rule_id in _KEYED:
                ids = sorted(set(_ORDER_ID_RE.findall(txt)))
                if not ids:
                    return None, "send"  # без id замовлення ключа нема — не ховати
                rule_id = f"{rule_id}:{','.join(ids)}"[:150]
            return rule_id, action
    return None, "send"
