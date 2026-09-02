"""control_panel.py — локальна панель керування PlutusToys (localhost, на ПК власника).

НАВІЩО (запит власника 2026-09-02): одне місце, де власник (1) БАЧИТЬ, що роблять агенти
(статуси + telegram-дайджест + критичний календар + баланси), (2) СЛЕ задачу агенту, (3)
ЗАПУСКАЄ сесію агента, (4) ЧАТИТЬ з агентом. Замість гортати Telegram і термінали.

АРХІТЕКТУРА: маленький stdlib-сервер (без Flask/залежностей) на 127.0.0.1 — не хоститься в
інтернет (VPS ми щойно замкнули; це свідомо localhost-only). Доступ із телефона — окремий опт-ін
крок пізніше (тунель+авторизація), не тут.

БЕЗПЕКА (CSRF): панель на 127.0.0.1 ДОСЯЖНА крос-доменно з локального браузера — тож POST-и,
що змінюють стан, захищені у `do_POST._csrf_ok` (кастомний заголовок X-Panel + Origin/Sec-Fetch),
щоб стороння вебсторінка не могла впорснути задачу/запустити сесію. НЕ «нуль поверхні» — поверхня
локальна й закрита свідомо (знахідка аудиту PR #469).

ПЕРЕВИКОРИСТАННЯ, не дублювання:
  • telegram_digest.build_digest — той самий дайджест, що вже перевірений на живих даних;
  • agent_watch.WATCHERS/COWORK_DIR/CLAUDE_BIN — той самий конфіг агентів і той самий рецепт
    запуску `claude` (Windows .cmd, cwd=папка каналів, headless), що вже будить агентів.

БЕЗПЕКА дій:
  • ЧАТ — `claude -p` з ЛИШЕ read-інструментами (Read/Glob/Grep): агент відповідає (stdout),
    але нічого не пише/не робить незворотного з панелі.
  • ЗАДАЧА — дописує рядок `## [Власник → <agent>] дата` у канал агента (як робить будь-хто),
    далі його підхоплює agent_watch. Це НЕ незворотна дія, лише запис у локальний канал-файл.
  • ЗАПУСК — відкриває ІНТЕРАКТИВНУ сесію `claude` в НОВОМУ терміналі (там власник сам керує);
    саме інтерактивна (не headless) сесія робить реальну збірку/PR (headless-Код sandboxed).
  • ТЕРМІНАЛ (/api/term) — виконує shell-команду в теці репо (вбудований термінал у панелі).
    ⚠️ Довільне ЛОКАЛЬНЕ виконання — прийнятне лише через bind 127.0.0.1 + CSRF-гейт (X-Panel):
    досяжний ЛИШЕ зі сторінки панелі власника, а локальний код і так має локальний exec, тож
    НОВОЇ зовнішньої поверхні не додає. НЕ відкривати панель у мережу без автентифікації.

ЗАПУСК: python control_panel.py [--port 8787]  → відкрий http://127.0.0.1:8787
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
    load_dotenv()   # підхопити ANTHROPIC_API_KEY з .env → дочірній `claude -p` авторизується (headless)
except Exception:
    pass

import telegram_digest
try:
    from agent_watch import WATCHERS, COWORK_DIR, CLAUDE_BIN, BASE_DIR
except Exception as e:  # агент_watch має імпортуватись чисто; якщо ні — не валимо панель
    print(f"[panel] не вдалось імпортувати agent_watch ({e}); чат/запуск вимкнено", file=sys.stderr)
    WATCHERS, COWORK_DIR, CLAUDE_BIN, BASE_DIR = [], Path("."), "claude", Path(".")

HOST = "127.0.0.1"          # LOCALHOST-ONLY — свідомо, не для інтернету
CHAT_TIMEOUT_SEC = 240
TERM_TIMEOUT_SEC = 90
_HEAD_RE = re.compile(r"^##\s+\[(.+?)\s*→\s*(.+?)\]\s*(\d{4}-\d{2}-\d{2})?")


# Ролі, доступні в ПАНЕЛІ додатково до agent_watch.WATCHERS (Код/SEO/SMM). Власник керує ними
# вручну — тож додаємо їх у панель, але СВІДОМО НЕ в auto-wake agent_watch, щоб не вмикати їм
# автопробудження, поки власник цього не вирішив (панель ≠ зміна набору спостерігачів монітора).
# label = мітка «до мене» в заголовках каналу (## [X → <label>]); у КОДВ мітка каналу = "КОДВ".
PANEL_EXTRA_AGENTS = [
    {"name": "Консультант", "target_label": "Консультант", "channels": ["CONSULTANT_CHANNEL.md"]},
    {"name": "Бухгалтер", "target_label": "КОДВ", "channels": ["КОДВ_CHANNEL.md"]},
]


def _agents():
    """Агенти панелі: з agent_watch (Код/SEO/SMM) + PANEL_EXTRA (консультант/бухгалтер)."""
    out = []
    for w in list(WATCHERS) + PANEL_EXTRA_AGENTS:
        out.append({"name": w["name"], "label": w.get("target_label", w["name"]),
                    "channels": w.get("channels", []), "cwd": w.get("cwd") or str(COWORK_DIR)})
    return out


def _agent_by_name(name):
    for a in _agents():
        if a["name"] == name:
            return a
    return None


def _last_channel_activity(agent):
    """(останній запис ДО агента, останній запис ВІД агента) з його каналів."""
    to_me, by_me = None, None
    for ch in agent["channels"]:
        p = COWORK_DIR / ch
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            m = _HEAD_RE.match(line)
            if not m:
                continue
            src, dst = m.group(1).strip(), m.group(2).strip()
            if dst == agent["label"]:
                to_me = line.strip()[:160]
            if src == agent["label"]:
                by_me = line.strip()[:160]
    return to_me, by_me


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _status_payload():
    agents = []
    for a in _agents():
        to_me, by_me = _last_channel_activity(a)
        agents.append({"name": a["name"], "label": a["label"],
                       "channels": a["channels"], "to_me": to_me, "by_me": by_me})
    crit = _read_json(BASE_DIR / "reports" / "critical_status.json")
    return {"agents": agents, "critical": crit, "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def _digest_payload(days=7):
    log = BASE_DIR / "reports" / "telegram_alerts.md"
    entries = telegram_digest.parse_log(log)
    return telegram_digest.build_digest(entries, days)


def _append_task(agent, text):
    """Дописує задачу власника у ПЕРШИЙ канал агента як новий заголовок `## [Власник → label] дата`."""
    if not agent["channels"]:
        raise ValueError("в агента нема каналу")
    p = COWORK_DIR / agent["channels"][0]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = f"\n## [Власник → {agent['label']}] {stamp}\n\n{text.strip()}\n"
    with p.open("a", encoding="utf-8") as f:
        f.write(block)
    return str(p)


def _chat(agent, message):
    """`claude -p` у контексті агента, ЛИШЕ read-інструменти → відповідь зі stdout."""
    preamble = (f"Ти — сесія «{agent['name']}» проєкту PlutusToys. Канали у твоєму cwd "
                f"({', '.join(agent['channels']) or 'нема'}) — можеш читати. Це ШВИДКИЙ чат від "
                f"власника з панелі керування (read-only: відповідай, але нічого не пиши/не роби "
                f"незворотного — для роботи власник запустить інтерактивну сесію). Питання:\n\n{message}")
    # ⚠️ Промпт передаємо через STDIN, а не як argv: багаторядковий рядок як аргумент до
    # claude.CMD на Windows ріжеться на першому переносі (cmd.exe), і все після «Питання:\n\n»
    # губиться — агент бачить порожнє питання (діагностовано живо 2026-09-02). stdin надійний.
    cmd = [CLAUDE_BIN, "-p",
           "--add-dir", str(COWORK_DIR), "--add-dir", str(BASE_DIR),
           "--allowedTools", "Read", "Glob", "Grep"]
    env = {**os.environ, "PLUTUS_AGENT_HEADLESS": "1"}
    try:
        r = subprocess.run(cmd, input=preamble, cwd=agent["cwd"], capture_output=True, text=True,
                           env=env, timeout=CHAT_TIMEOUT_SEC, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            return f"[помилка claude exit={r.returncode}] {(r.stderr or '')[:400]}"
        return (r.stdout or "").strip() or "[порожня відповідь]"
    except subprocess.TimeoutExpired:
        return f"[таймаут {CHAT_TIMEOUT_SEC}s — питання складне, запусти інтерактивну сесію]"
    except Exception as e:
        return f"[не вдалось запустити claude: {e}]"


def _launch(agent):
    """Відкриває ІНТЕРАКТИВНУ сесію claude в новому терміналі (Windows). Власник далі керує сам."""
    if os.name != "nt":
        return False, "запуск нового терміналу реалізовано лише під Windows"
    # start "" cmd /k <claude> — новий консоль-вікно з інтерактивним claude у папці каналів
    inner = f'cd /d "{agent["cwd"]}" && "{CLAUDE_BIN}"'
    try:
        subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/k", inner], cwd=agent["cwd"])
        return True, f"відкрив термінал для «{agent['name']}» у {agent['cwd']}"
    except Exception as e:
        return False, f"не вдалось: {e}"


def _term(cmd: str) -> dict:
    """Виконує shell-команду в теці репо й повертає вивід. ⚠️ Це ДОВІЛЬНЕ ЛОКАЛЬНЕ виконання —
    прийнятне лише тому, що сервер прив'язаний до 127.0.0.1 + CSRF-гейт (X-Panel у do_POST), тож
    досяжний ЛИШЕ зі сторінки панелі власника; будь-який локальний код і так має локальний exec,
    тож НОВОЇ зовнішньої поверхні атаки це не додає (див. докстрінг модуля). Вивід обрізаємо."""
    try:
        r = subprocess.run(cmd, shell=True, cwd=str(BASE_DIR), capture_output=True, text=True,
                           timeout=TERM_TIMEOUT_SEC, encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        return {"out": out[-8000:] or "(порожній вивід)", "code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"out": f"[таймаут {TERM_TIMEOUT_SEC}s — команда триває задовго]", "code": -1}
    except Exception as e:
        return {"out": f"[помилка: {e}]", "code": -1}


# ── HTTP ──────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # тихо
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._send(200, _PAGE, "text/html")
            elif path == "/api/status":
                self._json(200, _status_payload())
            elif path == "/api/digest":
                self._json(200, {"text": _digest_payload()})
            else:
                self._send(404, "not found", "text/plain")
        except Exception as e:
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _csrf_ok(self) -> bool:
        """Захист від CSRF із ЛОКАЛЬНОГО браузера (панель на 127.0.0.1 досяжна крос-доменно).
        (1) кастомний заголовок X-Panel — крос-домен його не поставить на «простому» запиті без
        preflight, а preflight ми не схвалюємо (жодних CORS-заголовків) → браузер блокує.
        (2) Origin/Sec-Fetch-Site — додатковий шар. Фронтенд панелі шле X-Panel сам."""
        if self.headers.get("X-Panel") != "1":
            return False
        port = self.server.server_address[1]
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return False
        sfs = self.headers.get("Sec-Fetch-Site")
        if sfs and sfs != "same-origin":
            return False
        return True

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._csrf_ok():
            return self._json(403, {"error": "заборонено (локальний CSRF-захист)"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 1_000_000:
                return self._json(400, {"error": "тіло завелике"})
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad json"})
        agent = _agent_by_name(body.get("agent", ""))
        if path in ("/api/task", "/api/chat", "/api/launch") and not agent:
            return self._json(400, {"error": "невідомий агент"})
        if path == "/api/task":
            text = (body.get("text") or "").strip()
            if not text:
                return self._json(400, {"error": "порожня задача"})
            try:
                where = _append_task(agent, text)
                return self._json(200, {"ok": True, "msg": f"дописано в {Path(where).name}; "
                                        f"agent_watch розбудить {agent['name']} наступним циклом"})
            except Exception as e:
                return self._json(500, {"error": str(e)})
        if path == "/api/chat":
            msg = (body.get("text") or "").strip()
            if not msg:
                return self._json(400, {"error": "порожнє повідомлення"})
            return self._json(200, {"reply": _chat(agent, msg)})
        if path == "/api/launch":
            ok, msg = _launch(agent)
            return self._json(200 if ok else 500, {"ok": ok, "msg": msg})
        if path == "/api/term":          # термінал НЕ прив'язаний до агента (машинна оболонка)
            cmd = (body.get("cmd") or "").strip()
            if not cmd:
                return self._json(400, {"error": "порожня команда"})
            return self._json(200, _term(cmd))
        self._json(404, {"error": "not found"})


_PAGE = """<!doctype html><html lang=uk><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>PlutusToys — панель</title>
<style>
:root{--bg:#0f1216;--card:#181d24;--line:#2a323d;--tx:#e6e9ee;--mut:#8b96a5;--acc:#4a9eff;--ok:#3ecf8e;--warn:#f0b429;--err:#ff5c5c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 system-ui,Segoe UI,sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:14px}
h1{font-size:16px;margin:0}.mut{color:var(--mut)}.small{font-size:12px}.wrap{max-width:none;margin:0;padding:18px 38px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:820px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card h2{font-size:14px;margin:0 0 10px}pre{white-space:pre-wrap;word-break:break-word;margin:0;font:12.5px/1.5 ui-monospace,Consolas,monospace}
.scroll{max-height:300px;overflow:auto}
.tabs{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.tab{background:#222a33;color:var(--tx);border:1px solid var(--line);border-radius:9px;padding:12px 24px;font-size:15px;font-weight:700;cursor:pointer}
.tab.active{background:var(--acc);color:#04121f;border-color:var(--acc)}
.meta{font-size:12px;color:var(--mut);margin:2px 0}.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:20px;border:1px solid var(--line);color:var(--mut)}
.row{display:flex;gap:8px;margin-top:8px;flex-wrap:wrap}
textarea{width:100%;background:#0e1319;color:var(--tx);border:1px solid var(--line);border-radius:7px;padding:8px;font:13px system-ui;resize:vertical;min-height:58px}
button{background:var(--acc);color:#04121f;border:0;border-radius:7px;padding:9px 14px;font-weight:600;cursor:pointer;white-space:nowrap}
button.ghost{background:#222a33;color:var(--tx)}button:disabled{opacity:.5;cursor:wait}
.chat{background:#0e1319;border:1px solid var(--line);border-radius:7px;padding:12px;margin:10px 0;height:360px;overflow:auto}
.reply{border-left:2px solid var(--acc);padding-left:8px;margin:8px 0;white-space:pre-wrap}.b{color:var(--acc)}
.sess{display:grid;grid-template-columns:3fr 2fr;gap:16px}@media(max-width:900px){.sess{grid-template-columns:1fr}}
.term{background:#05070a;border:1px solid var(--line);border-radius:7px;padding:10px;margin:6px 0;height:360px;overflow:auto;font:12.5px/1.5 ui-monospace,Consolas,monospace;white-space:pre-wrap;word-break:break-word}
.tinp{width:100%;background:#0e1319;color:var(--tx);border:1px solid var(--line);border-radius:7px;padding:9px;font:12.5px ui-monospace,Consolas,monospace}
.tcmd{color:var(--acc)}
</style></head><body>
<header><h1>🎛️ PlutusToys — панель керування</h1><span class=mut id=gen></span>
<span style="margin-left:auto"><button class=ghost onclick=refresh()>↻ оновити</button></span></header>
<div class=wrap>
<div class=card>
  <h2>🤖 Агенти — обери, щоб відкрити сесію</h2>
  <div class=tabs id=tabs>завантаження…</div>
  <div id=session></div>
</div>
<div class=grid style=margin-top:16px>
  <div class=card><h2>🧭 Що діється (Telegram-дайджест, 7 дн.)</h2><pre id=digest class=scroll>завантаження…</pre></div>
  <div class=card><h2>🚦 Критичний статус</h2><div id=crit class="small scroll">завантаження…</div></div>
</div>
</div>
<script>
const $=s=>document.querySelector(s);
const H={'Content-Type':'application/json','X-Panel':'1'};
let AG=[], SEL=null, SHOWN=null;
const LOG={};    // історія чату в пам'яті, по агенту
const TLOG=[];   // історія терміналу (машинна, спільна — не по агенту)
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function jget(u){return (await fetch(u)).json()}
async function jpost(u,b){return (await fetch(u,{method:'POST',headers:H,body:JSON.stringify(b)})).json()}
async function refresh(){
  const st=await jget('/api/status');$('#gen').textContent='оновлено '+st.generated;
  AG=st.agents; if(!SEL && AG.length) SEL=AG[0].name;
  renderTabs();
  if(SHOWN!==SEL) renderSession();   // не перемальовуємо сесію на автооновленні (щоб не стерти ввід/чат)
  const dot={critical:'🔴',warn:'🟠',ok:'🟢'};const ev=(st.critical&&st.critical.events)||[];
  const ord={critical:0,warn:1,ok:2};ev.sort((a,b)=>(ord[a.state]??3)-(ord[b.state]??3));
  $('#crit').innerHTML=ev.length?ev.map(e=>`<div style="padding:5px 0;border-bottom:1px solid var(--line)">`
    +`${dot[e.state]||'⚪'} <b>${esc(e.name)}</b> <span class=mut>— ${esc(e.detail||'')}</span>`
    +(e.state!=='ok'&&e.blocks?`<div class="small mut" style="margin-left:20px">блокує: ${esc(e.blocks)}</div>`:'')
    +`</div>`).join(''):'нема reports/critical_status.json';
  const d=await jget('/api/digest');$('#digest').textContent=d.text;
}
function renderTabs(){
  $('#tabs').innerHTML=AG.map(a=>`<button class="tab${a.name===SEL?' active':''}" onclick="pick('${a.name}')">${esc(a.name)}</button>`).join('');
}
function pick(n){SEL=n;renderTabs();renderSession()}
function renderSession(){
  SHOWN=SEL;const a=AG.find(x=>x.name===SEL);if(!a){$('#session').innerHTML='';return}
  if(!LOG[a.name])LOG[a.name]=[];
  const hist=LOG[a.name].map(m=>`<div class=reply><b>${esc(m.who)}:</b> ${esc(m.txt)}</div>`).join('')
             ||'<span class=mut>Чат порожній — напиши повідомлення нижче.</span>';
  $('#session').innerHTML=`
    <div><span class=tag>${esc(a.channels.join(', ')||'—')}</span></div>
    <div class=meta>← до нього: ${esc(a.to_me||'—')}</div>
    <div class=meta>→ від нього: ${esc(a.by_me||'—')}</div>
    <div class=sess>
      <div>
        <div class=meta>💬 Чат з «${esc(a.name)}»</div>
        <div class=chat id=chat>${hist}</div>
        <textarea id=inp placeholder="повідомлення для ${esc(a.name)}…"></textarea>
        <div class=row><button onclick=send()>💬 надіслати</button><button class=ghost onclick=queue()>📥 у чергу (канал)</button></div>
      </div>
      <div>
        <div class=meta>🖥 Термінал — тека репо (rozetka_agent)</div>
        <div class=term id=term></div>
        <input id=tcmd class=tinp placeholder="команда + Enter (напр. git status)…"
               onkeydown="if(event.key==='Enter'){event.preventDefault();runTerm()}">
      </div>
    </div>`;
  const c=$('#chat');if(c)c.scrollTop=c.scrollHeight;
  renderTerm();
}
function renderTerm(){const t=$('#term');if(!t)return;
  t.innerHTML=TLOG.map(x=>`<div style="margin-bottom:8px"><span class=tcmd>&gt; ${esc(x.cmd)}</span><div>${esc(x.out)}</div></div>`).join('')
    ||'<span class=mut>Термінал порожній. Введи команду нижче (виконується в теці репо).</span>';
  t.scrollTop=t.scrollHeight;}
async function runTerm(){const i=$('#tcmd'),cmd=i.value.trim();if(!cmd)return;
  TLOG.push({cmd,out:'…виконую…'});renderTerm();i.value='';
  const r=await jpost('/api/term',{cmd});
  TLOG[TLOG.length-1].out=(r.out!==undefined?r.out:(r.error||'?'));renderTerm();}
async function send(){const a=SEL,t=$('#inp'),msg=t.value.trim();if(!msg)return;
  LOG[a].push({who:'ти',txt:msg});LOG[a].push({who:a,txt:'…думає (до 4 хв)…'});renderSession();
  const r=await jpost('/api/chat',{agent:a,text:msg});
  LOG[a].pop();LOG[a].push({who:a,txt:r.reply||r.error||'?'});if(SEL===a)renderSession();}
async function queue(){const a=SEL,t=$('#inp'),msg=t.value.trim();if(!msg)return;
  const r=await jpost('/api/task',{agent:a,text:msg});alert(r.msg||r.error);if(r.ok!==false&&$('#inp'))$('#inp').value='';}
refresh();setInterval(refresh,60000);
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="Локальна панель керування PlutusToys (localhost).")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PANEL_PORT", "8787")))
    args = ap.parse_args()
    srv = ThreadingHTTPServer((HOST, args.port), Handler)
    print(f"[panel] http://{HOST}:{args.port}  (Ctrl+C — стоп)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[panel] стоп")
        srv.shutdown()


if __name__ == "__main__":
    main()
