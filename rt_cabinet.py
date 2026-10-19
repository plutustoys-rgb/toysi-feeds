# -*- coding: utf-8 -*-
"""rt_cabinet.py — кабінет партнера RoyalToys (royaltoys.com.ua): замовлення вводяться ЛИШЕ там (API нема). Як і eva_cabinet_scraper: вхід робить ВЛАСНИК сам (пароль/2FA — тільки він), сесія зберігається у storage_state, далі — headless на VPS.

Режими (Playwright; секрети/паролі я не вводжу ніколи):
  python rt_cabinet.py --login      # раз, на ПК власника: відкриє вікно, власник логіниться, Enter → .local_secrets/rt_cabinet_state.json
  python rt_cabinet.py --explore    # ТІЛЬКИ ЧИТАННЯ: заходить у кабінет під збереженою сесією, обходить навігаційні посилання (GET), пише звіт rt_cabinet_explore_<дата>.md (текст сторінок + поля форм) — щоб побудувати введення замовлення за реальною структурою
  python rt_cabinet.py --keepalive  # оновлює сесію (як EVA/Prom keepalive)
Шлях стану: RT_CABINET_STATE_FILE (на VPS — /opt/plutustoys/.secrets/rt_cabinet_state.json). Режим введення замовлення (--order) додається ПІСЛЯ вивчення кабінету.
У режимі --explore нічого не натискається, крім посилань навігації; посилання на вихід/видалення/скасування/оформлення пропускаються."""
import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = Path(os.environ.get("RT_CABINET_STATE_FILE", str(BASE_DIR / ".local_secrets" / "rt_cabinet_state.json")))
AUTH_URL = "https://royaltoys.com.ua/ua/auth/"
HOME_URL = "https://royaltoys.com.ua/ua/"
REPORT_DIR = Path(os.environ.get("RT_EXPLORE_DIR", str(BASE_DIR / "reports")))
_SKIP_RE = re.compile(r"logout|exit|выход|вихід|delete|remove|cancel|clear|checkout|order/(?:create|add)|cart/add|javascript:|#|mailto:|tel:", re.I)
MAX_PAGES = 40


def login() -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(AUTH_URL)
        print("[RtCabinet] Увійдіть у кабінет партнера RoyalToys у вікні, що відкрилось, потім натисніть Enter тут.")
        try:
            input()
        except EOFError:
            print("[RtCabinet] Немає інтерактивного вводу — --login треба запускати вручну в терміналі.", file=sys.stderr)
            browser.close()
            sys.exit(2)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass
    print(f"[RtCabinet] Сесію збережено: {STATE_FILE}")


def _logged_in(page) -> bool:
    """Є посилання на вихід / немає форми входу."""
    return bool(page.query_selector('a[href*="logout"], a[href*="exit"]')) or not page.query_selector('a[href*="/auth/"]')


def keepalive() -> int:
    from playwright.sync_api import sync_playwright
    if not STATE_FILE.exists():
        print(f"[RtCabinet] нема сесії ({STATE_FILE}); запусти раз --login", file=sys.stderr)
        return 1
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        ok = _logged_in(page)
        if ok:
            ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    print("[RtCabinet] keepalive:", "сесію оновлено" if ok else "сесія протухла — потрібен --login")
    return 0 if ok else 1


def explore() -> int:
    """Читання: головна під сесією → навігаційні посилання кабінету (той самий домен, без небезпечних) → текст і форми кожної сторінки у звіт."""
    from playwright.sync_api import sync_playwright
    if not STATE_FILE.exists():
        print(f"[RtCabinet] нема сесії ({STATE_FILE}); запусти раз --login", file=sys.stderr)
        return 1
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"rt_cabinet_explore_{datetime.now():%Y-%m-%d}.md"
    lines = [f"# Кабінет RoyalToys — розвідка {datetime.now():%Y-%m-%d %H:%M} (лише читання)", ""]
    seen, queue = set(), [HOME_URL]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(storage_state=str(STATE_FILE))
        page = ctx.new_page()
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        if not _logged_in(page):
            print("[RtCabinet] сесія не прийнята — потрібен --login", file=sys.stderr)
            browser.close()
            return 1
        # кабінетні посилання: все, що не каталог товарів і не головна
        links = page.eval_on_selector_all("a[href]", "els => els.map(a => [a.textContent.trim().slice(0,60), a.href])")
        cab = [(t, h) for t, h in links if "royaltoys.com.ua" in h and not _SKIP_RE.search(h) and re.search(r"/my|cabinet|kabinet|profile|account|order|zamovl|dropship|partner|balance|payment|ttn|invoice", h, re.I)]
        queue = [h for _, h in dict.fromkeys(cab)]
        lines += ["## Посилання кабінету на головній", ""] + [f"- {t} → {h}" for t, h in dict.fromkeys(cab)] + [""]
        for url in queue:
            if url in seen or len(seen) >= MAX_PAGES:
                continue
            seen.add(url)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1500)
            except Exception as e:  # noqa: BLE001
                lines += [f"## {url}", f"Помилка: {type(e).__name__}", ""]
                continue
            text = re.sub(r"\n{3,}", "\n\n", (page.inner_text("body") or "").strip())[:6000]
            forms = page.eval_on_selector_all("form", "fs => fs.map(f => ({action: f.action, method: f.method, fields: [...f.querySelectorAll('input,select,textarea,button')].map(e => [e.tagName, e.type||'', e.name||'', (e.placeholder||e.textContent||'').trim().slice(0,40)])}))")
            sub = page.eval_on_selector_all("a[href]", "els => els.map(a => [a.textContent.trim().slice(0,50), a.href])")
            for t, h in sub:
                if "royaltoys.com.ua" in h and not _SKIP_RE.search(h) and re.search(r"/my|cabinet|order|zamovl|ttn|invoice|payment|balance", h, re.I) and h not in seen and h not in queue:
                    queue.append(h)
            lines += [f"## {page.title()} — {url}", "", "```", text, "```", ""]
            for f in forms:
                lines += [f"Форма {f['method']} {f['action']}:", ""] + [f"- {x}" for x in f["fields"][:60]] + [""]
        ctx.storage_state(path=str(STATE_FILE))
        browser.close()
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[RtCabinet] розвідка записана: {out} ({len(seen)} сторінок)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--login", action="store_true")
    g.add_argument("--explore", action="store_true")
    g.add_argument("--keepalive", action="store_true")
    a = ap.parse_args()
    if a.login:
        login()
    elif a.explore:
        sys.exit(explore())
    else:
        sys.exit(keepalive())
