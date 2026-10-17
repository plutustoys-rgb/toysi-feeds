"""run_marketplace_actions.py — ЛОКАЛЬНИЙ періодичний автоцикл ДІЙ у кабінетах EVA/ALLO.

Наказ власника 2026-08-31 (через SEO, SEO_CHANNEL.md): на відміну від Rozetka
(`run_rozetka_local.py`) і Prom («Імпорт за посиланням»), EVA й ALLO НЕ мали
періодичного автоциклу — SEO щодня руками (1) запускав повний імпорт EVA,
(2) проходив майстер «Зіставлення даних» ALLO, (3) подавав картки на модерацію.
Цей launcher робить те саме headless, по таймеру Windows (як RozetkaLocalChain).

ЩО РОБИТЬ (best-effort — збій/протухла сесія одного НЕ спиняє інший):
  1. EVA:  eva_cabinet_scraper.py --full-import   — повний імпорт «через посилання».
           (авто-імпорт EVA лише освіжає ціну/склад; НОВІ товари доходять до
           модерації ЛИШЕ через повний імпорт — тому робимо його періодично.)
  2. ALLO: allo_cabinet_scraper.py --auto-cycle    — авто-завершення майстра
           «Зіставлення даних» (категорії+характеристики) + подача «Нових» на
           модерацію.

БЕЗПЕКА СЕСІЇ: скрейпери працюють на збереженій storageState-сесії
(`.local_secrets/*_cabinet_state.json`, разовий `--login` власником у вікні).
Якщо сесія протухла — вони САМІ сигналять (Telegram) і ПРОПУСКАЮТЬ дію, ніколи
не діють наосліп (hardened 2026-08-31 на живому тесті протухлої сесії). Тому
таск безпечно тримати в розкладі навіть коли сесія протухла: буде сигнал+пропуск,
а не хибна дія. Кожен прогін пише звіт у AUDIT_REPORT_DIR (лічильники
оновлено/відхилено/помилок) — самодіагностика, а не мовчазний збій.

--dry-run: ганяє обидва скрейпери БЕЗ --apply (лише звіт, що БУДЕ зроблено —
жодних кліків у кабінеті). Для безпечної перевірки launcher'а.

БЕЗ ФІНАНСІВ: звіти кабінетних дій — лічильники, не cost/margin; тому
AUDIT_REPORT_DIR може вказувати у спільну Cowork-теку (SEO їх читає).
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
PY = sys.executable
DEFAULT_REPORT_DIR = r"C:\Users\smach\Claude\Projects\PlutusToys_avtonomiya\reports"


# Сторожовий таймер зависання, не поріг якості: 16-17.10 крок eva_category_check завис на ~17 год і заблокував увесь ланцюг
# (EVA повний імпорт, ALLO, перевірки сайту; таск не стартує нову копію, поки стара «Running»). Решта кроків тривають хвилини.
STEP_TIMEOUT_SEC = int(os.environ.get("MARKETPLACE_STEP_TIMEOUT_SEC", "1800"))
CHANNEL_FILE = Path(os.environ.get("EXECUTOR_CHANNEL_FILE") or (Path(DEFAULT_REPORT_DIR).parent / "EXECUTOR_CHANNEL.md"))


def _kill_tree(pid: int) -> None:
    """Знімає процес разом із дітьми (node-драйвер Playwright, браузер): subprocess.kill() на Windows дітей не чіпає."""
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)
    except Exception:  # noqa: BLE001
        pass


def _post_hang(label: str, timeout_sec: int, channel: Path | None = None) -> bool:
    """Запис `## [Автомат → Код] …` угорі EXECUTOR_CHANNEL.md (перед першим рядком, що ПОЧИНАЄТЬСЯ з «## »); один на добу на крок; атомарно."""
    import re
    from datetime import datetime
    channel = Path(channel or CHANNEL_FILE)
    try:
        with open(channel, encoding="utf-8", newline="") as _f:
            s = _f.read()
    except OSError:
        return False
    head = f"## [Автомат → Код] MarketplaceActions: крок «{label}» завис {datetime.now():%Y-%m-%d}"
    if head in s:
        return False
    nl = "\r\n" if "\r\n" in s else "\n"
    entry = (head + "\n" + f"Крок не завершився за {timeout_sec // 60} хв: процес і його діти зняті, ланцюг пішов далі. Без таймера один завислий крок блокував усі наступні запуски таска (16.10 20:30 → 17.10 14:30). Причину зависання (кабінет/браузер) розбирає Код.\n\n").replace("\n", nl)
    m = re.search(r"(?m)^## ", s)
    new = (s[:m.start()] + entry + s[m.start():]) if m else (s + nl + entry)
    try:
        tmp = channel.with_name(channel.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as _f:
            _f.write(new)
        os.replace(tmp, channel)
    except OSError:
        return False
    return True


def _run(label: str, cmd: list[str], timeout: int | None = None) -> int:
    """Один крок best-effort: друкує вивід, повертає returncode; виняток НЕ підіймає
    (щоб збій EVA не завадив ALLO і навпаки). Крок довший за timeout (STEP_TIMEOUT_SEC) знімається з дітьми, код 124, запис у канал."""
    timeout = timeout or STEP_TIMEOUT_SEC
    print(f"[marketplace-actions] {label}: {' '.join(cmd[1:])}")
    try:
        proc = subprocess.Popen(cmd, cwd=str(BASE_DIR), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding="utf-8", errors="replace")
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            try:
                out, _ = proc.communicate(timeout=30)
            except Exception:  # noqa: BLE001
                out = ""
            if out:
                print(out.rstrip())
            print(f"[marketplace-actions] {label}: ЗАВИС (>{timeout} с), процес знято, ланцюг іде далі.", file=sys.stderr)
            _post_hang(label, timeout)
            return 124
        if out:
            print(out.rstrip())
        if proc.returncode != 0:
            print(f"[marketplace-actions] {label}: код {proc.returncode} (скрейпер сам сигналить причину).",
                  file=sys.stderr)
        return proc.returncode
    except Exception as e:  # запуск процесу впав (нема python/скрипта тощо)
        print(f"[marketplace-actions] {label}: не вдалося запустити — {e}", file=sys.stderr)
        return 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="ганяти скрейпери БЕЗ --apply (лише звіт, без кліків у кабінеті)")
    a = ap.parse_args()

    # Звіти кабінетних дій → спільна Cowork-тека (SEO їх читає). Не перезаписуємо,
    # якщо env уже задано зовні (напр. запуск із розкладу з іншою текою).
    os.environ.setdefault("AUDIT_REPORT_DIR", DEFAULT_REPORT_DIR)
    Path(os.environ["AUDIT_REPORT_DIR"]).mkdir(parents=True, exist_ok=True)

    apply_flag = [] if a.dry_run else ["--apply"]
    if a.dry_run:
        print("[marketplace-actions] DRY-RUN: --apply НЕ передаю, кліків у кабінеті не буде.")

    rc_eva = _run("EVA повний імпорт",
                  [PY, str(BASE_DIR / "eva_cabinet_scraper.py"), "--full-import", *apply_flag])
    rc_allo = _run("ALLO автоцикл (зіставлення+модерація)",
                   [PY, str(BASE_DIR / "allo_cabinet_scraper.py"), "--auto-cycle", *apply_flag])

    # Звірка категорій кабінету EVA з фідом (лише читання; ≥1 товар у незіставлених категоріях → запис [Автомат → Код] у канал). Не впливає на код виходу.
    _run("EVA категорії кабінету ↔ фід", [PY, str(BASE_DIR / "eva_category_check.py")])
    # Машинна перевірка власного сайту очима покупця (лише читання; порушення → запис [Автомат → Код] у канал). Не впливає на код виходу.
    _run("Сайт: здоров'я очима покупця", [PY, str(BASE_DIR / "site_health_check.py")])
    # Доставка щоденних файлів GMC/Googlebot/GSC з VPS у спільну теку для SEO + запис [Автомат → SEO] у SEO_CHANNEL.md, якщо в gmc_summary є прапорець 🚩. Лише читання з VPS; не впливає на код виходу.
    _run("Файли VPS → спільна тека SEO", [PY, str(BASE_DIR / "daily_files_to_shared.py")])

    print(f"[marketplace-actions] Готово. EVA rc={rc_eva}, ALLO rc={rc_allo}. "
          f"Звіти: {os.environ['AUDIT_REPORT_DIR']}")
    # Ненульовий вихід лише якщо ОБИДВА впали (частковий успіх = ок для best-effort циклу).
    sys.exit(0 if (rc_eva == 0 or rc_allo == 0) else 1)


if __name__ == "__main__":
    main()
