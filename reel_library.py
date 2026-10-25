# -*- coding: utf-8 -*-
"""reel_library.py — бібліотека СХВАЛЕНИХ власником перших карт Плутуса для рілсів (автозбирач на VPS).

Рішення власника 25.10.2026: «я схвалю лише перші странички зі сценками плутуса, самі рілси збирайте вже з ними без схвалення».
Тому на VPS рілс збирається ЛИШЕ зі сцен, що в `manifest.json` бібліотеки мають status == "approved" (зараз: icecream, wink, nod, noselick).
Нова сцена з'являється тут тільки після схвалення власником (SMM змінює manifest у своїй теці, власник заливає бібліотеку на VPS).

Склад бібліотеки (теку готує SMM, `plutus_scene_library/`; на VPS — /opt/plutustoys/plutus_scene_library, поза git, ≈16 МБ):
  manifest.json            {"scenes": {<ключ>: {"status": "approved", "file": "first_card/<ключ>.mp4", "end_mascot_png": "end_mascot/<ключ>_f<N>.png", ...}}}
  first_card/<ключ>.mp4    ГОТОВА перша карта: 1080×1920, 30 к/с, ≈4,5 с, Плутус на кремовому градієнті (вирізка вже зроблена SMM — на VPS не ключуємо)
  end_mascot/<ключ>_fN.png RGBA-вирізка Плутуса з явно чистого кадру для статичної ендкартки (потрібна лише для сцен, що йдуть в ендкартку)
Бібліотека вмикається змінною PLUTUS_SCENE_LIBRARY (шлях) або наявністю /opt/plutustoys/plutus_scene_library. Нема → library_dir() = None, і reel_build
працює як раніше (локальний ручний потік SMM не змінюється).

Модуль легкий: numpy/imageio/PIL імпортуються ліниво, лише в функціях, що читають медіа.
"""
import json
import os
import re
from pathlib import Path

DEFAULT_VPS_DIR = "/opt/plutustoys/plutus_scene_library"
APPROVED = "approved"
FIRST_CARD_SIZE = (1920, 1080)       # (висота, ширина) кадру першої карти
FIRST_CARD_FRAMES = (100, 150)       # допустима кількість кадрів (≈4,5 с при 30 к/с): обрізаний/зіпсований файл не береться


def library_dir():
    """Тека бібліотеки або None (бібліотеку не налаштовано → старий потік)."""
    p = os.environ.get("PLUTUS_SCENE_LIBRARY") or (DEFAULT_VPS_DIR if os.path.isdir(DEFAULT_VPS_DIR) else "")
    return Path(p) if p else None


def load_manifest(lib=None):
    lib = Path(lib) if lib else library_dir()
    if not lib:
        return {}
    try:
        data = json.loads((lib / "manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"[reel_library] manifest.json нечитабельний у {lib}: {type(e).__name__}") from e
    return data if isinstance(data, dict) else {}


def scene_key(scene_file):
    """'scene_wink_RENDER_GREEN.mp4' | 'wink.mp4' | 'wink' → 'wink' (None для порожнього/дивного імені)."""
    s = str(scene_file or "").strip()
    s = re.sub(r"\.mp4$", "", s, flags=re.I)
    s = re.sub(r"^scene_", "", s)
    s = re.sub(r"_RENDER_GREEN$", "", s)
    return s if re.fullmatch(r"[a-z0-9]+", s) else None


def canonical_scene_file(scene):
    """'wink' | 'wink.mp4' | 'scene_wink_RENDER_GREEN.mp4' → 'scene_wink_RENDER_GREEN.mp4' (під цією назвою діють сезонні вікна reel_seasons);
    дивне ім'я → як є (воно не пройде схвалення)."""
    key = scene_key(scene)
    return f"scene_{key}_RENDER_GREEN.mp4" if key else str(scene or "")


def check_integrity(lib=None, require=False):
    """Звіряє схвалені файли (first_card + end_mascot PNG) із `SHA256SUMS` у теці бібліотеки (рядки «<sha256>  <відносний/шлях>»; пише скрипт заливки).
    Ловить обрізану/пошкоджену заливку (кількість кадрів 100–150 цього не ловить: кліп, обрізаний до 80 %, ще проходить діапазон).
    Повертає True, якщо звірено; False, якщо SHA256SUMS нема і require=False. Невідповідність/файл не в переліку → RuntimeError.
    require=True (автозбирач на VPS) — відсутність SHA256SUMS теж помилка."""
    import hashlib
    lib = Path(lib) if lib else library_dir()
    if not lib:
        return False
    sums = lib / "SHA256SUMS"
    if not sums.is_file():
        if require:
            raise RuntimeError(f"[reel_library] нема SHA256SUMS у {lib} — цілісність заливки не звірити")
        return False
    want = {}
    try:
        sums_text = sums.read_text(encoding="utf-8-sig")
    except (OSError, ValueError) as e:        # напр. SHA256SUMS у UTF-16 (PowerShell 5.1 `>`/Out-File): це помилка заливки, а не падіння
        raise RuntimeError(f"[reel_library] SHA256SUMS нечитабельний ({type(e).__name__}; потрібен UTF-8)") from e
    for ln in sums_text.splitlines():
        m = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+?)\s*", ln)
        if m:
            want[m.group(2).replace("\\", "/")] = m.group(1).lower()
    bad = []
    for key, ent in approved_scenes(lib).items():
        for rel in (ent.get("file"), ent.get("end_mascot_png")):
            if not rel:
                continue
            exp = want.get(str(rel).replace("\\", "/"))
            if exp is None:
                bad.append(f"{rel}: нема в SHA256SUMS")
                continue
            h = hashlib.sha256()
            try:
                with open(lib / rel, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
            except OSError as e:
                bad.append(f"{rel}: файл нечитабельний ({type(e).__name__})")
                continue
            if h.hexdigest() != exp:
                bad.append(f"{rel}: sha256 не збігається (файл пошкоджено/обрізано)")
    if bad:
        raise RuntimeError("[reel_library] цілісність бібліотеки: " + "; ".join(bad))
    return True


def approved_scenes(lib=None):
    """{ключ: запис manifest} лише зі status == 'approved' І наявним first_card-файлом."""
    lib = Path(lib) if lib else library_dir()
    if not lib:
        return {}
    out = {}
    for key, ent in (load_manifest(lib).get("scenes") or {}).items():
        if not isinstance(ent, dict) or ent.get("status") != APPROVED:
            continue
        f = ent.get("file")
        if f and (lib / f).is_file() and (lib / f).stat().st_size > 0:
            out[key] = ent
    return out


def first_card_path(scene_file, lib=None):
    """Шлях до першої карти схваленої сцени або None."""
    lib = Path(lib) if lib else library_dir()
    key = scene_key(scene_file)
    ent = approved_scenes(lib).get(key) if (lib and key) else None
    return (lib / ent["file"]) if ent else None


def end_mascot_path(scene_file, lib=None):
    """Шлях до RGBA-PNG ендкартки схваленої сцени або None (нема PNG → ця сцена не може бути ендсценою)."""
    lib = Path(lib) if lib else library_dir()
    key = scene_key(scene_file)
    ent = approved_scenes(lib).get(key) if (lib and key) else None
    png = ent.get("end_mascot_png") if ent else None
    if png and (lib / png).is_file() and (lib / png).stat().st_size > 0:
        return lib / png
    return None


def iter_first_card_frames(scene_file, lib=None, size=FIRST_CARD_SIZE, frames_range=FIRST_CARD_FRAMES):
    """Кадри першої карти ПОТОКОМ (по одному — 135 кадрів 1080×1920 разом ≈ 840 МБ, на VPS з 2,7 ГБ вільної RAM так не тримаємо).
    Перевіряє розмір кожного кадру та кількість кадрів; невідповідність → RuntimeError (рілс не збирається)."""
    import imageio
    path = first_card_path(scene_file, lib)
    if path is None:
        raise RuntimeError(f"[reel_library] сцена {scene_file!r} не схвалена або файлу нема")
    n = 0
    reader = imageio.get_reader(str(path))
    try:
        for fr in reader:
            if fr.shape[:2] != tuple(size) or (fr.ndim == 3 and fr.shape[2] != 3):
                raise RuntimeError(f"[reel_library] кадр {n} першої карти {path.name}: {fr.shape}, очікувалось {size}×3")
            n += 1
            yield fr
    finally:
        reader.close()
    if not (frames_range[0] <= n <= frames_range[1]):
        raise RuntimeError(f"[reel_library] перша карта {path.name}: {n} кадрів, очікувалось {frames_range[0]}–{frames_range[1]} (файл обрізано?)")


def load_end_mascot(scene_file, lib=None):
    """RGBA-вирізка для ендкартки → (rgb float32 H×W×3, alpha float32 H×W у 0..1), обрізана по bbox видимого (alpha>0.03)."""
    import numpy as np
    from PIL import Image
    path = end_mascot_path(scene_file, lib)
    if path is None:
        raise RuntimeError(f"[reel_library] для сцени {scene_file!r} нема end_mascot PNG (вона не може бути ендсценою)")
    im = np.asarray(Image.open(path).convert("RGBA")).astype(np.float32)
    rgb, al = im[..., :3], im[..., 3] / 255.0
    ys, xs = np.where(al > 0.03)
    if not len(ys):
        raise RuntimeError(f"[reel_library] {path.name}: порожній альфа-канал")
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return rgb[y0:y1, x0:x1], al[y0:y1, x0:x1]
