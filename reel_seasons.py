# -*- coding: utf-8 -*-
"""reel_seasons.py — сезонні вікна сцен Плутуса (легкий модуль без numpy/PIL: його читають і reel_build, і reel_batch/reel_weekly).

{файл: ((місяць, день) з, (місяць, день) до)} — вікна щорічні, рік не береться до уваги. SMM 21.10.2026: halloween — «до 31.10» (беремо жовтень: 01.10–31.10, щоб не було «хелловіну» в січні);
snowflake — «з 25.11» (кінцеву дату SMM не назвала — консервативно до 31.12; продовжити скаже SMM)."""
from datetime import date

SEASONAL_SCENES = {
    "scene_halloween_RENDER_GREEN.mp4": ((10, 1), (10, 31)),
    "scene_snowflake_RENDER_GREEN.mp4": ((11, 25), (12, 31)),
}


def scene_season_ok(scene_file, on_date):
    """Сезонна сцена доступна лише у своєму вікні (on_date — дата ПУБЛІКАЦІЇ рілса, не рендера). Несезонна — завжди True."""
    win = SEASONAL_SCENES.get(scene_file)
    if not win:
        return True
    md = (on_date.month, on_date.day)
    lo, hi = win
    return lo <= md <= hi


def season_not_after(scene_files, on_date):
    """Остання дата, коли рілс із цими сценами ще доречний (кінець сезонного вікна ЦЬОГО року публікації), або None, якщо всі сцени несезонні."""
    ends = [date(on_date.year, *SEASONAL_SCENES[s][1]) for s in scene_files if s in SEASONAL_SCENES]
    return min(ends) if ends else None
