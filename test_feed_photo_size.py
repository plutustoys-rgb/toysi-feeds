#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_feed_photo_size.py — Google/Meta/Bing: фото Prom < 500 px → оригінал Toysi, коли він більший (SEO 20.11.2026; feed_photo_size.py).
ІНВАРІАНТИ: розмір береться лише з заголовка файлу; мережа — лише у prefetch (build_feed_items читає кеш); невідомий розмір / збій → фото НЕ змінюється;
міняємо лише коли Prom < 500 І оригінал більший за меншою стороною; Prom ≥ 500 і не-Prom фото не чіпаємо; лише https images.prom.ua / toysi.ua;
FEED_SMALL_PHOTO_FIX=0 вимикає. `python test_feed_photo_size.py` → exit 0/1. Мережі не використовує.
"""
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["AUDIT_NO_TELEGRAM"] = "1"
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "site"))

_TMP = tempfile.mkdtemp(prefix="fps_test_")
os.environ["FEED_PHOTO_DIMS_CACHE"] = str(Path(_TMP) / "dims.json")
os.environ.pop("FEED_SMALL_PHOTO_FIX", None)

import feed_photo_size as fps  # noqa: E402
import generate_google_feed as gg  # noqa: E402

F = []


def chk(label, ok):
    print(("[OK ] " if ok else "[FAIL] ") + label)
    if not ok:
        F.append(label)


def jpeg(w, h):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9 + sof + b"\x00" * 20


def png(w, h):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"


def webp_vp8x(w, h):
    return b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8X" + b"\x0a\x00\x00\x00" + b"\x00\x00\x00\x00" + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")


def webp_vp8l(w, h):
    bits = (w - 1) | ((h - 1) << 14)
    return b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8L" + b"\x0a\x00\x00\x00" + b"\x2f" + struct.pack("<I", bits) + b"\x00" * 4


# ── парсер розміру ──
chk("JPEG 475x527 → (475, 527)", fps.parse_dims(jpeg(475, 527)) == (475, 527))
chk("PNG 800x600", fps.parse_dims(png(800, 600)) == (800, 600))
chk("WEBP VP8X 1200x900", fps.parse_dims(webp_vp8x(1200, 900)) == (1200, 900))
chk("WEBP VP8L 640x480", fps.parse_dims(webp_vp8l(640, 480)) == (640, 480))
chk("HTML/сміття/порожнє/обрізане → None", all(fps.parse_dims(b) is None for b in (b"<html>404</html>", b"", b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")))

# ── хости ──
chk("дозволені лише https images.prom.ua і toysi.ua",
    fps.host_allowed("https://images.prom.ua/1.jpg") and fps.host_allowed("https://toysi.ua/p/1.jpg"))
chk("чужі/небезпечні адреси відхилено (http, localhost, file, toysi.ua.evil.com, userinfo)",
    not any(fps.host_allowed(u) for u in ("http://toysi.ua/p/1.jpg", "https://127.0.0.1/x.jpg", "file:///etc/passwd", "https://toysi.ua.evil.com/p/1.jpg",
                                          "https://toysi.ua@evil.com/p/1.jpg", "https://evil.com/images.prom.ua/1.jpg", "", None, 5)))

_calls = []


class _NoNet:
    def __call__(self, *a, **k):
        _calls.append(a)
        raise AssertionError("мережа не мала викликатись")


_orig_urlopen = fps.urllib.request.urlopen
fps.urllib.request.urlopen = _NoNet()
chk("fetch_dims для чужого хоста → None БЕЗ мережі", fps.fetch_dims("https://evil.com/a.jpg") is None and not _calls)

class _Resp:
    def __init__(self, url, body):
        self._u, self._b = url, body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def geturl(self):
        return self._u

    def read(self, n=-1):
        return self._b


fps.urllib.request.urlopen = lambda rq, timeout=0: _Resp("https://evil.example/x.jpg", jpeg(900, 900))
chk("редірект на чужий хост (geturl не з дозволених) → None, розмір не береться", fps.fetch_dims("https://toysi.ua/p/1.jpg") is None)
fps.urllib.request.urlopen = lambda rq, timeout=0: _Resp("http://toysi.ua/p/1.jpg", jpeg(900, 900))
chk("редірект на http (схема) → None", fps.fetch_dims("https://toysi.ua/p/1.jpg") is None)
fps.urllib.request.urlopen = lambda rq, timeout=0: _Resp("https://toysi.ua/p/1.jpg", jpeg(900, 800))
chk("без редіректу: розмір береться (900, 800)", fps.fetch_dims("https://toysi.ua/p/1.jpg") == (900, 800))
fps.urllib.request.urlopen = _orig_urlopen

# ── prefetch з підміненою мережею ──
SIZES = {
    "https://images.prom.ua/a_w1024_h1024_x.jpg": (500, 400),      # мале Prom, оригінал більший → заміна
    "https://toysi.ua/p/1.jpg": (900, 800),
    "https://images.prom.ua/b_w1024_h1024_x.jpg": (500, 500),      # Prom вже 500 → не чіпаємо
    "https://toysi.ua/p/2.jpg": (1200, 1200),
    "https://images.prom.ua/c_w1024_h1024_x.jpg": (300, 300),      # мале Prom, оригінал не більший → лишаємо
    "https://toysi.ua/p/3.jpg": (300, 280),
    "https://images.prom.ua/d_w1024_h1024_x.jpg": (400, 400),      # мале Prom, оригінал недоступний → лишаємо
    "https://toysi.ua/p/4.jpg": None,
}
fetched = []


def fake_fetch(u):
    fetched.append(u)
    return SIZES.get(u)


fps.fetch_dims = fake_fetch
fps.reset_cache()
pairs = [("https://images.prom.ua/a_w1024_h1024_x.jpg", "https://toysi.ua/p/1.jpg"), ("https://images.prom.ua/b_w1024_h1024_x.jpg", "https://toysi.ua/p/2.jpg"),
         ("https://images.prom.ua/c_w1024_h1024_x.jpg", "https://toysi.ua/p/3.jpg"), ("https://images.prom.ua/d_w1024_h1024_x.jpg", "https://toysi.ua/p/4.jpg")]
t_now = time.time()
st = fps.prefetch(pairs, 60, now=t_now)
chk("prefetch: Prom опитано 4; оригінали Toysi — лише для 3 малих (b ≥ 500 не опитуємо)", st["prom"] == 4 and st["toysi"] == 3 and "https://toysi.ua/p/2.jpg" not in fetched)
chk("prefetch: Prom < 500 — 3, замін — 1 (лише a)", st["need"] == 3 and st["swap"] == 1)
chk("choose: a → оригінал Toysi", fps.choose_image(*pairs[0]) == "https://toysi.ua/p/1.jpg")
chk("choose: b (Prom 500) → Prom", fps.choose_image(*pairs[1]) == pairs[1][0])
chk("choose: c (оригінал не більший) → Prom", fps.choose_image(*pairs[2]) == pairs[2][0])
chk("choose: d (оригінал недоступний) → Prom", fps.choose_image(*pairs[3]) == pairs[3][0])
chk("choose: невідомий розмір (нема в кеші) → Prom", fps.choose_image("https://images.prom.ua/zz.jpg", "https://toysi.ua/p/9.jpg") == "https://images.prom.ua/zz.jpg")
chk("кеш записано атомарно (файл є, жодного .tmp* не лишилось)", fps.cache_path().exists() and not list(fps.cache_path().parent.glob(fps.cache_path().name + ".tmp*")))

fetched.clear()
fps.reset_cache()           # перечитати з файлу
fps.prefetch(pairs, 60, now=t_now + 3600)
chk("повторний прогін за годину: свіжі успіхи не опитуємо знову (мережа 0 — крім збою d/4, його TTL 1 добу теж свіжий)", fetched == [])
fps.reset_cache()
fps.prefetch(pairs, 60, now=t_now + 2 * 24 * 3600)
chk("через 2 доби: збій (toysi/p/4.jpg) опитано знову, успіхи — ні", fetched == ["https://toysi.ua/p/4.jpg"])
fps.reset_cache()
fetched.clear()
fps.prefetch(pairs, 60, now=t_now + 40 * 24 * 3600)
chk("через 40 діб: усе прострочено → опитано заново (Prom 4 + оригінали 3)", len(fetched) == 7)

# збій через 30+ діб не затирає давній успіх (аудит #1861)
fps.reset_cache()
fps.prefetch(pairs, 60, now=t_now)
_ok = SIZES["https://toysi.ua/p/1.jpg"]
SIZES["https://toysi.ua/p/1.jpg"] = None
fps.reset_cache()
fps.prefetch(pairs, 60, now=t_now + 40 * 24 * 3600)
chk("тимчасовий збій після 30 діб НЕ затирає давній успіх (розмір лишився 900x800, мітка часу оновлена)",
    fps.lookup("https://toysi.ua/p/1.jpg") == (900, 800) and fps._load()["https://toysi.ua/p/1.jpg"]["t"] == t_now + 40 * 24 * 3600)
SIZES["https://toysi.ua/p/1.jpg"] = _ok

fps.reset_cache()
os.remove(fps.cache_path())
fetched.clear()
st = fps.prefetch(pairs, -1, now=t_now)
chk("бюджет вичерпано → нічого не опитано, без винятку, фото як були", fetched == [] and fps.choose_image(*pairs[0]) == pairs[0][0])

os.environ["FEED_SMALL_PHOTO_FIX"] = "0"
fps.reset_cache()
fps.prefetch(pairs, 60, now=t_now)
fetched_off = list(fetched)
os.environ.pop("FEED_SMALL_PHOTO_FIX", None)
fps.reset_cache()
fps.prefetch(pairs, 60, now=t_now)
os.environ["FEED_SMALL_PHOTO_FIX"] = "0"
chk("FEED_SMALL_PHOTO_FIX=0: prefetch нічого не качає, choose завжди Prom", fetched_off == [] and fps.choose_image(*pairs[0]) == pairs[0][0])
os.environ.pop("FEED_SMALL_PHOTO_FIX", None)

# ── у зібраному елементі фіду ──
def item(pid, pics, name="Звичайний конструктор"):
    return {"id": pid, "name": name, "vendor": "Test", "price": 200.0, "stock": 5, "category_name": "Різне", "category_id": "1",
            "pictures": pics, "description": "Опис товару достатньої довжини для фіда.", "barcode": ""}


cat = {
    "1": item("1", ["https://toysi.ua/p/enl_1.jpg"]),          # Prom мале (a) → оригінал /p/1.jpg
    "2": item("2", ["https://toysi.ua/p/enl_2.jpg"]),          # Prom 500×500 → Prom
    "5": item("5", ["https://toysi.ua/p/enl_5.jpg"]),          # Prom-фото без запису в кеші → Prom
    "6": item("6", ["https://toysi.ua/p/enl_6.jpg"]),          # без Prom-фото: лише Toysi → оригінал enl→N як раніше
}
prom = {"1": {"main_image": "https://images.prom.ua/a_w200_h200_x.jpg"}, "2": {"main_image": "https://images.prom.ua/b_w200_h200_x.jpg"},
        "5": {"main_image": "https://images.prom.ua/e_w200_h200_x.jpg"}}


def build(c, p):
    items, st = gg.build_feed_items(c, p, {}, {}, None, None)
    return {i["pid"]: i["image_link"] for i in items}, st


fps.reset_cache()
fps.prefetch([("https://images.prom.ua/a_w1024_h1024_x.jpg", "https://toysi.ua/p/1.jpg"), ("https://images.prom.ua/b_w1024_h1024_x.jpg", "https://toysi.ua/p/2.jpg")], 60, now=time.time())
def _no_net(u):
    raise AssertionError("build_feed_items не має ходити в мережу")


fps.fetch_dims = _no_net
got, st = build(cat, prom)
chk("елемент: Prom 500×400 + оригінал 900×800 → https://toysi.ua/p/1.jpg", got.get("1") == "https://toysi.ua/p/1.jpg")
chk("елемент: Prom 500×500 → Prom (w1024)", got.get("2") == "https://images.prom.ua/b_w1024_h1024_x.jpg")
chk("елемент: Prom-фото без запису в кеші → Prom (як було)", got.get("5") == "https://images.prom.ua/e_w1024_h1024_x.jpg")
chk("елемент: лише фото Toysi (нема Prom) → /p/6.jpg як і раніше", got.get("6") == "https://toysi.ua/p/6.jpg")
chk("лічильник photo_toysi_original = 1", st.get("photo_toysi_original") == 1)
os.environ["FEED_SMALL_PHOTO_FIX"] = "0"
got, st = build(cat, prom)
chk("FEED_SMALL_PHOTO_FIX=0: елемент 1 лишається Prom, лічильника нема", got.get("1") == "https://images.prom.ua/a_w1024_h1024_x.jpg" and "photo_toysi_original" not in st)
os.environ.pop("FEED_SMALL_PHOTO_FIX", None)

img, alt = gg.feed_image_candidates(item("7", ["https://toysi.ua/p/3/enl_7.jpg"]), {"main_image": "https://images.prom.ua/q_w200_h200_x.jpg"})
chk("кандидат: вкладений шлях /p/3/enl_ лишається як є (оригіналу за правилом нема); рішення — за розмірами кешу", img.endswith("_w1024_h1024_x.jpg") and alt == "https://toysi.ua/p/3/enl_7.jpg")
img, alt = gg.feed_image_candidates(item("8", ["https://toysi.ua/p/enl_8.jpg"]), {})
chk("кандидат: без Prom-фото заміни нема", img == "https://toysi.ua/p/enl_8.jpg" and alt is None)
img, alt = gg.feed_image_candidates(item("9", []), {})
chk("кандидат: фото нема → (None, None)", img is None and alt is None)

import shutil  # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)
print()
if F:
    print(f"FAIL: {len(F)}")
sys.exit(1 if F else 0)
