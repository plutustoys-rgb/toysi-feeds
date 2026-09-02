"""telegram_digest.py — дайджест-аналізатор усього, що пушилось у Telegram.

НАВІЩО (запит власника 2026-09-02): у Telegram сиплеться потік алертів від 10+ автоматик
(order-pipeline, watchdog, фіди, скрапери, дедлайни...). Власник хоче НЕ гортати стрічку, а
дістати «загальний висновок ПО ЗАПИТУ» — що взагалі відбувається, що гучне, що грошове, що
повторюється, де тиша. Цей скрипт читає append-лог, який `telegram_notify.py` веде для КОЖНОГО
надісланого повідомлення (`reports/telegram_alerts.md`), і зводить його у стислий висновок.

ДЖЕРЕЛО: `telegram_notify._log_alert_to_shared_folder` пише блоками
    `## YYYY-MM-DD HH:MM:SS — <source.py>` + текст + роздільник `---`.
Скрипт ДЖЕРЕЛО-АГНОСТИЧНИЙ: `--file <шлях>` дозволяє націлити будь-який telegram_alerts.md
(локальний АБО VPS-ний /opt/plutustoys/reports/telegram_alerts.md), бо алерти розділені —
локальні автоматики пишуть у локальний лог, VPS-ні — у VPS-ний. Дефолт — лог поряд зі скриптом.

READ-ONLY: лише читає лог, нічого не шле й не змінює. «Висновок» друкується у stdout
(далі його може підхопити дашборд або окрема кнопка-в-Telegram — це вже інший крок).

ЗАПУСК: python telegram_digest.py [--days N] [--file шлях] [--source підрядок]
    напр. python telegram_digest.py --days 7
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_LOG = Path(__file__).parent / "reports" / "telegram_alerts.md"
# Заголовок блоку: "## 2026-09-01 14:24:37 — rozetka_price_monitor.py"
_HEAD_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+[—-]\s+(.+?)\s*$")

# Класифікація за змістом (порядок = пріоритет: помилка важливіша за гроші тощо).
# Кожен запис: (мітка, емодзі, [підрядки-тригери у нижньому регістрі]).
_CATEGORIES = [
    ("error",   "🚨", ["🚨", "❌", "збій", "протух", "помилк", "критич", "fail", "timeout",
                        "не вдалося", "недоступ", "впав", "exception", "traceback", "authenticationfailed"]),
    ("money",   "💰", ["💰", "грн", "оплат", "дохід", "дохід", "платіж", "комісі", "баланс",
                        "замовлен", "продаж", "storno", "сторно", "повернення", "абонплат"]),
    ("warning", "⚠️", ["⚠️", "увага", "застаріл", "порушен", "дедлайн", "спливає", "закінч"]),
    ("ok",      "✅", ["✅", "готово", "успішно", "done", "опубліков", "відправлено"]),
]
_CAT_LABELS = {"error": "🚨 помилки/збої", "money": "💰 гроші", "warning": "⚠️ попередження",
               "ok": "✅ успіх", "info": "ℹ️ інфо"}


def parse_log(path: Path):
    """Повертає список записів {dt, source, text, category}. Порожньо, якщо файлу нема."""
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8", errors="replace")
    entries = []
    cur = None
    body = []
    for line in raw.splitlines():
        m = _HEAD_RE.match(line)
        if m:
            if cur:
                cur["text"] = "\n".join(body).strip()
                entries.append(cur)
            try:
                dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                dt = None
            cur = {"dt": dt, "source": m.group(2).strip(), "text": "", "category": "info"}
            body = []
        elif line.strip() == "---":
            continue
        elif cur is not None:
            body.append(line)
    if cur:
        cur["text"] = "\n".join(body).strip()
        entries.append(cur)
    for e in entries:
        e["category"] = _classify(e["text"])
    return entries


def _classify(text: str) -> str:
    # Класифікуємо за ЗАГОЛОВКОМ алерту (перший непорожній рядок), а не за всім тілом:
    # багато алертів ЦИТУЮТЬ вміст каналів/замовлень нижче, і ті цитати містять грошові
    # слова («грн», «замовлення»), що хибно фарбувало б попередження як «гроші»
    # (agent_watch «Код нічого не написав: ## [SEO…» → цитата, не грошовий сигнал).
    first = ""
    for ln in text.splitlines():
        if ln.strip():
            first = ln.strip().lower()
            break
    for label, _emoji, triggers in _CATEGORIES:
        if any(t in first for t in triggers):
            return label
    return "info"


def _norm(text: str) -> str:
    """Нормалізує текст для групування повторів: без цифр/пунктуації, стисло."""
    t = re.sub(r"\d+", "#", text.lower())
    t = re.sub(r"[^\w\s#]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:80]


def build_digest(entries, days: int, source_filter: str = "") -> str:
    now = datetime.now()
    cutoff = now - timedelta(days=days)
    win = [e for e in entries if e["dt"] and e["dt"] >= cutoff]
    if source_filter:
        win = [e for e in win if source_filter.lower() in e["source"].lower()]
    out = []
    title = f"📋 TELEGRAM-ДАЙДЖЕСТ — останні {days} дн."
    if source_filter:
        title += f" · джерело~«{source_filter}»"
    out.append(title)
    if not win:
        out.append(f"\nЗа вікном ({cutoff:%Y-%m-%d} → {now:%Y-%m-%d}) повідомлень нема.")
        if entries:
            last = max((e["dt"] for e in entries if e["dt"]), default=None)
            out.append(f"(усього в лозі {len(entries)} записів; останній {last:%Y-%m-%d %H:%M})"
                       if last else f"(усього в лозі {len(entries)} записів)")
        return "\n".join(out)

    span_lo = min(e["dt"] for e in win)
    span_hi = max(e["dt"] for e in win)
    by_cat = Counter(e["category"] for e in win)
    by_src = Counter(e["source"] for e in win)

    # ── Загальний висновок (авто з чисел) ──
    verdict = _verdict(win, by_cat, by_src, days)
    out.append(f"\n🧭 ВИСНОВОК: {verdict}")
    out.append(f"\nВсього {len(win)} повідомл. ({span_lo:%m-%d %H:%M} → {span_hi:%m-%d %H:%M}).")

    # ── За категоріями ──
    out.append("\nЗа типом:")
    for cat in ("error", "money", "warning", "ok", "info"):
        if by_cat.get(cat):
            out.append(f"  {_CAT_LABELS[cat]}: {by_cat[cat]}")

    # ── Топ-джерела ──
    out.append("\nТоп-джерела:")
    for src, cnt in by_src.most_common(6):
        latest = max((e for e in win if e["source"] == src), key=lambda e: e["dt"])
        snippet = latest["text"].replace("\n", " ")[:70]
        out.append(f"  • {src}: {cnt}× — «{snippet}»")

    # ── Повторювані проблеми (error/warning, згруповані за нормалізованим текстом) ──
    groups = defaultdict(list)
    for e in win:
        if e["category"] in ("error", "warning"):
            groups[(e["source"], _norm(e["text"]))].append(e)
    repeats = sorted(((k, v) for k, v in groups.items() if len(v) >= 3),
                     key=lambda kv: len(kv[1]), reverse=True)
    if repeats:
        out.append("\nПовторювані проблеми (≥3×):")
        for (src, _key), evs in repeats[:6]:
            snippet = evs[-1]["text"].replace("\n", " ")[:60]
            out.append(f"  ⟳ {len(evs)}× {src} — «{snippet}»")

    # ── Гроші (виносимо явно) ──
    money = [e for e in win if e["category"] == "money"]
    if money:
        out.append(f"\n💰 Грошові сигнали ({len(money)}):")
        for e in sorted(money, key=lambda e: e["dt"], reverse=True)[:5]:
            snippet = e["text"].replace("\n", " ")[:80]
            out.append(f"  • {e['dt']:%m-%d %H:%M} {e['source']}: «{snippet}»")

    return "\n".join(out)


def _verdict(win, by_cat, by_src, days: int) -> str:
    """Одне-два речення простою мовою — що головне за вікном."""
    parts = []
    total = len(win)
    err = by_cat.get("error", 0)
    money = by_cat.get("money", 0)
    if err and err >= total * 0.4:
        top_src, top_cnt = by_src.most_common(1)[0]
        parts.append(f"переважають помилки/збої ({err} з {total}), найгучніше — {top_src} ({top_cnt}×)")
    elif err:
        parts.append(f"{err} помилок/збоїв серед {total}")
    else:
        parts.append(f"{total} повідомлень, помилок нема")
    if money:
        parts.append(f"грошових сигналів {money} (перевір окремо)")
    else:
        parts.append("грошових сигналів нема")
    return "; ".join(parts) + "."


def main() -> int:
    ap = argparse.ArgumentParser(description="Дайджест того, що пушилось у Telegram (по запиту).")
    ap.add_argument("--days", type=int, default=7, help="Вікно в днях (дефолт 7).")
    ap.add_argument("--file", default=str(DEFAULT_LOG), help="Шлях до telegram_alerts.md.")
    ap.add_argument("--source", default="", help="Фільтр за підрядком назви джерела.")
    args = ap.parse_args()
    path = Path(args.file)
    entries = parse_log(path)
    if not entries and not path.exists():
        print(f"[digest] лог не знайдено: {path}", file=sys.stderr)
        return 1
    print(build_digest(entries, args.days, args.source))
    return 0


if __name__ == "__main__":
    sys.exit(main())
