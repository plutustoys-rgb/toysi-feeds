# -*- coding: utf-8 -*-
"""telegram_triage.py — що з автоматичних повідомлень реально іде у Telegram власника (рішення 09.10.2026).

ПРОБЛЕМА (власник: «ти зробив з Telegram смітник, купа спаму»; виміряно за 02–09.10 по reports/telegram_alerts.md: VPS 208 + десктоп 185 за 8 діб ≈ 50 повідомлень на
добу). Основні джерела: нескінченні «сесія протухла» EVA/ALLO, звіти прайсера з тим самим станом щопрогону, «нижче підлогового порога» щопрогону, одне й те саме
непідтверджене замовлення по 20 разів, інформаційні «автодеплой ✅»/«нічний скан»/«соцпостинг: 0 помилок».

РІШЕННЯ — центральне сито в `telegram_notify.send_telegram_message` (єдина точка, через яку йде кожне повідомлення):
  • MUTE     — чисто інформаційне повідомлення: у Telegram НЕ йде, але лишається в `reports/telegram_alerts.md` (журнал для агентів/дайджесту) з позначкою.
  • THROTTLE — повторюваний СТАН: у Telegram іде ПЕРШЕ повідомлення, далі не частіше ніж раз на COOLDOWN (24 год) на ключ правила.
               Правила з ключем (_KEYED) мають ключ = що саме повторюється (id замовлення, майданчик): ІНШЕ замовлення/майданчик = інший ключ = іде одразу.
  • усе інше — як і раніше, одразу (замовлення, гроші, фіскалізація, КОДВ, нові класи збоїв: невідоме = показати, не ховати).
ПРИНЦИП (аудит #643): mute/throttle — лише allow-list ТОЧНИХ шаблонів. Шапка повідомлення не доказ «все гаразд» (одна шапка в успіху й у збою), тож шаблон
перевіряє ТІЛО. Нове джерело спаму = новий рядок тут, а не «вимкнути алерти». Повернення True для заглушених — свідоме: виклик «доставку» не вимагав,
а виклики з ретраєм (`service_watchdog`) інакше зациклились би на повторах.
"""
import hashlib
import re

COOLDOWN_SEC = 24 * 60 * 60
_NOT_SESSION_TAIL = r"(?:(?!Якщо сесія протухла)[^\n])*"  # кожен збій ALLO/EVA завершується підказкою «Якщо сесія протухла — --login»: шукаємо ознаку ДО неї

# (id, regex джерела (basename sys.argv[0]), regex тексту (re.S, ^ = початок), дія)
RULES = [
    # --- MUTE: інформаційне ---
    # ЛИШЕ рядок успіху «підтягнуто commit …» (allow-list). Збої/відновлення/фід-пайплайн — завжди send (аудит #643 B1, F3).
    ("watchdog_autodeploy_ok", r"service_watchdog",
     r"^🚀 Watchdog PlutusToys: автодеплой\n\n✅ Автодеплой: підтягнуто commit \w+ о [^\n(]+ (?:\(\d+ файл\(ів\) змінено\)|\(без змін коду\))\s*$", "mute"),
    # чистий звіт прайсера: цін скориговано N, видалено <100, без виключень через комісію, Помилок: 0 (B3)
    ("pricer_apply_report", r"prom_competitor_pricer",
     r"^💰 prom_competitor_pricer\.py --apply: скориговано цін — \d+, видалено як неконкурентні — \d{1,2} товарів"
     r"(?: \(кап[^)]*\))?\. Помилок: 0\.\s*$", "mute"),
    ("catalog_scan_nightly", r"full_catalog_competitor_scan", r"^🌙 Нічний скан каталогу", "mute"),
    ("social_post_ok", r"social_auto_poster", r"^📣 Соцпостинг: [^\n]*Помилок: 0\.\s*$", "mute"),
    ("eva_import_ok", r"eva_cabinet_scraper", r"^🟣 EVA повний імпорт [^\n]*подано \(чекбокс модерації True\)", "mute"),
    # --- THROTTLE: повторюваний стан/збій сесії ---
    ("pricer_circuit", r"prom_competitor_pricer", r"^🚨 prom_competitor_pricer\.py --apply: коригування ЦІНИ ЗУПИНЕНО", "throttle"),
    ("pricer_delist_cap", r"prom_competitor_pricer", r"^ℹ️ prom_competitor_pricer\.py --apply: delist КАПІРОВАНО", "throttle"),
    # звіт прайсера з ЗАБЛОКОВАНО / «виключено через комісію» — стан щопрогону, але лише коли видалено <100 І Помилок: 0 (F2: масове зняття й помилки йдуть завжди)
    ("pricer_blocked_report", r"prom_competitor_pricer",
     r"^💰 prom_competitor_pricer\.py --apply: (?=[^\n]*видалено як неконкурентні — \d{1,2} товарів)(?=[\s\S]*Помилок: 0\.\s*$)"
     r"скориговано цін — 0 \(ЗАБЛОКОВАНО", "throttle"),
    ("pricer_commission_skipped", r"prom_competitor_pricer",
     r"^💰 prom_competitor_pricer\.py --apply: (?=[^\n]*видалено як неконкурентні — \d{1,2} товарів)(?=[\s\S]*Помилок: 0\.\s*$)"
     r"[^\n]*виключено через непідтверджену комісію", "throttle"),
    # keepalive-збої — «сесія протухла/збій ({e})» за дизайном, усі однакові
    ("eva_keepalive", r"eva_cabinet_scraper", r"^🚨 eva_cabinet_scraper keepalive", "throttle"),
    ("allo_keepalive", r"allo_cabinet_scraper", r"^🚨 allo_cabinet_scraper keepalive", "throttle"),
    ("prom_session", r"prom_notifications_scraper|prom_cabinet", r"^🚨 prom_\w+ keepalive", "throttle"),
    # збій дії: лише ознака СЕСІЇ/таймауту ДО підказки «Якщо сесія протухла» (F1); решта причин (верстка, кнопка) — одразу
    ("eva_session", r"eva_cabinet_scraper",
     r"^🚨 eva повний імпорт не вдався: " + _NOT_SESSION_TAIL + r"(?:сесі[юя] не прийнято|Page\.goto: Timeout)", "throttle"),
    ("allo_session", r"allo_cabinet_scraper",
     r"^🚨 allo " + _NOT_SESSION_TAIL + r"сесі[юя] не прийнято", "throttle"),
    ("rozetka_price_timeout", r"rozetka_price_monitor", r"^⚠️ rozetka_price_monitor: тимчасовий таймаут", "throttle"),
    ("sysmap_drift", r"system_map_driftcheck", r"^🗺️ SYSTEM_MAP дрейф", "throttle"),
    # «нижче підлогового порога» — лише повідомлення БЕЗ 🛑/📉; ключ = набір майданчиків (🔻 EVA після 🔻 PROM — інший ключ)
    ("catalog_floor", r"catalog_size_tracker", r"^📊 Наповненість вітрини — увага:\s*\n🔻[^🛑📉]*$", "throttle"),
    # ключ — id замовлень: НОВЕ замовлення = новий ключ = іде одразу; без id у тексті — send
    ("watchdog_toysi_unconfirmed", r"service_watchdog", r"^🚨 Watchdog PlutusToys: замовлення передане, але Toysi не підтверджує", "throttle"),
    ("watchdog_toysi_recovered", r"service_watchdog", r"^✅ Watchdog PlutusToys: звірка з Toysi відновлена", "throttle"),
]
_COMPILED = [(i, re.compile(s), re.compile(t, re.S), a) for i, s, t, a in RULES]

_ORDER_ID_RE = re.compile(r"\b(?:prom|rozetka|eva|allo|site)_[\w-]+")
_MARKET_RE = re.compile(r"🔻 (\w+):")
# rule_id → витягувач ключа з тексту (порожній набір → send: ключа нема, не ховаємо)
_KEYED = {
    "watchdog_toysi_unconfirmed": _ORDER_ID_RE,
    "watchdog_toysi_recovered": _ORDER_ID_RE,
    "catalog_floor": _MARKET_RE,
}


def classify(source: str, text: str):
    """→ (rule_id, action) або (None, 'send'). Збіг по джерелу І по тексту.
    Для правил з ключем rule_id = «правило:sha1(відсортований набір ключів)» — інше замовлення/майданчик = інше вікно тиші."""
    src = source or ""
    txt = (text or "").lstrip()
    for rule_id, src_re, text_re, action in _COMPILED:
        if src_re.search(src) and text_re.search(txt):
            if rule_id in _KEYED:
                keys = sorted(set(_KEYED[rule_id].findall(txt)))
                if not keys:
                    return None, "send"
                rule_id = f"{rule_id}:{hashlib.sha1(','.join(keys).encode('utf-8')).hexdigest()[:16]}"
            return rule_id, action
    return None, "send"
