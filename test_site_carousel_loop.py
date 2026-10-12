#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_site_carousel_loop.py — 12.10.2026 (SMM/власник): (1) карусель «Добірка Плутуса» не йшла по колу; (2) Плутус при русі лишав двійника (poster просвічував крізь прозорий кліп).
Поведінку в реальному Chromium (стрілки 14× вперед/12× назад на 1280 і 390 px, жодного стрибка, завжди 5/3 видимі картки; Плутус: poster ⇄ кліп без кадру «обидва видимі» чи «жоден») перевіряє незалежний аудит
(python Playwright). Тут — те, що ловиться без браузера: структура коду, інваріанти файлів кліпів, відсутність кросфейду.
Мутації: прибрати клонування / aria-hidden / data-clone→індекс / вимкнення країв / жорстку заміну — відповідні перевірки червоніють."""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


app = (HERE / "site" / "assets" / "app.js").read_text(encoding="utf-8")
css = (HERE / "site" / "assets" / "styles.css").read_text(encoding="utf-8")
car = app[app.index("function initHomeCarousel()"):app.index("function initListTracking()")]
mas = app[app.index("function initMascot()"):app.index("function initContactTracking()")]

# ── карусель ──
chk("цикл: setupLoop клонує перші N карток у кінець і останні N на початок (appendChild + insertBefore), N ≤ 10", "function setupLoop()" in car and "track.appendChild(mk(slides[i]))" in car and "track.insertBefore(mk(slides[j]), slides[0])" in car and "Math.min(n,10)" in car)
chk("клони: aria-hidden=true, tabindex=-1, data-clone=індекс оригіналу, лінива картинка без fetchpriority (скрін-рідер/Tab/вага)", all(x in car for x in ('setAttribute("aria-hidden","true")', 'setAttribute("tabindex","-1")', 'setAttribute("data-clone"', 'im.loading="lazy"', 'removeAttribute("fetchpriority")')))
chk("клони не потрапляють у slides/аналітику: slides збирається ДО клонування, клік по клону → індекс оригіналу (data-clone)", car.index('querySelectorAll("a.card")') < car.index("function setupLoop()") and 'getAttribute("data-clone")!==null' in car and "i=+a.getAttribute(\"data-clone\")" in car)
chk("тихе повернення: loopNormalize зсуває scrollLeft на період кола БЕЗ анімації (пряме присвоєння, не scrollTo smooth), коли прокрутка зупинилась (таймер 140 мс) і перед кожним кроком", "track.scrollLeft -= P" in car and "track.scrollLeft += P" in car and "setTimeout(loopNormalize, 140)" in car and "loopNormalize(true);" in car)
chk("період кола = відстань першого оригіналу до першого доданого клона (з проміжками), а не жорстка константа", "track.children[LOOP_N + slides.length].getBoundingClientRect().left - slides[0].getBoundingClientRect().left" in car)
chk("краї зникли: стрілки не disabled у режимі циклу; автопрокрутка не «відлітає на початок» (atEnd лише без циклу)", "if(loopOn){ left.disabled=false; right.disabled=false; return; }" in car and "var atEnd = !loopOn &&" in car)
chk("менше 6 слайдів або немає DOM-API клонування — лінійний режим (n<6 → return)", "if(n<6 || !track || !track.appendChild || !track.insertBefore || !slides[0].cloneNode) return;" in car)
chk("пауза hover/focus/touch і reduced-motion збережені (автопрокрутка)", all(x in car for x in ("auto.hover", "auto.focus", "auto.touch", "reducedMotion()")))
chk("resize/orientationchange: вирівнюємо на оригінал (loopAlign), довжина кола змінюється разом із шириною", "loopTimer=setTimeout(loopAlign, 150)" in car and 'addEventListener("resize", onResize)' in car)

# ── Плутус ──
chk("кросфейд прибрано: у CSS кліпа немає opacity/transition; кліп ховається visibility, poster ховається на час кліпу (.clip-on)", ".mascot-clip{position:absolute;left:0;top:0;visibility:hidden" in css and ".mascot-clip.on{visibility:visible}" in css and ".mascot-wrap.clip-on #hero-mascot{visibility:hidden}" in css
    and "opacity" not in css[css.index(".hero2 .mascot-wrap .mascot-clip"):css.index(".hero2 .mascot-wrap.clip-on")])
chk("JS: poster ховається (clip-on) лише ПІСЛЯ decode першого кадру кліпу, повертається одразу на stop(); src кліпу знімається (наступний показ — з першого кадру)", "clipImg.decode().then(go, go)" in mas and 'wrap.classList.add("clip-on")' in mas and 'wrap.classList.remove("clip-on")' in mas and 'clipImg.removeAttribute("src")' in mas and "fadeTimer" not in mas)
chk("go() не виконується, якщо за час decode показ уже скасовано (playing=false)", "function go(){\n          if(!playing) return;" in mas.replace("\r\n", "\n"))

# ── файли кліпів: перший і останній кадр = poster (стик непомітний) ──
try:
    import numpy as np
    from PIL import Image
    P = np.asarray(Image.open(HERE / "site/assets/mascot/plutus_hero_poster.webp").convert("RGBA")).astype(int)
    ok = True
    worst = 0.0
    for n in ("tailtrip", "ear_scratch", "grooming", "sniff", "shakeoff"):
        im = Image.open(HERE / f"site/assets/mascot/plutus_{n}.webp")
        for k in (0, im.n_frames - 1):
            im.seek(k)
            d = float(np.abs(np.asarray(im.convert("RGBA")).astype(int) - P).mean())
            worst = max(worst, d)
            ok = ok and d < 9.0
    chk(f"кліпи: перший і останній кадр збігаються з poster (середня різниця ≤ 9/255, найгірше {worst:.1f}; шум стиснення ≈6; до виправлення ear_scratch давав ≈28)", ok)
except ImportError:
    chk("PIL/numpy доступні для перевірки кадрів кліпів", False)

print()
if F:
    print(f"FAILED ({len(F)}):")
    for n in F:
        print("  -", n)
    sys.exit(1)
print("ALL OK")
