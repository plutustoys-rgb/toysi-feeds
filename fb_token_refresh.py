"""fb_token_refresh.py — перевидати короткий FB-токен у ДОВГОСТРОКОВИЙ Page-токен і оновити .env.

Замінює ручний «одноряковик», який збирали щоразу при додаванні дозволу до застосунку
(catalog_management, instagram_shopping_tag_products, read_insights, ...). ЗАПУСК НА VPS —
короткий токен і FB_APP_SECRET лишаються на сервері, у чат/Cowork не потрапляють:

    /opt/plutustoys/venv/bin/python3 fb_token_refresh.py           # токен зі stdin (не лишає в історії)
    /opt/plutustoys/venv/bin/python3 fb_token_refresh.py --token EAAB...   # або аргументом
    ... fb_token_refresh.py --dry-run                              # усе перевірити, .env НЕ писати

Ланцюг (перевірено найновіше-26/27, CODE_LOG): короткий токен → fb_exchange_token (довгостроковий,
потрібен FB_APP_SECRET) → якщо це User-токен, me/accounts → Page-токен → debug_token перевіряє
(тип PAGE, expires_at=0 «безстроковий», потрібні scope) → ЛИШЕ ТОДІ пише FB_PAGE_ACCESS_TOKEN у
.env (з бекапом .env.bak). Самі токени НІКОЛИ не друкуються.

БЕЗПЕКА (verify-before-write): якщо будь-яка перевірка не проходить — .env НЕ чіпаємо, робочий
токен лишається на місці, скрипт падає з поясненням ЧОМУ (правило власника: код сам каже, що не так).
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# При РУЧНОМУ запуску на VPS .env не підвантажується автоматично (systemd-юніти беруть його через
# EnvironmentFile, а гола сесія — ні). Тягнемо самі, як решта скриптів (bank_check/daily_report/...).
# PLUTUS_ENV_PATH дозволяє вказати інший шлях (тести/локаль); за замовч. .env на VPS. Не оверрайдить
# уже наявні змінні оточення (default override=False), тож set -a; . .env лишається сумісним.
load_dotenv(os.environ.get("PLUTUS_ENV_PATH", "/opt/plutustoys/.env"))

GRAPH = f"https://graph.facebook.com/{os.environ.get('GRAPH_VERSION', 'v21.0')}"
APP_ID = os.environ.get("FB_APP_ID", "1787100088972907").strip()   # застосунок «PlutusToys Poster»
APP_SECRET = os.environ.get("FB_APP_SECRET", "").strip()
PAGE_ID = os.environ.get("FB_PAGE_ID", "").strip()
DEFAULT_ENV = os.environ.get("PLUTUS_ENV_PATH", "/opt/plutustoys/.env")
# Мінімум, який має бути в Page-токені, щоб і постинг, і per-post метрики працювали.
REQUIRED_SCOPES = {"read_insights", "pages_manage_posts", "instagram_content_publish"}
REQUEST_TIMEOUT = 30


def _die(msg: str) -> None:
    print(f"[fb_token_refresh] ✖ {msg}", file=sys.stderr)
    sys.exit(1)


def _get(url: str, params: dict) -> dict:
    """GET Graph API з єдиним місцем обробки помилок (Graph віддає {'error': {...}} з HTTP 200)."""
    try:
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        _die(f"мережева помилка до {url}: {e}")
    try:
        j = r.json()
    except ValueError:
        _die(f"не JSON з {url}: HTTP {r.status_code} {r.text[:200]}")
    if isinstance(j, dict) and j.get("error"):
        err = j["error"]
        _die(f"Graph помилка: {err.get('message')} "
             f"(code {err.get('code')}, subcode {err.get('error_subcode')})")
    return j


def _app_access_token() -> str:
    return f"{APP_ID}|{APP_SECRET}"


def _debug_token(token: str) -> dict:
    data = _get(f"{GRAPH}/debug_token",
                {"input_token": token, "access_token": _app_access_token()})
    return data.get("data", {}) if isinstance(data, dict) else {}


def exchange_and_resolve(short_token: str) -> tuple:
    """Повертає (page_token, page_debug_info). Падає (_die) на будь-якій проблемі — .env не чіпаємо."""
    # 1) короткий → довгостроковий
    ex = _get(f"{GRAPH}/oauth/access_token",
              {"grant_type": "fb_exchange_token", "client_id": APP_ID,
               "client_secret": APP_SECRET, "fb_exchange_token": short_token})
    long_token = ex.get("access_token")
    if not long_token:
        _die("обмін fb_exchange_token не повернув access_token")
    info = _debug_token(long_token)
    ttype = info.get("type")
    print(f"[fb_token_refresh] обмінено; тип={ttype}, expires_at={info.get('expires_at')}")

    # 2) дістати Page-токен
    if ttype == "PAGE":
        page_token = long_token
    else:
        accts = _get(f"{GRAPH}/me/accounts",
                     {"access_token": long_token, "fields": "id,name,access_token"})
        pages = accts.get("data", []) if isinstance(accts, dict) else []
        chosen = None
        if PAGE_ID:
            chosen = next((p for p in pages if str(p.get("id")) == PAGE_ID), None)
            if not chosen:
                _die(f"сторінку FB_PAGE_ID={PAGE_ID} не знайдено в me/accounts "
                     f"(є: {[p.get('id') for p in pages]})")
        elif len(pages) == 1:
            chosen = pages[0]
        else:
            _die("кілька (або нуль) сторінок у me/accounts і FB_PAGE_ID не задано — "
                 "вкажи FB_PAGE_ID у .env, щоб не взяти не ту сторінку")
        page_token = chosen.get("access_token")
        print(f"[fb_token_refresh] сторінка: {chosen.get('name')} ({chosen.get('id')})")
    if not page_token:
        _die("не вдалось отримати Page-токен")

    # 3) перевірити фінальний Page-токен ПЕРЕД записом
    pinfo = _debug_token(page_token)
    if pinfo.get("type") != "PAGE":
        _die(f"фінальний токен тип={pinfo.get('type')}, а треба PAGE — інакше #200 при постингу "
             f"(в Explorer обери 'Page Token', не 'User Token', і перегенеруй)")
    if pinfo.get("expires_at") not in (0, None):
        _die(f"Page-токен НЕ безстроковий (expires_at={pinfo.get('expires_at')}) — обмін на "
             f"довгостроковий не спрацював; за годину помре і FB, і IG постинг")
    return page_token, pinfo


def write_env(env_path: Path, page_token: str) -> None:
    """In-place заміна FB_PAGE_ACCESS_TOKEN у .env з бекапом .env.bak. Токен не друкуємо."""
    if not env_path.exists():
        _die(f"нема файлу оточення {env_path} (задай --env або PLUTUS_ENV_PATH)")
    shutil.copy2(str(env_path), str(env_path) + ".bak")
    lines = env_path.read_text(encoding="utf-8").splitlines()
    out, found = [], False
    for ln in lines:
        if ln.startswith("FB_PAGE_ACCESS_TOKEN="):
            out.append(f"FB_PAGE_ACCESS_TOKEN={page_token}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"FB_PAGE_ACCESS_TOKEN={page_token}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", help="короткий FB-токен (краще без цього — подати у stdin, "
                                    "щоб не лишати в історії shell)")
    ap.add_argument("--env", default=DEFAULT_ENV, help=f"шлях до .env (за замовч. {DEFAULT_ENV})")
    ap.add_argument("--dry-run", action="store_true", help="усе перевірити, але .env НЕ писати")
    a = ap.parse_args()

    if not APP_SECRET:
        _die("нема FB_APP_SECRET в оточенні (.env на VPS) — обмін неможливий")
    short = (a.token or sys.stdin.readline() or "").strip()
    if not short:
        _die("порожній вхідний токен (подай --token або встав у stdin)")

    page_token, pinfo = exchange_and_resolve(short)
    scopes = sorted(pinfo.get("scopes", []) or [])
    missing = REQUIRED_SCOPES - set(scopes)
    if missing:
        print(f"[fb_token_refresh] ⚠ у токені бракує scope: {sorted(missing)} — "
              f"відповідні функції не діятимуть (додай дозвіл на застосунку і перегенеруй токен)",
              file=sys.stderr)
    print(f"[fb_token_refresh] ✓ Page-токен: безстроковий; scopes={scopes}")

    if a.dry_run:
        print("[fb_token_refresh] --dry-run: .env НЕ змінено.")
        return

    write_env(Path(a.env), page_token)
    print(f"[fb_token_refresh] ✓ FB_PAGE_ACCESS_TOKEN оновлено в {a.env} "
          f"(бекап {a.env}.bak). Токен НЕ друкую.")
    print("[fb_token_refresh] Далі: social_insights_report.py --probe <media_id> — звірити метрики.")


if __name__ == "__main__":
    main()
