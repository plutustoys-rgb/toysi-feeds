# -*- coding: utf-8 -*-
"""reel_guard.py — щоденний сторож черги рілсів на VPS: бачить утримані рілси ДО слота й (з брамою REEL_GUARD=1) автоматично збирає заміну.

ЧОМУ (факти з VPS 23.11.2026): слоти 13.11, 17.11, 20.11 публікація стояла — у рілсах зник товар (22118 / 236460), `reel_autopublish.check_item` правильно утримував їх, а алерт
лягав лише в reports/telegram_alerts.md на VPS (режим Telegram «лише замовлення» його не пускає; health_check показує категорію warning як ℹ️, яку звіт не друкує) → 10 днів простою
непомітно. Автозбирач `reel_weekly` добирає чергу раз на тиждень (нд) і не виселяє «притримані» ручні рілси. Слово власника 23.11: «перевірте та зробіть автоматизацію».

ЩО РОБИТЬ (запуск щодня ≈12:00, reel-guard.timer; oneshot):
  1. ПЕРЕВІРКА (завжди, без запису в чергу). Для кожного рілса в reels_queue зі not_before ≤ найближчий слот (вт/пт 18:30) + 3 доби запускає ТЕ САМЕ, що publisher:
     reel_weekly.check_queue_item = reel_autopublish.refresh_prices + check_item на тимчасовій копії (рілс у черзі не змінюється). Ціни, які publisher сам виправить (refresh_prices), проблемою НЕ є.
  2. ВИДИМІСТЬ (завжди, крім --dry): reels_pending/_guard_state.json (його читає vps_health → health_check 🟠 для Коду/SMM), рядки в reports/reel_guard_log.jsonl,
     сповіщення «⚠️ reel_guard: …» (категорія warning у telegram_digest; у Telegram власника їх НЕ шле режим «лише замовлення» — вони для Коду/SMM).
  3. ЗАМІНА (лише з REEL_GUARD=1; за замовчуванням ВИМКНЕНО → режим «спостереження»: пп.1–2, жодних змін у черзі). Для рілса, ЗІБРАНОГО автозбирачем (є спека reels_pending/_specs/<ім'я>.json,
     id спеки = id verify), у якого лишилась проблема з ТОВАРОМ (зник з наявності / ціна не збігається й refresh_prices не лікує / ціни нема в підписі):
       а) якщо проблема лише в ціні й живі ціни збираються зі спеки (reel_batch.build_manifest/build_caption, як reel_weekly.refresh_queue) — підпис і verify переписуються без рендера;
       б) інакше ЗБИРАЄТЬСЯ ЗАМІНА тією ж логікою, що reel_weekly (reel_weekly.run_build: ті самі теми/відбір товарів/ротація СХВАЛЕНИХ сцен бібліотеки/рендер reel_build) на ТОЙ САМИЙ слот
          (publish_date = not_before старого) і ТУ САМУ тему (якщо в ній ≥3 придатних товарів, інакше — звичайний вибір теми); готова заміна проходить перевірку publisher'а ДО запису в чергу;
       в) лише ПІСЛЯ успішної заміни старий рілс виселяється в reels_pending/_evicted/ (reel_weekly.evict_to_staging; json першим), заміна успадковує mtime старого (порядок черги publisher'а).
     Збій збірки/перевірки/запису → старий рілс ЛИШАЄТЬСЯ в черзі (нічого не втрачено), сповіщення, наступна спроба не раніше ніж через добу.
     Ліміти: ≤1 спроба за добу на слот, ≤2 за добу всього (спроби рахуються й невдалі — навантаження ffmpeg на VPS), ≤3 невдалі спроби на рілс (далі лише сигнал), масове утримання (≥2 і ≥ половини рілсів збирача) = схоже на проблему даних сайту → заміни зупинено.
     НЕ чіпає: ручні рілси (SMM: нема спеки збирача або id не збігаються), рілси з `.publishing`, опубліковані (published.json / тека published/), рілси поза горизонтом.
  --dry: нічого не пише (ні черга, ні стан, ні журнал, ні сповіщення, ні збірка) — друкує план.

ЗАПУСК:  python reel_guard.py [--dry] [--queue-dir …] [--staging-dir …]   ·   REEL_GUARD=1 у /opt/plutustoys/.env вмикає заміни.
ВІДКАТ: прибрати REEL_GUARD з .env (або =0) — повертається режим спостереження; `systemctl disable --now reel-guard.timer` — зупиняє й перевірки."""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reel_library  # noqa: E402
import reel_weekly as rw  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GATE_ENV = "REEL_GUARD"
SLOT_HOUR, SLOT_MIN = 18, 30            # reel-autopublish.timer: Tue,Fri 18:30
HORIZON_DAYS = 3                        # перевіряємо рілси зі not_before ≤ найближчий слот + 3 доби
MAX_PER_DAY = 2                         # спроб заміни за добу всього
MAX_PER_SLOT_DAY = 1                    # спроб заміни за добу на слот (= на рілс)
MAX_FAILED_PER_REEL = 3                 # невдалих спроб заміни одного рілса за ATTEMPT_KEEP_DAYS: далі не пробуємо (лише сигнал) — захист від щоденного зациклення збірки
MASS_MIN = 2                            # масове утримання: ≥ MASS_MIN і ≥ половини рілсів збирача → не діємо (схоже на проблему даних сайту; як reel_weekly.refresh_queue: ≥2)
MIN_PRICES = 1000                       # як reel_weekly: менше цін сайту = дані неповні, не вгадуємо
LOCK_STALE_H = 3
ATTEMPT_KEEP_DAYS = 30
PROBLEM_MAX = 160
# проблеми, які означають «товар/ціна» (заміна має сенс); решта (файли, підпис, маніфест, збій перевірки) — лише сигнал, без заміни
PRODUCT_PROBLEM_PREFIXES = ("товару ", "ціна товару ", "ціни ")
PRICE_PROBLEM_PREFIXES = ("ціна товару ", "ціни ")
LOG_FILE = os.environ.get("REEL_GUARD_LOG") or os.path.join(HERE, "reports", "reel_guard_log.jsonl")


def gate_on():
    return os.environ.get(GATE_ENV, "").strip() == "1"


def _alert(key, text):
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=24 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[reel_guard] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


def next_slot_date(now):
    """Дата найближчого слота publisher'а (вт/пт 18:30), який ще не минув."""
    d = now.date()
    if (now.hour, now.minute) >= (SLOT_HOUR, SLOT_MIN):
        d += timedelta(days=1)
    return rw._next_slot_on_or_after(d)


# ───────────────────────── стан, журнал, блокування ─────────────────────────
def _state_path(staging_dir):
    return os.path.join(staging_dir, "_guard_state.json")


def _read_state_file(path):
    """-> dict стану або None (нема/нечитабельний/не тієї форми)."""
    try:
        st = json.load(open(path, encoding="utf-8"))
        if isinstance(st, dict):
            st.setdefault("attempts", [])
            st.setdefault("held", {})
            if isinstance(st["attempts"], list) and isinstance(st["held"], dict):
                return st
    except (OSError, ValueError):
        pass
    return None


def load_state(staging_dir):
    """-> (стан, статус). «fresh» — файлу стану ніколи не було (перший запуск); «ok»; «recovered» — основний файл ПОШКОДЖЕНО або ВТРАЧЕНО, лічильники взято з _guard_state.json.bak (або порожні):
    лічильники спроб/лімітів могли загубитись, тому у цей запуск заміни НЕ виконуємо (fail-closed, лише сигнал)."""
    p = _state_path(staging_dir)
    st = _read_state_file(p)
    if st is not None:
        return st, "ok"
    bak = _read_state_file(p + ".bak")
    if os.path.exists(p) or bak is not None:
        return (bak or {"attempts": [], "held": {}}), "recovered"
    return {"attempts": [], "held": {}}, "fresh"


def save_state(staging_dir, st):
    """Атомарно (tmp + os.replace); попередню ВАЛІДНУ версію перед цим копіюємо в .bak (битий файл у .bak НЕ потрапляє)."""
    os.makedirs(staging_dir, exist_ok=True)
    p = _state_path(staging_dir)
    if _read_state_file(p) is not None:
        try:
            import shutil
            shutil.copyfile(p, p + ".bak")
        except OSError:
            pass
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _log(event, now, **kw):
    """Рядок у reports/reel_guard_log.jsonl (best-effort: збій запису журналу не валить сторож)."""
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"at": now.isoformat(timespec="seconds"), "event": event, **kw}, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[reel_guard] журнал не записано: {type(e).__name__}", file=sys.stderr)


def _acquire_lock(staging_dir, now):
    """Один запуск за раз (O_EXCL). Застарілий (> LOCK_STALE_H) замок знімаємо. -> шлях замка або None (зайнято)."""
    os.makedirs(staging_dir, exist_ok=True)
    p = os.path.join(staging_dir, "_guard.lock")
    for _ in range(2):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as fh:
                fh.write(now.isoformat(timespec="seconds"))
            return p
        except FileExistsError:
            try:
                if (datetime.now().timestamp() - os.path.getmtime(p)) / 3600 > LOCK_STALE_H:
                    os.remove(p)
                    continue
            except OSError:
                pass
            return None
    return None


def _age_h(iso, now):
    try:
        return (now - datetime.fromisoformat(iso)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def _within(iso, now, hours):
    """Чи минуло менше `hours` годин від `iso` (свіжа спроба має вік 0.0 — це не «невідомо»)."""
    h = _age_h(iso, now)
    return h is not None and h < hours


# ───────────────────────── перевірка черги ─────────────────────────
def _read_spec(staging_dir, name, manifest):
    """Спека автозбирача для рілса або None (= ручний рілс: нема спеки / не тієї форми / id не збігаються з verify). Той самий маркер, що в reel_weekly.refresh_queue (спека в staging/_specs/)."""
    try:
        spec = json.load(open(os.path.join(staging_dir, "_specs", name + ".json"), encoding="utf-8"))
        if not isinstance(spec, dict) or str(spec.get("queue_name")) != name or not spec.get("first_scene"):
            return None
        a = {str(i["id"]) for i in spec["items"]}
        b = {str(v["id"]) for v in manifest["verify"]}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    return spec if a and a == b else None


def _parse_nb(nb):
    """not_before → (datetime|None, помилка|None); tz-aware → місцевий час, як у publisher."""
    if not nb:
        return None, None
    try:
        dt = datetime.fromisoformat(str(nb))
    except ValueError:
        return None, f"not_before «{nb}» нечитабельний"
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt, None


def _trim(problems):
    return [str(p)[:PROBLEM_MAX] for p in problems][:4]


def classify_queue(queue_dir, staging_dir, prices, now, heal=False):
    """Перевірка черги → список записів. `heal`: чи справді лікувати ціни зі спеки (лише в режимі заміни; пише в чергу).
    Запис: {name, nb (date|None), state: ok|held|far, problems, built (спека є), spec, replaceable, healable, healed}. Опубліковані й `done` пропускаються."""
    from pathlib import Path
    import reel_autopublish as ra
    index = {str(i): {"price": float(p), "name": ""} for i, p in prices.items()}
    slot = next_slot_date(now)
    horizon_end = slot + timedelta(days=HORIZON_DAYS)
    done = {p.get("name") for p in ra._load_published(Path(queue_dir)) if isinstance(p, dict)}
    out = []
    for name, mpath in rw._queue_manifests(queue_dir):
        if name in done:
            continue
        e = {"name": name, "nb": None, "state": "ok", "problems": [], "built": False, "spec": None, "replaceable": False, "healable": False, "healed": False}
        out.append(e)
        if os.path.exists(os.path.join(queue_dir, name + ".publishing")):
            e.update(state="held", problems=["є .publishing: результат публікації невідомий — потрібна людина (перевірити Instagram)"])
            continue
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            e.update(state="held", problems=["ім'я файлу має бути лише a-z, 0-9, _ і -"])
            continue
        try:
            manifest = json.loads(Path(mpath).read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("маніфест має бути об'єктом")
        except (OSError, ValueError) as ex:
            e.update(state="held", problems=[f"маніфест нечитабельний ({type(ex).__name__})"])
            continue
        nb_dt, err = _parse_nb(manifest.get("not_before"))
        if err:
            e.update(state="held", problems=[err])
            continue
        e["nb"] = nb_dt.date() if nb_dt else None
        if e["nb"] and e["nb"] > horizon_end:
            e["state"] = "far"                                 # поза горизонтом: ще не час
            continue
        e["spec"] = _read_spec(staging_dir, name, manifest)
        e["built"] = e["spec"] is not None
        problems = rw.check_queue_item(queue_dir, name, mpath, manifest, index)
        if problems and e["built"] and all(str(p).startswith(PRICE_PROBLEM_PREFIXES) for p in problems):
            e["healable"] = _spec_heal_possible(e["spec"], prices)
            if e["healable"] and heal:
                if _heal_from_spec(queue_dir, name, mpath, manifest, e["spec"], prices):
                    problems = rw.check_queue_item(queue_dir, name, mpath, json.loads(Path(mpath).read_text(encoding="utf-8")), index)
                    e["healed"] = not problems
        if problems:
            e.update(state="held", problems=problems)
            product_only = all(str(p).startswith(PRODUCT_PROBLEM_PREFIXES) for p in problems)
            e["replaceable"] = bool(e["built"] and product_only and not (e["healable"] and not heal))      # ціну, яку лікує спека, у режимі спостереження ще не «заміна»
    return out


def _spec_heal_possible(spec, prices):
    """Чи збираються живі підпис і verify зі спеки (товари є в наявності) — чиста перевірка, нічого не пише."""
    import reel_batch
    try:
        reel_batch.build_manifest(spec, prices)
        reel_batch.build_caption(spec, prices)
        return True
    except (KeyError, SystemExit, TypeError, AttributeError, ValueError):
        return False


def _heal_from_spec(queue_dir, name, mpath, manifest, spec, prices):
    """Як reel_weekly.refresh_queue: verify і підпис зі спеки за живими цінами; порядок запису — підпис, потім маніфест (збій між ними → check_item утримає, fail-closed)."""
    import reel_batch
    try:
        new_manifest = reel_batch.build_manifest(spec, prices)
        new_caption = reel_batch.build_caption(spec, prices)
        manifest = dict(manifest)
        manifest["verify"] = new_manifest["verify"]
        rw._write_atomic(os.path.join(queue_dir, name + ".caption.txt"), new_caption + "\n")
        rw._write_atomic(str(mpath), json.dumps(manifest, ensure_ascii=False))
        return True
    except (KeyError, SystemExit, TypeError, AttributeError, ValueError, OSError):
        return False


# ───────────────────────── заміна ─────────────────────────
def replacement_slot(nb, now):
    """Дата слота заміни: not_before старого, якщо він у майбутньому (або сьогодні до 18:30), інакше найближчий слот; завжди вт/пт."""
    base = nb if nb and nb >= now.date() else now.date()
    if base == now.date() and (now.hour, now.minute) >= (SLOT_HOUR, SLOT_MIN):
        base += timedelta(days=1)
    return rw._next_slot_on_or_after(base)


def _library_ready():
    """-> None, якщо схвалена бібліотека на місці й ціла; інакше текст причини (заміну не збираємо)."""
    d = reel_library.library_dir()
    if not d or not os.path.isdir(str(d)):
        return "бібліотеку схвалених сцен не залито"
    try:
        reel_library.check_integrity(require=True)
        if not rw.library_rotation() or not rw.library_end_choices():
            return "у бібліотеці нема схвалених несезонних сцен з end_mascot PNG"
    except RuntimeError as e:
        return str(e)[:200]
    return None


def build_replacement(e, slot, queue_dir, staging_dir, prices, now, feed_file=None, build_kw=None):
    """Збирає ОДНУ заміну рілса e на слот `slot` (reel_weekly.run_build, та сама тема). Готовий рілс проходить перевірку publisher'а у staging і лише тоді кладеться в чергу.
    -> (нова назва | None, причина невдачі | None). Нічого не виселяє."""
    failures = []
    index = {str(i): {"price": float(p), "name": ""} for i, p in prices.items()}
    kw = dict(build_kw or {})
    kw.setdefault("specs_dir", "")        # спеки репо (reels_specs) на VPS не враховуємо — як run_vps

    def on_built(sp):
        name = sp["queue_name"]
        mp = os.path.join(staging_dir, name + ".json")
        manifest = json.load(open(mp, encoding="utf-8"))
        probs = rw.check_queue_item(staging_dir, name, mp, manifest, index)
        if probs:
            raise RuntimeError("заміна не пройшла перевірку publisher'а: " + "; ".join(str(p)[:80] for p in probs)[:240])
        rw.publish_to_queue(name, staging_dir, queue_dir)

    try:
        built = rw.run_build(1, staging_dir, dry_run=False, today=now.date(), state_dir=staging_dir, scan_dirs=(queue_dir,), feed_file=feed_file,
                             slots=[slot], prefer_theme=e["name"].rsplit("_", 1)[0], state_seed=lambda st, keys: rw.seed_state_from_queue(st, queue_dir, keys),
                             on_built=on_built, failures=failures, **kw)
    except MemoryError:
        raise
    except (Exception, SystemExit) as ex:  # noqa: BLE001
        return None, f"{type(ex).__name__}: {str(ex)[:200]}"
    if not built:
        return None, "; ".join(failures)[:300] or "збірка нічого не дала"
    return built[0]["queue_name"], None


def _evict_after_replace(queue_dir, staging_dir, old, new):
    """Заміна `new` уже в черзі: переносимо mtime старого на нову (порядок черги publisher'а: mtime, ім'я), потім виселяємо старий. Нова відсутня в черзі → нічого не виселяємо."""
    new_json, old_json = os.path.join(queue_dir, new + ".json"), os.path.join(queue_dir, old + ".json")
    if not os.path.isfile(new_json):
        return False
    try:
        stt = os.stat(old_json)
        os.utime(new_json, ns=(stt.st_atime_ns, stt.st_mtime_ns))
    except OSError:
        pass
    rw.evict_to_staging(queue_dir, staging_dir, old)
    return True


# ───────────────────────── основний запуск ─────────────────────────
def run(queue_dir=rw.VPS_QUEUE, staging_dir=rw.VPS_STAGING, now=None, dry=False, enabled=None, prices=None, alert=None, feed_file=None, build_kw=None):
    """Один запуск сторожа. Не кидає назовні вичерпно-очікуваних збоїв: перевірка/збірка fail-closed. -> dict з підсумком (для тестів і друку)."""
    now = now or datetime.now()
    alert = alert or _alert
    enabled = gate_on() if enabled is None else enabled
    res = {"mode": "dry" if dry else ("active" if enabled else "observe"), "slot": None, "ok": [], "held": {}, "replaced": [], "failed": [], "healed": [], "skipped": None, "evict_finished": []}
    if not os.path.isdir(queue_dir):
        res["skipped"] = "нема теки черги"
        return res
    if prices is None:
        import reel_batch
        prices = reel_batch.site_prices()
    if len(prices) < MIN_PRICES:
        res["skipped"] = f"ціни сайту неповні ({len(prices)})"
        if not dry:
            alert("reel_guard_prices", f"⚠️ reel_guard: ціни сайту неповні ({len(prices)} < {MIN_PRICES}) — перевірку черги пропущено\nНе вгадуємо, що придатне; наступний запуск повторить.")
            _log("skipped_prices", now, prices=len(prices))
        return res
    lock = None
    if not dry:
        lock = _acquire_lock(staging_dir, now)
        if lock is None:
            res["skipped"] = "інший запуск ще триває (_guard.lock)"
            return res
    try:
        return _run_locked(res, queue_dir, staging_dir, now, dry, enabled, prices, alert, feed_file, build_kw)
    finally:
        if lock:
            try:
                os.remove(lock)
            except OSError:
                pass


def _recover_interrupted(st, queue_dir, staging_dir, now, lock_held=False):
    """Спроба заміни, що лишилась «in_progress» (процес убито: OOM/SIGKILL/TimeoutStartSec/MemoryError — запис про завершення не зроблено), — це НЕВДАЛА спроба (вона вже рахується в ліміті,
    бо ok=False). Тут ще: (а) якщо в черзі з'явився рівно один новий повний рілс зі спекою — заміна встигла лягти в чергу: позначаємо ok, і наступний крок лише виселить старий (без другої збірки);
    (б) інакше прибираємо осиротіле: спеки _specs/<ім'я>.json, яких не було перед спробою й яких нема в черзі (інакше їх id «згоряють» у used_ids і ім'я лишається зайнятим), недоплетені
    staging/<ім'я>.{mp4,caption.txt,json} і *.incoming у черзі. -> кількість відновлених спроб.
    ЛИШЕ під спільним замком збірки reel_build.lock (його вже тримає викликач із `lock_held=True` або беремо тут): чужий живий рендер (reel_weekly пише спеку в _specs і mp4 у staging)
    інакше був би прийнятий за «осиротіле» й видалений. Замок зайнятий/недоступний → нічого не змінюємо, спроба лишається in_progress (рахується як невдала) до наступного запуску.
    «Заміною, що встигла лягти» визнаємо лише рілс ТІЄЇ Ж теми (префікс імені) з not_before == слоту спроби — легітимний рілс weekly іншої теми/слоту заміною не вважається."""
    if not any(isinstance(a, dict) and a.get("in_progress") for a in st["attempts"]):
        return 0
    own = None
    if not lock_held:
        try:
            own = rw.acquire_build_lock(staging_dir)
        except rw.BuildLockError:
            own = None
        if own is None:
            _log("recover_skipped", now, reason="замок збірки зайнятий або недоступний")
            return 0
    try:
        return _recover_locked(st, queue_dir, staging_dir, now)
    finally:
        rw.release_build_lock(own)


def _manifest_slot(queue_dir, name):
    try:
        return str(json.load(open(os.path.join(queue_dir, name + ".json"), encoding="utf-8")).get("not_before") or "")[:10]
    except (OSError, ValueError, AttributeError):
        return ""


def _recover_locked(st, queue_dir, staging_dir, now):
    fixed = 0
    for a in st["attempts"]:
        if not (isinstance(a, dict) and a.get("in_progress")):
            continue
        fixed += 1
        specs_dir = os.path.join(staging_dir, "_specs")
        try:
            spec_names = {f[:-5] for f in os.listdir(specs_dir) if f.endswith(".json")}
        except OSError:
            spec_names = set()
        qnow = {n for n, _ in rw._queue_manifests(queue_dir)}
        landed = [n for n in sorted(qnow - set(a.get("queue_before") or [])) if n in spec_names
                  and n.rsplit("_", 1)[0] == a.get("theme") and _manifest_slot(queue_dir, n) == a.get("slot")
                  and os.path.isfile(os.path.join(queue_dir, n + ".mp4")) and os.path.isfile(os.path.join(queue_dir, n + ".caption.txt"))]
        if len(landed) == 1:
            a.update(new=landed[0], ok=True, recovered=True)
        else:
            for n in sorted(spec_names - set(a.get("specs_before") or []) - qnow):
                for victim in (os.path.join(specs_dir, n + ".json"), *(os.path.join(staging_dir, n + e) for e in (".mp4", ".caption.txt", ".json"))):
                    try:
                        os.remove(victim)
                    except OSError:
                        pass
                try:
                    for f in os.listdir(queue_dir):
                        if f.startswith(n + ".") and f.endswith(".incoming"):
                            os.remove(os.path.join(queue_dir, f))
                except OSError:
                    pass
            a.update(new=None, ok=False, error="перервано (kill/OOM/TimeoutStartSec/MemoryError): запис про завершення не зроблено")
        a["in_progress"] = False
        a.pop("specs_before", None)
        a.pop("queue_before", None)
        _log("attempt_recovered", now, old=a.get("old"), ok=a.get("ok"), new=a.get("new"))
    if fixed:
        save_state(staging_dir, {**st, "at": now.isoformat(timespec="seconds")})
    return fixed


def _summarize(entries, prev_held, now, slot):
    """-> (перевірені, утримані, {ім'я: опис утримання зі збереженим first_seen}, придатні для найближчого слота)."""
    checked = [e for e in entries if e["state"] != "far"]
    held = [e for e in checked if e["state"] == "held"]
    new_held = {}
    for e in held:
        old = prev_held.get(e["name"]) or {}
        new_held[e["name"]] = {"first_seen": old.get("first_seen") or now.isoformat(timespec="seconds"), "problems": _trim(e["problems"]), "manual": not e["built"],
                               "replaceable": bool(e["replaceable"]), "not_before": e["nb"].isoformat() if e["nb"] else None}
    covered = [e for e in checked if e["state"] == "ok" and (e["nb"] is None or e["nb"] <= slot)]
    return checked, held, new_held, covered


def _run_locked(res, queue_dir, staging_dir, now, dry, enabled, prices, alert, feed_file, build_kw):
    active = enabled and not dry
    slot = next_slot_date(now)
    res["slot"] = slot.isoformat()
    st, st_status = load_state(staging_dir)
    res["state_status"] = st_status
    if st_status == "recovered":
        active = False        # fail-closed: стан сторожа пошкоджено/втрачено — лічильники ліміту могли обнулитись, заміни цього запуску не виконуємо
        if not dry:
            alert("reel_guard_state", "⚠️ reel_guard: стан сторожа (_guard_state.json) пошкоджено або втрачено — лічильники взято з .bak, заміни цього запуску вимкнено\nПеревірка й сповіщення працюють; наступний запуск продовжить із відновленим станом.")
            _log("state_recovered", now)
    if active:        # у режимі спостереження/dry нічого не прибираємо й не визнаємо (лише читання + власні звіти)
        _recover_interrupted(st, queue_dir, staging_dir, now)
    st["attempts"] = [a for a in st["attempts"] if isinstance(a, dict) and (_age_h(a.get("at"), now) or 0) < ATTEMPT_KEEP_DAYS * 24]
    entries = classify_queue(queue_dir, staging_dir, prices, now, heal=active)
    checked, held, new_held, covered = _summarize(entries, st["held"], now, slot)
    res["ok"] = [e["name"] for e in checked if e["state"] == "ok"]
    res["healed"] = [e["name"] for e in checked if e["healed"]]
    res["held"] = {n: dict(v) for n, v in new_held.items()}
    res["slot_covered"] = bool(covered)

    # --- заміни (лише REEL_GUARD=1, не dry) ---
    if active:
        _do_replacements(res, entries, held, st, queue_dir, staging_dir, prices, now, alert, feed_file, build_kw, new_held)
        if res["replaced"] or res["evict_finished"]:      # черга змінилась: підсумок (придатні, утримані, чи закрито слот) — за свіжою перевіркою, а не за станом ДО заміни
            entries = classify_queue(queue_dir, staging_dir, prices, now, heal=False)
            checked, held, new_held, covered = _summarize(entries, st["held"], now, slot)
            res["ok"] = [e["name"] for e in checked if e["state"] == "ok"]
            res["held"] = {n: dict(v) for n, v in new_held.items()}
            res["slot_covered"] = bool(covered)
    st.update({"at": now.isoformat(timespec="seconds"), "mode": res["mode"], "slot": slot.isoformat(), "checked": len(checked), "ok": res["ok"], "held": new_held,
               "slot_covered": res["slot_covered"]})

    # --- видимість (не dry): стан для vps_health/health_check, журнал, сповіщення ---
    if dry:
        _print_plan(res, entries, now)
        return res
    save_state(staging_dir, st)
    _log("run", now, mode=res["mode"], slot=res["slot"], checked=len(checked), ok=res["ok"], held=sorted(new_held), replaced=res["replaced"], failed=res["failed"], healed=res["healed"])
    for name in res["healed"]:
        _log("healed", now, name=name)
    for name, h in new_held.items():
        days = (_age_h(h["first_seen"], now) or 0) / 24
        note = ("ручний рілс (не збирач): замінити товар у підписі/маніфесті або прибрати — SMM" if h["manual"] else
                ("збирач: заміну збере сторож (REEL_GUARD=1)" if h["replaceable"] and active else
                 ("збирач: проблема товару — заміну збере сторож після ввімкнення REEL_GUARD=1" if h["replaceable"] else "потрібна перевірка людиною")))
        alert(f"reel_guard_held:{name}", f"⚠️ reel_guard: рілс {name} утримано (слот {slot:%d.%m}, {days:.0f} дн у черзі)\n" + "; ".join(h["problems"]) + f"\n{note}")
        _log("held", now, name=name, problems=h["problems"], manual=h["manual"], days=round(days, 1))
    if not covered:
        alert(f"reel_guard_slot:{slot.isoformat()}", f"⚠️ reel_guard: на слот {slot:%d.%m} {SLOT_HOUR}:{SLOT_MIN:02d} нема придатного рілса в черзі (перевірено {len(checked)}, утримано {len(held)})\n"
              "Публікація цього слота не відбудеться, якщо не з'явиться придатний рілс.")
        _log("slot_uncovered", now, slot=res["slot"], checked=len(checked), held=sorted(new_held))
    return res


def _do_replacements(res, entries, held, st, queue_dir, staging_dir, prices, now, alert, feed_file, build_kw, new_held):
    cands = sorted([e for e in held if e["replaceable"]], key=lambda e: (e["nb"] or date.min, e["name"]))
    vps_built = len([e for e in entries if e["built"] and e["state"] != "far"])
    if len(cands) >= MASS_MIN and len(cands) * 2 >= vps_built:
        alert("reel_guard_mass", f"⚠️ reel_guard: утримано {len(cands)} з {vps_built} рілсів збирача — схоже на проблему даних сайту, заміни зупинено\n" + ", ".join(e["name"] for e in cands))
        _log("mass_stop", now, held=[e["name"] for e in cands], built=vps_built)
        return
    if not cands:
        return
    # завершення перерваної заміни: нова вже в черзі, старий ще не виселено (збій між кроками) — лише виселити, нову не збирати вдруге
    pending = []
    for e in cands:
        prev = next((a for a in reversed(st["attempts"]) if a.get("old") == e["name"] and a.get("ok") and a.get("new")), None)
        if prev and os.path.isfile(os.path.join(queue_dir, prev["new"] + ".json")):
            if _evict_after_replace(queue_dir, staging_dir, e["name"], prev["new"]):
                res["evict_finished"].append(e["name"])
                new_held.pop(e["name"], None)
                res["held"].pop(e["name"], None)
                _log("evict_finish", now, old=e["name"], new=prev["new"])
            continue
        pending.append(e)
    if not pending:
        return
    why = _library_ready()
    if why:
        alert("reel_guard_lib", f"⚠️ reel_guard: заміну не зібрано — {why}\nРілси збирача з проблемою: " + ", ".join(e["name"] for e in pending))
        _log("replace_blocked", now, reason=why, names=[e["name"] for e in pending])
        return
    if feed_file is None and os.path.isfile(rw.VPS_FEED) and (now.timestamp() - os.path.getmtime(rw.VPS_FEED)) / 3600 <= rw.FEED_MAX_AGE_H:
        feed_file = rw.VPS_FEED
    try:
        bl = rw.acquire_build_lock(staging_dir)      # спільний із reel_weekly замок збірки (flock): два рендери ffmpeg на 2 ядрах VPS одночасно не запускаємо
    except rw.BuildLockError as ex:
        alert("reel_guard_lock_error", f"⚠️ reel_guard: заміну відкладено — файл замка збірки не відкривається\n{str(ex)[:150]}\nБез замка не рендеримо (щоб не збігтись із reel-weekly); перевірити права на reel_build.lock.")
        _log("lock_error", now, error=str(ex)[:200])
        return
    if bl is None:
        alert("reel_guard_busy", "⚠️ reel_guard: заміну відкладено — замок збірки зайнятий (reel-weekly збирає чергу)\nНаступний запуск (через добу) спробує знову; спроба не рахується.")
        _log("busy", now, names=[e["name"] for e in pending])
        return
    try:
        for e in pending:
            recent = [a for a in st["attempts"] if _within(a.get("at"), now, 24)]
            slot = replacement_slot(e["nb"], now)
            if sum(1 for a in st["attempts"] if a.get("old") == e["name"] and not a.get("ok")) >= MAX_FAILED_PER_REEL:
                alert(f"reel_guard_giveup:{e['name']}", f"⚠️ reel_guard: рілс {e['name']} — {MAX_FAILED_PER_REEL} невдалі спроби заміни, більше не пробую (слот {slot:%d.%m})\nПотрібна людина: замінити рілс вручну або виправити причину (див. reports/reel_guard_log.jsonl).")
                _log("giveup", now, name=e["name"])
                continue
            if len(recent) >= MAX_PER_DAY or sum(1 for a in recent if a.get("slot") == slot.isoformat()) >= MAX_PER_SLOT_DAY:
                alert(f"reel_guard_limit:{e['name']}", f"⚠️ reel_guard: ліміт замін за добу вичерпано — рілс {e['name']} лишається утриманим (слот {slot:%d.%m})\nНаступна спроба — після доби від попередньої.")
                _log("limit", now, name=e["name"], slot=slot.isoformat(), recent=len(recent))
                continue
            try:
                specs_before = sorted(f[:-5] for f in os.listdir(os.path.join(staging_dir, "_specs")) if f.endswith(".json"))
            except OSError:
                specs_before = []
            # спробу фіксуємо ДО збірки як «in_progress» (ok=False: уже рахується в лімітах): якщо збірку вб'є OOM/SIGKILL/TimeoutStartSec/MemoryError, лічильник не загубиться (аудит 23.11)
            att = {"at": now.isoformat(timespec="seconds"), "slot": slot.isoformat(), "old": e["name"], "new": None, "ok": False, "in_progress": True, "theme": e["name"].rsplit("_", 1)[0],
                   "specs_before": specs_before, "queue_before": sorted(n for n, _ in rw._queue_manifests(queue_dir))}
            st["attempts"].append(att)
            save_state(staging_dir, {**st, "at": now.isoformat(timespec="seconds")})
            try:
                new, why_not = build_replacement(e, slot, queue_dir, staging_dir, prices, now, feed_file, build_kw)
            except BaseException:
                _recover_interrupted(st, queue_dir, staging_dir, now, lock_held=True)      # MemoryError/KeyboardInterrupt/SystemExit: спробу закриваємо як невдалу, осиротіле прибираємо, далі піднімаємо
                raise
            att.update(new=new, ok=bool(new), in_progress=False)
            att.pop("specs_before", None)
            att.pop("queue_before", None)
            if why_not:
                att["error"] = why_not[:200]
            save_state(staging_dir, {**st, "at": now.isoformat(timespec="seconds")})      # результат фіксуємо ДО виселення: збій далі не призведе до другої збірки
            if not new:
                res["failed"].append(e["name"])
                alert(f"reel_guard_fail:{e['name']}", f"⚠️ reel_guard: заміну рілса {e['name']} не зібрано (слот {slot:%d.%m}); старий лишається в черзі\nПричина: {why_not}")
                _log("replace_failed", now, old=e["name"], slot=slot.isoformat(), reason=why_not)
                continue
            if _evict_after_replace(queue_dir, staging_dir, e["name"], new):
                res["replaced"].append([e["name"], new])
                new_held.pop(e["name"], None)
                res["held"].pop(e["name"], None)
                alert(f"reel_guard_done:{e['name']}", f"⚠️ reel_guard: рілс {e['name']} замінено на {new} (слот {slot:%d.%m})\nПричина: " + "; ".join(_trim(e["problems"])))
                _log("replaced", now, old=e["name"], new=new, slot=slot.isoformat(), reason=_trim(e["problems"]))
            else:
                res["failed"].append(e["name"])
                alert(f"reel_guard_fail:{e['name']}", f"⚠️ reel_guard: заміну {new} зібрано, але в черзі її нема — старий {e['name']} не виселено")
                _log("replace_failed", now, old=e["name"], reason="нова відсутня в черзі")
    finally:
        rw.release_build_lock(bl)


def _print_plan(res, entries, now):
    print(f"[reel_guard] DRY ({now:%Y-%m-%d %H:%M}) слот {res['slot']}: придатних {len(res['ok'])}, утримано {len(res['held'])}, слот закрито: {res.get('slot_covered')}")
    for e in entries:
        if e["state"] == "far":
            print(f"  {e['name']}: поза горизонтом (not_before {e['nb']})")
        elif e["state"] == "held":
            kind = "збирач" if e["built"] else "РУЧНИЙ (не чіпаємо)"
            print(f"  {e['name']}: УТРИМАНО [{kind}]{' [заміна можлива]' if e['replaceable'] else ''}{' [лікується зі спеки]' if e['healable'] else ''}: " + "; ".join(_trim(e["problems"])))
        else:
            print(f"  {e['name']}: ok")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="нічого не пише (ні черга, ні стан, ні журнал, ні сповіщення): друкує план")
    ap.add_argument("--queue-dir", default=rw.VPS_QUEUE)
    ap.add_argument("--staging-dir", default=rw.VPS_STAGING)
    a = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    try:
        res = run(a.queue_dir, a.staging_dir, dry=a.dry)
    except (Exception, SystemExit) as e:  # noqa: BLE001
        if not a.dry:
            _alert("reel_guard_crash", f"⚠️ reel_guard: запуск перервано — {type(e).__name__}: {str(e)[:300]}")
        print(f"[reel_guard] перервано: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"[reel_guard] режим {res['mode']}, слот {res['slot']}: придатних {len(res['ok'])}, утримано {len(res['held'])}, замінено {len(res['replaced'])}, не вдалось {len(res['failed'])}"
          + (f", пропущено: {res['skipped']}" if res["skipped"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
