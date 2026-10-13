#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_reel_autopublish.py — автопублікація IG-Reels за розкладом (reel_autopublish, рішення власника 13.10.2026).
Інваріанти: (1) чергу порожню/відсутню — тихо нічого; (2) готовий Reel (ціни й наявність збігаються з сайтом) → РІВНО один публікується, media_id у published.json, файли в published/;
(3) у запуску публікується максимум ОДИН Reel; (4) ціна на сайті ≠ ціні в підписі, або товару нема в каталозі (немає в наявності) → НЕ публікується, сигнал, береться наступний; (5) підпис >2200, відео порожнє, нема verify → НЕ публікується;
(6) not_before у майбутньому → чекає; (7) каталог сайту недоступний → нічого не публікуємо; (8) збій хостингу відео (до Instagram) → claim знято, повтор можливий; (9) Instagram відхилив → claim знято, повтор можливий;
(10) виняток під час виклику Instagram → claim ЛИШЕНО, автоповтору нема, сигнал; (11) .publishing із минулого запуску блокує повтор; (12) вже опублікований не публікується вдруге. Мережа не потрібна. `python test_reel_autopublish.py` → exit 0/1."""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import reel_autopublish as ra

F = []


def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c:
        F.append(n)


alerts = []
ra._alert = lambda key, text: alerts.append((key, text))
q = Path(tempfile.mkdtemp())
INDEX = {"1": {"price": 100, "name": "A"}, "2": {"price": 200, "name": "B"}}
hosted, published = [], []


def make(name, verify=None, caption="Підпис 100 грн", video=b"mp4data", extra=None, age=0):
    if video is not None:
        (q / f"{name}.mp4").write_bytes(video)
    (q / f"{name}.caption.txt").write_text(caption, encoding="utf-8")
    m = {"verify": verify if verify is not None else [{"id": "1", "price": 100}]}
    m.update(extra or {})
    p = q / f"{name}.json"
    p.write_text(json.dumps(m), encoding="utf-8")
    import os
    t = datetime.now().timestamp() - age
    os.utime(p, (t, t))


def run(**over):
    kw = dict(fetch_index=lambda: INDEX, host=lambda mp4: (hosted.append(mp4.name) or f"https://raw.githubusercontent.com/x/{mp4.name}"),
              publish=lambda url, cap: (published.append((url, cap)) or {"ok": True, "id": f"mid{len(published)}"}))
    kw.update(over)
    return ra.run_once(q, **kw)


chk("черга відсутня → тихо нічого", ra.run_once(q / "нема") == {"published": None, "held": [], "unknown": []})
chk("порожня черга → нічого", run()["published"] is None)

make("r1", age=300)
make("r2", age=200)
res = run()
chk("готовий Reel публікується; у запуску РІВНО один (найстарший)", res["published"] == "r1" and len(published) == 1 and hosted == ["r1.mp4"])
log = json.loads((q / "published.json").read_text(encoding="utf-8"))
chk("published.json: name, media_id, at, url", log[0]["name"] == "r1" and log[0]["media_id"] == "mid1" and log[0]["url"].endswith("r1.mp4"))
chk("файли першого перенесено в published/, у черзі лишився другий", (q / "published" / "r1.mp4").exists() and not (q / "r1.mp4").exists() and (q / "r2.mp4").exists())
res = run()
chk("наступний запуск публікує другий; перший вдруге не публікується", res["published"] == "r2" and len(published) == 2)

# ── ціна/наявність
alerts.clear()
make("p1", verify=[{"id": "1", "price": 120}], age=300)         # ціна на сайті 100 ≠ 120
make("p2", verify=[{"id": "9", "price": 50}], age=250)          # нема в каталозі
make("p3", age=200)                                             # нормальний
n0 = len(published)
res = run()
chk("ціна ≠ підпису → НЕ публікується, сигнал; нема в каталозі → теж; береться наступний готовий", res["published"] == "p3" and set(res["held"]) == {"p1", "p2"} and len(published) == n0 + 1)
chk("сигнали містять причину (ціна 100 ≠ 120; немає в каталозі)", any("100 ≠ 120" in t for _, t in alerts) and any("немає в каталозі" in t for _, t in alerts))
for f in ("p1", "p2"):
    for ext in (".mp4", ".caption.txt", ".json"):
        (q / f"{f}{ext}").unlink()

# ── інші факти
alerts.clear()
make("c1", caption="x" * 2300, age=300)
make("c2", video=b"", age=290)
make("c3", verify=[], age=280)
n0 = len(published)
res = run()
chk("підпис >2200, порожнє відео, нема verify → жоден не публікується", res["published"] is None and set(res["held"]) == {"c1", "c2", "c3"} and len(published) == n0)
for f in ("c1", "c2", "c3"):
    for ext in (".mp4", ".caption.txt", ".json"):
        (q / f"{f}{ext}").unlink(missing_ok=True)

make("f1", extra={"not_before": (datetime.now() + timedelta(days=2)).isoformat(timespec="minutes")})
n0 = len(published)
chk("not_before у майбутньому → чекає", run()["published"] is None and len(published) == n0)
(q / "f1.json").write_text(json.dumps({"verify": [{"id": "1", "price": 100}], "not_before": (datetime.now() - timedelta(days=1)).isoformat(timespec="minutes")}), encoding="utf-8")
chk("not_before у минулому → публікується", run()["published"] == "f1")

make("i1")
n0 = len(published)
res = run(fetch_index=lambda: (_ for _ in ()).throw(RuntimeError("сайт лежить")))
chk("каталог сайту недоступний → нічого не публікуємо (без перевірки цін)", res["published"] is None and len(published) == n0 and (q / "i1.mp4").exists())

res = run(host=lambda mp4: (_ for _ in ()).throw(ra.HostingError("хостинг не дав URL")))
chk("збій хостингу відео (до Instagram) → claim знято, у черзі, повтор можливий", res["published"] is None and not (q / "i1.publishing").exists() and "i1" in res["held"])
res = run(publish=lambda url, cap: {"ok": False, "error": "Instagram: ліміт"})
chk("Instagram відхилив → claim знято, у черзі", res["published"] is None and not (q / "i1.publishing").exists() and "i1" in res["held"])

alerts.clear()
res = run(publish=lambda url, cap: (_ for _ in ()).throw(RuntimeError("таймаут")))
chk("виняток під час виклику Instagram → claim ЛИШЕНО, сигнал «перевір Instagram»", (q / "i1.publishing").exists() and "i1" in res["unknown"] and any("Перевір Instagram" in t for _, t in alerts))
n0 = len(published)
res = run()
chk(".publishing блокує автоповтор (не публікуємо вдруге)", res["published"] is None and len(published) == n0 and "i1" in res["unknown"])

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
