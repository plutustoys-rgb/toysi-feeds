# -*- coding: utf-8 -*-
"""desktop_code_sync.sync(): fast-forward master лише коли безпечно; нічого не затирає; не падає."""
import os, subprocess, sys, tempfile
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import desktop_code_sync as dcs

F = []
def chk(n, c):
    print(f"[{'OK ' if c else 'FAIL'}] {n}")
    if not c: F.append(n)

def g(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()

tmp = Path(tempfile.mkdtemp())
origin = tmp / "origin.git"; work = tmp / "work"; pusher = tmp / "pusher"
subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True)
subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
for r in (work,):
    g(r, "config", "user.email", "t@t"); g(r, "config", "user.name", "t")
(work / "a.txt").write_text("1", encoding="utf-8"); g(work, "add", "a.txt"); g(work, "commit", "-qm", "c1"); g(work, "branch", "-M", "master"); g(work, "push", "-q", "origin", "master")
subprocess.run(["git", "clone", "-q", str(origin), str(pusher)], check=True, capture_output=True)
g(pusher, "config", "user.email", "t@t"); g(pusher, "config", "user.name", "t")
(pusher / "a.txt").write_text("2", encoding="utf-8"); g(pusher, "commit", "-qam", "c2"); g(pusher, "push", "-q", "origin", "master")

# 1. брудне дерево → пропуск, файл не затертий
(work / "a.txt").write_text("LOCAL", encoding="utf-8")
r = dcs.sync(work)
chk("брудне відстежуване дерево → пропуск, локальні правки цілі", "незакомічені" in r and (work / "a.txt").read_text(encoding="utf-8") == "LOCAL")
g(work, "checkout", "-q", "--", "a.txt")
# 2. не master → пропуск
g(work, "checkout", "-q", "-b", "feature")
r = dcs.sync(work)
chk("гілка не master → пропуск (код не оновлено)", "очікується master" in r and (work / "a.txt").read_text(encoding="utf-8") == "1")
g(work, "checkout", "-q", "master")
# 3. чисто → fast-forward
r = dcs.sync(work)
chk("чисте дерево на master → fast-forward", r.startswith("оновлено") and (work / "a.txt").read_text(encoding="utf-8") == "2")
# 4. повторно → актуально
chk("повторний виклик → «актуально»", dcs.sync(work).startswith("актуально"))
# 5. worktree (.git — файл) → пропуск
wt = tmp / "wt"; g(work, "worktree", "add", "-q", "-b", "wtb", str(wt))
chk("worktree (.git — файл) → пропуск", "не основна копія" in dcs.sync(wt))
# 6. не git-тека → не падає
plain = tmp / "plain"; plain.mkdir()
chk("не git-тека → рядок-статус, без винятку", isinstance(dcs.sync(plain), str))
# 7. локальний коміт (розбіжна історія) → fast-forward неможливий, нічого не ламаємо
(work / "b.txt").write_text("x", encoding="utf-8"); g(work, "add", "b.txt"); g(work, "commit", "-qm", "local")
(pusher / "c.txt").write_text("y", encoding="utf-8"); g(pusher, "add", "c.txt"); g(pusher, "commit", "-qm", "c3"); g(pusher, "push", "-q", "origin", "master")
r = dcs.sync(work)
chk("розбіжна історія → «fast-forward неможливий», дерево не зламане", "fast-forward неможливий" in r and (work / "b.txt").exists())
print(f"\n{'❌ ПРОВАЛЕНО: ' + str(F) if F else '✅ desktop_code_sync — усі перевірки коректні.'}")
sys.exit(1 if F else 0)
