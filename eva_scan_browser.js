// eva_scan_browser.js — збір карток категорії EVA у ВАШОМУ Chrome (читання, 0 токенів). Нічого не міняє на EVA і в нашій системі.
// ЯК ЗАПУСТИТИ: 1) у Chrome відкрий https://eva.ua/ua/13515/detskie-igrushki/  2) F12 → вкладка Console  3) встав увесь цей файл → Enter
//   (Chrome може попросити набрати «allow pasting» — набери і встав ще раз)  4) вкладку НЕ закривай; ~100 хв (250 стор.). Статус: window.__scan.log.length
// ТЕМП: 18–26 с між сторінками. ПЕРШИЙ блок (403/Cloudflare) → зупинка й збереження того, що є. Нічого не обходить.
// ВИХІД: файли у Завантаженнях: eva_scan_part_50.json, _100..., і eva_scan_full_<дата>.json в кінці. Далі: python eva_competitor_scan.py --from-json <файл>
(() => {
  const SEED = location.origin + '/ua/13515/detskie-igrushki/';        // «Дитячі іграшки»: 250 стор. × 40 карток (ліміт видачі EVA 10 000)
  const MAXP = 250, DMIN = 18000, DJIT = 8000;
  const BLOCK = /Attention Required|Sorry, you have been blocked|Just a moment/i;
  const S = window.__scan = { cards: {}, log: [], done: false, blocked: false, started: new Date().toISOString() };
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
      if (best) out.set(pr, best.slice(0, 300));
    }
    return [...out.entries()];
  }
  function dl(name) {
    const b = new Blob([JSON.stringify({ cards: S.cards, log: S.log, blocked: S.blocked, started: S.started })], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = name; document.body.appendChild(a); a.click(); a.remove();
  }
  (async () => {
    for (let n = 1; n <= MAXP; n++) {
      const url = n === 1 ? SEED : SEED + '?p=' + n;
      let html = '', status = 0;
      try { const r = await fetch(url, { credentials: 'include' }); status = r.status; html = await r.text(); }
      catch (e) { S.log.push({ url, err: String(e).slice(0, 60) }); break; }
      if (status !== 200 || BLOCK.test(html.slice(0, 3000))) { S.log.push({ url, status, stop: 'BLOCK' }); S.blocked = true; break; }
      const items = extract(new DOMParser().parseFromString(html, 'text/html')); let nw = 0;
      for (const [pr, text] of items) if (!S.cards[pr]) { S.cards[pr] = { t: text, cat: SEED }; nw++; }
      S.log.push({ n, status, cards: items.length, new: nw });
      if (!items.length) break;
      if (n % 50 === 0) dl('eva_scan_part_' + n + '.json');
      await new Promise(r => setTimeout(r, DMIN + Math.random() * DJIT));
    }
    S.done = true; dl('eva_scan_full_' + new Date().toISOString().slice(0, 10) + '.json');
    console.log('EVA scan завершено: карток', Object.keys(S.cards).length, 'сторінок', S.log.length, 'блок:', S.blocked);
  })();
  console.log('EVA scan запущено; стежити: window.__scan.log.length');
})();
