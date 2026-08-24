"""matte.py — чистий виріз маскота з зеленої сценки для СТАТИЧНОЇ ендкарти.

ПРОБЛЕМА, яку цей модуль розв'язує (знайдена власником на темному фоні).
`key_green` + `largest_component_mask` дають силует, у який разом із персонажем
потрапляє ПЛАСКА СВІТЛА ПЛЯМА-«ПІДЛОГА» з вихідного кадру Pika/Pixverse — велика
кремова калюжа під хвостом і лапами з рваними майже прямими краями. Вона НЕ зелена,
тому хромакей її не бачить; вона торкається персонажа, тому largest-component її не
відкидає. На кремовому фоні вона зливалась із тлом і була невидима; темний фон її
оголив — і виріз читається як кострубатий.

ЧОМУ НЕ КОЛІР. По кольору плита тотожна кремовому хутру грудей — будь-який колірний
поріг або лишає плиту, або вигризає діри у хутрі. Більше того: яскравість НЕ розділяє
взагалі (у плиті лежить контактна тінь, її медіана 188 при p10=140, а хутро 160/121).

ЧИМ РОЗДІЛЯЄТЬСЯ. Локальною ВАРІАЦІЄЮ. Плита — пласка заливка, хутро — високочастотна
текстура навіть на світлих ділянках. Виміряно на середньому кадрі scene03,
локСКВ у вікні 5x5 по пікселях силуету:

    плита праворуч від хвоста : p10/50/90 = 1.1 / 2.8 / 4.8
    хутро грудей              : p10/50/90 = 4.8 / 8.9 / 15.1
    хутро хвоста              : p10/50/90 = 3.4 / 9.6 / 21.8

АЛГОРИТМ (кожен крок — лік конкретної пастки, див. коментарі у функціях).
  1. хромакей + найбільша компонента — як було;
  2. кандидат = ТІЛЬКИ пласкість (локСКВ < FLAT_MAX) + відсік дуже темного;
     локСКВ рахується ЛИШЕ по пікселях силуету, інакше межа з зеленим фоном дає
     хибне кільце високого СКВ і запечатує плиту;
  3. opening -> компоненти -> лишаємо ті, у яких (а) помітна частка периметра виходить
     НА ФОН і (б) центроїд у нижній частині силуету (підлога лежить ПІД персонажем);
  4. геодезична реконструкція в межах тієї ж нижньої смуги — повертає плиті те,
     що з'їв opening, і не дає їй розлитись угору по хутру;
  5. tidy: закриття дірок, найбільша компонента;
  6. перо + ЖОРСТКИЙ деспіл по краю (штатний лишає зелену нитку по контуру,
     на білій картці вона видна при зумі).

ЧЕСНИЙ РЕЗУЛЬТАТ (не підганяю). Хибних спрацювань нема — хутро не втрачається ніде.
Але прибирається НЕ вся плита: та її частина, що впритул до хутра, невіддільна, бо
у вікні 5x5 край хутра сам піднімає СКВ. Знято приблизно: scene03 добре, scene02
добре, scene04 слабо. Тому ПРАВИЛО КОНВЕЄРА: маскот ставиться тільки на світлу
поверхню, де залишок плити невидимий (див. build_reel.py, mascot_on_light).
"""
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plutus_overlay import key_green, largest_component_mask, read_frames  # noqa: E402

SCENES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plutus_scenes")

WIN = 5              # вікно локального СКВ
FLAT_MAX = 6.0       # плита СКВ p50=2.8, хутро p50=8.9. Розгорнутий свіп 4.5/6/8/11
                     # (diag/sweep_flat.png): 4.5 лишає пів-плити, 8 уже вигризає хутро
                     # стегна. 6.0 — плита йде, хутро ціле
DARK_MIN = 90.0      # відсікає роззявлену пащу й ніс: пласкі, але дуже темні
MIN_PX = 150         # менші пласкі клапті — шум, не плита
OPEN_FRAC_MIN = 0.10 # частка периметра компоненти, що виходить НА ФОН
                     # (плита 0.28-0.37, внутрішні плями хутра 0.00-0.07)
BOTTOM_FRAC = 0.80   # ТРЕТЯ пастка: на scene04 (різкіший рендер, м'якший перехід хутра)
                     # самих лише «пласка + виходить на фон» не досить — маска почала
                     # з'їдати ТІМ'Я і край вуха: там теж пласко й теж межа з фоном.
                     # Додано геометричний пріор: підлога лежить ПІД персонажем.
                     # Центроїд компоненти має бути нижче цієї частки висоти силуету.


def local_std(gray, k=WIN):
    g = gray.astype(np.float32)
    m = ndimage.uniform_filter(g, k)
    m2 = ndimage.uniform_filter(g * g, k)
    return np.sqrt(np.maximum(m2 - m * m, 0.0))


def masked_local_std(gray, inside, k=WIN):
    """локСКВ, пораховане ЛИШЕ по пікселях силуету.

    ПАСТКА, на яку я наступив і яка коштувала двох ітерацій. Звичайний локСКВ на межі
    «зелений фон / кремова калюжа» зашкалює — стрибок яскравості величезний. Виходить
    кільце завширшки з вікно (5 px), де «плаский» хибний, і воно ЗАПЕЧАТУЄ калюжу
    зсередини: заливка від фону до неї не доходить, викидалось 0 px. Спроба
    перестрибнути кільце дилатацією кандидата (BRIDGE=4) розв'язала це, але ціною
    протікання зв'язності крізь хутро — маска вигризала діри в морді й вусі.
    Правильний лік — не рахувати фон узагалі: тоді на межі калюжі СКВ низький,
    і калюжа торкається фону напряму.
    """
    g = gray.astype(np.float32)
    m = inside.astype(np.float32)
    cnt = ndimage.uniform_filter(m, k)
    s1 = ndimage.uniform_filter(g * m, k)
    s2 = ndimage.uniform_filter(g * g * m, k)
    cnt = np.maximum(cnt, 1e-3)
    mu = s1 / cnt
    return np.sqrt(np.maximum(s2 / cnt - mu * mu, 0.0))


def flat_floor_mask(frame, alpha, flat_max=FLAT_MAX, dark_min=DARK_MIN,
                    min_px=MIN_PX, open_frac_min=OPEN_FRAC_MIN,
                    bottom_frac=BOTTOM_FRAC):
    """Маска пласкої плити-«підлоги» всередині силуету.

    ДРУГА ПАСТКА, яку довелось виміряти. Спершу я вимагав «СВІТЛИЙ І плаский».
    Виміряно на scene03 (вікно 5x5, лише пікселі силуету):

        плита праворуч від хвоста : lum p10/50/90 = 140/188/208   СКВ = 1.1/2.8/4.8
        плита під лапами          : lum         ... 74/140/228     СКВ = 0.0/3.7/30
        хутро грудей              : lum p10/50/90 = 121/160/208   СКВ = 4.8/8.9/15.1
        хутро хвоста              : lum          85/131/163       СКВ = 3.4/9.6/21.8

    Тобто плита НЕ світла — у ній лежить контактна тінь, її медіана 188 і хвіст до 74.
    Умова «lum>178» відрізала більшу частину плити й лишала її в кадрі. Яскравість
    між плитою і хутром НЕ розділяє взагалі (діапазони збігаються), а ПЛАСКІСТЬ
    розділяє: медіани 2.8 проти 8.9-9.6. Тому головний критерій — тільки СКВ;
    яскравість лишилась як слабкий відсік дуже темного (роззявлена паща, ніс —
    вони теж пласкі, але вони всередині тіла).

    Замість заливки від фону (вона протікала вгору по кремовому хутру грудей —
    ниткою пласких пікселів) — покомпонентний критерій: пласка компонента
    вважається підлогою, якщо помітна частка її периметра виходить НА ФОН.
    У плити це 0.28-0.37, у внутрішніх пласких плям хутра 0.00-0.07.
    """
    gray = frame.astype(np.float32).mean(2)
    inside = alpha > 0.5
    sd = masked_local_std(gray, inside)

    cand = (sd < flat_max) & (gray > dark_min) & inside
    # opening рве 1-2-піксельні ниточні перемички, якими пласкі плями хутра
    # злипаються з плитою в одну компоненту
    seed_src = ndimage.binary_opening(cand, np.ones((3, 3)), iterations=2)

    lbl, n = ndimage.label(seed_src, np.ones((3, 3)))
    if n == 0:
        return np.zeros_like(inside)
    ys_in = np.where(inside.any(1))[0]
    y_top, y_bot = int(ys_in.min()), int(ys_in.max())
    y_gate = y_top + bottom_frac * (y_bot - y_top)

    seed = np.zeros_like(seed_src)
    for i in range(1, n + 1):
        m = lbl == i
        if m.sum() < min_px:
            continue
        if float(np.where(m)[0].mean()) < y_gate:      # компонента не «під» персонажем
            continue
        ring = ndimage.binary_dilation(m, iterations=2) & ~m
        if float((ring & ~inside).sum()) / max(1, int(ring.sum())) >= open_frac_min:
            seed |= m
    if not seed.any():
        return np.zeros_like(inside)
    # Геодезична реконструкція: повертаємо плиті те, що з'їв opening. ЧЕТВЕРТА пастка:
    # реконструкція по всьому `cand` протікає вгору — на scene04 вона з зернятка у 238 px
    # розповзлась на 3553 і з'їла світлий блик на хвості. Тому росте лише в межах
    # тієї самої нижньої смуги, де плита взагалі може бути.
    band = np.zeros_like(cand)
    band[int(np.ceil(y_gate)):] = True
    floor = ndimage.binary_propagation(seed, mask=cand & band)

    # Додатково — «крила» плити, що стирчать збоку від персонажа: колонка силуету,
    # у якій НЕМА жодного текстурного пікселя, це чиста плита, там хутра просто нема.
    tex = (sd >= flat_max) & inside
    dead_col = inside.any(0) & ~tex.any(0)
    return floor | (inside & dead_col[None, :])


def strip_flat_floor(frame, alpha, **kw):
    """-> (alpha без підлоги, скільки px викинуто)"""
    floor = flat_floor_mask(frame, alpha, **kw)
    return np.where(floor, 0.0, alpha), int((floor & (alpha > 0.5)).sum())


def tidy(alpha, open_iter=2, close_iter=3):
    """Знімає рвані зубці по лінії зрізу й затягує дірки всередині тіла."""
    b = alpha > 0.5
    b = ndimage.binary_opening(b, ndimage.generate_binary_structure(2, 1), open_iter)
    lbl, n = ndimage.label(b)
    if n:
        sizes = ndimage.sum(b, lbl, range(1, n + 1))
        b = lbl == (int(np.argmax(sizes)) + 1)
    b = ndimage.binary_closing(b, ndimage.generate_binary_structure(2, 2), close_iter)
    b = ndimage.binary_fill_holes(b)
    return np.minimum(alpha, b.astype(np.float32)), b


def clean_mascot(scene_file, frame_idx=None, feather=1.2, debug=None):
    """-> (rgb float HxWx3, alpha float HxW 0..1) обрізані по силуету, вже чисті."""
    frames, _ = read_frames(os.path.join(SCENES, scene_file))
    fr = frames[len(frames) // 2 if frame_idx is None else frame_idx]
    rgb, alpha = key_green(fr)
    a0 = alpha * largest_component_mask(alpha > 0.5)

    a1, dropped = strip_flat_floor(fr, a0)
    a2, hard = tidy(a1)
    a2 = np.where(hard, np.maximum(a2, a1), 0.0)
    a2 = ndimage.gaussian_filter(a2, feather)
    a2 = np.clip((a2 - 0.22) / 0.66, 0.0, 1.0)   # підтискаємо напівпрозорий шлейф

    # ЖОРСТКИЙ ДЕСПІЛ ПО КРАЮ. Штатний despill у key_green (g -> min(g, max(r,b)+8))
    # лишає на крайових напівпрозорих пікселях зелений слід. На кремовому фоні його
    # не було видно; на білій картці при зумі краю це помітна зелена нитка по контуру.
    # У смузі краю тиснемо зелений до рівня решти каналів без запасу.
    edge = (a2 > 0.0) & (a2 < 0.98)
    r, g, b_ = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    rgb[..., 1] = np.where(edge, np.minimum(g, (r + b_) * 0.5), np.minimum(g, np.maximum(r, b_) + 8))

    if debug is not None:
        os.makedirs(debug, exist_ok=True)
        tag = scene_file.split("_")[0]
        Image.fromarray((a0 * 255).astype(np.uint8)).save(
            os.path.join(debug, f"{tag}_a_before.png"))
        Image.fromarray((a2 * 255).astype(np.uint8)).save(
            os.path.join(debug, f"{tag}_a_after.png"))
        print(f"[matte] {tag}: викинуто підлоги {dropped} px "
              f"({100.0 * dropped / max(1, (a0 > 0.5).sum()):.1f}% силуету)")

    ys, xs = np.where(a2 > 0.03)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return rgb[y0:y1, x0:x1], a2[y0:y1, x0:x1]


def scaled(scene_file, target_h, **kw):
    rgb, a = clean_mascot(scene_file, **kw)
    w = max(1, int(round(rgb.shape[1] * target_h / rgb.shape[0])))
    rgb = np.asarray(Image.fromarray(rgb.clip(0, 255).astype(np.uint8))
                     .resize((w, target_h), Image.LANCZOS)).astype(np.float32)
    a = np.asarray(Image.fromarray((a * 255).astype(np.uint8))
                   .resize((w, target_h), Image.LANCZOS)).astype(np.float32) / 255.0
    return rgb, a


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.abspath(__file__))
    dbg = os.path.join(here, "diag")
    for s in ("scene02_sit_smile_ear_scratch_pika_GREEN.mp4",
              "scene03_stretch_yawn_pika_GREEN.mp4",
              "scene04_sniff_curious_pika_GREEN.mp4"):
        clean_mascot(s, debug=dbg)
