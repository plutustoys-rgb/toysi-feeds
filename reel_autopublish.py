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
  4. ЗАХИСТ ВІД ДУБЛЯ: перед викликом Instagram створюється файл <name>.publishing. Якщо він лишився (процес перервано чи результат невідомий) — Reel НЕ публікується повторно, сигнал «перевір Instagram»;
     людина або видаляє .publishing (якщо Reel НЕ вийшов), або переносить файли в published/ (якщо вийшов).
"""
import json
import os
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
    return {str(p["id"]): {"price": int(p["pr"]), "name": p.get("n", "")} for p in r.json() if "id" in p and "pr" in p}


def host_video(mp4: Path) -> str:
    """publish_reel_video.sh → публічний raw-URL (останній рядок виводу). Кидає виняток, якщо URL нема."""
    r = subprocess.run(["bash", str(HERE / "publish_reel_video.sh"), str(mp4)], capture_output=True, text=True, timeout=600)
    url = (r.stdout.strip().splitlines() or [""])[-1].strip()
    if r.returncode != 0 or not url.startswith("https://raw.githubusercontent.com/"):
        raise HostingError(f"хостинг відео не дав URL (код {r.returncode}): {r.stderr.strip()[-300:] or r.stdout.strip()[-300:]}")
    return url


def publish_reel(url: str, caption: str) -> dict:
    import social_auto_poster
    return social_auto_poster.post_single_reel(url, caption, True)


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
    for v in ver:
        pid, price = str((v or {}).get("id", "")), (v or {}).get("price")
        item = index.get(pid)
        if not pid or price is None:
            problems.append(f"некоректний запис verify: {v!r}")
        elif item is None:
            problems.append(f"товару {pid} немає в каталозі сайту (нема в наявності)")
        elif int(item["price"]) != int(price):
            problems.append(f"ціна товару {pid} на сайті {item['price']} ≠ {price} у підписі")
    return problems


def _load_published(queue: Path) -> list:
    try:
        return json.loads((queue / "published.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def run_once(queue_dir=None, *, fetch_index=None, host=None, publish=None, now=None) -> dict:
    """Один запуск: публікує максимум ОДИН Reel. Не кидає назовні. Повертає {'published': name|None, 'held': [...], 'unknown': [...]}."""
    queue = Path(queue_dir or QUEUE_DIR)
    out = {"published": None, "held": [], "unknown": []}
    try:
        if not queue.is_dir():
            return out
        fetch_index, host, publish = fetch_index or fetch_site_index, host or host_video, publish or publish_reel
        now = now or datetime.now()
        done = {p.get("name") for p in _load_published(queue)}
        index = None
        for mpath in sorted(queue.glob("*.json"), key=lambda p: (p.stat().st_mtime, p.name)):
            if mpath.name == "published.json":
                continue
            name = mpath.stem
            if name in done:
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
                    if datetime.fromisoformat(str(nb)) > now:
                        continue                                  # ще не настав час цього Reel
                except ValueError:
                    pass
            if index is None:
                try:
                    index = fetch_index()
                except Exception as e:  # noqa: BLE001
                    print(f"[reel_autopublish] каталог сайту недоступний ({type(e).__name__}: {e}) — публікація відкладена", file=sys.stderr)
                    return out                                    # без перевірки цін не публікуємо
            problems = check_item(name, manifest, mp4, cap, index)
            if problems:
                out["held"].append(name)
                _alert(f"reel_hold:{name}", f"⚠️ Reel {name} НЕ опубліковано, поки: " + "; ".join(problems) + ". SMM: онови підпис/ціни й маніфест, власник: перезалий файли.")
                continue
            claim = queue / f"{name}.publishing"
            claim.write_text(now.isoformat(timespec="seconds"), encoding="utf-8")   # claim ДО виклику Instagram
            try:
                url = host(mp4)
                res = publish(url, cap.read_text(encoding="utf-8").strip())
            except HostingError as e:
                claim.unlink(missing_ok=True)                     # збій ДО відправки в Instagram: Reel точно не вийшов, повтор наступного запуску
                print(f"[reel_autopublish] {name}: {e}", file=sys.stderr)
                out["held"].append(name)
                continue
            except Exception as e:  # noqa: BLE001
                # збій під час/після виклику Instagram — стан невідомий: claim ЛИШАЄТЬСЯ, автоповтору нема
                out["unknown"].append(name)
                _alert(f"reel_unknown:{name}", f"🔴 Reel {name}: збій під час публікації ({type(e).__name__}: {e}); результат невідомий, автоповтору нема. Перевір Instagram.")
                return out
            if not (res or {}).get("ok"):
                claim.unlink(missing_ok=True)                     # Instagram прямо повідомив про помилку: Reel не вийшов, повтор наступного запуску
                out["held"].append(name)
                print(f"[reel_autopublish] {name}: Instagram: {(res or {}).get('error')}", file=sys.stderr)
                _alert(f"reel_fail:{name}", f"⚠️ Reel {name}: Instagram відхилив публікацію ({(res or {}).get('error')}); повтор наступного запуску.")
                continue
            published = _load_published(queue) + [{"name": name, "media_id": res.get("id"), "at": now.isoformat(timespec="seconds"), "url": url}]
            (queue / "published.json").write_text(json.dumps(published, ensure_ascii=False, indent=1), encoding="utf-8")
            (queue / "published").mkdir(exist_ok=True)
            for f in (mp4, cap, mpath, claim):
                if f.exists():
                    f.replace(queue / "published" / f.name)
            out["published"] = name
            print(f"[reel_autopublish] ✅ {name}: media_id={res.get('id')}")
            _alert(f"reel_done:{name}", f"🎬 Reel опубліковано: {name}, media_id={res.get('id')}.")
            return out                                            # один Reel за запуск
    except Exception as e:  # noqa: BLE001
        print(f"[reel_autopublish] збій запуску: {type(e).__name__}: {e}", file=sys.stderr)
    return out


if __name__ == "__main__":
    print(run_once())
