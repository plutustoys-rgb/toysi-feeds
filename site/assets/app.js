/* PlutusToys — клієнтська логіка: кошик, лічильник, пошук, checkout.
   Статичний сайт: кошик у localStorage, оформлення POST → /api/order (бекенд крок 3). */
(function(){
  "use strict";
  var CART_KEY = "pt_cart_v1";
  // Суму доставки покупцю НЕ друкуємо (10.10.2026, рішення Консультанта: «65/70 ₴» нічим не підкріплене) — доки нема виміряної цифри з книги КОДВ.
  // Поріг безкоштовної доставки — число Консультанта (рекомендація CONSULTANT_CHANNEL.md
  // 2026-09-14/16, економічно перевірене 2026-09-18: ~3.1х середнього чека, floor витримує).
  // ⚠️ ВИПРАВЛЕННЯ АТРИБУЦІЇ (2026-09-18): попередній коментар посилався на "OWNER_INBOX 13.09" —
  // Консультант живо перевірив (grep) і власник живо перевірив (мною, зараз) — цього рядка
  // там НЕМА. Джерело числа — рекомендація Консультанта, не рішення власника; не приписувати
  // далі неіснуюче джерело.
  var FREE_SHIPPING_THRESHOLD = 1000;
  // 🔴 ВИМКНЕНО (2026-09-18, знахідка Консультанта + жива перевірка): обіцянка "безкоштовна
  // доставка" НІЧИМ не забезпечена — toysi_order_submit.py не має ЖОДНОГО поля для платника
  // доставки (grep підтвердив), а Toysi створює ТТН під СВОЇМ акаунтом НП, не нашим. Живий ТТН
  // реального замовлення (20451538255399, ClientBarcode eva_8-081403816/100451714) показав
  // PayerType="Recipient", FreeShipping="" — клієнт ЗАРАЗ платить доставку сам, незалежно від
  // суми замовлення. Показувати "Безкоштовно" клієнту, з якого потім спишуть на відділенні —
  // це гарантована відмова від COD-посилки (той самий клас ризику, що й 32% скасувань EVA).
  // Прогрес-бар лишається як несплачена обіцянка в коді — увімкнути (true) можна ЛИШЕ після
  // підтвердження з Toysi, що платник — відправник (ми) для замовлень сайту.
  var FREE_SHIPPING_ENABLED = false;

  function read(){ try{ return JSON.parse(localStorage.getItem(CART_KEY)) || {}; }catch(e){ return {}; } }
  function write(c){ try{ localStorage.setItem(CART_KEY, JSON.stringify(c)); }catch(e){} updateBadge(); }
  function count(){ var c=read(),n=0; for(var k in c){ n+=c[k].qty; } return n; }
  function total(){ var c=read(),s=0; for(var k in c){ s+=c[k].qty*c[k].price; } return s; }

  // ── Аналітика (GA4 + Meta Pixel; ID підставляє збірка в window.PT_ANALYTICS, без ID — нічого не шле) ──
  // ⚠ ТЕСТОВІ ПОДІЇ В GA4/Meta (не покупці; при трафіку ~2 відвідувачі/добу вони спотворюють воронку): 11.10.2026 ~13:27 — add_to_cart (самокат 6349 ₴, SMM);
  // 11.10.2026 ~16:27 — view_item, add_to_cart, begin_checkout (товар 235193, 468 ₴, Shopify-code, перевірка доставки подій). Тестове замовлення власника, коли
  // буде, дасть purchase — його теж позначити тут (дата/товар/«не покупець»). Дописувати сюди кожну наступну тестову подію.
  var FB_EVENT = {view_item:"ViewContent", add_to_cart:"AddToCart", begin_checkout:"InitiateCheckout", purchase:"Purchase"};
  function track(name, items, value, extra){
    try{
      var cfg=window.PT_ANALYTICS||{};
      var ga=items.map(function(i){ return {item_id:String(i.id), item_name:i.name, price:+i.price, quantity:i.qty||1}; });
      var gp={currency:"UAH", value:+value, items:ga}; for(var k in (extra||{})){ gp[k]=extra[k]; }
      if(cfg.ga4 && window.gtag){ window.gtag("event", name, gp); }
      if(cfg.fb && window.fbq && FB_EVENT[name]){
        var fp={content_type:"product", content_ids:items.map(function(i){ return String(i.id); }), currency:"UAH", value:+value,
                contents:items.map(function(i){ return {id:String(i.id), quantity:i.qty||1}; }), num_items:items.reduce(function(s,i){ return s+(i.qty||1); },0)};
        window.fbq("track", FB_EVENT[name], fp);
      }
    }catch(e){}
  }

  window.PT = {
    add:function(p){                 // p={id,name,price,photo}
      var c=read();
      if(c[p.id]){ c[p.id].qty++; } else { c[p.id]={id:p.id,name:p.name,price:+p.price,photo:p.photo,qty:1}; }
      write(c);
      track("add_to_cart", [{id:p.id, name:p.name, price:+p.price, qty:1}], +p.price);
      flash("Додано в кошик");
    },
    track:track,
    setQty:function(id,q){ var c=read(); if(c[id]){ c[id].qty=Math.max(0,q); if(c[id].qty===0){delete c[id];} write(c); } },
    remove:function(id){ var c=read(); delete c[id]; write(c); },
    clear:function(){ write({}); },
    read:read, count:count, total:total,
    search:{norm:normText, tokens:searchTokens, match:searchMatch, hay:buildHay}
  };

  function updateBadge(){
    var b=document.getElementById("cart-badge");
    if(!b) return;
    var n=count();
    b.textContent=n;
    b.classList.toggle("on", n>0);
  }

  // невелике сповіщення "додано"
  var toast;
  function flash(msg){
    if(!toast){
      toast=document.createElement("div");
      toast.style.cssText="position:fixed;left:50%;bottom:110px;transform:translateX(-50%);background:#141413;color:#fff;"+
        "padding:10px 16px;border-radius:12px;font-size:14px;z-index:60;opacity:0;transition:opacity .2s;pointer-events:none;max-width:90%";
      document.body.appendChild(toast);
    }
    toast.textContent=msg; toast.style.opacity="1";
    clearTimeout(toast._t); toast._t=setTimeout(function(){ toast.style.opacity="0"; },1400);
  }

  // ── Пошуковий оверлей (індекс index.json) ──
  var idx=null, idxLoading=false;
  function ensureIndex(cb){
    if(idx){ cb(idx); return; }
    if(idxLoading) return;
    idxLoading=true;
    fetch("index.json").then(function(r){return r.json();}).then(function(d){ idx=d; idxLoading=false; cb(idx); })
      .catch(function(){ idxLoading=false; });
  }
  function openSearch(){
    var o=document.getElementById("search-overlay");
    if(!o) return;
    o.classList.add("on");
    var inp=o.querySelector("input");
    inp.value=""; inp.focus();
    o.querySelector(".results").innerHTML="";
    ensureIndex(function(){});
  }
  function closeSearch(){ var o=document.getElementById("search-overlay"); if(o){ o.classList.remove("on"); } }
  // ── Пошук (зауваження Тестувальника/Консультанта 10.10.2026): токени через AND у БУДЬ-ЯКОМУ порядку (було: літеральний підрядок —
  // «лялька барбі» давало 0); апострофи U+02BC/U+2019/U+2018/U+0027/U+0060 прибираються з ОБОХ боків («мʼяка»=«м'яка»=«мяка»);
  // показуємо «Показано N з M» і сортування (раніше мовчазна стеля 40 за порядком файлу).
  var SEARCH_SHOW=40, searchSort="rec", lastMatches=[], lastQuery="";
  function normText(s){ return String(s==null?"":s).toLowerCase().replace(/[\u02bc\u02b9\u2019\u2018\u2032\u0027\u0060]/g,""); }
  // закінчення відкидаємо (≥5 літер: лишається max(4, довжина−2)), щоб «хлопчика»/«поліція» знаходили «хлопчиків»/«поліцейська»
  function stemTok(t){ return t.length>=5 ? t.slice(0, Math.max(4, t.length-2)) : t; }
  // Словник брендів (Консультант 10.10.2026): той самий бренд у каталозі Toysi пишеться обома абетками (L.O.L 65 лат./7 кирил., Funko 30/0, ...),
  // тож запит будь-якою абеткою мусить знаходити обидві. Це ЯВНИЙ обмежений словник (не транслітерація): кожна група — варіанти одного бренду
  // (уже в нормалізованому вигляді: нижній регістр, без апострофів). Назва/запит, що містить варіант (збіг з ПОЧАТКУ слова), отримує тег #bN.
  var BRANDS=[
    ["лол","l.o.l","lol"], ["фанко","funko"], ["майнкрафт","minecraft"], ["барбі","barbie"],
    ["хот вілс","хот вілз","хот вилз","хотвілс","hot wheel","hotwheel"], ["щенячий патруль","paw patrol","pawpatrol"], ["пепа","пеппа","peppa"],
    ["холодне серце","холодное сердце","frozen"], ["марвел","marvel"], ["дісне","дисне","disney"], ["соник","сонік","sonic"], ["покемон","pokemon"],
    ["гаррі поттер","гарри поттер","harry potter"], ["павук","spider"], ["трансформер","transformer"]
  ];
  var WORDCH=/[a-z\u0430-\u044f\u0456\u0457\u0454\u0491\u0027\u02bc0-9]/;
  // Позиція збігу варіанта бренду з ПОЧАТКУ слова; для КОРОТКИХ варіантів (≤4 літер: «лол», «lol») після нього допустимо не більше 1 літери
  // (лолі — ок; «лоліта», «lolly» — ні). Повертає {i,end} (end — кінець СЛОВА, щоб видаляти слово цілком) або null.
  function findBrand(s, v){
    var i=s.indexOf(v);
    while(i>=0){
      if(i===0 || !WORDCH.test(s.charAt(i-1))){
        var end=i+v.length, j=end;
        while(j<s.length && WORDCH.test(s.charAt(j))) j++;
        if(v.length>4 || (j-end)<=1) return {i:i, end:j};
      }
      i=s.indexOf(v, i+1);
    }
    return null;
  }
  function brandNorm(s){ return s.replace(/[-_]+/g," ").replace(/\s+/g," "); }   // «hot-wheels»/«hot  wheels» = «hot wheels»
  function brandTags(nn){
    var t="", sn=brandNorm(nn);
    for(var g=0; g<BRANDS.length; g++){
      for(var k=0; k<BRANDS[g].length; k++){ if(findBrand(sn, BRANDS[g][k])){ t+=" #b"+g+"_"; break; } }
    }
    return t;
  }
  // haystack: {n: назва + теги брендів, c: КАТЕГОРІЯ}; категорія враховується лише для токенів ≥4 літер (аудит #665: «гра» не повинна тягнути «Іграшки…»)
  function buildHay(p){ var nn=normText(p.n); return {n:nn+brandTags(nn), c:normText(p.c)}; }
  function searchTokens(q){
    var nq=brandNorm(normText(q)), tags=[];
    for(var g=0; g<BRANDS.length; g++){
      var vs=BRANDS[g].slice().sort(function(a,b){return b.length-a.length;});   // довші варіанти першими
      for(var k=0; k<vs.length; k++){
        var m=findBrand(nq, vs[k]);
        if(m){ tags.push("#b"+g+"_"); nq=nq.slice(0,m.i)+" "+nq.slice(m.end); break; }   // видаляємо слово цілком, а не лише префікс
      }
    }
    return nq.split(/\s+/).map(function(t){return t.replace(/^[^a-z\u0430-\u044f\u0456\u0457\u0454\u0491\u0027\u02bc0-9]+|[^a-z\u0430-\u044f\u0456\u0457\u0454\u0491\u0027\u02bc0-9]+$/g,"");})
      .filter(function(t){return t.length>0;}).map(stemTok).concat(tags);
  }
  function searchMatch(h, toks){
    for(var i=0;i<toks.length;i++){
      var t=toks[i];
      if(h.n.indexOf(t)>=0) continue;
      if(t.length>=4 && t.charAt(0)!=="#" && h.c.indexOf(t)>=0) continue;
      return false;
    }
    return true;
  }
  function sortedMatches(list, mode){
    var a=list.slice();
    if(mode==="cheap") a.sort(function(x,y){return x.pr-y.pr;});
    else if(mode==="dear") a.sort(function(x,y){return y.pr-x.pr;});
    return a;   // rec = порядок index.json (рекомендовані)
  }
  function renderSearch(){
    var res=document.querySelector("#search-overlay .results");
    if(!res) return;
    if(!lastMatches.length){
      res.innerHTML='<div class="empty" style="padding:40px 0">Нічого не знайдено за «'+esc(lastQuery)+'»</div>';
      return;
    }
    var out=sortedMatches(lastMatches, searchSort).slice(0, SEARCH_SHOW);
    var meta='<div class="sr-meta"><span>Показано '+out.length+' з '+lastMatches.length+'</span><span class="sr-sort">'+
      [["rec","Рекомендовані"],["cheap","Дешевші"],["dear","Дорожчі"]].map(function(m){
        return '<button type="button" data-sort="'+m[0]+'"'+(searchSort===m[0]?' class="on"':'')+'>'+m[1]+'</button>';
      }).join("")+'</span></div>';
    res.innerHTML = meta + out.map(function(p){
      return '<a class="sr" href="product-'+p.id+'.html">'+
        '<img src="'+p.p+'" loading="lazy" alt="">'+
        '<div><div class="srn">'+esc(p.n)+'</div><div class="srp">'+p.pr+' ₴</div></div></a>';
    }).join("");
  }
  function runSearch(q){
    var res=document.querySelector("#search-overlay .results");
    if(!res) return;
    q=(q||"").trim();
    if(q.length<2){ res.innerHTML=""; lastMatches=[]; return; }
    var toks=searchTokens(q);
    if(!toks.length){ res.innerHTML=""; lastMatches=[]; return; }   // запит лише з апострофів/розділових → не показуємо «весь каталог»
    ensureIndex(function(data){
      var all=[];
      for(var i=0;i<data.length;i++){
        var p=data[i];
        if(p._nn===undefined) p._nn=buildHay(p);   // раз на елемент індексу (кеш)
        if(searchMatch(p._nn, toks)) all.push(p);
      }
      lastMatches=all; lastQuery=q;
      renderSearch();
    });
  }
  function esc(s){ return String(s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];}); }

  // ── Каталог: сортування + фільтри (клієнтом з index.json, весь каталог) ──
  function catCard(p){
    var av = p.s ? '<span class="av">У наявності</span>' : '<span class="av oos">Немає</span>';
    var img = p.p ? '<img src="'+esc(p.p)+'" loading="lazy" alt="'+esc(p.n)+'">' : '<div class="ph"></div>';
    return '<a class="card" href="product-'+esc(p.id)+'.html"><div class="ph">'+img+'</div>'+
      '<div class="info"><div class="nm">'+esc(p.n)+'</div>'+
      '<div class="foot"><span class="pr">'+p.pr+' ₴</span>'+av+'</div></div></a>';
  }
  function initCatalog(){
    var controls=document.getElementById("cat-controls");
    if(!controls) return;  // не сторінка каталогу з панеллю
    var elSort=document.getElementById("cf-sort"), elCat=document.getElementById("cf-cat"),
        elMin=document.getElementById("cf-min"), elMax=document.getElementById("cf-max"),
        elStock=document.getElementById("cf-instock"), elReset=document.getElementById("cf-reset"),
        grid=document.getElementById("cat-js"), count=document.getElementById("cat-count"),
        moreWrap=document.getElementById("cat-more-wrap"), moreBtn=document.getElementById("cat-more"),
        statik=document.getElementById("cat-static");
    var PER=24, shown=0, filtered=[];
    ensureIndex(function(data){
      var cats={};
      for(var i=0;i<data.length;i++){ if(data[i].c) cats[data[i].c]=(cats[data[i].c]||0)+1; }
      Object.keys(cats).sort(function(a,b){return a.localeCompare(b,"uk");}).forEach(function(c){
        var o=document.createElement("option"); o.value=c; o.textContent=c+" ("+cats[c]+")"; elCat.appendChild(o);
      });
      controls.hidden=false;
      apply();
    });
    function apply(){
      var min=parseInt(elMin.value,10), max=parseInt(elMax.value,10);
      if(isNaN(min)) min=null; if(isNaN(max)) max=null;
      var cat=elCat.value, ins=elStock.checked, sort=elSort.value;
      filtered=(idx||[]).filter(function(p){
        if(cat && p.c!==cat) return false;
        if(ins && !p.s) return false;
        if(min!=null && p.pr<min) return false;
        if(max!=null && p.pr>max) return false;
        return true;
      });
      if(sort==="cheap") filtered.sort(function(a,b){return a.pr-b.pr;});
      else if(sort==="dear") filtered.sort(function(a,b){return b.pr-a.pr;});
      else if(sort==="az") filtered.sort(function(a,b){return String(a.n).localeCompare(String(b.n),"uk");});
      // rec = природний порядок index.json (рекомендовані, за замовчуванням)
      shown=0; grid.innerHTML="";
      if(statik) statik.hidden=true;
      grid.hidden=false; count.hidden=false;
      renderMore();
    }
    function renderMore(){
      if(!filtered.length){
        grid.innerHTML='<div class="empty" style="padding:40px 0">Нічого не знайдено за фільтром. <a href="#" id="cf-reset2">Скинути</a></div>';
        var r2=document.getElementById("cf-reset2"); if(r2) r2.addEventListener("click",function(e){e.preventDefault();doReset();});
        count.textContent="Знайдено: 0"; moreWrap.hidden=true; return;
      }
      var slice=filtered.slice(shown, shown+PER);
      grid.insertAdjacentHTML("beforeend", slice.map(catCard).join(""));
      shown+=slice.length;
      count.textContent="Знайдено: "+filtered.length+" · показано "+shown;
      moreWrap.hidden = shown>=filtered.length;
    }
    function doReset(){ elSort.value="rec"; elCat.value=""; elMin.value=""; elMax.value=""; elStock.checked=false; apply(); }
    var deb=debounce(apply,250);
    [elSort,elCat,elStock].forEach(function(e){ if(e) e.addEventListener("change",apply); });
    [elMin,elMax].forEach(function(e){ if(e) e.addEventListener("input",deb); });
    if(moreBtn) moreBtn.addEventListener("click",renderMore);
    if(elReset) elReset.addEventListener("click",doReset);
  }

  // ── Рендер кошика (сторінка cart.html) ──
  function renderCart(){
    var box=document.getElementById("cart-body");
    if(!box) return;
    var c=read(), ids=Object.keys(c);
    var form=document.getElementById("checkout-form");
    if(ids.length===0){
      box.innerHTML='<div class="empty"><div class="fox"><img class="mascot" src="assets/plutus_mascot_s.png" alt="Плутус" width="48" height="44" decoding="async"></div>Кошик порожній.<br>Оберіть іграшки в <a href="catalog.html" style="color:var(--accent)">каталозі</a>.</div>';
      var s=document.getElementById("cart-summary"); if(s) s.style.display="none";
      if(form) form.style.display="none";
      return;
    }
    var s0=document.getElementById("cart-summary"); if(s0) s0.style.display="";
    if(form) form.style.display="";
    var html="";
    ids.forEach(function(id){
      var it=c[id];
      html+='<div class="cart-item" data-id="'+id+'">'+
        '<img src="'+it.photo+'" loading="lazy" alt="">'+
        '<div style="flex:1"><div class="ci-nm">'+esc(it.name)+'</div>'+
        '<div class="ci-pr">'+it.price+' ₴</div>'+
        '<div class="qty"><button data-act="dec" aria-label="Менше"'+(it.qty<=1?' disabled':'')+'>−</button><span class="q">'+it.qty+'</span><button data-act="inc" aria-label="Більше">+</button></div>'+
        '</div><button class="ci-rm" data-act="rm">Прибрати</button></div>';
    });
    box.innerHTML=html;
    renderSummary();
  }
  function renderSummary(){
    var goods=total();
    // FREE_SHIPPING_ENABLED=false (2026-09-18, поки платник доставки не з'ясований з Toysi) —
    // freeShip завжди false: сума доставки не друкується (вартість визначає Нова Пошта, сплачується при отриманні).
    var freeShip=FREE_SHIPPING_ENABLED && goods>=FREE_SHIPPING_THRESHOLD;
    var g=document.getElementById("sum-goods"), d=document.getElementById("sum-delivery"), t=document.getElementById("sum-total");
    if(g) g.textContent=goods+" ₴";
    if(d) d.textContent=freeShip ? "Безкоштовно" : "орієнтовно 70–90 ₴ до 2 кг";
    if(t) t.textContent=goods+" ₴";
    var block=document.getElementById("free-ship-block");
    if(block) block.style.display=FREE_SHIPPING_ENABLED ? "" : "none";
    var note=document.getElementById("free-ship-note");
    if(note && FREE_SHIPPING_ENABLED){
      if(freeShip){
        note.textContent="🎉 Вітаємо — у вас безкоштовна доставка Новою Поштою!";
        note.classList.add("free-ship-done");
      } else {
        var left=FREE_SHIPPING_THRESHOLD-goods;
        note.textContent="Додайте ще "+left+" ₴ до безкоштовної доставки";
        note.classList.remove("free-ship-done");
      }
      var pct=Math.min(100, Math.round(goods/FREE_SHIPPING_THRESHOLD*100));
      var bar=document.getElementById("free-ship-bar");
      if(bar) bar.style.width=pct+"%";
    }
  }

  // ── Ініціалізація на кожній сторінці ──
  document.addEventListener("DOMContentLoaded", function(){
    updateBadge();

    // делеговані кліки: додати в кошик, пошук, кошик-керування
    document.body.addEventListener("click", function(e){
      var t=e.target.closest("[data-add]");
      if(t){ e.preventDefault(); PT.add(JSON.parse(t.getAttribute("data-add"))); return; }
      if(e.target.closest("#open-search")){ e.preventDefault(); openSearch(); return; }
      if(e.target.closest("#close-search")){ e.preventDefault(); closeSearch(); return; }
      var sb=e.target.closest("#search-overlay [data-sort]");
      if(sb){ e.preventDefault(); searchSort=sb.getAttribute("data-sort"); renderSearch(); return; }
      var ci=e.target.closest(".cart-item [data-act]");
      if(ci){
        var wrap=ci.closest(".cart-item"), id=wrap.getAttribute("data-id"), act=ci.getAttribute("data-act");
        var c=read();
        if(act==="inc") PT.setQty(id, (c[id]?c[id].qty:0)+1);
        else if(act==="dec"){ var cur=c[id]?c[id].qty:0; if(cur>1) PT.setQty(id, cur-1); }   // «−» при кількості 1 НЕ видаляє товар (видалення — лише явне «Прибрати»)
        else if(act==="rm") PT.remove(id);
        renderCart();
      }
    });

    var so=document.querySelector("#search-overlay input");
    if(so){ var sTimer=null; so.addEventListener("input", function(){ clearTimeout(sTimer); sTimer=setTimeout(function(){ runSearch(so.value); }, 200); }); }   // debounce 200 мс: не перераховуємо 21 тис. на кожне натискання

    renderCart();
    initCheckout();
    initNpAutocomplete();
    initCatalog();
    initHomeCarousel();
    initListTracking();
    initContactTracking();

    // сторінка подяки — підставити номер замовлення
    var oidEl=document.getElementById("thanks-oid");
    if(oidEl){ try{ oidEl.textContent = sessionStorage.getItem("pt_last_order") || ""; }catch(e){} }

    // подія purchase — один раз на замовлення (оновлення сторінки не дублює): дані зберіг checkout у sessionStorage
    if(oidEl){
      try{
        var po=sessionStorage.getItem("pt_last_order"), pj=sessionStorage.getItem("pt_last_order_items");
        if(po && pj && sessionStorage.getItem("pt_purchase_sent")!==po){
          var pit=JSON.parse(pj), pv=pit.reduce(function(s,i){ return s+i.price*i.qty; },0);
          track("purchase", pit, pv, {transaction_id:po});
          sessionStorage.setItem("pt_purchase_sent", po);
        }
      }catch(e){}
    }

    // подія view_item — на картці товару в наявності (дані з кнопки «У кошик»)
    var vi=document.querySelector("h1.prod") && document.querySelector("[data-add]");
    if(vi){ try{ var vp=JSON.parse(vi.getAttribute("data-add")); track("view_item", [{id:vp.id, name:vp.name, price:+vp.price, qty:1}], +vp.price); }catch(e){} }
  });

  // ── Головна: карусель «Добірка Плутуса» — стрілки, view_item_list / select_item (GA4) ──
  function trackRaw(name, params, fbName, fbParams, fbCustom){
    try{
      var cfg=window.PT_ANALYTICS||{};
      if(cfg.ga4 && window.gtag){ window.gtag("event", name, params); }
      if(cfg.fb && window.fbq && fbName){ window.fbq(fbCustom?"trackCustom":"track", fbName, fbParams||{}); }
    }catch(e){}
  }
  function slideData(a, idx){
    var m=/product-(\d+)\.html/.exec(a.getAttribute("href")||"");
    var nm=a.querySelector(".nm"), pr=a.querySelector(".pr");
    return {item_id:m?m[1]:"", item_name:nm?nm.textContent.trim():"", price:pr?parseFloat(pr.textContent):0, index:idx, item_list_name:""};
  }
  function initHomeCarousel(){
    var car=document.getElementById("home-car");
    if(!car) return;
    var track=car.querySelector(".track"), left=car.querySelector(".arrow.l"), right=car.querySelector(".arrow.r");
    var listName=car.getAttribute("data-list")||"Добірка Плутуса";
    var slides=Array.prototype.slice.call(car.querySelectorAll("a.card"));
    function items(){ return slides.map(function(a,i){ var d=slideData(a,i); d.item_list_name=listName; return d; }); }
    // Широкий екран показує 5 слайдів одразу — 4-й і 5-й не мають лишатись lazy (порожні картки на першому екрані). У Chromium вони й так у зоні lazy-порога (≈1250 px) — виміряно аудитом #678;
    // це страхування для браузерів із меншим порогом (Safari/Firefox), без претензії на економію ваги.
    if(typeof matchMedia==="function" && matchMedia("(min-width: 700px)").matches){
      slides.slice(3,5).forEach(function(a){ var im=a.querySelector("img"); if(im){ im.loading="eager"; } });
    }
    car.classList.add("js");   // стрілки показуємо лише коли JS працює (без нього вони були б «мертвими»; нативний скрол/свайп лишається)
    function sync(){
      if(!track||!left||!right) return;
      // допуск ±6 px: padding .track + scroll-snap лишають scrollLeft ≈2,4 у стані спокою (аудит #678)
      left.disabled = track.scrollLeft <= 6;
      right.disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 6;
    }
    function scrollBy(dir){
      var reduce = typeof matchMedia==="function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
      if(track) track.scrollBy({left:dir*Math.max(160, track.clientWidth*0.8), behavior: reduce ? "auto" : "smooth"});
    }
    if(left) left.addEventListener("click", function(){ userAction(); scrollBy(-1); });
    if(right) right.addEventListener("click", function(){ userAction(); scrollBy(1); });
    if(track){ track.addEventListener("scroll", sync); sync(); }
    window.addEventListener("resize", sync);   // ширина вікна/поворот екрана міняють «край»
    window.addEventListener("orientationchange", sync);
    // ── Автопрокрутка (SMM 12.10: власник бачить нерухому карусель як «не крутиться»): по 1 картці кожні ~4 с зі scroll-snap. НЕ крутимо, коли:
    //   курсор мишею над каруселлю (pointerType touch не рахується: після тапу «наведення» на телефоні залипає), клавіатурний фокус всередині (:focus-visible — клік мишею по стрілці фокус
    //   не «тримає»), палець на екрані, вкладка прихована (document.hidden), карусель менше ніж на 40% у вікні (IntersectionObserver), prefers-reduced-motion, або щойно була дія
    //   покупця (стрілка/свайп/колесо/клавіша — пауза 10 с, щоб не відбирати керування). На краю — плавно на початок (без клонів слайдів, але далі крутиться по колу, поки нема паузи; кнопки паузи нема — лише hover/фокус/дія). Нових зображень не підвантажуємо: порядок
    //   і набір слайдів ті самі; в аналітику автопрокрутку НЕ пишемо (select_item лише на клік, view_item_list — як було).
    var AUTO_MS = 4000, USER_GRACE_MS = 10000;
    var auto = {hover:false, focus:false, touch:false, touchAt:0, visible:true, until:0};
    function reducedMotion(){ return typeof matchMedia==="function" && matchMedia("(prefers-reduced-motion: reduce)").matches; }
    function userAction(){ auto.until = Date.now() + USER_GRACE_MS; }
    function autoStep(){
      if(auto.touch && Date.now() - auto.touchAt > 30000) auto.touch = false;   // запобіжник: touchend/touchcancel не прийшли (системний жест iOS) — не стоїмо вічно
      if(!track || reducedMotion() || document.hidden || !auto.visible || auto.hover || auto.focus || auto.touch || Date.now() < auto.until) return;
      var atEnd = track.scrollLeft + track.clientWidth >= track.scrollWidth - 6;
      if(atEnd){ if(track.scrollTo) track.scrollTo({left:0, behavior:"smooth"}); return; }
      var step = slides.length > 1 ? (slides[1].offsetLeft - slides[0].offsetLeft) : 0;
      if(!(step > 0)) step = Math.max(160, track.clientWidth * 0.8);
      track.scrollBy({left:step, behavior:"smooth"});
    }
    if(track && slides.length > 1){
      car.addEventListener("pointerenter", function(e){ if(e.pointerType !== "touch") auto.hover = true; });
      car.addEventListener("pointerleave", function(e){ if(e.pointerType !== "touch") auto.hover = false; });
      car.addEventListener("focusin", function(e){
        var vis = true; try{ vis = e.target && e.target.matches ? e.target.matches(":focus-visible") : true; }catch(err){}
        if(vis) auto.focus = true;
      });
      car.addEventListener("focusout", function(){ auto.focus = false; });
      car.addEventListener("touchstart", function(){ auto.touch = true; auto.touchAt = Date.now(); }, {passive:true});
      car.addEventListener("touchend", function(e){ if(!e || !e.touches || e.touches.length === 0) auto.touch = false; userAction(); }, {passive:true});   // мультитач: поки лишився хоч один палець — стоїмо
      car.addEventListener("touchcancel", function(){ auto.touch = false; userAction(); }, {passive:true});
      car.addEventListener("wheel", userAction, {passive:true});
      car.addEventListener("keydown", userAction);
      if(typeof IntersectionObserver === "function"){
        var vio = new IntersectionObserver(function(es){ es.forEach(function(e){ auto.visible = !!e.isIntersecting; }); }, {threshold:0.4});
        vio.observe(car);
        auto.visible = false;   // до першого повідомлення від IntersectionObserver вважаємо «не видно» — карусель нижче першого екрана не крутиться вхолосту
      }
      setInterval(autoStep, AUTO_MS);
    }
    // view_item_list: один раз, коли карусель реально потрапила на екран (без IntersectionObserver — одразу)
    var sent=false;
    function fireList(){
      if(sent) return; sent=true;
      trackRaw("view_item_list", {item_list_id:"home_pick", item_list_name:listName, items:items()});
    }
    if(typeof IntersectionObserver==="function"){
      var io=new IntersectionObserver(function(es){ es.forEach(function(e){ if(e.isIntersecting){ fireList(); io.disconnect(); } }); }, {threshold:0.4});
      io.observe(car);
    } else { fireList(); }
    car.addEventListener("click", function(e){
      var a=e.target.closest("a.card"); if(!a) return;
      var i=slides.indexOf(a); var d=slideData(a, i<0?0:i); d.item_list_name=listName;
      trackRaw("select_item", {item_list_id:"home_pick", item_list_name:listName, items:[d]});
    });
  }
  // ── Посадкова /ig.html: сітка карток з item_list_name «ig_landing» — view_item_list один раз (коли сітка видна), select_item лише на клік ──
  function initListTracking(){
    var box=document.getElementById("ig-list");
    if(!box) return;
    var listName=box.getAttribute("data-list")||"ig_landing";
    var cards=Array.prototype.slice.call(box.querySelectorAll("a.card"));
    function items(){ return cards.map(function(a,i){ var d=slideData(a,i); d.item_list_name=listName; return d; }); }
    var sent=false;
    function fireList(){
      if(sent) return; sent=true;
      trackRaw("view_item_list", {item_list_id:"ig_landing", item_list_name:listName, items:items()});
    }
    if(typeof IntersectionObserver==="function"){
      // поріг 0.1, а не 0.4: на 320 px сітка в 1 колонку (≈2250 px заввишки) — 40% її висоти у вікні не бувають, і view_item_list не слався б ніколи (аудит #686)
      var io=new IntersectionObserver(function(es){ es.forEach(function(e){ if(e.isIntersecting){ fireList(); io.disconnect(); } }); }, {threshold:0.1});
      io.observe(box);
    } else { fireList(); }
    box.addEventListener("click", function(e){
      var a=e.target.closest("a.card"); if(!a) return;
      var i=cards.indexOf(a); var d=slideData(a, i<0?0:i); d.item_list_name=listName;
      trackRaw("select_item", {item_list_id:"ig_landing", item_list_name:listName, items:[d]});
    });
  }
  // ── Контакти/соцмережі: клік по телефону й IG/FB у футері (SMM: міряти соцтрафік) ──
  function initContactTracking(){
    document.addEventListener("click", function(e){
      var t=e.target.closest ? e.target.closest("a[href]") : null; if(!t) return;
      var href=t.getAttribute("href")||"";
      if(href.indexOf("tel:")===0){
        trackRaw("phone_click", {link_url:href, page_path:location.pathname}, "Contact", {method:"phone"}, false);
      } else if(t.closest(".fsoc")){
        var net=/instagram\.com/.test(href)?"instagram":(/facebook\.com/.test(href)?"facebook":"other");
        trackRaw("social_click", {network:net, link_url:href, page_path:location.pathname}, "SocialClick", {network:net}, true);
      }
    });
  }

  // ── Автокомпліт міста/відділення Нової Пошти (через site_order_api) ──
  var selectedCityRef = "";
  var cityListShown = false;   // довідник міст відповів непорожнім списком для поточного тексту
  var cityPending = false;     // текст міста змінено, відповідь довідника ще не прийшла (fail-closed до таймауту 6 с)
  var lastCityList = null;     // останній список — щоб повернути його, коли клік повз прибрав випадайку, а submit заблоковано
  var cityRerender = function(){};
  function cityNeedsPick(){ return !selectedCityRef && (cityPending || cityListShown); }
  var selectedCityArea = "";  // область обраного міста (НП AreaDescription) → у np_branch, щоб Toysi
                               // не переплутав однойменні населені пункти різних областей (24.09.2026)
  var selectedWarehouseNumber = "";  // № обраного з автокомпліту відділення НП → віддаємо Toysi напряму
  function debounce(fn, ms){ var t; return function(){ var a=arguments, self=this; clearTimeout(t); t=setTimeout(function(){ fn.apply(self,a); }, ms); }; }
  function renderAc(box, opts, onPick, extra){
    if(!opts.length){ box.innerHTML=""; return; }
    // Опції — role=option з tabindex=0: вибір і з клавіатури (Tab/Enter/Space, стрілки, Esc), а не лише вказівником (аудит #682 р.2: інакше клавіатурний/скрінрідер-покупець застрягав).
    if(box.setAttribute){ box.setAttribute("role","listbox"); box.setAttribute("aria-label","Підказки"); }
    box.innerHTML = opts.map(function(o,i){
      return '<div class="opt" role="option" tabindex="0" data-i="'+i+'">'+esc(o.label)+(o.sub?'<small>'+esc(o.sub)+'</small>':'')+'</div>';
    }).join("") + (extra ? '<div class="opt opt-none" role="option" tabindex="0" data-i="-1">'+esc(extra.label)+'</div>' : "");
    function choose(el){
      var i=+el.getAttribute("data-i");
      if(i<0){ if(extra) extra.onPick(); } else { onPick(opts[i]); }
      box.innerHTML="";
    }
    Array.prototype.forEach.call(box.querySelectorAll(".opt"), function(el){
      el.addEventListener("mousedown", function(ev){ ev.preventDefault(); choose(el); });
      el.addEventListener("keydown", function(ev){
        var k=ev.key;
        if(k==="Enter" || k===" "){ ev.preventDefault(); choose(el); }
        else if(k==="ArrowDown" || k==="ArrowUp"){
          ev.preventDefault();
          var sib = k==="ArrowDown" ? el.nextElementSibling : el.previousElementSibling;
          if(sib && sib.focus) sib.focus();
        }
        else if(k==="Escape"){ box.innerHTML=""; }
      });
    });
  }
  function initNpAutocomplete(){
    var city=document.getElementById("f-city"), acCity=document.getElementById("ac-city");
    var wh=document.getElementById("f-warehouse"), acWh=document.getElementById("ac-warehouse");
    if(!city || !wh) return;

    // Місто: СКИДАННЯ ref/області — негайно на кожен ввід (а не в debounce: інакше submit/тап у вікні 250 мс ніс би застарілий ref іншого міста), пошук — з debounce.
    // citySeq відсікає запізнілі/невпорядковані відповіді довідника; cityPending блокує submit, поки відповідь не прийшла (fail-closed), але не довше 6 с.
    function arrowToList(input, box){
      input.addEventListener("keydown", function(ev){
        if(ev.key==="ArrowDown"){ var f=box.querySelector ? box.querySelector(".opt") : null; if(f && f.focus){ ev.preventDefault(); f.focus(); } }
      });
    }
    arrowToList(city, acCity); arrowToList(wh, acWh);
    var citySeq=0, whSeq=0, pendingTimer=null;
    function lookupCity(){
      var q=city.value.trim(), my=citySeq;
      if(q.length<2){ cityPending=false; acCity.innerHTML=""; return; }
      fetch("api/np/city?q="+encodeURIComponent(q)).then(function(r){return r.json();}).then(function(d){
        if(my!==citySeq || selectedCityRef) return;      // застаріла відповідь або місто вже обране — не перемальовуємо
        var cities=(d && Array.isArray(d.cities)) ? d.cities : [];
        cityPending=false; cityListShown=cities.length>0; lastCityList=cities; cityRerender();
      }).catch(function(){
        // API НП недоступний — не лишаємо форму в глухому куті: підказок нема, ручний ввід уже дозволено.
        if(my!==citySeq) return;
        cityPending=false; cityListShown=false; lastCityList=null; acCity.innerHTML="";
      });
    }
    var lookupTimer=null;
    function lookupDebounced(){ clearTimeout(lookupTimer); lookupTimer=setTimeout(lookupCity, 250); }   // власний таймер: вибір / «Мого міста немає» його СКАСОВУЮТЬ (інакше відкладений пошук повертав блок)
    cityRerender=function(){
      var cities=lastCityList||[];
      renderAc(acCity, cities.map(function(c){ return {label:c.name, sub:c.area, area:c.area||"", ref:c.ref, name:c.name}; }),
        function(pick){ citySeq++; clearTimeout(lookupTimer); whSeq++; city.value=pick.name; selectedCityRef=pick.ref||""; selectedCityArea=pick.area||""; cityListShown=false; cityPending=false; wh.placeholder="Номер або адреса відділення"; wh.focus(); },
        {label:"Мого міста немає в списку", onPick:function(){ citySeq++; clearTimeout(lookupTimer); cityListShown=false; cityPending=false; wh.placeholder="Номер відділення"; wh.focus(); }});   // citySeq++: запізнілі відповіді довідника вже не повернуть блок
    };
    city.addEventListener("input", function(){
      citySeq++; whSeq++;
      // Щойно користувач торкнувся міста — РОЗБЛОКОВУЄМО відділення й БІЛЬШЕ не вимикаємо його.
      // Раніше поле лишалось disabled, доки не тапнеш підказку міста — на телефоні це зривало
      // оформлення (не влучив пальцем у випадайку → відділення «мертве»). Тап по місту лише
      // додає ref для автопідказок відділення; без тапу — ручний ввід відділення (він і так
      // приймається як вільний текст, order_router розбирає при форварді).
      selectedCityRef=""; selectedCityArea=""; selectedWarehouseNumber=""; wh.value=""; acWh.innerHTML="";
      cityListShown=false; lastCityList=null;
      wh.disabled=false; wh.placeholder="Оберіть місто зі списку — або введіть відділення";
      cityPending = city.value.trim().length>=2;
      clearTimeout(pendingTimer);
      if(cityPending){ var my=citySeq; pendingTimer=setTimeout(function(){ if(my===citySeq) cityPending=false; }, 6000); }
      lookupDebounced();
    });

    wh.addEventListener("input", debounce(function(){
      selectedWarehouseNumber="";  // ввід руками скидає обраний № — щоб не лишити стале значення
      if(!selectedCityRef) return;
      var q=wh.value.trim(), myWh=++whSeq, myCity=selectedCityRef;
      fetch("api/np/warehouse?city_ref="+encodeURIComponent(selectedCityRef)+"&q="+encodeURIComponent(q)).then(function(r){return r.json();}).then(function(d){
        if(myWh!==whSeq || myCity!==selectedCityRef) return;     // запізніла відповідь іншого міста/запиту (аудит #682 р.2, F5): склад чужого міста не пропонуємо
        renderAc(acWh, (d.warehouses||[]).map(function(w){ return {label:w.description, name:w.description, number:w.number}; }),
          function(pick){ wh.value=pick.name; selectedWarehouseNumber=pick.number||""; acWh.innerHTML=""; });
      }).catch(function(){ acWh.innerHTML=""; });
    }, 250));

    document.addEventListener("click", function(e){
      if(!e.target.closest("#f-city")&&!e.target.closest("#ac-city")) acCity.innerHTML="";
      if(!e.target.closest("#f-warehouse")&&!e.target.closest("#ac-warehouse")) acWh.innerHTML="";
    });
  }

  // ── Оформлення: POST /api/order → редірект на LiqPay або підтвердження ──
  function initCheckout(){
    var form=document.getElementById("checkout-form");
    if(!form) return;
    var started=false;   // begin_checkout — один раз, коли покупець почав заповнювати форму
    form.addEventListener("focusin", function(){
      if(started) return; started=true;
      var c=read(), its=Object.keys(c).map(function(id){ return {id:id, name:c[id].name, price:+c[id].price, qty:c[id].qty}; });
      if(its.length){ track("begin_checkout", its, total()); }
    });
    form.addEventListener("submit", function(e){
      e.preventDefault();
      var msg=document.getElementById("checkout-msg");
      var btn=document.getElementById("checkout-submit");
      function fail(t){ if(msg){ msg.classList.add("err"); msg.textContent=t; } if(btn){ btn.disabled=false; btn.textContent="Оформити замовлення"; } }
      if(msg){ msg.classList.remove("err"); msg.textContent=""; }

      var cart=read(), items=Object.keys(cart).map(function(id){ return {id:id, qty:cart[id].qty}; });
      if(!items.length){ fail("Кошик порожній."); return; }
      // Довідник НП показав міста, а покупець жодного не обрав → у Toysi пішла б лише НАЗВА міста без області/CityRef, і посилка могла б поїхати в однойменне село
      // (інциденти Бородянський 906224962 і «Південне»→«Южное» EVA 8-081747967). Виходи: довідник мертвий/мовчить >6 с, міста нема в ньому, або кнопка «Мого міста немає в списку».
      if(cityNeedsPick()){
        fail("Оберіть своє місто зі списку підказок — так посилка точно піде у ваше місто, а не в однойменне. Немає у списку? Натисніть «Мого міста немає в списку».");
        if(lastCityList && lastCityList.length) cityRerender();   // клік повз прибрав випадайку — повертаємо список
        var fc=document.getElementById("f-city"); if(fc && fc.focus) fc.focus();
        return;
      }
      var payEl=document.querySelector('input[name="payment"]:checked');
      var payment=payEl ? payEl.value : "cod";
      var payload={
        items:items,
        name:(document.getElementById("f-name").value||"").trim(),
        phone:(document.getElementById("f-phone").value||"").trim(),
        email:(document.getElementById("f-email").value||"").trim(),
        city_name:(document.getElementById("f-city").value||"").trim(),
        warehouse_name:(document.getElementById("f-warehouse").value||"").trim(),
        np_city_ref:selectedCityRef||"",            // CityRef НП, якщо місто обране з автокомпліту
        np_city_area:selectedCityArea||"",           // область обраного міста → у np_branch (розрізняє однойменні міста)
        np_warehouse_number:selectedWarehouseNumber||"", // № відділення, якщо обране з автокомпліту → Toysi напряму
        payment_method:payment
      };
      if(btn){ btn.disabled=true; btn.textContent="Обробляємо…"; }

      fetch("api/order", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)})
        .then(function(r){ return r.json().then(function(j){ return {ok:r.ok, j:j}; }); })
        .then(function(res){
          if(!res.ok){ fail(res.j && res.j.error ? res.j.error : "Не вдалося оформити замовлення."); return; }
          var d=res.j;
          try{
            sessionStorage.setItem("pt_last_order", d.order_id);
            // purchase лише для накладеного платежу: при prepaid (LiqPay) покупець повертається на thanks.html ще БЕЗ підтвердженої оплати —
            // подію не шлемо, щоб не завищувати конверсії (аудит #626); для prepaid позиції не зберігаємо
            if(payment!=="prepaid"){
              sessionStorage.setItem("pt_last_order_items", JSON.stringify(Object.keys(cart).map(function(id){
                return {id:id, name:cart[id].name, price:+cart[id].price, qty:cart[id].qty}; })));   // для події purchase на thanks.html
            } else { sessionStorage.removeItem("pt_last_order_items"); }
          }catch(e){}
          // Оплата карткою: редірект на LiqPay. Якщо LiqPay не налаштований — НЕ імітуємо «дякуємо»,
          // а чесно кажемо обрати накладений (рев'ю покупця: фейкове «замовлення прийнято» без оплати).
          if(payment==="prepaid"){
            if(d.liqpay && d.liqpay.data){ PT.clear(); redirectToLiqPay(d.liqpay); return; }
            fail("Онлайн-оплата тимчасово недоступна. Оберіть «Оплата при отриманні».");
            return;
          }
          // Накладений платіж: замовлення прийнято, оплата при отриманні на Новій Пошті.
          PT.clear();
          location.href = "thanks.html";
        })
        .catch(function(){ fail("Немає звʼязку з сервером. Спробуйте ще раз."); });
    });
  }
  function redirectToLiqPay(lq){
    var f=document.createElement("form");
    f.method="POST"; f.action=lq.action_url; f.acceptCharset="utf-8";
    [["data",lq.data],["signature",lq.signature]].forEach(function(kv){
      var i=document.createElement("input"); i.type="hidden"; i.name=kv[0]; i.value=kv[1]; f.appendChild(i);
    });
    document.body.appendChild(f); f.submit();
  }
})();
