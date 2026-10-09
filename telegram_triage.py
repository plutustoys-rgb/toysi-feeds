# -*- coding: utf-8 -*-
"""telegram_triage.py — що з автоматичних повідомлень реально іде у Telegram власника (рішення 09.10.2026).

ПРОБЛЕМА (власник: «ти зробив з Telegram смітник, купа спаму»; виміряно за 02–09.10 по reports/telegram_alerts.md: VPS 207 + десктоп 185 ≈ 55 повідомлень на
добу). Основні джерела: нескінченні «сесія протухла» EVA/ALLO (≈135 однакових), інформаційні звіти «автодеплой»/«скориговано цін»/«наповненість вітрини»/
«нічний скан»/«соцпостинг: 0 помилок» (≈160), усе — без дії для власника.

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
RULES = [
    # --- інформаційні, дії від власника не потребують ---
    ("watchdog_autodeploy", r"service_watchdog", r"^🚀 Watchdog PlutusToys: (автодеплой|фід-пайплайн)", "mute"),
    ("catalog_size_info", r"catalog_size_tracker", r"^📊 Наповненість вітрини", "mute"),
    ("pricer_apply_report", r"prom_competitor_pricer", r"^(💰|ℹ️) prom_competitor_pricer\.py --apply", "mute"),
    ("catalog_scan_nightly", r"full_catalog_competitor_scan", r"^🌙 Нічний скан каталогу", "mute"),
    ("social_post_ok", r"social_auto_poster", r"^📣 Соцпостинг:.*Помилок: 0\b", "mute"),
    ("eva_import_ok", r"eva_cabinet_scraper", r"^🟣 EVA повний імпорт .*подано", "mute"),
    # --- повторювані збої: першу появу показати, далі раз на добу ---
    ("pricer_circuit", r"prom_competitor_pricer", r"^🚨 prom_competitor_pricer\.py --apply: коригування ЦІНИ ЗУПИНЕНО", "throttle"),
    ("eva_session", r"eva_cabinet_scraper", r"^🚨 (eva_cabinet_scraper keepalive|eva повний імпорт не вдався)", "throttle"),
    ("allo_session", r"allo_cabinet_scraper", r"^🚨 allo ", "throttle"),
    ("prom_session", r"prom_notifications_scraper|prom_cabinet", r"^🚨 prom_\w+ keepalive", "throttle"),
    ("rozetka_price_timeout", r"rozetka_price_monitor", r"^⚠️ rozetka_price_monitor: тимчасовий таймаут", "throttle"),
    ("sysmap_drift", r"system_map_driftcheck", r"^🗺️ SYSTEM_MAP дрейф", "throttle"),
]
_COMPILED = [(i, re.compile(s), re.compile(t, re.S), a) for i, s, t, a in RULES]


def classify(source: str, text: str):
    """→ (rule_id, action) або (None, 'send'). Збіг по джерелу І по початку тексту."""
    src = source or ""
    txt = (text or "").lstrip()
    for rule_id, src_re, text_re, action in _COMPILED:
        if src_re.search(src) and text_re.search(txt):
            return rule_id, action
    return None, "send"
