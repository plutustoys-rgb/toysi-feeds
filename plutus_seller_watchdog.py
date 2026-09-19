"""plutus_seller_watchdog.py — детекція смерті живої інтерактивної сесії Продажника + алерт.

НАВІЩО (інцидент 2026-09-18/19): claude.exe завис і був примусово закритий Windows («stopped
interacting with Windows and was closed», Event Log Id=1002, 2026-09-18 20:25:12). Жива
`/loop`-сесія Продажника (Upwork-полювання) загинула разом з ним. `agent_watch.WATCHERS` тут не
рятує — Продажник у ньому лише на подієве пробудження каналу, живої інтерактивної сесії не тримає
і не піднімає; headless-режим у нього все одно без браузера. Без цього скрипта падіння лишалось
непоміченим >24 год — Консультант помітив мовчання каналу вручну, власник дізнався постфактум.

ЧОМУ ЛИШЕ ДЕТЕКЦІЯ+АЛЕРТ, БЕЗ АВТОРЕЛОНЧУ (звірено живо 2026-09-19, file:line):
`.claude/agents/plutus-seller.md:3-8` і `wake_prompt` у `agent_watch.py` (запис "Продажник")
досі мають місію «пошук виробників іграшок/пряме представництво» — ЖОДНОЇ згадки Upwork, без
браузерних `tools:`. Автоматичний `--agent plutus-seller` підняв би НЕ той напрям і замаскував би
падіння видимим-але-хибним вікном (гірше за мовчання — виглядає як «все ОК»). Дочекатись
переозброєння персони (`бриф_переозброєння_Продажника_Upwork.md`) — окрема задача; її власний
мандат (автономна ПОДАЧА заявок через браузер) до того ж уже спростований цієї ж сесії раніше:
Upwork's Cloudflare блокує Playwright/CDP-керований браузер навіть видимий і без headless-ознак,
офіційний API подачі заявок не підтримує. Тому бриф вимагає переоцінки Консультантом/власником
перед переписом персони — не сліпого виконання застарілого §6.

ЩО РОБИТЬ: детектує вікно консолі, заголовок якого починається з "Продажник" — той самий префікс,
що ставить `control_panel.py:_launch()` (`start "Продажник" ... cmd /k claude ...`); перевірено
живо 2026-09-19 тестовим спавном, cmd.exe лишає його на початку `MainWindowTitle`, навіть коли
claude.cmd дописує власний хвіст команди в заголовок. Нема вікна → мертвий. Раз на «епізод
смерті» — один Telegram-алерт + один запис у SELLER_CHANNEL.md (newest-on-top), далі тихо, поки
або (а) вікно з'явиться знову (власник відкрив сам), або (б) минув REALERT_INTERVAL_HOURS —
тоді один нагадувальний алерт, без спаму щоцикл.

ЗАПУСК: python plutus_seller_watchdog.py [--dry-run]
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
COWORK_DIR = Path(os.environ.get("PLUTUS_COWORK_DIR",
                                 r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya"))
SELLER_CHANNEL = COWORK_DIR / "SELLER_CHANNEL.md"
STATE_FILE = BASE_DIR / ".local_secrets" / "seller_watchdog_state.json"
WINDOW_TITLE_PREFIX = "Продажник"
REALERT_INTERVAL_HOURS = 3
_NO_TELEGRAM = os.environ.get("AUDIT_NO_TELEGRAM") == "1"


def _notify(msg: str) -> bool:
    """Повертає True лише якщо повідомлення справді пішло — щоб main() не позначав алерт
    «відправленим», коли доставка провалилась (маскований best-effort збій, PR #546)."""
    if _NO_TELEGRAM:
        return False
    try:
        sys.path.insert(0, str(BASE_DIR))
        from telegram_notify import send_telegram_message
        return bool(send_telegram_message(msg))
    except Exception as e:  # noqa: BLE001
        print(f"[SellerWatchdog] Telegram не надіслано (не критично): {e}", file=sys.stderr)
        return False


def is_alive() -> bool:
    """Живо (PowerShell) перевіряє, чи є вікно з заголовком, що починається на
    WINDOW_TITLE_PREFIX, І чи під ним справді ще живий `claude.exe` (не лише порожня
    `cmd /k`-оболонка). `/k` (control_panel.py:_launch()) навмисно тримає вікно відкритим
    ПІСЛЯ завершення команди — якщо claude.exe завершиться нештатно (не hang, а звичайний
    крах), голе вікно лишиться з тим самим заголовком, і перевірка лише за заголовком дала б
    хибний «живий» (звірено живо 2026-09-19: реальний запуск має claude.exe прямим child
    процесом cmd-хоста). Збій самої перевірки (powershell недоступний, таймаут) трактуємо як
    «невідомо» = не мертвий — щоб дефект перевірки не бив у Telegram фальшивою тривогою."""
    ps_cmd = (
        f"$h = Get-Process -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.MainWindowTitle -like '{WINDOW_TITLE_PREFIX}*' }};"
        f"if (-not $h) {{ 0; exit }}"
        f"$alive = 0;"
        f"foreach ($p in $h) {{"
        f"  $c = Get-CimInstance Win32_Process -Filter \"ParentProcessId=$($p.Id)\" -ErrorAction SilentlyContinue |"
        f"       Where-Object {{ $_.Name -match 'claude' }};"
        f"  if ($c) {{ $alive = 1 }}"
        f"}}"
        f"$alive"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd],
                            capture_output=True, text=True, timeout=30)
        return int((r.stdout or "0").strip() or "0") > 0
    except (subprocess.TimeoutExpired, ValueError, OSError) as e:
        print(f"[SellerWatchdog] перевірка вікна не вдалась ({e}) — «невідомо», не мертвим", file=sys.stderr)
        return True


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def _post_channel(text: str) -> bool:
    """Newest-on-top: вставляє одразу після ПЕРШОГО `---` (кінець блоку протоколу), як і решта
    записів у SELLER_CHANNEL.md. Повертає True лише якщо запис справді записано на диск."""
    try:
        old = SELLER_CHANNEL.read_text(encoding="utf-8") if SELLER_CHANNEL.exists() else ""
    except OSError:
        return False
    marker = "\n---\n"
    idx = old.find(marker)
    if idx == -1:
        new = old + marker + text
    else:
        insert_at = idx + len(marker)
        new = old[:insert_at] + text + "\n---\n" + old[insert_at:]
    try:
        SELLER_CHANNEL.write_text(new, encoding="utf-8")
        return True
    except OSError as e:
        print(f"[SellerWatchdog] не вдалось дописати в канал: {e}", file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Детекція смерті живої сесії Продажника + алерт (без relaunch).")
    ap.add_argument("--dry-run", action="store_true", help="лише перевірити й надрукувати, нічого не писати/слати")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    alive = is_alive()
    st = _load_state()

    if alive:
        print("[SellerWatchdog] живий — вікно «Продажник*» знайдено")
        if not args.dry_run:
            st["last_alive"] = now.isoformat()
            st["death_alerted_at"] = None
            _save_state(st)
        return 0

    print("[SellerWatchdog] МЕРТВИЙ — вікна «Продажник*» немає")
    if args.dry_run:
        return 1

    last_alert = st.get("death_alerted_at")
    should_alert = last_alert is None
    if not should_alert:
        try:
            hours_since = (now - datetime.fromisoformat(last_alert)).total_seconds() / 3600
            should_alert = hours_since >= REALERT_INTERVAL_HOURS
        except (ValueError, TypeError):
            should_alert = True

    if should_alert:
        first_time = last_alert is None
        header = ("🔴 Продажник: жива сесія НЕ знайдена (вікно «Продажник*» відсутнє)" if first_time
                   else f"🔴 Продажник: досі мертвий (попередній алерт {last_alert}, тепер {now.isoformat()})")
        msg = (
            f"{header}. Upwork-полювання стоїть. Авторелонч НЕ виконано (персона plutus-seller.md "
            f"досі стара місія «виробники іграшок», без Upwork/браузера) — відкрий сесію вручну "
            f"(панель control_panel.py → Продажник → 🔗 відкрити сесію, або сайдбар «SELLER»)."
        )
        telegram_ok = _notify(msg)
        channel_ok = _post_channel(
            f"## [Код → Продажник] {now.strftime('%Y-%m-%d %H:%M')} — {header}\n\n"
            f"Вотчер `plutus_seller_watchdog.py` не знайшов вікна консолі з заголовком «Продажник*» "
            f"{'вперше в цьому епізоді' if first_time else f'повторно (попередній алерт {last_alert})'}.\n\n"
            f"Авторелонч НЕ зроблено навмисно: `.claude/agents/plutus-seller.md` і `wake_prompt` у "
            f"`agent_watch.py` досі місія «виробники іграшок», без Upwork і без браузерних tools "
            f"(перевірено живо {now.strftime('%Y-%m-%d')}) — сліпий relaunch підняв би не той напрям.\n\n"
            f"Дія: власник відкриває сесію вручну.\n"
        )
        if telegram_ok or channel_ok:
            # Позначаємо «алерт відправлено» лише коли хоч один канал справді доставив —
            # інакше збій ОБОХ мовчки продовжив би 3-годинну паузу, ніхто б не дізнався
            # (маскований best-effort збій, PR #546).
            st["death_alerted_at"] = now.isoformat()
            _save_state(st)
        else:
            print("[SellerWatchdog] і Telegram, і запис у канал провалились — "
                  "стан НЕ оновлено, наступний цикл (15 хв) спробує знову", file=sys.stderr)
    else:
        print(f"[SellerWatchdog] мертвий, але алерт уже був о {last_alert} "
              f"(< {REALERT_INTERVAL_HOURS}год тому) — тихо")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
