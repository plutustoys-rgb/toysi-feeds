# -*- coding: utf-8 -*-
"""reel_batch.py — партія рілсів «сцена першою» із JSON-специфікацій: рендер (reel_build) + підпис + маніфест для автопублікації (reel_autopublish).

Для кожної спеки reels_specs/<queue_name>.json (поля reel_build + `queue_name`, `publish_date`, `caption`, у товарів `cap_emoji`/`cap_text`):
  1. живий фід наявності (feeds/meta_feed.xml або --feed) → reel_build.build() рендерить mp4 (перша карта = сцена, 3 товари, картка бренду; ПРАВИЛО №0: ціна не в кадрі);
  2. ЖИВІ ціни з опублікованого сайту (index-t.json / index-rt.json / index.json): підпис «<текст> — N грн» і маніфест verify (id, price) беруться з ЦЬОГО ж зрізу — те, що reel_autopublish звірить перед публікацією;
  3. <out_dir>/<queue_name>.mp4 + .caption.txt (≤ 2200) + .json {"verify": [...], "not_before": "<publish_date>T00:00"} — формат черги /opt/plutustoys/reels_queue/.
Нічого на VPS не пише й не публікує: результат — у теку для QA (SMM) і ОДНОГО scp власника.

Запуск:  python reel_batch.py reels_specs/puzzles_01.json [ще спеки…] [--out C:\\шлях\\reels_queue_upload] [--feed шлях/до/meta_feed.xml] [--prices-dir шлях з index*.json] [--no-render]"""
import argparse
import json
import os
import shutil
import sys
import urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from reel_seasons import season_not_after  # noqa: E402
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

IG_CAPTION_MAX = 2200
SITE = "https://plutustoys.com.ua"
TAIL = "Замовлення — на сайті plutustoys.com.ua (посилання в шапці профілю), поки є в наявності"
BASE_TAGS = ["#plutustoys", "#дитячііграшки", "#подарунокдитині"]


def fetch_json(name):
    with urllib.request.urlopen(f"{SITE}/{name}", timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def site_prices(prices_dir=None):
    """{id: ціна на сайті (int)} з index-t.json + index-rt.json (+ index.json як запасний): те, що бачить покупець ЗАРАЗ. Лише товари в наявності (s=1)."""
    out, seen = {}, set()
    for name in ("index-t.json", "index-rt.json", "index.json"):
        try:
            data = json.load(open(os.path.join(prices_dir, name), encoding="utf-8")) if prices_dir else fetch_json(name)
        except Exception as e:  # noqa: BLE001
            print(f"[reel_batch] ⚠ {name} не прочитано ({type(e).__name__}) — пропускаю", file=sys.stderr)
            continue
        for x in data:
            i = str(x.get("id"))
            if i in seen:                       # перший файл, де є товар, вирішує (index-t → index-rt → index): «нема в наявності» там не перекривається застарілим index.json
                continue
            seen.add(i)
            if x.get("s") == 1 and "pr" in x:
                out[i] = int(round(float(x["pr"])))
    return out


def build_caption(spec, prices):
    """Підпис: вступ, (опц.) підрядок, рядки товарів з ЖИВОЮ ціною, клич, хвіст про сайт, хештеги. Ціна лише з сайту — довільних знижок немає."""
    cap = spec["caption"]
    lines = [cap["intro"]]
    if cap.get("sub"):
        lines.append(cap["sub"])
    for it in spec["items"]:
        pr = prices.get(str(it["id"]))
        if pr is None:
            sys.exit(f"[reel_batch] СТОП: товару {it['id']} нема в наявності на сайті — підпис без ціни не пишемо")
        lines.append(f"{it['cap_emoji']} {it['cap_text']} — {pr} грн")
    lines.append("")
    lines.append(f"{cap['cta']} 👉 {TAIL}")
    lines.append("")
    tags = list(dict.fromkeys(BASE_TAGS + [t if t.startswith("#") else "#" + t for t in cap.get("hashtags", [])]))
    lines.append(" ".join(tags))
    text = "\n".join(lines)
    if len(text) > IG_CAPTION_MAX:
        sys.exit(f"[reel_batch] СТОП: підпис {len(text)} > {IG_CAPTION_MAX} символів")
    return text


def build_manifest(spec, prices):
    m = {"verify": [{"id": str(it["id"]), "price": prices[str(it["id"])]} for it in spec["items"]],
         "not_before": f"{spec['publish_date']}T00:00"}
    # сезонна сцена (halloween/snowflake): not_after = кінець вікна. reel_autopublish цього ключа НЕ читає — його перевіряє reels_upload.ps1 (не заливає прострочене) і ще раз — SMM при QA
    na = season_not_after([spec.get("first_scene"), spec.get("endscene")], date.fromisoformat(spec["publish_date"]))
    if na:
        m["not_after"] = f"{na.isoformat()}T23:59"
    return m


def process(spec_path, out_dir, feed_path, prices, render=True):
    spec = json.load(open(spec_path, encoding="utf-8"))
    for k in ("queue_name", "publish_date", "caption", "items"):
        if k not in spec:
            sys.exit(f"[reel_batch] СТОП: у {spec_path} нема ключа '{k}'")
    for it in spec["items"]:
        for k in ("cap_emoji", "cap_text"):
            if k not in it:
                sys.exit(f"[reel_batch] СТОП: у товарі {it.get('id')} нема '{k}'")
    caption = build_caption(spec, prices)
    manifest = build_manifest(spec, prices)
    os.makedirs(out_dir, exist_ok=True)
    name = spec["queue_name"]
    if render:
        import reel_build
        mp4 = reel_build.build({k: v for k, v in spec.items() if k not in ("queue_name", "caption")}, feed_path=feed_path)
        shutil.copyfile(mp4, os.path.join(out_dir, name + ".mp4"))
    with open(os.path.join(out_dir, name + ".caption.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write(caption + "\n")
    with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False)
    print(f"[reel_batch] {name}: готово → {out_dir} (публікація не раніше {spec['publish_date']}, verify {len(manifest['verify'])} цін)")
    return name


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="+")
    ap.add_argument("--out", default=os.path.join(HERE, "reels_queue_upload"))
    ap.add_argument("--feed", default=os.path.join(HERE, "feeds", "meta_feed.xml"))
    ap.add_argument("--prices-dir", default=None)
    ap.add_argument("--no-render", action="store_true", help="лише підписи й маніфести (без mp4)")
    a = ap.parse_args(argv)
    prices = site_prices(a.prices_dir)
    if len(prices) < 1000:
        sys.exit(f"[reel_batch] СТОП: цін із сайту підозріло мало ({len(prices)}) — зріз недоступний, підпис і verify не складаємо")
    for sp in a.specs:
        process(sp, a.out, a.feed, prices, render=not a.no_render)


if __name__ == "__main__":
    main()
