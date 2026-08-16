"""run_rozetka_local.py — ЛОКАЛЬНИЙ ланцюг Rozetka (Фаза 3, задача власника 2026-08-16).

Rozetka НЕ переїжджає на VPS: конкурентні дані (рекомендовані ціни + точні комісії) живуть за
ЛОКАЛЬНОЮ кабінетною сесією (VPS без екрана логін не зробить). Тому ВЕСЬ ланцюг локальний, а з
GitHub Actions генерацію Rozetka ПРИБРАНО (щоб два джерела не билися за feed-data).

Ланцюг (щопрогону, по таймеру Windows ~4-6 год):
  1. пулер (rozetka_price_monitor.py) — свіжі конкурентні дані [best-effort: збій сесії не валить фід]
  2. репрайсер (rozetka_competitor_repricer.py) — rozetka_price_overrides.json [best-effort]
  3. restore own_product_links_cache.json з feed-data (потрібен фіду для <url>)
  4. generate_rozetka_feed.py (застосовує override-и)
  5. --preflight (валідатор Rozetka)
  6. якщо генерація/preflight впали → restore останньої РОБОЧОЇ rozetka_feed.xml з feed-data + алерт
  7. перевірка непорожнього фіду
  8. ПУБЛІКАЦІЯ у feed-data — ТОЧНО той самий restore-all-then-orphan-force-push патерн, що робив
     GH Actions (git checkout origin/feed-data -- . відновлює УСІ чужі VPS-фіди, тоді копіюємо свій
     rozetka_feed.xml + rozetka_static_selection.json і force-push). Зберігає файли інших фідів.

--dry-run: усі кроки, КРІМ фінального git push (для безпечної перевірки).
БЕЗПЕКА: токен git береться з git credential (як усюди), у код/лог не потрапляє.
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
FEED_BRANCH = "feed-data"
FEED_FILE = BASE_DIR / "feeds" / "rozetka_feed.xml"
STATIC_SEL = BASE_DIR / "rozetka_static_selection.json"
PY = sys.executable


def _run(cmd, cwd=None, check=False, quiet=False):
    r = subprocess.run(cmd, cwd=cwd or str(BASE_DIR), text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    if not quiet and r.stdout:
        print(r.stdout.rstrip())
    if check and r.returncode != 0:
        raise RuntimeError(f"команда впала ({r.returncode}): {' '.join(cmd)}")
    return r


def _notify(msg: str) -> None:
    if os.environ.get("AUDIT_NO_TELEGRAM") == "1":
        return
    try:
        sys.path.insert(0, str(BASE_DIR))
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:
        print(f"[RzLocal] Telegram не надіслано: {e}", file=sys.stderr)


def _git_token() -> str:
    """Токен GitHub із git credential (той самий механізм, що всюди). Не логуємо."""
    r = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                       text=True, capture_output=True)
    for line in r.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return ""


def _restore_from_feed_data(path_in_branch: str, dest: Path) -> bool:
    """git show origin/feed-data:<path> > dest. True якщо вдалось (файл непорожній)."""
    _run(["git", "fetch", "origin", FEED_BRANCH], quiet=True)
    r = subprocess.run(["git", "show", f"origin/{FEED_BRANCH}:{path_in_branch}"],
                       cwd=str(BASE_DIR), capture_output=True)
    if r.returncode == 0 and r.stdout:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.stdout)
        return dest.exists() and dest.stat().st_size > 0
    return False


def _publish(dry_run: bool) -> None:
    """restore-all-then-orphan-force-push (як GH Actions). Зберігає чужі (VPS) файли feed-data."""
    token = _git_token()
    if not token and not dry_run:
        raise RuntimeError("нема git-токена — публікацію пропущено")
    repo = "plutustoys-rgb/toysi-feeds"
    with tempfile.TemporaryDirectory() as work:
        def g(*a, **kw):
            return _run(["git", *a], cwd=work, **kw)
        g("init", "-q", check=True)
        remote = (f"https://x-access-token:{token}@github.com/{repo}.git" if token
                  else f"https://github.com/{repo}.git")
        g("remote", "add", "origin", remote, check=True)
        g("config", "user.name", "feed-bot")
        g("config", "user.email", "feed-bot@users.noreply.github.com")
        g("fetch", "--depth", "1", "origin", FEED_BRANCH, quiet=True)
        g("checkout", "--orphan", FEED_BRANCH, "-q")
        g("checkout", f"origin/{FEED_BRANCH}", "--", ".", quiet=True)  # ВІДНОВИТИ УСІ чужі файли

        (Path(work) / "feeds").mkdir(parents=True, exist_ok=True)
        (Path(work) / "feeds" / "rozetka_feed.xml").write_bytes(FEED_FILE.read_bytes())
        if STATIC_SEL.exists():
            (Path(work) / "rozetka_static_selection.json").write_bytes(STATIC_SEL.read_bytes())

        g("add", "-f", "feeds/rozetka_feed.xml", "rozetka_static_selection.json")
        # звірка: чи в дереві збереглися чужі фіди (захист від затирання)
        tracked = g("ls-files", quiet=True).stdout.split()
        others = [f for f in tracked if f.startswith("feeds/") and "rozetka_feed.xml" not in f]
        print(f"[RzLocal] у дереві feed-data: {len(tracked)} файлів, чужих feeds/*: {len(others)}.")
        if dry_run:
            print("[RzLocal] --dry-run: push ПРОПУЩЕНО. Дерево підготовлено коректно.")
            return
        g("commit", "-q", "-m", "Rozetka feed update (local chain)")
        g("push", "--force", "origin", f"{FEED_BRANCH}:{FEED_BRANCH}", check=True)
        print("[RzLocal] опубліковано feeds/rozetka_feed.xml у feed-data.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="усе, крім фінального git push")
    ap.add_argument("--skip-pull", action="store_true", help="не оновлювати конкурентні дані (взяти наявні)")
    a = ap.parse_args()

    # 1-2. Свіжі конкурентні дані + override-и (best-effort — збій НЕ валить публікацію фіду).
    if not a.skip_pull:
        print("[RzLocal] 1) пулер конкурентних даних...")
        _run([PY, str(BASE_DIR / "rozetka_price_monitor.py")])
    print("[RzLocal] 2) репрайсер (override-и цін)...")
    _run([PY, str(BASE_DIR / "rozetka_competitor_repricer.py")])

    # 3. own_product_links_cache для <url> (фід ЛИШЕ читає його).
    if not _restore_from_feed_data("own_product_links_cache.json", BASE_DIR / "own_product_links_cache.json"):
        (BASE_DIR / "own_product_links_cache.json").write_text("{}", encoding="utf-8")

    # 3b. rozetka_static_selection.json (MEMBERSHIP) — якщо локально зник/битий, тягнемо з feed-data,
    # щоб _build_rozetka_static_selection НЕ перерахував набір заново (це була б хвиля нової модерації).
    if not (STATIC_SEL.exists() and STATIC_SEL.stat().st_size > 2):
        _restore_from_feed_data("rozetka_static_selection.json", STATIC_SEL)

    # 4-5. Генерація + preflight.
    print("[RzLocal] 4) генерація Rozetka-фіду...")
    gen_ok = _run([PY, str(BASE_DIR / "generate_rozetka_feed.py")]).returncode == 0
    print("[RzLocal] 5) preflight...")
    pre_ok = _run([PY, str(BASE_DIR / "generate_rozetka_feed.py"), "--preflight"]).returncode == 0

    # 6. Фолбек на останню робочу версію, якщо щось впало.
    if not (gen_ok and pre_ok):
        print("[RzLocal] генерація/preflight впали — відновлюю останню робочу rozetka_feed.xml.", file=sys.stderr)
        _restore_from_feed_data("feeds/rozetka_feed.xml", FEED_FILE)
        _notify("⚠️ run_rozetka_local: генерація/preflight Rozetka впали — публікую попередню робочу версію.")

    # 7. Перевірка.
    if not (FEED_FILE.exists() and FEED_FILE.stat().st_size > 0):
        _notify("🚨 run_rozetka_local: feeds/rozetka_feed.xml порожній/відсутній і фолбек не спрацював — НЕ публікую.")
        print("[RzLocal] порожній фід — вихід без публікації.", file=sys.stderr)
        sys.exit(1)

    # 8. Публікація.
    print("[RzLocal] 8) публікація у feed-data...")
    _publish(a.dry_run)
    print("[RzLocal] готово.")


if __name__ == "__main__":
    main()
