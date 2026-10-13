#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""feed_meta.py — feeds/feed_meta.json: мітка ГЕНЕРАЦІЇ кожного опублікованого фіду (запит Консультанта 12.10: час коміту гілки feed-data брехливий — eva-feed.timer щогодини
робить свіжий коміт, хоч Google/Meta/Bing не оновлювались). Тут: generated_at = час зміни ВИХІДНОГО файлу на VPS (коли його згенерував пайплайн), offers, bytes.
Фід, який цього прогону НЕ опублікований (порожній/заблокований гейтом), лишає попередній запис — вік видно чесно.
Використання (publish_feed_pipeline_vps.sh): python3 feed_meta.py <робоче-дерево-публікації> <каталог-фідів-VPS> <файл> [<файл> ...] — перелік ОПУБЛІКОВАНИХ цього прогону."""
import json
import os
import re
import sys
from datetime import datetime, timezone

_COUNT_RE = re.compile(rb"<item>|<offer[ >]")


def update_meta(workdir: str, src_dir: str, published: list) -> dict:
    path = os.path.join(workdir, "feeds", "feed_meta.json")
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            meta = {}
    except (OSError, ValueError):
        meta = {}
    for name in published:
        src = os.path.join(src_dir, name)
        try:
            st = os.stat(src)
            with open(src, "rb") as f:
                offers = len(_COUNT_RE.findall(f.read()))
        except OSError:
            continue
        meta[name] = {"generated_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                      "offers": offers, "bytes": st.st_size}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return meta


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: feed_meta.py <workdir> <src_dir> [file ...]")
    m = update_meta(sys.argv[1], sys.argv[2], sys.argv[3:])
    print(f"[feed_meta] {len(m)} фідів у feed_meta.json")
