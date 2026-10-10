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
    ["хот вілс","хотвілс","hot wheels","hotwheels"], ["щенячий патруль","paw patrol","pawpatrol"], ["пепа","пеппа","peppa"],
    ["холодне серце","frozen"], ["марвел","marvel"], ["дісней","disney"], ["соник","sonic"], ["покемон","pokemon"],
    ["гаррі поттер","harry potter"], ["павук","spider"], ["трансформер","transformer"]
  ];
  function hasWordStart(s, v){
    var i=s.indexOf(v);
    while(i>=0){
      if(i===0 || !/[a-z\u0430-\u044f\u0456\u0457\u0454\u0491\u0430-\u044f0-9]/.test(s.charAt(i-1))) return true;
      i=s.indexOf(v, i+1);
    }
    return false;
  }
  function brandTags(nn){
    var t="";
    for(var g=0; g<BRANDS.length; g++){
      for(var k=0; k<BRANDS[g].length; k++){ if(hasWordStart(nn, BRANDS[g][k])){ t+=" #b"+g+"_"; break; } }
    }
    return t;
  }
  // haystack одного товару: назва + КАТЕГОРІЯ (поле c вже є в індексі: «радіокерована машина» 0→6) + теги брендів
  function buildHay(p){ var nn=normText(p.n)+" "+normText(p.c); return nn+brandTags(nn); }
  function searchTokens(q){
    var nq=normText(q), tags=[];
    for(var g=0; g<BRANDS.length; g++){
      var vs=BRANDS[g].slice().sort(function(a,b){return b.length-a.length;});   // довші варіанти першими («hot wheels» до «wheels»)
      for(var k=0; k<vs.length; k++){
        if(hasWordStart(nq, vs[k])){ tags.push("#b"+g+"_"); nq=nq.replace(vs[k], " "); break; }
      }
    }
    return nq.split(/\s+/).filter(function(t){return t.length>0;}).map(stemTok).concat(tags);
  }
  function searchMatch(nn, toks){ for(var i=0;i<toks.length;i++){ if(nn.indexOf(toks[i])<0) return false; } return true; }
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
    if(d) d.textContent=freeShip ? "Безкоштовно" : "за тарифами НП";
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

  // ── Автокомпліт міста/відділення Нової Пошти (через site_order_api) ──
  var selectedCityRef = "";
  var selectedCityArea = "";  // область обраного міста (НП AreaDescription) → у np_branch, щоб Toysi
                               // не переплутав однойменні населені пункти різних областей (24.09.2026)
  var selectedWarehouseNumber = "";  // № обраного з автокомпліту відділення НП → віддаємо Toysi напряму
  function debounce(fn, ms){ var t; return function(){ var a=arguments, self=this; clearTimeout(t); t=setTimeout(function(){ fn.apply(self,a); }, ms); }; }
  function renderAc(box, opts, onPick){
    if(!opts.length){ box.innerHTML=""; return; }
    box.innerHTML = opts.map(function(o,i){
      return '<div class="opt" data-i="'+i+'">'+esc(o.label)+(o.sub?'<small>'+esc(o.sub)+'</small>':'')+'</div>';
    }).join("");
    Array.prototype.forEach.call(box.querySelectorAll(".opt"), function(el){
      el.addEventListener("mousedown", function(ev){ ev.preventDefault(); onPick(opts[+el.getAttribute("data-i")]); box.innerHTML=""; });
    });
  }
  function initNpAutocomplete(){
    var city=document.getElementById("f-city"), acCity=document.getElementById("ac-city");
    var wh=document.getElementById("f-warehouse"), acWh=document.getElementById("ac-warehouse");
    if(!city || !wh) return;

    city.addEventListener("input", debounce(function(){
      // Щойно користувач торкнувся міста — РОЗБЛОКОВУЄМО відділення й БІЛЬШЕ не вимикаємо його.
      // Раніше поле лишалось disabled, доки не тапнеш підказку міста — на телефоні це зривало
      // оформлення (не влучив пальцем у випадайку → відділення «мертве»). Тап по місту лише
      // додає ref для автопідказок відділення; без тапу — ручний ввід відділення (він і так
      // приймається як вільний текст, order_router розбирає при форварді).
      selectedCityRef=""; selectedCityArea=""; selectedWarehouseNumber=""; wh.value=""; acWh.innerHTML="";
      wh.disabled=false; wh.placeholder="Оберіть місто зі списку — або введіть відділення";
      var q=city.value.trim(); if(q.length<2){ acCity.innerHTML=""; return; }
      fetch("api/np/city?q="+encodeURIComponent(q)).then(function(r){return r.json();}).then(function(d){
        var cities=(d.cities||[]);
        renderAc(acCity, cities.map(function(c){ return {label:c.name, sub:c.area, ref:c.ref, name:c.name}; }),
          function(pick){ city.value=pick.name; selectedCityRef=pick.ref; selectedCityArea=pick.sub||""; wh.placeholder="Номер або адреса відділення"; wh.focus(); });
      }).catch(function(){
        // API НП недоступний — не лишаємо форму в глухому куті: підказок нема, ручний ввід уже дозволено.
        acCity.innerHTML="";
      });
    }, 250));

    wh.addEventListener("input", debounce(function(){
      selectedWarehouseNumber="";  // ввід руками скидає обраний № — щоб не лишити стале значення
      if(!selectedCityRef) return;
      var q=wh.value.trim();
      fetch("api/np/warehouse?city_ref="+encodeURIComponent(selectedCityRef)+"&q="+encodeURIComponent(q)).then(function(r){return r.json();}).then(function(d){
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
