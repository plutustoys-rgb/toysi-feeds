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


def _run(label: str, cmd: list[str]) -> int:
    """Один крок best-effort: друкує вивід, повертає returncode; виняток НЕ підіймає
    (щоб збій EVA не завадив ALLO і навпаки)."""
    print(f"[marketplace-actions] {label}: {' '.join(cmd[1:])}")
    try:
        r = subprocess.run(cmd, cwd=str(BASE_DIR), text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           encoding="utf-8", errors="replace")
        if r.stdout:
            print(r.stdout.rstrip())
        if r.returncode != 0:
            print(f"[marketplace-actions] {label}: код {r.returncode} (скрейпер сам сигналить причину).",
                  file=sys.stderr)
        return r.returncode
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

    print(f"[marketplace-actions] Готово. EVA rc={rc_eva}, ALLO rc={rc_allo}. "
          f"Звіти: {os.environ['AUDIT_REPORT_DIR']}")
    # Ненульовий вихід лише якщо ОБИДВА впали (частковий успіх = ок для best-effort циклу).
    sys.exit(0 if (rc_eva == 0 or rc_allo == 0) else 1)


if __name__ == "__main__":
    main()
