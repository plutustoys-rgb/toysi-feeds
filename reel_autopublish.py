# -*- coding: utf-8 -*-
"""reel_autopublish.py — автопублікація IG-Reels за розкладом (рішення власника 13.10.2026: «автопублікація Reels за розкладом: так»).

ЯК ПРАЦЮЄ (VPS, reel-autopublish.timer: вт і пт 18:30 Київ; один Reel за запуск):
  1. ЧЕРГА — каталог /opt/plutustoys/reels_queue/. Reel = три файли з однаковим іменем: <name>.mp4, <name>.caption.txt, <name>.json (маніфест). Файли у чергу кладе власник однією командою scp
     на ПАРТІЮ (не на кожну публікацію); маніфест пише SMM (формат нижче).
     Маніфест: {"verify": [{"id": "196937", "price": 376}, ...], "not_before": "2026-10-14T00:00" (необов'язково)}.
  2. ПЕРЕД ПУБЛІКАЦІЄЮ — перевірка ФАКТІВ на живому сайті (без довільних порогів): кожен товар з verify є в каталозі сайту (значить у наявності) і його ціна на сайті = ціні з маніфесту (тієї, що в підписі);
     підпис ≤ 2200 символів (ліміт Instagram); відео існує й не порожнє. Не пройшло → Reel НЕ публікується, лишається в черзі, сигнал SMM/власнику (журнал) раз на добу, береться НАСТУПНИЙ Reel з черги.
  3. ПУБЛІКАЦІЯ — publish_reel_video.sh кладе відео в feed-data/media і дає публічний raw-URL, social_auto_poster.post_single_reel публікує. Успіх → media_id у reels_queue/published.json,
     файли в reels_queue/published/. Помилка → лишається в черзі, повтор наступного запуску.
  2а. Якщо розбіжність лише в ЦІНІ (ціна в підписі — текст, у кадрі цін нема), refresh_prices ПЕРЕД перевіркою переписує підпис і verify на живі ціни сайту (однозначні випадки); нема в наявності — утримує, як раніше.
  4. ЗАХИСТ ВІД ДУБЛЯ: перед викликом Instagram створюється файл <name>.publishing. Якщо він лишився (процес перервано чи результат невідомий) — Reel НЕ публікується повторно, сигнал «перевір Instagram»;
     людина або видаляє .publishing (якщо Reel НЕ вийшов), або переносить файли в published/ (якщо вийшов).
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
QUEUE_DIR = Path(os.environ.get("REEL_QUEUE_DIR", "/opt/plutustoys/reels_queue"))
SITE_INDEX_URL = "https://plutustoys.com.ua/index.json"
IG_CAPTION_MAX = 2200
IG_USERNAME = os.environ.get("IG_USERNAME", "plutustoys.ua")
IG_REELS_FILE = Path(os.environ.get("IG_REELS_FILE", str(HERE / "ig_reels.json")))      # ЧИТАЄ site/build_site.py (/ig.html); поза site/ і git


class HostingError(RuntimeError):
    """Відео не вдалось виставити на публічний хостинг: ДО Instagram, Reel точно не вийшов (claim знімається)."""


def _alert(key: str, text: str) -> None:
    try:
        from telegram_notify import send_throttled_alert
        send_throttled_alert(key, text, cooldown_sec=24 * 60 * 60)
    except Exception as e:  # noqa: BLE001
        print(f"[reel_autopublish] сигнал не надіслано: {type(e).__name__}", file=sys.stderr)


def fetch_site_index() -> dict:
    """{id(str): {'price': int, 'name': str}} з публічного каталогу сайту (він містить лише товари в наявності)."""
    r = requests.get(SITE_INDEX_URL, timeout=60)
    r.raise_for_status()
    return {str(p["id"]): {"price": float(p["pr"]), "name": p.get("n", "")} for p in r.json() if "id" in p and "pr" in p}


def host_video(mp4: Path) -> str:
    """publish_reel_video.sh → публічний raw-URL (останній рядок виводу). Кидає виняток, якщо URL нема."""
    try:
        r = subprocess.run(["bash", str(HERE / "publish_reel_video.sh"), str(mp4)], capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as e:
        raise HostingError(f"хостинг відео: таймаут {e.timeout} с") from e
    url = (r.stdout.strip().splitlines() or [""])[-1].strip()
    if r.returncode != 0 or not url.startswith("https://raw.githubusercontent.com/"):
        raise HostingError(f"хостинг відео не дав URL (код {r.returncode}): {r.stderr.strip()[-300:] or r.stdout.strip()[-300:]}")
    return url


def publish_reel(url: str, caption: str) -> dict:
    import social_auto_poster
    return social_auto_poster.post_single_reel(url, caption, True)


def _ig_permalink(media_id) -> str:
    """Публічний permalink опублікованого Reel (Graph GET /{media_id}?fields=permalink) → нормалізований https://www.instagram.com/<IG_USERNAME>/reel/<код>/. Нема/не розібрано → ''.
    Токен іде в query, а текст винятків requests містить повний URL — тому жодного raise_for_status/str(e) назовні (аудит #902 B1: токен потрапляв у журнал і Telegram)."""
    import re
    import social_auto_poster as sap
    try:
        r = requests.get(f"https://graph.facebook.com/{sap.GRAPH_VERSION}/{media_id}",
                         params={"fields": "permalink", "access_token": sap.FB_PAGE_ACCESS_TOKEN}, timeout=30)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Graph недоступний ({type(e).__name__})") from None
    if r.status_code != 200:
        raise RuntimeError(f"Graph HTTP {r.status_code}")
    m = re.fullmatch(r"https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9._]+/)?(?:reel|reels|p)/([A-Za-z0-9_-]+)/?(?:\?.*)?", str((r.json() or {}).get("permalink") or ""))
    return f"https://www.instagram.com/{IG_USERNAME}/reel/{m.group(1)}/" if m else ""


def _start_site_rebuild() -> None:
    r = subprocess.run(["systemctl", "start", "--no-block", "site-rebuild.service"], timeout=30, check=False, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"systemctl код {r.returncode}")


def _safe(e) -> str:
    """Текст помилки без секретів (токен у URL/повідомленні)."""
    try:
        import social_auto_poster as sap
        return sap._mask_secrets(f"{type(e).__name__}: {e}")
    except Exception:  # noqa: BLE001
        return type(e).__name__


IG_REELS_MAX = 50


def register_on_site(name: str, manifest: dict, caption: str, media_id, now, *, permalink=None, rebuild=None, path=None) -> bool:
    """Дописує опублікований Reel у ig_reels.json (читає site/build_site.py → /ig.html: «найновіший першим, зі своїми товарами») і запускає перебудову сайту. BEST-EFFORT: Reel уже опубліковано —
    збій тут НЕ скасовує публікацію (алерт, повторити вручну). Формат (узгоджено з Shopify-code 20.10): {"reels":[{"date","url","title","ids":[…]}]}; НАЙНОВІШИЙ ПЕРШИМ; дублі за url не додаємо; ≤IG_REELS_MAX.
    ids = id товарів із manifest.verify. Файл відсутній → новий; ЗІПСОВАНИЙ/нечитаний → не чіпаємо (алерт), щоб не стерти історію (аудит #902 S1). Збій лише перебудови ≠ збій запису (S2)."""
    try:
        ids = [str(v["id"]).strip() for v in (manifest.get("verify") or []) if isinstance(v, dict) and str(v.get("id") or "").strip()]
        url = (permalink or _ig_permalink)(media_id)
        if not url or not ids:
            raise RuntimeError(f"нема {'permalink' if not url else 'id товарів у manifest.verify'}")
        title = str(manifest.get("title") or "").strip() or next((ln.strip() for ln in caption.splitlines() if ln.strip() and not ln.strip().startswith("#")), name)
        target = Path(path or IG_REELS_FILE)
        top = {}
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        if not isinstance(data, dict):
            raise ValueError("ig_reels.json: очікувався об'єкт — файл не чіпаю")
        top = {k: v for k, v in data.items() if k != "reels"}
        reels = data.get("reels", [])
        if not isinstance(reels, list):
            raise ValueError("ig_reels.json: reels не список — файл не чіпаю")
        if any(isinstance(r, dict) and r.get("url") == url for r in reels):
            return True                                           # уже записано (повторний виклик ідемпотентний)
        reels.insert(0, {"date": now.date().isoformat(), "url": url, "title": title[:90], "ids": ids})
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({**top, "reels": reels[:IG_REELS_MAX]}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)                                   # атомарно
    except Exception as e:  # noqa: BLE001
        print(f"[reel_autopublish] {name}: запис на сайт не вдався ({_safe(e)})", file=sys.stderr)
        _alert(f"reel_site:{name}", f"⚠️ Reel {name} опубліковано, але на сайт (/ig.html) НЕ дописано: {_safe(e)}. Додати вручну в ig_reels.json (найновіший першим) або ig_featured.json.")
        return False
    try:
        (rebuild or _start_site_rebuild)()
    except Exception as e:  # noqa: BLE001
        print(f"[reel_autopublish] {name}: запис є, перебудова сайту не стартувала ({_safe(e)})", file=sys.stderr)
        _alert(f"reel_site_rebuild:{name}", f"⚠️ Reel {name}: у ig_reels.json ЗАПИСАНО, але перебудова сайту не стартувала ({_safe(e)}); /ig.html оновиться за таймером site-rebuild (≤ 2 год).")
    return True


def check_item(name: str, manifest: dict, mp4: Path, caption_path: Path, index: dict) -> list:
    """Список проблем (порожній = можна публікувати). Лише факти."""
    problems = []
    if not mp4.is_file() or mp4.stat().st_size == 0:
        problems.append("відео відсутнє або порожнє")
    if not caption_path.is_file() or not caption_path.read_text(encoding="utf-8").strip():
        problems.append("підпис відсутній або порожній")
    else:
        n = len(caption_path.read_text(encoding="utf-8").strip())
        if n > IG_CAPTION_MAX:
            problems.append(f"підпис {n} символів > {IG_CAPTION_MAX} (ліміт Instagram)")
    ver = manifest.get("verify")
    if not isinstance(ver, list) or not ver:
        problems.append("у маніфесті немає списку verify (товари й ціни з підпису)")
        ver = []
    cap_text = caption_path.read_text(encoding="utf-8") if caption_path.is_file() else ""
    for v in ver:
        if not isinstance(v, dict):
            problems.append(f"некоректний запис verify: {v!r}")
            continue
        pid, price = str(v.get("id", "")), v.get("price")
        item = index.get(pid)
        try:
            price_f = float(price)
        except (TypeError, ValueError):
            problems.append(f"некоректна ціна у verify: {v!r}")
            continue
        if not pid:
            problems.append(f"некоректний запис verify: {v!r}")
        elif item is None:
            problems.append(f"товару {pid} немає в каталозі сайту (нема в наявності)")
        elif abs(float(item["price"]) - price_f) > 0.005:
            problems.append(f"ціна товару {pid} на сайті {item['price']:g} ≠ {price_f:g} у підписі")
        elif not re.search(rf"(?<!\d){re.escape(format(price_f, 'g'))}(?!\d)", cap_text):
            problems.append(f"ціни {price_f:g} (товар {pid}) немає в тексті підпису")
    return problems


def refresh_prices(name: str, manifest: dict, mpath: Path, cap_path: Path, index: dict):
    """Ціна в підписі — ТЕКСТ (у кадрі цін нема: ПРАВИЛО №0 reel_build), тож коли ціна на сайті змінилась за дні очікування в черзі, замість утримання рілса
    (запит власника 27.10.2026: «чому не поставити актуальні ціни?») переписуємо підпис і verify на ЖИВІ ціни. Робимо це лише коли це однозначно:
      • усі товари з verify є в каталозі сайту (нема в наявності → не чіпаємо, check_item утримає, як і раніше);
      • кожна стара ціна з маніфесту стоїть у підписі як «<N> грн» РІВНО стільки разів, скільки товарів verify мають таку ціну (інакше незрозуміло, що саме міняти);
      • дві різні нові ціни не відображаються з однієї старої.
    Заміна — одним проходом re.sub (ціна A→B не зачепить інший товар, чия стара ціна = B). Порядок запису: підпис, потім маніфест (як refresh_queue у reel_weekly):
    збій між ними залишає підпис з новою ціною та маніфест зі старою → check_item утримає (fail-closed), а не опублікує чужу ціну.
    Повертає список «id: стара→нова» (порожній = нічого не змінено)."""
    ver = manifest.get("verify")
    if not isinstance(ver, list) or not ver or not cap_path.is_file():
        return []
    mapping, changes, olds = {}, [], {}
    for v in ver:
        if not isinstance(v, dict):
            return []
        item = index.get(str(v.get("id", "")))
        try:
            old = float(v.get("price"))
        except (TypeError, ValueError):
            return []
        if item is None:
            return []
        new = float(item["price"])
        olds[format(old, "g")] = olds.get(format(old, "g"), 0) + 1
        if mapping.setdefault(format(old, "g"), format(new, "g")) != format(new, "g"):
            return []                                                   # одна стара ціна → дві різні нові: неоднозначно
        if abs(new - old) > 0.005:
            changes.append(f"{v.get('id')}: {old:g}→{new:g}")
    if not changes:
        return []
    cap_text = cap_path.read_text(encoding="utf-8")
    for old_s, n in olds.items():
        if len(re.findall(rf"(?<![\d.,]){re.escape(old_s)}(?= грн)", cap_text)) != n:
            return []                                                   # у підписі стара ціна не там/не стільки разів — не вгадуємо
    new_cap = re.sub(r"(?<![\d.,])(\d+(?:\.\d+)?)(?= грн)", lambda m: mapping.get(m.group(1), m.group(1)), cap_text)
    new_ver = []
    for v in ver:
        new_price = float(index[str(v["id"])]["price"])
        new_ver.append({**v, "price": int(new_price) if new_price == int(new_price) else new_price})
    tmp_c, tmp_m = cap_path.with_name(cap_path.name + ".tmp"), mpath.with_name(mpath.name + ".tmp")
    tmp_c.write_text(new_cap, encoding="utf-8")
    os.replace(tmp_c, cap_path)
    tmp_m.write_text(json.dumps({**manifest, "verify": new_ver}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_m, mpath)
    return changes


def _load_published(queue: Path) -> list:
    try:
        return json.loads((queue / "published.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def run_once(queue_dir=None, *, fetch_index=None, host=None, publish=None, now=None, register=None) -> dict:
    """Один запуск: публікує максимум ОДИН Reel. Не кидає назовні. Повертає {'published': name|None, 'held': [...], 'unknown': [...]}."""
    queue = Path(queue_dir or QUEUE_DIR)
    out = {"published": None, "held": [], "unknown": []}
    try:
        if not queue.is_dir():
            return out
        fetch_index, host, publish = fetch_index or fetch_site_index, host or host_video, publish or publish_reel
        register = register or register_on_site
        now = now or datetime.now()
        done = {p.get("name") for p in _load_published(queue)}
        index = None
        for mpath in sorted(queue.glob("*.json"), key=lambda p: (p.stat().st_mtime, p.name)):
            if mpath.name == "published.json":
                continue
            name = mpath.stem
            if name in done:
                continue
            if not re.fullmatch(r"[a-z0-9_-]+", name):
                out["held"].append(name)
                _alert(f"reel_hold:{name}", f"⚠️ Reel «{name}»: ім'я файлу має бути лише a-z, 0-9, _ і - (інакше публічний URL відео може не прийняти Instagram). Перейменуй три файли й перезалий.")
                continue
            mp4, cap = queue / f"{name}.mp4", queue / f"{name}.caption.txt"
            if (queue / f"{name}.publishing").exists():
                out["unknown"].append(name)
                _alert(f"reel_unknown:{name}", f"🔴 Reel {name}: попередня публікація перервана або результат невідомий (є {name}.publishing). Автоповтору нема: перевір Instagram; якщо Reel вийшов, перенеси файли в reels_queue/published/ і додай запис у published.json; якщо ні, видали {name}.publishing.")
                continue
            try:
                manifest = json.loads(mpath.read_text(encoding="utf-8"))
                if not isinstance(manifest, dict):
                    raise ValueError("маніфест має бути об'єктом")
            except (OSError, ValueError) as e:
                out["held"].append(name)
                _alert(f"reel_hold:{name}", f"⚠️ Reel {name}: маніфест нечитабельний ({e}); не публікую.")
                continue
            nb = manifest.get("not_before")
            if nb:
                try:
                    nb_dt = datetime.fromisoformat(str(nb))
                    if nb_dt.tzinfo is not None:
                        nb_dt = nb_dt.astimezone().replace(tzinfo=None)   # місцевий час VPS (Київ), як і now
                except ValueError:
                    out["held"].append(name)                      # fail-closed: нечитабельна дата НЕ означає «публікуй зараз»
                    _alert(f"reel_hold:{name}", f"⚠️ Reel {name}: not_before «{nb}» нечитабельний (потрібно РРРР-ММ-ДДTГГ:ХХ); не публікую.")
                    continue
                if nb_dt > now:
                    continue                                      # ще не настав час цього Reel
            if index is None:
                try:
                    index = fetch_index()
                except Exception as e:  # noqa: BLE001
                    print(f"[reel_autopublish] каталог сайту недоступний ({type(e).__name__}: {e}) — публікація відкладена", file=sys.stderr)
                    _alert("reel_infra_index", f"⚠️ Автопублікація Reels відкладена: каталог сайту недоступний ({type(e).__name__}); без перевірки цін не публікую, повтор наступного запуску.")
                    return out                                    # без перевірки цін не публікуємо
            try:
                changes = refresh_prices(name, manifest, mpath, cap, index)
                if changes:
                    manifest = json.loads(mpath.read_text(encoding="utf-8"))
                    print(f"[reel_autopublish] {name}: ціни в підписі й маніфесті оновлено за живим сайтом: " + "; ".join(changes))
                    _alert(f"reel_prices_refreshed:{name}", f"ℹ️ Reel {name}: за дні в черзі ціни на сайті змінились — підпис і маніфест оновлено на живі ціни ({'; '.join(changes)}), публікація йде далі.")
            except Exception as e:  # noqa: BLE001
                print(f"[reel_autopublish] {name}: автооновлення цін не вдалось ({type(e).__name__}: {e}) — далі звичайна перевірка", file=sys.stderr)
            try:
                problems = check_item(name, manifest, mp4, cap, index)
            except Exception as e:  # noqa: BLE001
                problems = [f"перевірка не вдалась ({type(e).__name__}: {e})"]   # fail-closed: зіпсований маніфест не глушить чергу мовчки
            if problems:
                out["held"].append(name)
                _alert(f"reel_hold:{name}", f"⚠️ Reel {name} НЕ опубліковано, поки: " + "; ".join(problems) + ". SMM: онови підпис/ціни й маніфест, власник: перезалий файли.")
                continue
            claim = queue / f"{name}.publishing"
            try:
                fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY)          # АТОМАРНИЙ claim ДО виклику Instagram: другий процес не пройде
            except FileExistsError:
                out["unknown"].append(name)
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(now.isoformat(timespec="seconds"))
            try:
                url = host(mp4)
                caption_txt = cap.read_text(encoding="utf-8").strip()
                res = publish(url, caption_txt)
            except HostingError as e:
                claim.unlink(missing_ok=True)                     # збій ДО відправки в Instagram: Reel точно не вийшов, повтор наступного запуску
                print(f"[reel_autopublish] {name}: {e}", file=sys.stderr)
                _alert("reel_infra_hosting", f"⚠️ Автопублікація Reels: хостинг відео не вдався для {name} ({e}); повтор наступного запуску.")
                out["held"].append(name)
                return out                                        # не каскадувати спроби по всій черзі
            except Exception as e:  # noqa: BLE001
                # збій під час/після виклику Instagram — стан невідомий: claim ЛИШАЄТЬСЯ, автоповтору нема
                out["unknown"].append(name)
                _alert(f"reel_unknown:{name}", f"🔴 Reel {name}: збій під час публікації ({type(e).__name__}: {e}); результат невідомий, автоповтору нема. Перевір Instagram.")
                return out
            if not (res or {}).get("ok"):
                if (res or {}).get("dryrun"):
                    claim.unlink(missing_ok=True)                 # нема IG-токенів у .env: нічого не було відправлено
                    out["held"].append(name)
                    _alert("reel_infra_tokens", "⚠️ Автопублікація Reels: у .env нема IG_USER_ID/FB_PAGE_ACCESS_TOKEN — публікація неможлива; черга чекає.")
                    return out
                if (res or {}).get("unknown"):
                    # запит media_publish пішов, а результат невідомий (таймаут/обрив/5xx): Instagram міг опублікувати — claim ЛИШАЄТЬСЯ, автоповтору нема
                    out["unknown"].append(name)
                    _alert(f"reel_unknown:{name}", f"🔴 Reel {name}: запит публікації надіслано, але результат невідомий ({(res or {}).get('error')}). Автоповтору нема: перевір Instagram; якщо Reel вийшов, перенеси файли в reels_queue/published/ і додай запис у published.json; якщо ні, видали {name}.publishing.")
                    return out
                claim.unlink(missing_ok=True)                     # Instagram ВИЗНАЧЕНО відхилив (або збій до media_publish): Reel не вийшов, повтор наступного запуску
                out["held"].append(name)
                print(f"[reel_autopublish] {name}: Instagram: {(res or {}).get('error')}", file=sys.stderr)
                _alert(f"reel_fail:{name}", f"⚠️ Reel {name}: Instagram відхилив публікацію ({(res or {}).get('error')}); повтор наступного запуску.")
                continue
            published = _load_published(queue) + [{"name": name, "media_id": res.get("id"), "at": now.isoformat(timespec="seconds"), "url": url}]
            tmp_pub = queue / "published.json.tmp"
            tmp_pub.write_text(json.dumps(published, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp_pub, queue / "published.json")      # атомарно: обрізаний published.json не втратить історію
            (queue / "published").mkdir(exist_ok=True)
            for f in (mp4, cap, mpath, claim):
                if f.exists():
                    f.replace(queue / "published" / f.name)
            out["published"] = name
            print(f"[reel_autopublish] ✅ {name}: media_id={res.get('id')}")
            register(name, manifest, caption_txt, res.get("id"), now)       # best-effort: Reel уже опубліковано, збій запису на сайт його не скасовує
            _alert(f"reel_done:{name}", f"🎬 Reel опубліковано: {name}, media_id={res.get('id')}.")
            return out                                            # один Reel за запуск
    except Exception as e:  # noqa: BLE001
        print(f"[reel_autopublish] збій запуску: {type(e).__name__}: {e}", file=sys.stderr)
    return out


if __name__ == "__main__":
    print(run_once())
