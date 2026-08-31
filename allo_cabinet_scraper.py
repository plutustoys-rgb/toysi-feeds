"""
allo_cabinet_scraper.py — headless-читання кабінету ALLO (partner.allo.ua):
баланси (ТМ + абонплати + прапорець попередження про блокування) + лічильники
замовлень по статусах. Read-only, той самий Playwright+storageState патерн, що
eva/toysi/rozetka/prom-скрейпери.

НАВІЩО (2026-08-05, завдання Cowork — аудитор ALLO): жива перевірка показала —
у кабінеті ALLO прямим текстом «Поповніть Баланс абонплати, для уникнення
блокування» (Баланс абонплати 120 ₴), 0 замовлень, і 876/1085 товарів НЕ додались
при імпорті. ALLO не має публічного API балансу/статусів — лише кабінет.

ВЕРСТКА ЗВІРЕНА ЖИВО 2026-08-06 (перший --login-прогін): баланси НЕ на дашборді, а
на `/billing/status` («Баланс ТМ:\\n\\n2 500 ₴», «Баланс абонплати:\\n\\n120 ₴»,
«...для уникнення блокування»); замовлення на дашборді картками «N% N СтатусЛейбл»
(число ПЕРЕД міткою). При parse-невдачі зберігається локальний дамп сирого тексту
(allo_cabinet_debug_*.txt) для доналаштування, якщо ALLO змінить верстку.

БЕЗПЕКА: лише навігація + читання тексту. storageState — секрет у .local_secrets/
(gitignore), не в Cowork-папці. (Позначення сповіщень прочитаними — окрема write-дія,
за дозволом власника, ще НЕ реалізовано тут — цей аудитор read-only.)

ЗАПУСК:
    python allo_cabinet_scraper.py --login   # раз: вікно, логін, стан збережено
    python allo_cabinet_scraper.py           # headless (local_cabinet_audit.ps1)

РЕЗУЛЬТАТ: reports/allo_cabinet_YYYY-MM-DD.md + рядок у balance_history.jsonl
(platform="allo") + Telegram (якщо не AUDIT_NO_TELEGRAM).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from telegram_notify import send_telegram_message

load_dotenv()

BASE_DIR = Path(__file__).parent
_OUT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (BASE_DIR / "reports"))
REPORT_DIR = _OUT_DIR
HISTORY_FILE = _OUT_DIR / "balance_history.jsonl"
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"

STATE_FILE = Path(
    os.environ.get("ALLO_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "allo_cabinet_state.json"))
)

DASHBOARD_URL = "https://partner.allo.ua/"           # дашборд = картки замовлень
BILLING_URL = "https://partner.allo.ua/billing/status"  # баланси ТМ/абонплати/Холд + попередження
LOGIN_START_URL = "https://partner.allo.ua/"
# WRITE-дії (автоцикл, наказ власника 2026-08-31 через SEO — EVA/ALLO не мали автоциклу, як Rozetka/Prom):
PRICES_URL = "https://partner.allo.ua/products/prices"     # список прайс-листів → «Редагувати» → майстер зіставлення
CONTENT_URL = "https://partner.allo.ua/products/content"   # Товари → вкладка «Керування» → «Надіслати на модерацію все»
ALLO_BASE = "https://partner.allo.ua"
NAV_TIMEOUT_MS = 30000

ORDER_LABELS = {"accepted": "Прийнято", "picking": "Комплектується", "delivering": "Доставляється",
                "delivered": "Доставлено", "done": "Виконано", "cancelled": "Скасовано"}
# Попередження ALLO про потребу поповнити абонплату (фінансовий прапорець).
BLOCK_WARN_MARKER = "уникнення блокування"


class AlloCabinetError(Exception):
    pass


def _notify(msg: str) -> None:
    if _NO_TELEGRAM:
        return
    try:
        send_telegram_message(msg)
    except Exception as e:
        print(f"[AlloCabinet] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def _save_failure_artifacts(page, prefix: str, text: str = None) -> None:
    """Скріншот + (для доналаштування парсера) сирий текст — у ЛОКАЛЬНУ reports/
    (не в спільну папку: містить персональні/фінансові дані кабінету)."""
    try:
        fail_dir = BASE_DIR / "reports"
        fail_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        page.screenshot(path=str(fail_dir / f"allo_cabinet_failure_{prefix}_{ts}.png"), full_page=True)
        if text is not None:
            (fail_dir / f"allo_cabinet_debug_{prefix}_{ts}.txt").write_text(text, encoding="utf-8")
    except Exception:
        pass


def create_state() -> None:
    print("[AlloCabinet] Відкриваю partner.allo.ua. Залогінься, потім Enter...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(LOGIN_START_URL, timeout=NAV_TIMEOUT_MS)
        try:
            input()
        except EOFError:
            print("[AlloCabinet] Немає інтерактивного вводу — --login запускай вручну в терміналі.", file=sys.stderr)
            browser.close()
            sys.exit(1)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    print(f"[AlloCabinet] Сесію збережено: {STATE_FILE}")


def _amount_after(text: str, label: str):
    """Сума (грн) після мітки: «мітка … N ₴» (N з роздільниками тисяч). float|None."""
    m = re.search(re.escape(label) + r"[^\d]{0,20}?(\d[\d\xa0   ]*(?:[.,]\d{1,2})?)", text)
    if not m:
        return None
    raw = (m.group(1).replace("\xa0", "").replace(" ", "").replace(" ", "")
           .replace(" ", "").replace(",", "."))
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def _count_before(text: str, label: str):
    """Ціле число ПЕРЕД міткою (дашборд ALLO: картка «N% N СтатусЛейбл» — число count
    йде перед назвою статусу). Живо звірено 2026-08-06. int|None."""
    m = re.search(r"(\d+)[^\d]{0,6}" + re.escape(label), text)
    return int(m.group(1)) if m else None


def read_cabinet(page) -> dict:
    # 1) Баланси — сторінка /billing/status (на дашборді їх нема: у шапці лише сума ТМ
    #    без мітки, а «Баланс абонплати» + попередження — лише тут). Живо звірено 06.08:
    #    «Баланс ТМ:\n\n2 500 ₴», «Баланс абонплати:\n\n120 ₴», «...для уникнення блокування».
    page.goto(BILLING_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    if "sign_in" in page.url.lower() or "login" in page.url.lower():
        raise AlloCabinetError(f"сесію не прийнято — редірект на {page.url} (треба --login)")
    bill = page.inner_text("body")
    balance_tm = _amount_after(bill, "Баланс ТМ")
    balance_sub = _amount_after(bill, "Баланс абонплати")
    block_warning = BLOCK_WARN_MARKER in bill

    # 2) Замовлення — дашборд «/»: картки «N% N СтатусЛейбл» (число ПЕРЕД міткою).
    page.goto(DASHBOARD_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    dash = page.inner_text("body")
    orders = {k: _count_before(dash, lbl) for k, lbl in ORDER_LABELS.items()}

    if balance_tm is None and balance_sub is None:
        raise AlloCabinetError("на /billing/status не знайдено балансів (сесія протухла "
                               "або змінилась верстка)")
    return {"balance_tm": balance_tm, "balance_sub": balance_sub,
            "block_warning": block_warning, "orders": orders, "_dash": bill}


_LOGIN_URL_MARKERS = ("sign_in", "/login", "/auth", "oauth")
# Текст логін-форми ALLO (звірено живо 2026-08-31 на протухлій сесії) — потрібен, бо
# редірект на /sign_in КЛІЄНТСЬКИЙ і стається на ~4с ПІСЛЯ domcontentloaded: гола перевірка
# URL одразу його НЕ бачить, і дія мовчки повертає «0» (реальний баг, спіймано живим тестом).
_LOGIN_BODY_MARKERS = ("Відновити пароль", "E-mail або телефон", "Запам'ятати мене")


def _ensure_session(page) -> None:
    """Кидає AlloCabinetError, якщо storageState протух. Головний сигнал 'онови сесію' —
    щоб автоцикл НЕ мовчав про протухлу сесію (як 10-денний мовчазний збій EVA, CODE_LOG
    29.08). Спершу ДОЧЕКАТИСЬ, поки клієнтський редірект на логін встигне статись (networkidle,
    best-effort), потім перевірити і URL, і текст логін-форми."""
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass  # SPA-зʼєднання можуть не «затихати» — тоді покладаємось на перевірки нижче
    u = (page.url or "").lower()
    if any(m in u for m in _LOGIN_URL_MARKERS):
        raise AlloCabinetError(f"сесію не прийнято — редірект на {page.url} (треба --login)")
    try:
        body = page.inner_text("body")
    except Exception:
        body = ""
    if any(m in body for m in _LOGIN_BODY_MARKERS):
        raise AlloCabinetError(f"сесію не прийнято — сторінка логіну на {page.url} (треба --login)")


def _btn_actionable(btn) -> bool:
    """Кнопка існує, видима, увімкнена й не aria-disabled (ALLO дизейблить
    «Автозіставлення...» тултіпом «Триває...» доки async триває — тоді пропускаємо)."""
    try:
        if btn.count() == 0:
            return False
        b = btn.first
        if not b.is_visible() or not b.is_enabled():
            return False
        if (b.get_attribute("aria-disabled") or "").lower() == "true":
            return False
        return True
    except Exception:
        return False


def automap_pricelists(page, apply: bool) -> dict:
    """Частина B автоциклу: авто-завершення майстра «Зіставлення даних прайса».
    Заходить на /products/prices, для КОЖНОГО прайса відкриває майстер («Редагувати»
    → /products/import/<код>) і тисне ДОСТУПНІ кнопки «Автозіставлення категорій» /
    «Автозіставлення характеристик». Діє за станом КНОПКИ (enabled), не за текстом
    статусу — ідемпотентно: вже зіставлені/ті, де триває async, пропускає.

    ОБМЕЖЕННЯ (чесно): майстер відкривається на ПОТОЧНОМУ незавершеному кроці. Кнопку
    «Автозіставлення характеристик» (крок 3) звірено живо 2026-08-31 (тост «Прайс-лист
    відправлено на автозіставлення», async). «Автозіставлення категорій» (крок 2) —
    тиснеться, лише якщо присутня на завантаженій сторінці; випадок, коли прайс застряг
    саме на кроці категорій (треба спершу перемкнути крок), тут НЕ покрито — окремий
    інкремент, якщо трапиться (поки не бачив живого)."""
    page.goto(PRICES_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    _ensure_session(page)
    hrefs = []
    try:
        links = page.get_by_role("link", name="Редагувати")
        for i in range(links.count()):
            h = links.nth(i).get_attribute("href")
            if h and "/products/import/" in h and h not in hrefs:
                hrefs.append(h)
    except Exception as e:
        raise AlloCabinetError(f"не зчитав перелік прайс-листів: {e}")
    res = {"pricelists": len(hrefs), "acted": [], "skipped": 0}
    for h in hrefs:
        url = h if h.startswith("http") else ALLO_BASE + h
        code = h.rstrip("/").split("/")[-1]
        try:
            page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_timeout(3500)
            _ensure_session(page)
        except AlloCabinetError:
            raise
        except Exception as e:
            print(f"[AlloCabinet] {code}: не відкрив майстер зіставлення ({e})", file=sys.stderr)
            continue
        did = []
        for label in ("Автозіставлення категорій", "Автозіставлення характеристик"):
            btn = page.get_by_role("button", name=label)
            if not _btn_actionable(btn):
                continue
            if apply:
                try:
                    btn.first.click()
                    page.wait_for_timeout(3000)  # старт фонового автозіставлення + тост
                except Exception as e:
                    print(f"[AlloCabinet] {code}: {label} — клік не вдався ({e})", file=sys.stderr)
                    continue
            did.append(label)
        if did:
            res["acted"].append({"code": code, "buttons": did})
        else:
            res["skipped"] += 1
    return res


def moderate_all(page, apply: bool) -> dict:
    """Частина A автоциклу: bulk-подача «Надіслати на модерацію все» (вкладка «Керування»
    сторінки Товари /products/content). Звірено живо 2026-08-31: ПРЯМА дія без діалогу
    підтвердження, тост «Товари додані в обробку», кнопка одразу знову активна."""
    page.goto(CONTENT_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    _ensure_session(page)
    try:
        page.get_by_role("tab", name="Керування").first.click()
        page.wait_for_timeout(2000)
    except Exception as e:
        raise AlloCabinetError(f"не знайшов вкладку 'Керування': {e}")
    body = page.inner_text("body")
    m = re.search(r"Новий\D{0,8}(\d[\d\xa0   ]*)", body)
    pending = None
    if m:
        try:
            pending = int(m.group(1).replace("\xa0", "").replace(" ", "").replace(" ", "").replace(" ", ""))
        except ValueError:
            pending = None
    res = {"pending_new": pending, "submitted": False, "toast": None}
    if apply:
        btn = page.get_by_role("button", name="Надіслати на модерацію все")
        if _btn_actionable(btn):
            btn.first.click()
            page.wait_for_timeout(3500)
            try:
                if page.get_by_text("додані в обробку").count():
                    res["toast"] = "Товари додані в обробку"
            except Exception:
                pass
            res["submitted"] = True
    return res


def run_actions(apply: bool, do_automap: bool, do_moderate: bool) -> None:
    """Оркестратор автоциклу ALLO. Одна сесія браузера на обидві дії. Кожна дія в своєму
    try/except — збій однієї не глушить іншу, протухла сесія сигналиться в Telegram."""
    if not STATE_FILE.exists():
        msg = (f"🚨 allo_cabinet_scraper: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти раз `python allo_cabinet_scraper.py --login`.")
        print(f"[AlloCabinet] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)

    automap_res = None
    moderate_res = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            if do_automap:
                try:
                    automap_res = automap_pricelists(page, apply)
                except (PlaywrightTimeoutError, AlloCabinetError) as e:
                    _save_failure_artifacts(page, "automap")
                    msg = (f"🚨 allo автозіставлення не вдалось: {e}. "
                           f"Якщо сесія протухла — `python allo_cabinet_scraper.py --login`.")
                    print(f"[AlloCabinet] {msg}", file=sys.stderr)
                    _notify(msg)
            if do_moderate:
                try:
                    moderate_res = moderate_all(page, apply)
                except (PlaywrightTimeoutError, AlloCabinetError) as e:
                    _save_failure_artifacts(page, "moderate")
                    msg = (f"🚨 allo подача на модерацію не вдалась: {e}. "
                           f"Якщо сесія протухла — `python allo_cabinet_scraper.py --login`.")
                    print(f"[AlloCabinet] {msg}", file=sys.stderr)
                    _notify(msg)
        finally:
            browser.close()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    mode = "APPLY" if apply else "DRY-RUN"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"# ALLO авто-дії ({mode}), {now.strftime('%Y-%m-%d %H:%M')}", ""]
    if automap_res is not None:
        lines.append("## Автозіставлення прайс-листів")
        lines.append(f"- Прайс-листів усього: {automap_res['pricelists']}")
        lines.append(f"- Оброблено цей прогін: {len(automap_res['acted'])}")
        for a in automap_res["acted"]:
            lines.append(f"  - {a['code']}: {', '.join(a['buttons'])}")
        lines.append(f"- Пропущено (нема що зіставляти / триває / вже готово): {automap_res['skipped']}")
        lines.append("")
    if moderate_res is not None:
        lines.append("## Подача на модерацію")
        lines.append(f"- Було у статусі 'Новий': {moderate_res['pending_new']}")
        sub = "так" if moderate_res["submitted"] else "ні (dry-run)"
        if moderate_res.get("toast"):
            sub += f" — {moderate_res['toast']}"
        lines.append(f"- Подано: {sub}")
        lines.append("")
    report_path = REPORT_DIR / f"allo_actions_{today}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[AlloCabinet] Звіт авто-дій: {report_path}")

    parts = []
    if automap_res is not None:
        parts.append(f"автозіставлення {len(automap_res['acted'])}/{automap_res['pricelists']} прайсів")
    if moderate_res is not None and moderate_res["submitted"]:
        parts.append(f"подано на модерацію (було Новий {moderate_res['pending_new']})")
    if parts:
        _notify(f"🅰️ ALLO авто-дії {today} ({mode}): " + "; ".join(parts) + ".")
        print(f"[AlloCabinet] {'; '.join(parts)}")


def scrape() -> None:
    if not STATE_FILE.exists():
        msg = (f"🚨 allo_cabinet_scraper: нема збереженої сесії ({STATE_FILE.name}). "
               f"Запусти раз `python allo_cabinet_scraper.py --login`.")
        print(f"[AlloCabinet] {msg}", file=sys.stderr)
        _notify(msg)
        sys.exit(1)

    dash_text = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        try:
            try:
                data = read_cabinet(page)
                dash_text = data.pop("_dash", None)
            except (PlaywrightTimeoutError, AlloCabinetError) as e:
                try:
                    dash_text = page.inner_text("body")
                except Exception:
                    dash_text = None
                _save_failure_artifacts(page, "cabinet", text=dash_text)
                msg = (f"🚨 allo_cabinet_scraper: не вдалось прочитати кабінет ALLO: {e}. "
                       f"Якщо сесія протухла — `python allo_cabinet_scraper.py --login`.")
                print(f"[AlloCabinet] {msg}", file=sys.stderr)
                _notify(msg)
                sys.exit(1)
        finally:
            browser.close()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    o = data["orders"]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    report_path = REPORT_DIR / f"allo_cabinet_{today}.md"
    lines = [f"# Кабінет ALLO — автоматичне читання, {now.strftime('%Y-%m-%d %H:%M')}", ""]
    lines.append("## Баланси")
    lines.append(f"- Баланс ТМ: {data['balance_tm']} ₴")
    lines.append(f"- Баланс абонплати: {data['balance_sub']} ₴")
    if data["block_warning"]:
        lines.append("- 🔴 **Попередження кабінету: поповніть Баланс абонплати для уникнення блокування**")
    lines.append("")
    lines.append("## Замовлення (по статусах)")
    for k, lbl in ORDER_LABELS.items():
        lines.append(f"- {lbl}: {o[k]}")
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[AlloCabinet] Звіт: {report_path}")

    row = {"ts": now.isoformat(timespec="seconds"), "platform": "allo",
           "balance_total": data["balance_tm"], "balance_sub": data["balance_sub"],
           "block_warning": data["block_warning"]}
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[AlloCabinet] не вдалось дописати історію: {e}", file=sys.stderr)

    warn = " 🔴поповнити абонплату" if data["block_warning"] else ""
    summary = (f"🅰️ Баланс ALLO {today}: ТМ {data['balance_tm']} ₴, абонплата {data['balance_sub']} ₴{warn}. "
               f"Замовлень: прийнято {o['accepted']}.")
    _notify(summary)
    print(f"[AlloCabinet] {summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Кабінет ALLO (Playwright + storageState): читання балансів/замовлень + автоцикл дій.")
    parser.add_argument("--login", action="store_true", help="Раз: вікно, логін, зберегти сесію.")
    parser.add_argument("--automap", action="store_true",
                        help="Авто-завершити майстер 'Зіставлення даних' усіх прайс-листів.")
    parser.add_argument("--moderate-all", action="store_true",
                        help="'Надіслати на модерацію все' (вкладка Керування сторінки Товари).")
    parser.add_argument("--auto-cycle", action="store_true",
                        help="automap + moderate-all послідовно — щоденний автоцикл (як Rozetka/Prom).")
    parser.add_argument("--apply", action="store_true",
                        help="Реально виконати дії. Без нього — DRY-RUN: лише звіт що БУДЕ зроблено, без кліків.")
    args = parser.parse_args()
    if args.login:
        create_state()
    elif args.automap or args.moderate_all or args.auto_cycle:
        run_actions(apply=args.apply,
                    do_automap=args.automap or args.auto_cycle,
                    do_moderate=args.moderate_all or args.auto_cycle)
    else:
        scrape()


if __name__ == "__main__":
    main()
