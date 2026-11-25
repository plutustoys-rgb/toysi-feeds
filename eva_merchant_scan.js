// eva_merchant_scan.js — ПОВІЛЬНЕ зчитування вітрини ПРОДАВЦЯ-КОНКУРЕНТА на eva.ua (ЛИШЕ ЧИТАННЯ; 0 токенів; нічого не міняє). Адреси без параметрів або з ?p= — дозволені robots.txt EVA.
// ЗАПУСК: відкрити у Chrome https://eva.ua/ua/merchant-1218/ → F12 → Console → вставити файл → Enter (вкладку не закривати). Продовжує з місця зупинки (localStorage), навіть після перезапуску вкладки.
// ТЕМП ЛЮДИНИ: 60–95 с між сторінками + раз на ~12 сторінок довга пауза 4–8 хв (як людина відволіклась). ПЕРШИЙ блок/перевірка безпеки, що не минула за 20 с → ЗУПИНКА, статус 'blocked' (повторно не стукаємо; власник вирішує коли продовжити).
// НЕ обходить захист: жодних підмін/обхідних прийомів; пасивну перевірку Cloudflare просто чекаємо як звичайний браузер.
// ВИХІД: Завантаження: eva_merchant_<id>_part_<N>.json (кожні 10 стор.) і eva_merchant_<id>_full.json (кінець/блок). Далі: python eva_competitor_scan.py --from-json <файл> --competitor-only
(() => {
  const MERCHANT = (location.pathname.match(/merchant-(\d+)/) || [])[1];
  if (!MERCHANT) { console.error('Відкрий сторінку https://eva.ua/ua/merchant-<id>/'); return; }
  const SEED = location.origin + '/ua/merchant-' + MERCHANT + '/';
  const KEY = 'eva_merchant_scan_' + MERCHANT;
  const MAXP = 250, DMIN = 60000, DJIT = 35000, LONG_EVERY = 12;
  const BLOCK = /Attention Required|Sorry, you have been blocked|Just a moment|Триває перевірка безпеки|Трохи зачекайте/i;
  let S; try { S = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) { S = null; }
  if (!S || S.status === 'blocked') S = Object.assign(S || {}, { cards: (S && S.cards) || {}, next: (S && S.next) || 1, log: (S && S.log) || [], status: 'running' });
  window.__merchant = S;
  try { navigator.locks.request('eva-merchant-keepalive', () => new Promise(() => {})); } catch (e) {}
  const wk = new Worker(URL.createObjectURL(new Blob(['onmessage=e=>setTimeout(()=>postMessage(1),e.data)'], { type: 'text/javascript' })));
  const sleep = ms => new Promise(r => { wk.onmessage = () => r(); wk.postMessage(ms); });
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) { console.warn('localStorage повний — зберігаю лише файлом'); } };
  function dl(name) {
    const b = new Blob([JSON.stringify({ cards: S.cards, log: S.log, status: S.status, merchant: MERCHANT })], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = name; document.body.appendChild(a); a.click(); a.remove();
  }
  function extract(doc) {
    const out = new Map();
    for (const a of doc.querySelectorAll('a[href]')) {
      const m = (a.getAttribute('href') || '').match(/\/pr(\d+)\/?(#.*)?$/); if (!m) continue;
      const pr = m[1]; if (out.has(pr)) continue;
      let c = a, best = null;
      for (let i = 0; i < 6 && c; i++, c = c.parentElement) {
        const t = (c.textContent || '').replace(/\s+/g, ' ').trim();
        const prs = new Set([...c.querySelectorAll('a[href]')].map(x => (x.getAttribute('href') || '').match(/\/pr(\d+)/)).filter(Boolean).map(x => x[1]));
        if (prs.size > 1) break;
        if (/₴|Немає в наявності/.test(t)) best = t;
      }
      if (best) out.set(pr, best.slice(0, 200));
    }
    return [...out.entries()];
  }
  (async () => {
    for (let n = S.next; n <= MAXP; n++) {
      const url = n === 1 ? SEED : SEED + '?p=' + n;
      let html = '', status = 0;
      try { const r = await fetch(url, { credentials: 'include' }); status = r.status; html = await r.text(); }
      catch (e) { S.log.push({ n, err: String(e).slice(0, 50) }); S.status = 'error'; save(); break; }
      if (status !== 200 || BLOCK.test(html.slice(0, 3000))) {
        await sleep(20000);                                                   // пасивна перевірка: чекаємо як звичайний браузер, один повторний запит
        try { const r2 = await fetch(url, { credentials: 'include' }); status = r2.status; html = await r2.text(); } catch (e) {}
        if (status !== 200 || BLOCK.test(html.slice(0, 3000))) { S.log.push({ n, status, stop: 'BLOCK' }); S.status = 'blocked'; save(); break; }
      }
      const items = extract(new DOMParser().parseFromString(html, 'text/html')); let nw = 0;
      for (const [pr, text] of items) if (!S.cards[pr]) { S.cards[pr] = { t: text }; nw++; }
      S.log.push({ n, status, cards: items.length, new: nw, at: new Date().toISOString().slice(11, 19) });
      S.next = n + 1; save();
      if (!items.length) { S.status = 'end'; save(); break; }
      if (n % 10 === 0) dl('eva_merchant_' + MERCHANT + '_part_' + n + '.json');
      await sleep(DMIN + Math.random() * DJIT + (n % LONG_EVERY === 0 ? 240000 + Math.random() * 240000 : 0));
    }
    if (S.status === 'running') S.status = 'done';
    save(); dl('eva_merchant_' + MERCHANT + '_full.json');
    console.log('EVA merchant scan:', S.status, 'карток', Object.keys(S.cards).length, 'сторінок', S.log.length);
  })();
  console.log('EVA merchant scan запущено (продавець ' + MERCHANT + '), з сторінки', S.next, '; стан: window.__merchant');
})();
