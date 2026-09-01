"""marketplace_requirements_gate.py — ОСТАТОЧНЕ структурне рішення проти рецидиву
«умову площадки не виконали / довідник не зберегли / обійшли дисципліну».

ЧОМУ саме гейт, а не памʼять/флаг (пряме зауваження власника 2026-09-01): памʼять і «механічні
флаги» — це ДИСЦИПЛІНА, а дисципліну сесія раз-по-раз обходить («ти навіть флаг ставив і все одно
обходите»). Тому enforcement НЕ має залежати від того, чи сесія згадала правило. Цей скрипт —
АВТОМАТИЧНИЙ гейт (як merge-guard хук): гониться щодня в пайплайні й гучно падає + сигналить, коли
для якогось фіда обов'язкова вимога площадки НЕ виконана структурно.

ЩО перевіряє для КОЖНОГО фіда, який вимагає авторитетного довідника площадки:
  1. довідник ЗБЕРЕЖЕНИЙ у репо (файл є й не порожній) — щоб не брати наново щоразу;
  2. є робоча АВТОПЕРЕВІРКА, і вона ПРОХОДИТЬ (verify exit 0) — щоб «зроблено» було доказовим.
Порушення enforced-платформи → звіт + Telegram + exit 1 (щоб пайплайн/чергове око це побачило).
Платформи зі статусом audit_pending — це ЧЕСНИЙ відкритий борг (довідник ще не заведено): вони
щодня видимі у звіті як «TODO дисципліни», але не спамлять алертом, поки їх не переведуть у enforced.

РЕЄСТР — єдине джерело правди, які фіди під дисципліною. Заводиш нову вимогу площадки → додаєш сюди
enforced-запис із довідником+verify; поки не завів — audit_pending кричить у звіті, що борг відкритий.

ЗАПУСК: python marketplace_requirements_gate.py   (0 = усі enforced ок; 1 = є порушення enforced)
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
_OUT_DIR = Path(os.environ.get("AUDIT_REPORT_DIR") or (BASE / "reports"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Єдине джерело правди. status: "enforced" (довідник+verify мають бути) | "audit_pending" (борг).
REGISTRY = [
    # enforced — площадка ВИМАГАЄ валідні значення зі свого дерева, товари ламаються без цього:
    {"platform": "EVA", "requirement": "дерево категорій EVA (авто-матч за назвою+BTK_id)",
     "reference": "eva_category_reference.csv", "verify": ["verify_eva_category_map.py"],
     "status": "enforced"},
    {"platform": "Google/Meta/Bing", "requirement": "google_product_category (офіційна таксономія Google)",
     "reference": "google_product_taxonomy.txt", "verify": ["verify_google_category_ids.py"],
     "status": "enforced"},
    # not_required — модель площадки НЕ вимагає збереженого зовнішнього дерева (аудит 2026-09-01):
    {"platform": "Prom", "requirement": "віддаємо ВЛАСНЕ дерево категорій — Prom гнучкий, зовнішнього матчити не треба",
     "reference": None, "verify": None, "status": "not_required"},
    {"platform": "Rozetka", "requirement": "rz_id РЕКОМЕНДОВАНИЙ (не обов'язк.); Rozetka матчить за назвою + membership вже схвалено",
     "reference": None, "verify": None, "status": "not_required"},
    {"platform": "ALLO", "requirement": "матч за назвою + кабінетне авто-зіставлення (allo_cabinet_scraper --automap)",
     "reference": None, "verify": None, "status": "not_required"},
]


def _telegram(msg: str) -> None:
    """Алерт про порушення дисципліни. ПОВАЖАЄ AUDIT_NO_TELEGRAM=1 — щоб локальні/тестові прогони
    НЕ спамили власника (реальний інцидент 2026-09-01: тест-порушення надіслало хибний алерт).
    У проді гейт запускають БЕЗ AUDIT_NO_TELEGRAM → сигнал доходить."""
    if os.environ.get("AUDIT_NO_TELEGRAM") == "1":
        print("[mp-gate] AUDIT_NO_TELEGRAM=1 — алерт НЕ надіслано (локальний/тестовий прогін).")
        return
    try:
        from telegram_notify import send_telegram_message
        send_telegram_message(msg)
    except Exception as e:  # noqa: BLE001 — best-effort, звіт на диску вже є
        print(f"[mp-gate] Telegram не надіслано (не критично): {e}", file=sys.stderr)


def check_enforced(entry: dict) -> list:
    """Повертає список порушень для enforced-платформи (порожній = ок)."""
    problems = []
    ref = entry.get("reference")
    if not ref:
        return ["enforced без reference — помилка реєстру"]
    ref_path = BASE / ref
    if not ref_path.exists() or ref_path.stat().st_size == 0:
        problems.append(f"довідник відсутній/порожній: {ref}")
    verify = entry.get("verify")
    if verify:
        script = BASE / verify[0]
        if not script.exists():
            problems.append(f"verify-скрипт відсутній: {verify[0]}")
        else:
            env = dict(os.environ, AUDIT_NO_TELEGRAM="1")  # verify сам не має слати свій алерт із гейта
            try:
                r = subprocess.run([sys.executable, str(script)] + list(verify[1:]),
                                   cwd=str(BASE), capture_output=True, text=True, timeout=180, env=env,
                                   encoding="utf-8", errors="replace")  # Windows cp1251 інакше валить кирилицю verify
                if r.returncode != 0:
                    tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
                    problems.append(f"verify НЕ пройшов ({verify[0]}, exit {r.returncode}): {tail[0][:120]}")
            except Exception as e:  # noqa: BLE001
                problems.append(f"verify не запустився ({verify[0]}): {e}")
    return problems


def main() -> int:
    now = datetime.now()
    lines = [f"# Гейт вимог маркетплейсів — {now.strftime('%Y-%m-%d %H:%M')}", ""]
    violations = []       # порушення enforced-платформ (алерт)
    pending = []          # відкритий борг аудиту (видимо, без алерту)

    lines.append("## Enforced (довідник збережено + автоперевірка)")
    for e in REGISTRY:
        if e["status"] != "enforced":
            continue
        probs = check_enforced(e)
        if probs:
            violations.append((e["platform"], probs))
            lines.append(f"- ❌ **{e['platform']}** ({e['requirement']}): " + "; ".join(probs))
        else:
            lines.append(f"- ✅ {e['platform']} — {e['reference']} + verify OK")

    lines.append("")
    lines.append("## Not required (модель площадки не вимагає збереженого зовнішнього дерева — аудит)")
    for e in REGISTRY:
        if e["status"] == "not_required":
            lines.append(f"- ✅ {e['platform']}: {e['requirement']}")

    lines.append("")
    lines.append("## Audit pending (борг дисципліни — вимогу ще НЕ з'ясовано/не заведено)")
    pend_any = False
    for e in REGISTRY:
        if e["status"] != "audit_pending":
            continue
        pend_any = True
        pending.append(e["platform"])
        lines.append(f"- ⚠ {e['platform']}: {e['requirement']} — з'ясувати + завести довідник+verify")
    if not pend_any:
        lines.append("- (порожньо — усі фіди класифіковано)")

    lines.append("")
    lines.append(f"**Підсумок:** enforced-порушень {len(violations)}, борг аудиту {len(pending)}.")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = _OUT_DIR / "marketplace_requirements_gate.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"[mp-gate] Звіт: {report}")
    print(f"[mp-gate] enforced-порушень: {len(violations)} | борг аудиту: {len(pending)}")

    if violations:
        who = ", ".join(p for p, _ in violations)
        _telegram(f"🔴 Гейт вимог маркетплейсів: ПОРУШЕННЯ у {who} — довідник/автоперевірка зламані. "
                  f"Товари в цих категоріях будуть порожні. Див. marketplace_requirements_gate.md")
        print("[mp-gate] РЕЗУЛЬТАТ: ❌ є порушення enforced-платформ (див. вище).")
        return 1
    print("[mp-gate] РЕЗУЛЬТАТ: ✅ усі enforced-платформи в нормі "
          f"(відкритий борг аудиту: {len(pending)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
