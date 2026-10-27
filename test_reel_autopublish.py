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
              publish=lambda url, cap: (published.append((url, cap)) or {"ok": True, "id": f"mid{len(published)}"}),
              register=lambda *a, **k: True)
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


# ═══ ДОПОВНЕННЯ після аудиту PR #733 ═══
import os
import requests
import social_auto_poster as sap

# (A) невідомий результат media_publish (unknown=True) → claim ЛИШЕНО, автоповтору нема
make("u1", age=400)
alerts.clear()
res = run(publish=lambda url, cap: {"ok": False, "error": "ReadTimeout", "unknown": True})
chk("publish повернув unknown → claim лишено, сигнал «перевір Instagram», запуск зупинено", (q / "u1.publishing").exists() and "u1" in res["unknown"] and any("результат невідомий" in t_ for _, t_ in alerts))
n0 = len(published)
res = run()
chk("наступний запуск не публікує u1 повторно (.publishing тримає)", len(published) == n0 and "u1" in res["unknown"])
for ext in (".mp4", ".caption.txt", ".json", ".publishing"):
    (q / f"u1{ext}").unlink(missing_ok=True)

# (B) dry-run (нема IG-токенів) → зупинка, claim знято, сигнал про токени, наступні Reel не пушаться у feed-data
make("d1", age=400)
make("d2", age=390)
hosted.clear()
alerts.clear()
res = run(publish=lambda url, cap: {"ok": False, "dryrun": True})
chk("dryrun → claim знято, зупинено після ПЕРШОГО (host викликано раз), сигнал про токени", not (q / "d1.publishing").exists() and hosted == ["d1.mp4"] and any("нема IG_USER_ID" in t_ for _, t_ in alerts))
for f in ("d1", "d2"):
    for ext in (".mp4", ".caption.txt", ".json"):
        (q / f"{f}{ext}").unlink(missing_ok=True)

# (C) ціна з маніфесту має бути в тексті підпису
make("t1", verify=[{"id": "1", "price": 100}], caption="Тут ціна 999 грн", age=400)
alerts.clear()
n0 = len(published)
res = run()
chk("ціни з verify немає в тексті підпису → не публікується", len(published) == n0 and "t1" in res["held"] and any("немає в тексті підпису" in t_ for _, t_ in alerts))
for ext in (".mp4", ".caption.txt", ".json"):
    (q / f"t1{ext}").unlink(missing_ok=True)

# (D) ім'я файлу лише [a-z0-9_-]
make("Bad Name", age=400)
alerts.clear()
res = run()
chk("ім'я з великою літерою/пробілом → held з поясненням, не публікується", "Bad Name" in res["held"] and any("a-z, 0-9" in t_ for _, t_ in alerts))
for ext in (".mp4", ".caption.txt", ".json"):
    (q / f"Bad Name{ext}").unlink(missing_ok=True)

# (E) not_before: невалідний → held (fail-closed); aware з поясом коректно порівнюється
make("n1", extra={"not_before": "14 жовтня"}, age=400)
alerts.clear()
res = run()
chk("not_before нечитабельний → НЕ публікується (fail-closed), сигнал", res["published"] is None and "n1" in res["held"] and any("нечитабельний" in t_ for _, t_ in alerts))
(q / "n1.json").write_text(json.dumps({"verify": [{"id": "1", "price": 100}], "not_before": "2020-01-01T00:00:00+03:00"}), encoding="utf-8")
res = run()
chk("not_before з поясом (aware) у минулому → публікується, без винятку", res["published"] == "n1")

# (F) зіпсований маніфест не глушить чергу
make("z1", verify=[{"id": "1", "price": "376 грн"}], age=500)
make("z2", verify=["196937"], age=490)
make("z3", age=480)
alerts.clear()
res = run()
chk("price='376 грн' і verify=[рядок] → held із сигналом; наступний нормальний Reel публікується", res["published"] == "z3" and {"z1", "z2"} <= set(res["held"]))
for f in ("z1", "z2"):
    for ext in (".mp4", ".caption.txt", ".json"):
        (q / f"{f}{ext}").unlink(missing_ok=True)

# (G) атомарний claim: гонка (файл .publishing з'явився між перевіркою і створенням)
make("g1", age=400)
real_open = os.open
os.open = lambda path, flags, *a, **k: (_ for _ in ()).throw(FileExistsError()) if str(path).endswith(".publishing") else real_open(path, flags, *a, **k)
n0 = len(published)
res = run()
os.open = real_open
chk("гонка за claim (O_EXCL програно) → не публікуємо, unknown", len(published) == n0 and "g1" in res["unknown"])
for ext in (".mp4", ".caption.txt", ".json"):
    (q / f"g1{ext}").unlink(missing_ok=True)

chk("published.json пишеться атомарно (без .tmp у черзі)", not list(q.glob("published.json.tmp")))

# (H) social_auto_poster._publish_ig_reel: «визначена відмова» проти «невідомого результату», токен маскується
sap.IG_USER_ID, sap.FB_PAGE_ACCESS_TOKEN, sap.IG_REEL_POLL_SLEEP = "1", "SECRET_TOKEN_123", 0


class _Resp:
    def __init__(self, status=200, js=None, ok=None):
        self.status_code, self._js, self.content = status, js or {}, b"x"
        self.ok = (status < 400) if ok is None else ok

    def json(self):
        return self._js


def run_ig(post_seq, get_resp=None):
    calls = iter(post_seq)

    def fake_post(url, **kw):
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v
    sap.requests.post = fake_post
    sap.requests.get = lambda url, **kw: get_resp or _Resp(200, {"status_code": "FINISHED"})
    return sap._publish_ig_reel("https://x/v.mp4", "cap")


r = run_ig([_Resp(200, {"id": "c1"}), requests.exceptions.ReadTimeout("read timed out url=...access_token=SECRET_TOKEN_123&x=1")])
chk("таймаут НА media_publish → unknown=True, токен у помилці замасковано", r["ok"] is False and r["unknown"] is True and "SECRET_TOKEN_123" not in r["error"])
r = run_ig([requests.exceptions.ConnectionError("conn url=...access_token=SECRET_TOKEN_123")])
chk("збій ДО media_publish (створення контейнера) → unknown=False, токен замасковано", r["ok"] is False and not r.get("unknown") and "SECRET_TOKEN_123" not in r["error"])
r = run_ig([_Resp(200, {"id": "c1"}), _Resp(400, {"error": {"message": "Невалідне відео"}}, ok=False)])
chk("4xx з JSON-помилкою на media_publish → визначена відмова (unknown=False)", r["ok"] is False and r["unknown"] is False)
r = run_ig([_Resp(200, {"id": "c1"}), _Resp(502, {}, ok=False)])
chk("5xx на media_publish → unknown=True", r["ok"] is False and r["unknown"] is True)
r = run_ig([_Resp(200, {"id": "c1"}), _Resp(200, {"id": "m9"})])
chk("успіх → ok і id", r == {"ok": True, "id": "m9"})

# ---- запис опублікованого Reel на сайт (ig_reels.json), 20.10.2026: узгоджено з Shopify-code ----
import tempfile as _tf
from pathlib import Path as _P
_d = _P(_tf.mkdtemp())
_f = _d / "ig_reels.json"
_rebuilds = []
_now = datetime(2026, 10, 23, 18, 30)
_man = {"verify": [{"id": "177602", "price": 349}, {"id": "44404", "price": 598}, {"id": "288920", "price": 168}]}
ok1 = ra.register_on_site("builders_cars_01", _man, "Будуємо машинки!\n#іграшки", "mid1", _now, permalink=lambda m: "https://www.instagram.com/plutustoys.ua/reel/ABC123/", rebuild=lambda: _rebuilds.append(1), path=_f)
d1 = json.loads(_f.read_text(encoding="utf-8"))
chk("register_on_site: запис {date,url,title,ids}, запуск перебудови", ok1 and d1["reels"] == [{"date": "2026-10-23", "url": "https://www.instagram.com/plutustoys.ua/reel/ABC123/", "title": "Будуємо машинки!", "ids": ["177602", "44404", "288920"]}] and _rebuilds == [1])
ok2 = ra.register_on_site("builders_cars_01", _man, "x", "mid1", _now, permalink=lambda m: "https://www.instagram.com/plutustoys.ua/reel/ABC123/", rebuild=lambda: _rebuilds.append(1), path=_f)
chk("повторний виклик ідемпотентний (дубль за url не додається, перебудова не повторюється)", ok2 and len(json.loads(_f.read_text(encoding="utf-8"))["reels"]) == 1 and _rebuilds == [1])
ra.register_on_site("second", _man, "Другий", "mid2", _now, permalink=lambda m: "https://www.instagram.com/plutustoys.ua/reel/XYZ/", rebuild=lambda: _rebuilds.append(1), path=_f)
chk("другий Reel додається ПЕРЕД існуючими (найновіший першим)", [r["url"].split("/")[-2] for r in json.loads(_f.read_text(encoding="utf-8"))["reels"]] == ["XYZ", "ABC123"])
_alerts = []
ra._alert = lambda key, text: _alerts.append(key)
bad1 = ra.register_on_site("nopermalink", _man, "x", "mid3", _now, permalink=lambda m: "", rebuild=lambda: _rebuilds.append(1), path=_f)
bad2 = ra.register_on_site("noids", {"verify": []}, "x", "mid4", _now, permalink=lambda m: "https://www.instagram.com/plutustoys.ua/reel/Q/", rebuild=lambda: _rebuilds.append(1), path=_f)
def _boom(m):
    raise RuntimeError("graph down")
bad3 = ra.register_on_site("graphdown", _man, "x", "mid5", _now, permalink=_boom, rebuild=lambda: _rebuilds.append(1), path=_f)
chk("нема permalink / нема id / збій Graph → False, алерт, файл НЕ змінено, виняток назовні не йде", bad1 is False and bad2 is False and bad3 is False and len(_alerts) == 3 and len(json.loads(_f.read_text(encoding="utf-8"))["reels"]) == 2)
# порядок «найновіший першим» (аудит #902 B2) і обмеження довжини
_f2 = _d / "ig2.json"
for _i, _code in enumerate(("A1", "B2", "C3")):
    ra.register_on_site(f"n{_i}", _man, "T", f"m{_i}", datetime(2026, 10, 23 + _i, 18, 30), permalink=(lambda c: (lambda m: f"https://www.instagram.com/plutustoys.ua/reel/{c}/"))(_code), rebuild=lambda: None, path=_f2)
_dates = [r["date"] for r in json.loads(_f2.read_text(encoding="utf-8"))["reels"]]
chk("writer: найновіший ПЕРШИМ (дати 25,24,23)", _dates == ["2026-10-25", "2026-10-24", "2026-10-23"])
_big = {"reels": [{"date": "2026-01-01", "url": f"https://www.instagram.com/plutustoys.ua/reel/X{i}/", "title": "t", "ids": ["1"]} for i in range(60)], "extra": 1}
_f2.write_text(json.dumps(_big), encoding="utf-8")
ra.register_on_site("cap", _man, "T", "m", _now, permalink=lambda m: "https://www.instagram.com/plutustoys.ua/reel/NEW/", rebuild=lambda: None, path=_f2)
_d2 = json.loads(_f2.read_text(encoding="utf-8"))
chk("довжина ≤ IG_REELS_MAX, новий на початку, інші ключі верхнього рівня збережено", len(_d2["reels"]) == ra.IG_REELS_MAX and _d2["reels"][0]["url"].endswith("/NEW/") and _d2.get("extra") == 1)
# зіпсований файл НЕ стирається (S1)
_f.write_text("{зіпсовано", encoding="utf-8")
_alerts.clear()
_res_bad = ra.register_on_site("x", _man, "T", "m", _now, permalink=lambda m: "https://www.instagram.com/plutustoys.ua/reel/N1/", rebuild=lambda: None, path=_f)
chk("зіпсований ig_reels.json: False + алерт, файл НЕ перезаписано (історія не стирається)", _res_bad is False and _f.read_text(encoding="utf-8") == "{зіпсовано" and len(_alerts) == 1)
_f.write_text(json.dumps({"reels": "не список"}), encoding="utf-8")
chk("reels не список: False, файл не чіпаємо", ra.register_on_site("x", _man, "T", "m", _now, permalink=lambda m: "https://www.instagram.com/plutustoys.ua/reel/N1/", rebuild=lambda: None, path=_f) is False and json.loads(_f.read_text(encoding="utf-8")) == {"reels": "не список"})
# збій лише перебудови ≠ збій запису (S2)
_f3 = _d / "ig3.json"
_alerts.clear()
def _rb_fail():
    raise RuntimeError("systemctl код 5")
_ok_rb = ra.register_on_site("rb", _man, "T", "m", _now, permalink=lambda m: "https://www.instagram.com/plutustoys.ua/reel/RB/", rebuild=_rb_fail, path=_f3)
chk("збій перебудови: запис ЗБЕРЕЖЕНО, повертає True, окремий алерт reel_site_rebuild", _ok_rb is True and len(json.loads(_f3.read_text(encoding="utf-8"))["reels"]) == 1 and _alerts == ["reel_site_rebuild:rb"])
# токен не витікає (B1) + нормалізація permalink
class _R:
    def __init__(self, code, js=None):
        self.status_code = code
        self._js = js or {}
    def json(self):
        return self._js
import social_auto_poster as _sap
_sap.FB_PAGE_ACCESS_TOKEN = "SECRET_TOKEN_123"
_real_get = ra.requests.get
def _get_boom(*a, **k):
    raise ra.requests.exceptions.ConnectionError("HTTPSConnectionPool url=...access_token=SECRET_TOKEN_123")
ra.requests.get = _get_boom
_alerts_txt = []
ra._alert = lambda key, text: _alerts_txt.append(text)
ra.register_on_site("tok", _man, "T", "m", _now, rebuild=lambda: None, path=_d / "ig4.json")
ra.requests.get = lambda *a, **k: _R(400)
ra.register_on_site("tok2", _man, "T", "m", _now, rebuild=lambda: None, path=_d / "ig4.json")
chk("токен НЕ потрапляє в алерт при збої Graph (ConnectionError і HTTP 400)", len(_alerts_txt) == 2 and not any("SECRET_TOKEN_123" in x for x in _alerts_txt))
_ok = {}
for _pl in ("https://www.instagram.com/reel/AbC_1-x/", "https://www.instagram.com/plutustoys.ua/reel/AbC_1-x/", "https://instagram.com/reels/AbC_1-x", "https://www.instagram.com/p/AbC_1-x/?igsh=zzz", "http://www.instagram.com/reel/AbC_1-x/"):
    ra.requests.get = (lambda pl: (lambda *a, **k: _R(200, {"permalink": pl})))(_pl)
    _ok[_pl] = ra._ig_permalink("m")
chk("permalink: усі форми (з/без username і www, reels/p, query ?igsh, http) → https://www.instagram.com/plutustoys.ua/reel/<код>/", all(v == "https://www.instagram.com/plutustoys.ua/reel/AbC_1-x/" for v in _ok.values()))
ra.requests.get = lambda *a, **k: _R(200, {"permalink": "https://evil.example/reel/AbC/"})
chk("permalink чужого хоста → '' (не приймаємо)", ra._ig_permalink("m") == "")
ra.requests.get = _real_get
# run_once викликає register після публікації; збій register не скасовує публікацію
_calls = []
make("regtest", age=100)
r_ok = run(register=lambda *a, **k: _calls.append(a[0]) or True)
chk("run_once: після публікації викликається register(name,…)", r_ok["published"] == "regtest" and _calls == ["regtest"])

# ── автооновлення цін (запит власника 27.10.2026: «чому не поставити актуальні ціни?») — ціна в підписі це текст, у кадрі цін нема
ra._alert = lambda key, text: alerts.append((key, text))
IDX3 = {"177602": {"price": 329, "name": "A"}, "44404": {"price": 567, "name": "B"}, "288920": {"price": 170, "name": "C"}}
CAP3 = "Плутус підібрав:\n🚜 Трактор — 349 грн\n🏗️ Кран — 567 грн\n🚗 Машинка — 169 грн\n\n#plutustoys"
make("rf1", verify=[{"id": "177602", "price": 349}, {"id": "44404", "price": 567}, {"id": "288920", "price": 169}], caption=CAP3,
     extra={"not_before": "2020-01-01T00:00"}, age=900)
published.clear()
res = run(fetch_index=lambda: IDX3)
chk("ціни змінились (349→329, 169→170): рілс НЕ утримано, а опубліковано з живими цінами", res["published"] == "rf1" and res["held"] == [])
chk("опублікований підпис має нові ціни й не має старих (349/169), незмінена ціна 567 на місці",
    bool(published) and "329 грн" in published[-1][1] and "170 грн" in published[-1][1] and "567 грн" in published[-1][1]
    and "349" not in published[-1][1] and "169" not in published[-1][1])
_man_new = json.loads((q / "published" / "rf1.json").read_text(encoding="utf-8"))
chk("маніфест оновлено: verify на живі ціни, not_before збережено",
    [v["price"] for v in _man_new["verify"]] == [329, 567, 170] and _man_new.get("not_before") == "2020-01-01T00:00")
chk("сигнал «ℹ️ ціни оновлено» надіслано", any(k == "reel_prices_refreshed:rf1" and "329" in t for k, t in alerts))

# ланцюжок цін: 100→150 і 150→200 — одним проходом, без каскаду (інакше «100 грн» стало б 200)
IDX_CH = {"11": {"price": 150, "name": "A"}, "12": {"price": 200, "name": "B"}}
make("rf2", verify=[{"id": "11", "price": 100}, {"id": "12", "price": 150}], caption="А — 100 грн\nБ — 150 грн", age=800)
published.clear()
res = run(fetch_index=lambda: IDX_CH)
chk("ланцюг цін 100→150 і 150→200: без каскаду (А=150, Б=200)", res["published"] == "rf2" and published[-1][1] == "А — 150 грн\nБ — 200 грн")

# неоднозначно / нема в наявності / ціни немає в підписі → НЕ чіпаємо, утримуємо, файли без змін
make("rf3", verify=[{"id": "21", "price": 100}, {"id": "22", "price": 100}], caption="А — 100 грн\nБ — 100 грн", age=700)
_before = ((q / "rf3.caption.txt").read_text(encoding="utf-8"), (q / "rf3.json").read_text(encoding="utf-8"))
res = run(fetch_index=lambda: {"21": {"price": 110, "name": "A"}, "22": {"price": 120, "name": "B"}})
chk("одна стара ціна → дві різні нові: неоднозначно → утримано, файли не змінені",
    "rf3" in res["held"] and ((q / "rf3.caption.txt").read_text(encoding="utf-8"), (q / "rf3.json").read_text(encoding="utf-8")) == _before)
make("rf4", verify=[{"id": "31", "price": 100}, {"id": "99", "price": 50}], caption="А — 100 грн\nБ — 50 грн", age=600)
res = run(fetch_index=lambda: {"31": {"price": 105, "name": "A"}})
chk("товару нема в каталозі (нема в наявності) → утримано, підпис не переписано",
    "rf4" in res["held"] and "100 грн" in (q / "rf4.caption.txt").read_text(encoding="utf-8"))
make("rf6", verify=[{"id": "51", "price": 100}], caption="А — 100 грн", age=550)
res = run(fetch_index=lambda: {"51": {"price": 0, "name": "A"}})
chk("ціна на сайті 0 (збій даних) → НЕ підставляємо в підпис: утримано, підпис без змін",
    "rf6" in res["held"] and (q / "rf6.caption.txt").read_text(encoding="utf-8") == "А — 100 грн")
make("rf5", verify=[{"id": "41", "price": 100}], caption="Ціна 999 грн, без старої ціни", age=500)
res = run(fetch_index=lambda: {"41": {"price": 105, "name": "A"}})
chk("старої ціни нема в підписі → не вгадуємо: утримано, підпис без змін",
    "rf5" in res["held"] and (q / "rf5.caption.txt").read_text(encoding="utf-8") == "Ціна 999 грн, без старої ціни")

print("\nFAIL:" if F else "\nOK: усі перевірки пройдені", *F, sep="\n")
sys.exit(1 if F else 0)
