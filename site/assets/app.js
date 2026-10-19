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

  var MAX_QTY=50, COD_CEILING=3000;   // = site_order_api.MAX_QTY_PER_ITEM і SITE_COD_CEILING (сервер усе одно перевіряє)
  function read(){
    var raw; try{ raw=JSON.parse(localStorage.getItem(CART_KEY)) || {}; }catch(e){ return {}; }
    if(!raw || typeof raw!=="object" || Array.isArray(raw)) return {};
    // тестувальник 12.10: qty=-5 → «-50 ₴», price="abc" → «NaN ₴» (лише ручним втручанням у DevTools) — чистимо при читанні
    var out={};
    Object.keys(raw).forEach(function(id){
      var it=raw[id]; if(!it || typeof it!=="object") return;
      var price=+it.price, qty=Math.floor(+it.qty);
      if(!isFinite(price) || price<0) return;
      if(!isFinite(qty) || qty<1) qty=1;
      if(qty>MAX_QTY) qty=MAX_QTY;
      out[id]={id:String(it.id!==undefined?it.id:id), name:String(it.name||""), price:price, photo:String(it.photo||""), qty:qty};
    });
    return out;
  }
  function write(c){ try{ localStorage.setItem(CART_KEY, JSON.stringify(c)); }catch(e){} updateBadge(); }
  function count(){ var c=read(),n=0; for(var k in c){ n+=c[k].qty; } return n; }
  function total(){ var c=read(),s=0; for(var k in c){ s+=c[k].qty*c[k].price; } return s; }

  // ── Аналітика (GA4 + Meta Pixel; ID підставляє збірка в window.PT_ANALYTICS, без ID — нічого не шле) ──
  // ⚠ ТЕСТОВІ ПОДІЇ В GA4/Meta (не покупці; при трафіку ~2 відвідувачі/добу вони спотворюють воронку): 11.10.2026 ~13:27 — add_to_cart (самокат 6349 ₴, SMM);
  // 11.10.2026 ~16:27 — view_item, add_to_cart, begin_checkout (товар 235193, 468 ₴, Shopify-code, перевірка доставки подій). 12.10.2026 ~14:30 EEST — page_view, view_item, add_to_cart, begin_checkout (товар 221946, Shopify-code, перевірка доставки подій після відкладання трекерів; не покупець). Тестове замовлення власника, коли
  // буде, дасть purchase — його теж позначити тут (дата/товар/«не покупець»). Дописувати сюди кожну наступну тестову подію.
  var FB_EVENT = {view_item:"ViewContent", add_to_cart:"AddToCart", begin_checkout:"InitiateCheckout", purchase:"Purchase"};
  function track(name, items, value, extra){
    try{
      var cfg=window.PT_ANALYTICS||{};
      var ga=items.map(function(i){ var o={item_id:String(i.id), item_name:i.name, price:+i.price, quantity:i.qty||1}; if(i.list){ o.item_list_name=i.list; } return o; });
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
    add:function(p, listName){       // p={id,name,price,photo}; listName — звідки додано (item_list_name у GA4, напр. cart_addon)
      var c=read();
      if(c[p.id]){ if(c[p.id].qty>=MAX_QTY){ flash("Максимум "+MAX_QTY+" шт. одного товару"); return; } c[p.id].qty++; } else { c[p.id]={id:p.id,name:p.name,price:+p.price,photo:p.photo,qty:1}; }
      write(c);
      track("add_to_cart", [{id:p.id, name:p.name, price:+p.price, qty:1, list:listName||""}], +p.price);
      flash("Додано в кошик");
    },
    track:track,
    setQty:function(id,q){ var c=read(); if(c[id]){ c[id].qty=Math.min(MAX_QTY, Math.max(0,q)); if(c[id].qty===0){delete c[id];} write(c); } },
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
  var idxWaiters=[];
  function ensureIndex(cb, onFail){
    // черга: запит, набраний ПОКИ індекс ще вантажиться, раніше губився (callback відкидався) — результати не з'являлись, доки не наберуть ще літеру
    if(idx){ cb(idx); return; }
    idxWaiters.push({ok:cb, fail:onFail});
    if(idxLoading) return;
    idxLoading=true;
    fetch("index.json").then(function(r){ if(!r.ok) throw new Error("index "+r.status); return r.json(); })
      .then(function(d){ idx=d; idxLoading=false; var w=idxWaiters; idxWaiters=[]; w.forEach(function(x){ try{ x.ok(idx); }catch(e){} }); })
      .catch(function(){ idxLoading=false; var w=idxWaiters; idxWaiters=[]; w.forEach(function(x){ if(x.fail){ try{ x.fail(); }catch(e){} } }); });
  }
  // Оверлей пошуку кладе свій запис в історію (тестувальник #16: «Назад» виводив із САЙТУ): відкриття = pushState, «Назад» (popstate) / Escape / клік по фону / «Готово» закривають оверлей і лишають на сторінці.
  var searchPushed=false, searchLeaveHref=null;
  function searchEmptyHtml(){ var t=document.getElementById("search-empty"); return t ? t.innerHTML : ""; }
  function openSearch(){
    var o=document.getElementById("search-overlay");
    if(!o) return;
    var wasOn=o.classList.contains("on");
    var hd=document.querySelector("header.top");
    o.style.setProperty("--sp-top", Math.max(0, Math.round(hd ? hd.getBoundingClientRect().bottom : 0)) + "px");   // панель починається під шапкою
    o.classList.add("on");
    if(!wasOn){ try{ history.pushState({ptSearch:1}, "", location.href); searchPushed=true; }catch(e){ searchPushed=false; } }
    var inp=o.querySelector("input");
    inp.value=""; inp.focus();
    o.querySelector(".results").innerHTML=searchEmptyHtml();
    ensureIndex(function(){});
  }
  function closeSearch(fromPop){
    var o=document.getElementById("search-overlay");
    if(!o || !o.classList.contains("on")) return;
    o.classList.remove("on");
    if(searchPushed && fromPop!==true){ searchPushed=false; try{ history.back(); }catch(e){} }   // прибираємо власний запис; при «Назад» (popstate) браузер уже зняв його
    searchPushed=false;
  }
  if(typeof window!=="undefined" && typeof window.addEventListener==="function"){
    window.addEventListener("popstate", function(){
      closeSearch(true);
      if(searchLeaveHref){ var h=searchLeaveHref; searchLeaveHref=null; location.href=h; }
    });
  }
  if(typeof document!=="undefined" && typeof document.addEventListener==="function"){
    document.addEventListener("keydown", function(e){
      if(e.key==="Escape" || e.key==="Esc"){ var o=document.getElementById("search-overlay"); if(o && o.classList.contains("on")){ e.preventDefault(); closeSearch(); } }
    });
  }
  // ── Пошук (зауваження Тестувальника/Консультанта 10.10.2026): токени через AND у БУДЬ-ЯКОМУ порядку (було: літеральний підрядок —
  // «лялька барбі» давало 0); апострофи U+02BC/U+2019/U+2018/U+0027/U+0060 прибираються з ОБОХ боків («мʼяка»=«м'яка»=«мяка»);
  // показуємо «Показано N з M» і сортування (раніше мовчазна стеля 40 за порядком файлу).
  var SEARCH_SHOW=40, searchSort="rec", lastMatches=[], lastQuery="", searchSeq=0;
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
    ["гаррі поттер","гарри поттер","harry potter"], ["павук","spider"], ["трансформер","transformer"],
    ["хелловін","хеловін","хелоуін","хелоувін","хэллоуин","хеллоуин","геловін","гелловін","гелоуін","halloween","helloween"]   // Halloween (власник 16.10): на сайті єдина назва англійською, у пошуку — усі написання
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
        '<div><div class="srn">'+esc(p.n)+'</div><div class="srp">'+p.pr+' ₴'+(p.op ? ' <s class="pr-old">'+p.op+' ₴</s>' : '')+'</div></div></a>';
    }).join("");
  }
  function runSearch(q){
    var res=document.querySelector("#search-overlay .results");
    if(!res) return;
    q=(q||"").trim();
    if(q.length<2){ searchSeq++; res.innerHTML=searchEmptyHtml(); lastMatches=[]; return; }
    var toks=searchTokens(q);
    if(!toks.length){ searchSeq++; res.innerHTML=searchEmptyHtml(); lastMatches=[]; return; }   // запит лише з апострофів/розділових → не показуємо «весь каталог»
    var mySeq=++searchSeq;
    ensureIndex(function(data){
      if(mySeq!==searchSeq) return;   // новіший запит уже набрано
      var all=[];
      for(var i=0;i<data.length;i++){
        var p=data[i];
        if(p._nn===undefined) p._nn=buildHay(p);   // раз на елемент індексу (кеш)
        if(searchMatch(p._nn, toks)) all.push(p);
      }
      lastMatches=all; lastQuery=q;
      renderSearch();
    }, function(){
      if(mySeq!==searchSeq) return;
      res.innerHTML='<div class="empty" style="padding:40px 0">Не вдалося завантажити пошук. Перевірте звʼязок і спробуйте ще раз.</div>';
    });
  }
  function esc(s){ return String(s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];}); }   // ' теж: назви в data-add='…' (аудит #691: «М'яка іграшка» ламала кнопку й давала ін'єкцію атрибута)

  // ── Каталог: сортування + фільтри (клієнтом з index.json, весь каталог) ──
  function catCard(p){
    var av = p.s ? '<span class="av">У наявності</span>' : '<span class="av oos">Немає</span>';
    var img = p.p ? '<img src="'+esc(p.p)+'" loading="lazy" width="400" height="400" alt="'+esc(p.n)+'">' : '<div class="ph"></div>';
    var sale = p.op && p.op>p.pr;   // «Знижка тижня»: op — стара ціна (аудит 14.10: каталог показував акційну ціну без перекресленої старої й бейджа)
    var badge = sale ? '<span class="sale-badge">−'+Math.round((1-p.pr/p.op)*100)+'%</span>' : '';
    var price = sale ? '<span class="pr pr-sale">'+p.pr+' ₴</span><s class="pr-old">'+p.op+' ₴</s>' : '<span class="pr">'+p.pr+' ₴</span>';
    return '<a class="card" href="product-'+esc(p.id)+'.html"><div class="ph">'+img+badge+'</div>'+
      '<div class="info"><div class="nm">'+esc(p.n)+'</div>'+
      '<div class="foot">'+price+av+'</div></div></a>';
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
    // сторінка категорії (data-cat): та сама панель, категорія вибрана; зміна категорії = перехід на її сторінку, «Усі категорії» = /catalog.html
    var preset=controls.getAttribute("data-cat")||"", catUrls={};
    // сторінка ГРУПИ (data-group = JSON підкатегорій): працюємо лише з їхніми товарами; підкатегорія — з сайдбару (клік фільтрує на місці) або зі списку (телефон)
    var groupSet=null, sideLinks=[], ownCat=controls.getAttribute("data-own")||"", otherSet=null;
    try{ var go=controls.getAttribute("data-others"); if(go){ otherSet={}; JSON.parse(go).forEach(function(c){ otherSet[c]=1; }); } }catch(e){ otherSet=null; }
    try{ var gs=controls.getAttribute("data-group"); if(gs){ groupSet={}; JSON.parse(gs).forEach(function(c){ groupSet[c]=1; }); sideLinks=Array.prototype.slice.call(document.querySelectorAll(".sb-i")); } }catch(e){ groupSet=null; }
    function syncSide(){ sideLinks.forEach(function(a){ a.classList.toggle("on", (a.getAttribute("data-sub")||"")===elCat.value); }); }
    // ПОВНИЙ КАТАЛОГ із деревом (data-groups = [{n,s:[підкатегорії],o:власна}]): сайдбар «Усі товари / група / підкатегорія» фільтрує на місці; на вузьких екранах список категорій — з групами (optgroup)
    // службова сторінка (Уцінка/Акції): <script id=cat-ids> = id її товарів; панель сортує/фільтрує лише їх (а не весь каталог)
    var idSet=null;
    try{ var ie=document.getElementById("cat-ids"); if(ie){ idSet={}; JSON.parse(ie.textContent||"[]").forEach(function(i){ idSet[i]=1; }); } }catch(e){ idSet=null; }
    if(idSet && elCat){ elCat.hidden=true; elCat.style.display="none"; }   // власник 16.10: на сторінках чипів/Уцінки/Акцій список «категорій по 1 товару» не потрібен — лишаються сортування й ціна
    var tree=null, treeSets=[], selSet=null, curG=-1, treeEls=[], treeGrp=[];
    try{ var gd=controls.getAttribute("data-groups"); if(gd && !groupSet){ tree=JSON.parse(gd); tree.forEach(function(g){ var m={}; g.s.forEach(function(c){ m[c]=1; }); treeSets.push(m); }); treeEls=Array.prototype.slice.call(document.querySelectorAll(".sb .sb-i")).filter(function(a){ return !a.classList.contains("sb-nav"); }); treeGrp=Array.prototype.slice.call(document.querySelectorAll(".sb-grp")); } }catch(e){ tree=null; }
    function groupOf(c){ for(var i=0;i<treeSets.length;i++){ if(treeSets[i][c]) return i; } return -1; }
    function syncTree(){
      if(!tree) return;
      treeEls.forEach(function(a){
        var g=+a.getAttribute("data-g"), sub=a.getAttribute("data-sub")||"", on=false;
        if(a.classList.contains("all")) on = curG<0 && !elCat.value;
        else if(a.classList.contains("sb-gh")) on = g===curG && !elCat.value;
        else on = sub===elCat.value && sub!=="";
        a.classList.toggle("on", on);
      });
      treeGrp.forEach(function(d){ var open=(+d.getAttribute("data-g")===curG); d.classList.toggle("open", open); var s=d.querySelector(".sb-subs"); if(s) s.hidden=!open; });
    }
    if(preset){ try{ catUrls=JSON.parse((document.getElementById("cat-urls")||{}).textContent||"{}"); }catch(e){ catUrls={}; } }
    ensureIndex(function(data){
      var cats={};
      for(var i=0;i<data.length;i++){ if(data[i].c && (!groupSet || groupSet[data[i].c]) && (!idSet || idSet[data[i].id])) cats[data[i].c]=(cats[data[i].c]||0)+1; }
      function addOpt(parent, c, label){ var o=document.createElement("option"); o.value=c; o.textContent=(label||c)+" ("+cats[c]+")"; parent.appendChild(o); }
      if(tree){
        var placed={};
        tree.forEach(function(g){
          var og=document.createElement("optgroup"); og.label=g.n; var any=false;
          g.s.slice().sort(function(a,b){ return (a===g.o)-(b===g.o) || (cats[b]||0)-(cats[a]||0) || a.localeCompare(b,"uk"); }).forEach(function(c){ if(cats[c]){ addOpt(og,c, c===g.o ? "Інше · "+g.n : c); placed[c]=1; any=true; } });
          if(any) elCat.appendChild(og);
        });
        var rest=Object.keys(cats).filter(function(c){ return !placed[c]; }).sort(function(a,b){ return a.localeCompare(b,"uk"); });
        if(rest.length){ var og2=document.createElement("optgroup"); og2.label="Окремі розділи"; rest.forEach(function(c){ addOpt(og2,c); }); elCat.appendChild(og2); }
      } else {
        var otherN=0;
        Object.keys(cats).sort(function(a,b){ return groupSet ? (cats[b]-cats[a] || a.localeCompare(b,"uk")) : a.localeCompare(b,"uk"); }).forEach(function(c){ if(otherSet && otherSet[c]){ otherN+=cats[c]; return; } addOpt(elCat, c, c===ownCat ? "Інше" : c); });
        if(otherSet && otherN){ var oo=document.createElement("option"); oo.value="__other"; oo.textContent="Інше ("+otherN+")"; elCat.appendChild(oo); }
      }
      if(preset) elCat.value=preset;
      controls.hidden=false;
      apply();
    });
    function apply(){
      var min=parseInt(elMin.value,10), max=parseInt(elMax.value,10);
      if(isNaN(min)) min=null; if(isNaN(max)) max=null;
      var cat=elCat.value, ins=elStock ? elStock.checked : false, sort=elSort.value;
      filtered=(idx||[]).filter(function(p){
        if(groupSet && !groupSet[p.c]) return false;
        if(idSet && !idSet[p.id]) return false;
        if(selSet && !cat && !selSet[p.c]) return false;
        if(cat==="__other"){ if(!otherSet || !otherSet[p.c]) return false; }
        else if(cat && p.c!==cat) return false;
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
      syncSide(); syncTree();
      if(statik) statik.hidden=true;
      grid.hidden=false; count.hidden=false;
      renderMore();
    }
    function gridCols(){
      try{ var t=getComputedStyle(grid).gridTemplateColumns; var n=t && t!=="none" ? t.trim().split(/\s+/).length : 0; return n>0 ? n : 1; }catch(e){ return 1; }
    }
    function renderMore(){
      if(!filtered.length){
        grid.innerHTML='<div class="empty" style="padding:40px 0">Нічого не знайдено за фільтром. <a href="#" id="cf-reset2">Скинути</a></div>';
        var r2=document.getElementById("cf-reset2"); if(r2) r2.addEventListener("click",function(e){e.preventDefault();doReset();});
        count.textContent="Знайдено: 0"; moreWrap.hidden=true; return;
      }
      // порція кратна числу колонок сітки (власник 14.10: 24 карток у 5 колонок лишали неповний останній рядок після кожного «Показати ще»)
      var cols=gridCols(), upto=Math.max(shown+cols, Math.floor((shown+PER)/cols)*cols);
      var slice=filtered.slice(shown, upto);
      grid.insertAdjacentHTML("beforeend", slice.map(catCard).join(""));
      shown+=slice.length;
      count.textContent="Знайдено: "+filtered.length+" · показано "+shown;
      moreWrap.hidden = shown>=filtered.length;
    }
    function doReset(){ selSet=null; curG=-1; elSort.value="rec"; elCat.value=preset; elMin.value=""; elMax.value=""; if(elStock) elStock.checked=false; apply(); }
    var deb=debounce(apply,250);
    [elSort,elStock].forEach(function(e){ if(e) e.addEventListener("change",apply); });
    elCat.addEventListener("change",function(){
      if(preset){ var u=elCat.value ? catUrls[elCat.value] : "catalog.html"; if(u){ location.href=u; return; } elCat.value=preset; return; }
      if(tree){ selSet=null; curG=elCat.value ? groupOf(elCat.value) : -1; }
      apply();
    });
    [elMin,elMax].forEach(function(e){ if(e) e.addEventListener("input",deb); });
    if(moreBtn) moreBtn.addEventListener("click",renderMore);
    if(elReset) elReset.addEventListener("click",doReset);
    treeEls.forEach(function(a){
      a.addEventListener("click",function(e){
        if(e.ctrlKey||e.metaKey||e.shiftKey||e.altKey||e.button) return;
        e.preventDefault();
        var g=+a.getAttribute("data-g"), sub=a.getAttribute("data-sub")||"";
        curG=g; selSet = g>=0 ? treeSets[g] : null; elCat.value=sub; apply();
        var top=count.getBoundingClientRect().top+(window.pageYOffset||0)-140; if(top<(window.pageYOffset||0)) window.scrollTo(0,Math.max(0,top));
      });
    });
    sideLinks.forEach(function(a){
      a.addEventListener("click",function(e){
        if(e.ctrlKey||e.metaKey||e.shiftKey||e.altKey||e.button) return;   // відкриття в новій вкладці/вікні — звичайний перехід
        e.preventDefault(); elCat.value=a.getAttribute("data-sub")||""; apply();
        var top=count.getBoundingClientRect().top+(window.pageYOffset||0)-140; if(top<(window.pageYOffset||0)) window.scrollTo(0,Math.max(0,top));
      });
    });
    // вузький екран: кнопка «Ціна» розкриває від/до (на широкому панель завжди розгорнута, кнопка схована CSS)
    var pt=document.getElementById("cf-price-toggle"), pp=document.getElementById("cf-price-panel");
    if(pt && pp){
      function setOpen(o){ pp.classList.toggle("open",o); pt.setAttribute("aria-expanded",o?"true":"false"); }
      pt.addEventListener("click",function(e){ e.stopPropagation(); setOpen(!pp.classList.contains("open")); });
      document.addEventListener("click",function(e){ if(pp.classList.contains("open") && !pp.contains(e.target)) setOpen(false); });
      document.addEventListener("keydown",function(e){ if(e.key==="Escape" && pp.classList.contains("open")){ setOpen(false); pt.focus(); } });
      var mark=function(){ pt.classList.toggle("on", !!(elMin.value||elMax.value)); };
      [elMin,elMax].forEach(function(e){ e.addEventListener("input",mark); }); if(elReset) elReset.addEventListener("click",function(){ setOpen(false); mark(); });
    }
  }

  // ── Права колонка кошика (SMM 12.10): «Ваше замовлення», «Додайте до замовлення» (3 позиції з пулу cart.html, яких ще нема в кошику), динамічний рядок про відправку ──
  var cartAddonPool=null;
  function addonPool(){
    if(cartAddonPool) return cartAddonPool;
    try{ var el=document.getElementById("cart-addons"); cartAddonPool=el ? JSON.parse(el.textContent||"[]") : []; }catch(e){ cartAddonPool=[]; }
    return cartAddonPool;
  }
  // Орієнтовна вартість доставки НП для кошика (Код, 12.10: getDocumentPrice + реальні ТТН 704/881/982 — збіг до копійки): Toysi ставить оголошену вартість = сумі накладеного (мінімум 500), тож
  //   доставка до 2 кг = база (Київ 70 / інші великі міста 90) + 0,5% × S, якщо S > 500, інакше база; комісія за накладений платіж = 20 ₴ + 2% × S (точно на 190/400/1000/3000). Місто невідоме → діапазон Київ…інші.
  //   Понад DELIV_COD_MAX накладеного платежу немає → лише тариф. НЕ ВРАХОВАНО: вага >2 кг (у 2 реальних ТТН +25 ₴; ваги в каталозі Toysi немає) — тому в тексті «орієнтовно» і «точну суму назве Нова Пошта».
  //   Хто платить комісію — не доведено: текст говорить лише «орієнтовно» і «оплачується при отриманні».
  var DELIV_TARIFF_MIN=70, DELIV_TARIFF_MAX=90, DELIV_COD_FIXED=20, DELIV_COD_RATE=0.02, DELIV_COD_MAX=3000, DELIV_DECL_FREE=500, DELIV_DECL_RATE=0.005;
  function isPrepaid(){ var r=document.querySelector('input[name="payment"]:checked'); return !!r && r.value==="prepaid"; }
  function deliveryEstimate(goods, prepaid){
    // оплата карткою онлайн: комісії накладеного платежу нема, надбавка тарифу для передоплати не міряна → лише базовий тариф
    if(prepaid || !(goods>0) || goods>DELIV_COD_MAX) return "орієнтовно 70–90 ₴ до 2 кг";
    var extra=goods>DELIV_DECL_FREE ? DELIV_DECL_RATE*goods : 0;
    var fee=DELIV_COD_FIXED+DELIV_COD_RATE*goods+extra;
    return "орієнтовно "+Math.round(DELIV_TARIFF_MIN+fee)+"–"+Math.round(DELIV_TARIFF_MAX+fee)+" ₴";
  }

  // Рядок доставки, підпис і примітка залежать від способу оплати (тестувальник 13.10: при «карткою онлайн» лишався COD-івський підсумок 99–119 ₴ і підпис про комісію).
  // Оригінальний (накладений платіж) текст запам'ятовуємо при першому виклику; prepaid-версії — з data-prepaid розмітки (єдине джерело в build_site.py).
  function applyDeliveryView(goods){
    var pre=isPrepaid();
    [["sum-delivery","sum-delivery-label","delivery-note-sum"],["aside-delivery","aside-delivery-label","delivery-note-aside"]].forEach(function(ids){
      var v=document.getElementById(ids[0]), lb=document.getElementById(ids[1]), nt=document.getElementById(ids[2]);
      if(v) v.textContent=deliveryEstimate(goods, pre);
      [lb, nt].forEach(function(el){
        if(!el) return;
        if(el.getAttribute("data-cod")===null) el.setAttribute("data-cod", el.textContent);
        el.textContent = pre ? (el.getAttribute("data-prepaid")||el.textContent) : el.getAttribute("data-cod");
      });
    });
  }

  function renderAside(c, ids){
    var aside=document.getElementById("cart-aside");
    if(!aside) return;
    aside.style.display = ids.length ? "" : "none";
    if(!ids.length) return;
    var goods=ids.reduce(function(s,id){ return s+c[id].price*c[id].qty; },0);
    var items=document.getElementById("aside-items");
    if(items) items.innerHTML=ids.map(function(id){
      var it=c[id];
      return '<div class="ai"><img src="'+esc(it.photo)+'" alt="" loading="lazy"><div class="ai-nm">'+esc(it.name)+'</div><div class="ai-q">'+it.qty+' × '+it.price+' ₴</div></div>';
    }).join("");
    var g=document.getElementById("aside-goods"), t=document.getElementById("aside-total");
    if(g) g.textContent=goods+" ₴";
    if(t) t.textContent=goods+" ₴";
    applyDeliveryView(goods);
    var picks=addonPool().filter(function(a){ return !c[a.id]; }).slice(0,3);
    Array.prototype.forEach.call(document.querySelectorAll("[data-addon]"), function(box){
      if(!picks.length){ box.innerHTML=""; box.style.display="none"; return; }
      box.style.display="";
      box.innerHTML='<h3>Додайте до замовлення</h3>'+picks.map(function(a){
        return '<div class="ao"><img src="'+esc(a.photo)+'" alt="" loading="lazy"><div class="ao-nm">'+esc(a.name)+'</div><div class="ao-pr">'+a.price+' ₴</div>'+
          '<button type="button" class="ao-add" data-add=\''+esc(JSON.stringify({id:a.id,name:a.name,price:a.price,photo:a.photo}))+'\' data-list="cart_addon" aria-label="Додати до замовлення: '+esc(a.name)+'">+ Додати</button></div>';
      }).join("");
    });
  }
  function initShipToday(){
    // Політика сайту каже лише «замовлення до 12:00 зазвичай відправляємо того ж дня» (сторінки «Доставка», «Про нас»). Нових обіцянок не додаємо (аудит #691): рядок «відправимо сьогодні» показуємо ТІЛЬКИ коли зараз
    // у Києві будній день і до 12:00; після 12:00, у суботу/неділю, без Intl або з невідомою зоною лишається статичний текст політики. Обчислюється один раз при завантаженні.
    var el=document.getElementById("ship-today");
    if(!el) return;
    try{
      var f=new Intl.DateTimeFormat("en-GB",{hour:"numeric",hour12:false,weekday:"short",timeZone:"Europe/Kyiv"}).formatToParts(new Date());
      var hh=0, wd="";
      f.forEach(function(p){ if(p.type==="hour") hh=+p.value; if(p.type==="weekday") wd=p.value; });
      if(wd && wd!=="Sat" && wd!=="Sun" && hh<12) el.textContent="🚚 Замовте до 12:00 — зазвичай відправимо сьогодні";
    }catch(e){}
  }

  // ── Другий постачальник (RoyalToys, id «rt-…»; рішення власника 19.10): працює лише за повною передоплатою → у кошику з такою позицією «накладений платіж» неактивний, лишається «картка онлайн» ──
  // Сервер це теж перевіряє (409), тут — щоб покупець не обирав те, що буде відхилено. Нема rt-позицій — нічого не міняємо.
  function applyRtPayment(ids){
    var rt=ids.filter(function(k){ return /^rt-/.test(k); }).length, mixed=rt>0 && rt<ids.length;
    var cod=document.querySelector('input[name="payment"][value="cod"]'), pre=document.querySelector('input[name="payment"][value="prepaid"]');
    var note=document.getElementById("rt-pay-note");
    if(cod){
      cod.disabled = rt>0;
      var lab=cod.parentNode; if(lab && lab.classList) lab.classList.toggle("is-off", rt>0);
      if(rt>0 && cod.checked && pre){ cod.checked=false; pre.checked=true; applyDeliveryView(total()); }
    }
    if(note){
      if(rt>0){
        note.textContent = "Оплата при отриманні недоступна: "+(mixed ? "один або кілька товарів у кошику" : "цей товар")+" відправляється лише за передоплатою (оплата карткою онлайн)."+(mixed ? " Товари надсилаються окремими посилками, доставка для кожної рахується окремо." : "");
        note.hidden=false;
      } else { note.hidden=true; note.textContent=""; }
    }
  }

  // ── Рендер кошика (сторінка cart.html) ──
  function renderCart(){
    var box=document.getElementById("cart-body");
    if(!box) return;
    var c=read(), ids=Object.keys(c);
    var staleNote=document.getElementById("cart-stock-note"); if(staleNote && staleNote.parentNode) staleNote.parentNode.removeChild(staleNote);   // порожній кошик не має мати попередження (аудит)
    renderAside(c, ids);
    var cg=document.querySelector(".cart-grid"); if(cg) cg.classList.toggle("is-empty", ids.length===0);   // порожній кошик: одна колонка, по центру (власник 14.10: на ПК блок стояв лівіше центру)
    applyRtPayment(ids);   // RoyalToys (id «rt-…»): лише передоплата
    var codn=document.getElementById("cod-limit-note");
    if(codn) codn.style.display = total()>COD_CEILING ? "" : "none";
    var ct=document.getElementById("checkout-title"); if(ct) ct.style.display = ids.length ? "" : "none";
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
        '<div class="qty"><button data-act="dec" aria-label="Менше"'+(it.qty<=1?' disabled':'')+'>−</button><span class="q">'+it.qty+'</span><button data-act="inc" aria-label="Більше"'+(it.qty>=MAX_QTY?' disabled':'')+'>+</button></div>'+
        '</div><button class="ci-rm" data-act="rm">Прибрати</button></div>';
    });
    box.innerHTML=html;
    renderSummary();
    markCartStock();
  }
  function renderSummary(){
    var goods=total();
    // FREE_SHIPPING_ENABLED=false (2026-09-18, поки платник доставки не з'ясований з Toysi) —
    // freeShip завжди false: сума доставки не друкується (вартість визначає Нова Пошта, сплачується при отриманні).
    var freeShip=FREE_SHIPPING_ENABLED && goods>=FREE_SHIPPING_THRESHOLD;
    var g=document.getElementById("sum-goods"), d=document.getElementById("sum-delivery"), t=document.getElementById("sum-total");
    if(g) g.textContent=goods+" ₴";
    if(d && freeShip) d.textContent="Безкоштовно"; else applyDeliveryView(goods);
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

  // ── Свіжий залишок Toysi (власник 13.10, «критично»): /stock.json від Code-Agent, оновлюється кожні 15 хв (сторінки сайту збираються раз на 2 год).
  // s[id]==0 — точно нема; s[id]>0 — верхня межа («5...10 шт.» → 10); id нема — невідомо. Знімок старший за 45 хв (за Date − Last-Modified) вважаємо «невідомо»: кнопки не ховаємо.
  var STOCK_MAX_AGE_MS=45*60*1000, stockP=null;
  function stockSnapshot(){
    if(stockP) return stockP;
    stockP=fetch("stock.json", {cache:"no-cache"}).then(function(r){   // no-cache = умовний GET (If-Modified-Since → 304): великий файл не тягнемо на кожній сторінці
      if(!r.ok) return null;
      var d=r.headers.get("Date"), lm=r.headers.get("Last-Modified");
      return r.json().then(function(j){
        if(!j || typeof j.s!=="object" || !j.s || Array.isArray(j.s)) return null;
        var age=(d && lm) ? new Date(d).getTime()-new Date(lm).getTime() : NaN;   // без обох заголовків свіжість невідома (аудит: new Date(null)=0 давав «свіжий»)
        return {fresh:(isFinite(age) && age>=0 && age<=STOCK_MAX_AGE_MS), s:j.s};
      });
    }).catch(function(){ return null; });
    return stockP;
  }
  function stockOf(snap, id){
    if(!snap || !snap.fresh) return null;
    var v=snap.s[String(id)];
    return (typeof v==="number" && isFinite(v)) ? v : null;
  }
  function stockIssues(c, snap){
    var out=[];
    Object.keys(c).forEach(function(id){
      var st=stockOf(snap, id); if(st===null) return;
      if(st<=0) out.push({id:id, name:c[id].name, requested:c[id].qty, available:0});
      else if(c[id].qty>st) out.push({id:id, name:c[id].name, requested:c[id].qty, available:st});
    });
    return out;
  }
  function applyStockFix(issues){   // лише названі позиції: зменшити до наявного або прибрати; решта кошика не чіпається
    var c=read();
    issues.forEach(function(x){
      if(!c[x.id]) return;
      if(x.available>0) c[x.id].qty=Math.min(c[x.id].qty, x.available); else delete c[x.id];
    });
    write(c);
  }
  function stockLine(x){ return x.name+(x.available>0 ? " (доступно "+x.available+" шт., у кошику "+x.requested+")" : " (немає в наявності)"); }
  function initStockProduct(){
    var btn=document.querySelector("h1.prod") && document.querySelector(".btn[data-add]");
    if(!btn) return;
    var p; try{ p=JSON.parse(btn.getAttribute("data-add")); }catch(e){ return; }
    stockSnapshot().then(function(snap){
      if(stockOf(snap, p.id)!==0) return;
      btn.hidden=true; btn.disabled=true;
      var n=document.createElement("div"); n.className="oos-note stock-now"; n.setAttribute("role","status");
      n.innerHTML="<b>Щойно закінчився.</b> Подивіться схожі товари нижче.";
      btn.parentNode.insertBefore(n, btn.nextSibling);
      var av=document.querySelector(".avail"); if(av){ av.textContent="Немає в наявності"; av.classList.add("oos"); }
    });
  }
  function initStockCards(){   // списки/каталог/головна: «У наявності» → «Немає» для позицій, що вже закінчились (сторінки збираються раз на 2 год)
    if(!document.querySelector("a.card[href^='product-']")) return;
    stockSnapshot().then(function(snap){
      if(!snap || !snap.fresh) return;
      var cards=document.querySelectorAll("a.card[href^='product-']");   // ПІСЛЯ відповіді: у цей час уже є клони каруселі (аудит: клони лишались «У наявності»)
      Array.prototype.forEach.call(cards, function(a){
        var m=/product-([\w-]+)\.html/.exec(a.getAttribute("href")||""); if(!m || stockOf(snap, m[1])!==0) return;
        var av=a.querySelector(".av"); if(av){ av.textContent="Немає"; av.className="av oos"; }
        var sb=a.querySelector(".stock-badge"); if(sb && sb.parentNode) sb.parentNode.removeChild(sb);
      });
    });
  }
  function markCartStock(){
    var box=document.getElementById("cart-body"); if(!box) return;
    stockSnapshot().then(function(snap){
      var old=document.getElementById("cart-stock-note"); if(old && old.parentNode) old.parentNode.removeChild(old);
      Array.prototype.forEach.call(box.querySelectorAll(".ci-stock"), function(el){ el.parentNode.removeChild(el); });
      var issues=stockIssues(read(), snap);
      if(!issues.length) return;
      issues.forEach(function(x){
        var nm=null; Array.prototype.forEach.call(box.querySelectorAll(".cart-item"), function(row){ if(row.getAttribute("data-id")===String(x.id)) nm=row.querySelector(".ci-nm"); });
        if(!nm) return;
        var s=document.createElement("div"); s.className="ci-stock"; s.textContent=x.available>0 ? "Доступно не більше "+x.available+" шт." : "Щойно закінчився";
        nm.parentNode.insertBefore(s, nm.nextSibling);
      });
      var n=document.createElement("div"); n.id="cart-stock-note"; n.className="oos-note stock-now"; n.setAttribute("role","alert");
      var b=document.createElement("b"); b.textContent="Щойно закінчилось: "; n.appendChild(b);
      n.appendChild(document.createTextNode(issues.map(stockLine).join("; ")+". "));
      var fix=document.createElement("button"); fix.type="button"; fix.className="btn ghost"; fix.setAttribute("data-act","stock-fix"); fix.textContent="Прибрати / зменшити ці позиції";
      n.appendChild(fix);
      box.parentNode.insertBefore(n, box);
    });
  }
  function priceChanged(j){   // відповідь /api/order 409 {code:"price_changed", expected_total, total, lines:[{id,name,qty,price}]}: замовлення НЕ створено; кошик показує те, що сервер бачить ТЕПЕР
    var msg=document.getElementById("checkout-msg"), btn=document.getElementById("checkout-submit");
    if(btn){ btn.disabled=false; btn.textContent="Оформити замовлення"; }
    var c=read(), diffs=[];
    (Array.isArray(j.lines) ? j.lines : []).forEach(function(l){
      if(!l || typeof l!=="object" || l.id===undefined || !c[String(l.id)]) return;
      var np=+l.price; if(!isFinite(np) || np<0) return;
      var it=c[String(l.id)];
      if(it.price!==np){ diffs.push({name:it.name, was:it.price, now:np}); it.price=np; }
    });
    if(diffs.length){ write(c); }
    renderCart();
    if(!msg) return;
    msg.classList.add("err"); msg.textContent="";
    var t=document.createElement("div");
    var tot=(typeof j.total==="number" && isFinite(j.total)) ? j.total : total();
    t.textContent="Ціни змінились, поки ви оформлювали замовлення"+(diffs.length ? ": "+diffs.map(function(d){ return d.name+" — було "+d.was+" ₴, тепер "+d.now+" ₴"; }).join("; ") : "")+". Нова сума: "+tot+" ₴. Замовлення не створено — перевірте кошик і підтвердьте ще раз.";
    msg.appendChild(t);
  }
  function stockConflict(lines){   // відповідь /api/order 409 {code:"stock_changed", lines:[{id,name,requested,available}]}: замовлення НЕ створено
    var msg=document.getElementById("checkout-msg"), btn=document.getElementById("checkout-submit");
    if(btn){ btn.disabled=false; btn.textContent="Оформити замовлення"; }
    if(!msg) return;
    var issues=lines.filter(function(l){ return l && typeof l==="object" && l.id!==undefined; }).map(function(l){ return {id:String(l.id), name:String(l.name||""), requested:+l.requested||0, available:Math.max(0, +l.available||0)}; });
    if(!issues.length){ msg.classList.add("err"); msg.textContent="Деяких товарів уже немає в такій кількості. Перегляньте кошик і спробуйте ще раз."; return; }
    msg.classList.add("err"); msg.textContent="";
    var t=document.createElement("div"); t.textContent="Щойно закінчилось: "+issues.map(stockLine).join("; ")+". Замовлення не створено.";
    var b=document.createElement("button"); b.type="button"; b.className="btn ghost"; b.textContent="Прибрати / зменшити ці позиції і продовжити";
    b.addEventListener("click", function(){
      applyStockFix(issues); renderCart();
      msg.classList.remove("err"); msg.textContent="Кошик оновлено. Перевірте склад і суму та підтвердьте замовлення.";
    });
    msg.appendChild(t); msg.appendChild(b);
  }

  // ── Ініціалізація на кожній сторінці ──
  document.addEventListener("DOMContentLoaded", function(){
    updateBadge();
    // зміна способу оплати перераховує доставку, підпис і примітку (див. applyDeliveryView)
    document.addEventListener("change", function(e){ if(e.target && e.target.name==="payment" && document.getElementById("cart-summary")) applyDeliveryView(total()); });
    // браузер (Firefox/bfcache) може відновити ІНШИЙ вибраний спосіб оплати вже після першого рендера — підсумок доставки перераховуємо на pageshow, щоб показане відповідало обраному
    if(document.getElementById("cart-summary") && typeof window.addEventListener==="function"){   // лише на сторінці кошика (на решті сторінок зайвих слухачів/таймерів нема)
      var resyncDelivery=function(){ if(document.getElementById("cart-summary")) applyDeliveryView(total()); };
      window.addEventListener("pageshow", resyncDelivery); window.addEventListener("load", resyncDelivery);   // + load: pageshow приходить після всіх картинок/GTM, а Firefox відновлює форму раніше (аудит)
      setTimeout(resyncDelivery, 0);
    }
    initLiqpayTestEntry();

    // делеговані кліки: додати в кошик, пошук, кошик-керування
    document.body.addEventListener("click", function(e){
      var t=e.target.closest("[data-add]");
      if(t){ e.preventDefault(); PT.add(JSON.parse(t.getAttribute("data-add")), t.getAttribute("data-list")||""); if(document.getElementById("cart-body")) renderCart(); return; }
      if(e.target.closest("#open-search")){ e.preventDefault(); openSearch(); return; }
      if(e.target.closest("#close-search")){ e.preventDefault(); closeSearch(); return; }
      var ov=document.getElementById("search-overlay");
      if(ov && ov.classList.contains("on")){
        if(e.target===ov){ closeSearch(); return; }   // клік по затемненню за панеллю (не по самій панелі)
        var lk=e.target.closest("#search-overlay a[href]");
        if(lk && searchPushed && !(e.ctrlKey||e.metaKey||e.shiftKey||e.altKey||e.button)){
          // перехід із пошуку: спершу знімаємо свій запис, потім переходимо — інакше «Назад» зі сторінки товару повертав би на сторінку з порожнім записом (подвійний «Назад»)
          e.preventDefault(); searchLeaveHref=lk.getAttribute("href"); closeSearch();
          var hh=searchLeaveHref; setTimeout(function(){ if(searchLeaveHref===hh){ searchLeaveHref=null; location.href=hh; } }, 400);
          return;
        }
      }
      var sb=e.target.closest("#search-overlay [data-sort]");
      if(sb){ e.preventDefault(); searchSort=sb.getAttribute("data-sort"); renderSearch(); return; }
      var sf=e.target.closest("[data-act=stock-fix]");
      if(sf){ e.preventDefault(); stockSnapshot().then(function(snap){ applyStockFix(stockIssues(read(), snap)); renderCart(); }); return; }
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
    initStockProduct();
    initStockCards();
    initShipToday();
    initCheckout();
    initNpAutocomplete();
    initCatalog();
    initHomeCarousel();
    initListTracking();
    initNaboryChips();
    initMascot();
    initContactTracking();
    initMailCopy();
    initSaleTimer();
    initChatMenu();

    // сторінка подяки — підставити номер замовлення
    var oidEl=document.getElementById("thanks-oid");
    if(oidEl){ try{ oidEl.textContent = sessionStorage.getItem("pt_last_order") || ""; }catch(e){} }
    // кнопка «Отримувати статус у Telegram»: бот дізнається номер із ?start=<номер замовлення> (допустимі символи payload Telegram [A-Za-z0-9_-], ≤64; наш PT-YYYYMMDD-HHMMSS-xxxxxx підходить)
    var tgEl=document.getElementById("thanks-tg");
    if(tgEl){ try{ var tgo=(sessionStorage.getItem("pt_last_order")||"").trim(); if(/^[A-Za-z0-9_-]{1,64}$/.test(tgo)){ tgEl.setAttribute("href","https://t.me/plutustoys_support_bot?start="+encodeURIComponent(tgo)); var tw=document.getElementById("thanks-tg-wrap"); if(tw) tw.hidden=false; } }catch(e){} }

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
    var m=/product-([\w-]+)\.html/.exec(a.getAttribute("href")||"");
    var nm=a.querySelector(".nm"), pr=a.querySelector(".pr");
    return {item_id:m?m[1]:"", item_name:nm?nm.textContent.trim():"", price:pr?parseFloat(pr.textContent):0, index:idx, item_list_name:""};
  }
  function initHomeCarousel(){
    var car=document.getElementById("home-car");
    if(!car) return;
    var track=car.querySelector(".track"), left=car.querySelector(".arrow.l"), right=car.querySelector(".arrow.r");
    var listName=car.getAttribute("data-list")||"Добірка Плутуса";
    var slides=Array.prototype.slice.call(car.querySelectorAll("a.card"));
    function productSlides(){ return slides; }
    function items(){ return productSlides().map(function(a,i){ var d=slideData(a,i); d.item_list_name=listName; return d; }); }
    // Широкий екран показує 5 слайдів одразу — 4-й і 5-й не мають лишатись lazy (порожні картки на першому екрані). У Chromium вони й так у зоні lazy-порога (≈1250 px) — виміряно аудитом #678;
    // це страхування для браузерів із меншим порогом (Safari/Firefox), без претензії на економію ваги.
    if(typeof matchMedia==="function" && matchMedia("(min-width: 700px)").matches){
      slides.slice(3,5).forEach(function(a){ var im=a.querySelector("img"); if(im){ im.loading="eager"; } });
    }
    // ── Безкінечний цикл (SMM 12.10, власник: «карусель не йде по колу»): по N клонів перших карток у кінці й останніх на початку; коли прокрутка ЗУПИНИЛАСЬ у зоні клонів — тихо (без анімації)
    //   повертаємо scrollLeft на оригінал: клон візуально ідентичний, тож стрибка немає. Клони: aria-hidden + tabindex=-1 (скрін-рідер і Tab їх не бачать), у `slides`/аналітику НЕ входять (клік по клону = його оригінал).
    //   Менше 6 слайдів або немає DOM-API клонування — старий лінійний режим.
    var loopOn=false, loopTimer=null, resizeTimer=null, LOOP_N=0, loopFrac=0, loopW=0;   // loopFrac — прокручена частка кола (0..1), loopW — остання ширина вікна каруселі
    function trackPad(){ var cs=window.getComputedStyle ? getComputedStyle(track) : null; return cs ? (parseFloat(cs.paddingLeft)||0) : 0; }
    function loopOffset(){ return slides[0].getBoundingClientRect().left - track.getBoundingClientRect().left - trackPad(); }   // >0: оригінал-перший правіше за «вирівняне» положення
    function loopPeriod(){ return track.children[LOOP_N + slides.length].getBoundingClientRect().left - slides[0].getBoundingClientRect().left; }   // ширина повного кола (з проміжками)
    function setupLoop(){
      var n=slides.length;
      if(n<6 || !track || !track.appendChild || !track.insertBefore || !slides[0].cloneNode) return;
      LOOP_N=Math.min(n,10);   // буфер: вікно (до 5 карток) + крок стрілки (≈4 картки) з запасом
      function mk(src){
        var c=src.cloneNode(true);
        c.setAttribute("data-clone", String(slides.indexOf(src))); c.setAttribute("aria-hidden","true"); c.setAttribute("tabindex","-1");
        Array.prototype.forEach.call(c.querySelectorAll("img"), function(im){ im.loading="lazy"; im.removeAttribute("fetchpriority"); });
        return c;
      }
      for(var i=0;i<LOOP_N;i++) track.appendChild(mk(slides[i]));
      for(var j=n-LOOP_N;j<n;j++) track.insertBefore(mk(slides[j]), slides[0]);
      loopOn=true;
      loopAlign();
      loopW=track.clientWidth; loopFrac=0;
    }
    function loopAlign(){ if(loopOn) track.scrollLeft += loopOffset(); }   // стати на оригінал-перший (початкове положення й після зміни розміру вікна)
    function loopNormalize(force){
      if(!loopOn || (auto.touch && !force)) return;
      var off=-loopOffset(), P=loopPeriod();                                  // off = скільки прокручено від оригіналу-першого
      if(off >= P-2){ track.scrollLeft -= P; off -= P; } else if(off < -2){ track.scrollLeft += P; off += P; }
      loopFrac = off / P;                                                       // запам'ятовуємо логічну позицію — для відновлення після зміни ширини
    }
    // Зміна ШИРИНИ (поворот екрана, ресайз): довжина кола змінилась — відновлюємо ту саму ЛОГІЧНУ позицію (частку кола), а не скидаємо на першу картку.
    // Зміна лише ВИСОТИ (адресний рядок/клавіатура на телефоні при вертикальному скролі) карусель не чіпає (аудит #701: раніше скидало на першу картку).
    function loopRestore(){
      if(!loopOn) return;
      var w=track.clientWidth;
      if(w===loopW) return;
      loopW=w;
      track.scrollLeft += loopFrac*loopPeriod() + loopOffset();
    }
    // Чипи над каруселлю (SMM 12.10): «Усі» (основна добірка) / «Набори» міняють набір з 10 карток БЕЗ перезавантаження. Набори лежать у <template id="car-set-<ключ>"> (інертні, фото не вантажаться
    //   до кліку). Перемикання: прибрати клони циклу → підставити картки → перерахувати slides/loop → на початок. Аналітика: view_item_list і select_item з item_list_name «home_chip_<ключ>» (для «Усі» — як було).
    var baseHTML = track ? track.innerHTML : "", baseName = listName;
    function listId(){ return listName===baseName ? "home_pick" : listName; }   // id списку в GA4: основна добірка — як було, чипи — home_chip_<ключ>
    function teardownLoop(){
      if(!loopOn) return;
      Array.prototype.slice.call(track.querySelectorAll("[data-clone]")).forEach(function(c){ c.parentNode.removeChild(c); });
      loopOn=false; clearTimeout(loopTimer);
    }
    function setCarouselSet(html, name){
      teardownLoop();
      track.innerHTML=html;
      slides=Array.prototype.slice.call(car.querySelectorAll("a.card"));
      listName=name;
      track.scrollLeft=0;
      setupLoop();
      sync();
      userAction();
      sent=true;   // IO-подія основного набору більше не потрібна (шлемо власну нижче)
      trackRaw("view_item_list", {item_list_id:listId(), item_list_name:listName, items:items()});
    }
    var chipBar=document.getElementById("home-chips");
    if(chipBar && track){
      chipBar.classList.add("js");
      var chipBtns=Array.prototype.slice.call(chipBar.querySelectorAll("button[data-set]"));
      chipBar.addEventListener("click", function(e){
        var b=e.target.closest ? e.target.closest("button[data-set]") : null; if(!b) return;
        var was=b.classList.contains("active"), key=b.getAttribute("data-set"), html, name;   // повторний клік по активному чипу знімає вибір і вертає основну карусель (чипа «Усі» нема — власник 16.10)
        if(was){ html=baseHTML; name=baseName; }
        else { var tpl=document.getElementById("car-set-"+key); if(!tpl) return; html=tpl.innerHTML; name="home_chip_"+key; }
        chipBtns.forEach(function(x){ var on=(!was && x===b); x.classList.toggle("active", on); x.setAttribute("aria-pressed", on ? "true" : "false"); });
        var subEl=document.getElementById("home-chip-sub"); if(subEl) subEl.textContent = was ? "" : (b.getAttribute("data-sub") || "");   // підпис сезонного чипа (SMM)
        var allEl=document.getElementById("home-car-all"); if(allEl){ var da = was ? "" : b.getAttribute("data-all"); if(da){ allEl.setAttribute("href", da); allEl.textContent = b.getAttribute("data-all-label") || "Дивитись усі →"; allEl.hidden=false; } else { allEl.hidden=true; } }   // чип «Хелловін» → посилання на /helovin.html під чипами
        setCarouselSet(html, name);
      });
    }
    car.classList.add("js");   // стрілки показуємо лише коли JS працює (без нього вони були б «мертвими»; нативний скрол/свайп лишається)
    function sync(){
      if(!track||!left||!right) return;
      if(loopOn){ left.disabled=false; right.disabled=false; return; }   // цикл: країв немає
      // допуск ±6 px: padding .track + scroll-snap лишають scrollLeft ≈2,4 у стані спокою (аудит #678)
      left.disabled = track.scrollLeft <= 6;
      right.disabled = track.scrollLeft + track.clientWidth >= track.scrollWidth - 6;
    }
    function scrollBy(dir){
      loopNormalize(true);   // перед кроком — тихо повернути на оригінал, щоб буфер клонів завжди був попереду (швидкі кліки)
      var reduce = typeof matchMedia==="function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
      if(track) track.scrollBy({left:dir*Math.max(160, track.clientWidth*0.8), behavior: reduce ? "auto" : "smooth"});
    }
    if(left) left.addEventListener("click", function(){ userAction(); scrollBy(-1); });
    if(right) right.addEventListener("click", function(){ userAction(); scrollBy(1); });
    var auto = {hover:false, focus:false, touch:false, touchAt:0, visible:true, until:0};
    if(track){
      setupLoop();
      track.addEventListener("scroll", function(){ sync(); if(loopOn){ clearTimeout(loopTimer); loopTimer=setTimeout(loopNormalize, 140); } });
      sync();
    }
    function onResize(){ sync(); if(loopOn){ clearTimeout(resizeTimer); resizeTimer=setTimeout(loopRestore, 150); } }
    window.addEventListener("resize", onResize);   // ширина вікна/поворот екрана міняють «край» (і довжину кола)
    window.addEventListener("orientationchange", onResize);
    // ── Автопрокрутка (SMM 12.10: власник бачить нерухому карусель як «не крутиться»): по 1 картці кожні ~4 с зі scroll-snap. НЕ крутимо, коли:
    //   курсор мишею над каруселлю (pointerType touch не рахується: після тапу «наведення» на телефоні залипає), клавіатурний фокус всередині (:focus-visible — клік мишею по стрілці фокус
    //   не «тримає»), палець на екрані, вкладка прихована (document.hidden), карусель менше ніж на 40% у вікні (IntersectionObserver), prefers-reduced-motion, або щойно була дія
    //   покупця (стрілка/свайп/колесо/клавіша — пауза 10 с, щоб не відбирати керування). На краю — плавно на початок (без клонів слайдів, але далі крутиться по колу, поки нема паузи; кнопки паузи нема — лише hover/фокус/дія). Нових зображень не підвантажуємо: порядок
    //   і набір слайдів ті самі; в аналітику автопрокрутку НЕ пишемо (select_item лише на клік, view_item_list — як було).
    var AUTO_MS = 4000, USER_GRACE_MS = 10000;
    function reducedMotion(){ return typeof matchMedia==="function" && matchMedia("(prefers-reduced-motion: reduce)").matches; }
    function userAction(){ auto.until = Date.now() + USER_GRACE_MS; }
    function autoStep(){
      if(auto.touch && Date.now() - auto.touchAt > 30000) auto.touch = false;   // запобіжник: touchend/touchcancel не прийшли (системний жест iOS) — не стоїмо вічно
      if(!track || reducedMotion() || document.hidden || !auto.visible || auto.hover || auto.focus || auto.touch || Date.now() < auto.until) return;
      var atEnd = !loopOn && track.scrollLeft + track.clientWidth >= track.scrollWidth - 6;
      if(atEnd){ if(track.scrollTo) track.scrollTo({left:0, behavior:"smooth"}); return; }
      loopNormalize(false);
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
      trackRaw("view_item_list", {item_list_id:listId(), item_list_name:listName, items:items()});
    }
    if(typeof IntersectionObserver==="function"){
      var io=new IntersectionObserver(function(es){ es.forEach(function(e){ if(e.isIntersecting){ fireList(); io.disconnect(); } }); }, {threshold:0.4});
      io.observe(car);
    } else { fireList(); }
    car.addEventListener("click", function(e){
      var a=e.target.closest("a.card"); if(!a) return;
      var orig=a;
      if(slides.indexOf(a)<0 && a.getAttribute && a.getAttribute("data-clone")!==null){ orig=slides[+a.getAttribute("data-clone")] || a; }   // клон = свій оригінал
      var i=productSlides().indexOf(orig);
      var d=slideData(a, i<0?0:i); d.item_list_name=listName;
      trackRaw("select_item", {item_list_id:listId(), item_list_name:listName, items:[d]});
    });
  }
  // ── Посадкова /ig.html: сітка карток з item_list_name «ig_landing» — view_item_list один раз (коли сітка видна), select_item лише на клік ──
  function initListTracking(){
    var box=document.getElementById("ig-list") || document.getElementById("nabory-list") || document.getElementById("sale-list");   // /ig.html («ig_landing») і /nabory.html («nabory_page»)
    if(!box) return;
    var listName=box.getAttribute("data-list")||"ig_landing";
    var cards=Array.prototype.slice.call(box.querySelectorAll("a.card"));
    // view_item_list: лише картки, видимі в поточному фільтрі (на /nabory.html чипи ховають решту), не більше data-limit (GA4: розумний розмір події)
    function items(){
      var lim=+box.getAttribute("data-limit")||cards.length;
      return cards.filter(function(a){ return !a.hidden; }).slice(0,lim).map(function(a,i){ var d=slideData(a,i); d.item_list_name=listName; return d; });
    }
    var sent=false;
    function fireList(){
      if(sent) return; sent=true;
      trackRaw("view_item_list", {item_list_id:listName, item_list_name:listName, items:items()});
    }
    if(typeof IntersectionObserver==="function"){
      // поріг 0.1, а не 0.4: на 320 px сітка в 1 колонку (≈2250 px заввишки) — 40% її висоти у вікні не бувають, і view_item_list не слався б ніколи (аудит #686)
      var io=new IntersectionObserver(function(es){ es.forEach(function(e){ if(e.isIntersecting){ fireList(); io.disconnect(); } }); }, {threshold:0.1});
      // спостерігаємо ПЕРШУ картку, а не весь список: на /nabory.html список на 11–23 тис. px, і частка його видимості < 10% — подія не спрацьовувала б ніколи (аудит #706, той самий клас, що #686)
      io.observe(cards[0] || box);
    } else { fireList(); }
    box.addEventListener("click", function(e){
      var a=e.target.closest("a.card"); if(!a) return;
      var i=cards.indexOf(a); var d=slideData(a, i<0?0:i); d.item_list_name=listName;
      trackRaw("select_item", {item_list_id:listName, item_list_name:listName, items:[d]});
    });
  }
  // ── Анімований Плутус у герої головної (SMM 09.10 + 12.10): poster (idle-кадр) і кліп ЗАМІНЮЮТЬ один одного (жорстка заміна, не кросфейд — крізь прозорий кліп просвічував poster → двійник): раз на 12–20 с poster ховається й
  // ОДИН раз грає один із 5 кліпів (tailtrip, ear_scratch + дзеркальні grooming, sniff, shakeoff — усі з хвостом праворуч, як poster; перший і останній кадр кожного змішано з poster), потім poster повертається. Не безперервний луп.
  // Нічого не вантажимо й не граємо: prefers-reduced-motion, Save-Data, 2g/slow-2g; кліп тягнемо ПІСЛЯ load + 3 с по одному; не більше одного в польоті; пауза: прихована вкладка (кліп гасимо одразу й ПЛАНУЄМО наступний — аудит #688),
  // Плутус поза екраном (гасимо й плануємо). Розмір задано атрибутами width/height обох <img> — CLS нуль. src кліпу знімаємо після показу: наступний стартує з першого кадру. В аналітику не пише.
  function initMascot(){
    var img=document.getElementById("hero-mascot"), clipImg=document.getElementById("hero-mascot-clip");
    if(!img || !clipImg) return;
    var clips=(img.getAttribute("data-clips")||"").split("|").filter(Boolean);
    var durs=(img.getAttribute("data-ms")||"").split(",").map(Number);
    if(!clips.length) return;
    function reduced(){ return typeof matchMedia==="function" && matchMedia("(prefers-reduced-motion: reduce)").matches; }
    var conn=navigator.connection||{};
    if(reduced() || conn.saveData || /(^|-)2g$/.test(conn.effectiveType||"")) return;
    var dips=(img.getAttribute("data-dip")||"").split(",").filter(function(x){ return x!==""; }).map(Number);   // сезонні рухи, кадр спокою яких ≠ спільний poster: підміна через «занурення»
    var visible=true, playing=false, busy=false, last=-1, order=[], timer=null, endTimer=null, dipNow=false, lastEnd=0, REACT_COOL=3000;
    function wait(){ return 12000 + Math.floor(Math.random()*8000); }
    function schedule(ms){ clearTimeout(timer); timer=setTimeout(play, ms); }
    var wrap=img.parentNode;
    function stop(soft){
      clearTimeout(endTimer);
      if(!playing) return;
      playing=false; lastEnd=Date.now();
      function undo(){
        clipImg.classList.remove("on");
        if(wrap && wrap.classList) wrap.classList.remove("clip-on");    // poster повертається ВІДРАЗУ (останній кадр кліпу = poster)
        clipImg.removeAttribute("src");                                 // наступний показ стартує з першого кадру
      }
      if(soft && dipNow && wrap && wrap.classList){                     // кінець «сезонного» руху: гасимо → poster → проявляємо (без двійника і без стрибка шапки/гарбуза)
        wrap.classList.add("dip");
        setTimeout(function(){ undo(); wrap.classList.remove("dip"); }, 170);
      } else { if(wrap && wrap.classList) wrap.classList.remove("dip"); undo(); }
      dipNow=false;
    }
    if(typeof IntersectionObserver==="function"){
      var io=new IntersectionObserver(function(es){ es.forEach(function(e){ visible=!!e.isIntersecting; if(!visible && playing){ stop(); schedule(wait()); } if(visible && !playing && !busy) prefetchNext(); }); }, {threshold:0.3});
      io.observe(img);
      visible=false;   // до першого повідомлення IntersectionObserver вважаємо «не видно»
    }
    function canPlay(){ return !document.hidden && visible && !reduced(); }
    // Мішок із вагами (data-w: 2 — звичайні, 1 — «рідші» рухи): кожен рух у мішку стільки разів, яка вага; тягнемо випадково БЕЗ повтору попереднього (і на стику мішків); data-nofirst — індекси, які не можуть бути
    // найпершим показом (stretch_yawn). Мішок вичерпується повністю перед наступним: за цикл кожен рух зʼявляється, рідкі — рідше.
    var weights=(img.getAttribute("data-w")||"").split(",").map(Number);
    var noFirst=(img.getAttribute("data-nofirst")||"").split(",").filter(function(x){ return x!==""; }).map(Number);
    var started=false;
    function fillOrder(){
      if(!order.length){
        var pool=[]; clips.forEach(function(_,i){ for(var k=0;k<(weights[i]>0?weights[i]:1);k++) pool.push(i); });
        var prev=last, first=!started;
        while(pool.length){
          var cand=[]; pool.forEach(function(v,idx){ if(v!==prev && !(first && noFirst.indexOf(v)>=0)) cand.push(idx); });
          if(!cand.length) pool.forEach(function(v,idx){ cand.push(idx); });             // лишились лише однакові — неминучий повтор, беремо що є
          var pick=cand[Math.floor(Math.random()*cand.length)];
          prev=pool[pick]; order.push(prev); pool.splice(pick,1); first=false;
        }
      }
    }
    function next(){ fillOrder(); started=true; last=order.shift(); return last; }
    // Передзавантаження НАСТУПНОГО руху, поки Плутус сидить спокійно (власник 17.10: «тапати треба декілька разів» — на мобільному кожен рух спершу вантажився 1–8 с, і тап «не спрацьовував»):
    // fetch прогріває HTTP-кеш, тож пізніше new Image().src бере файл миттєво. Лише коли Плутуса видно й вкладка активна (без зайвого трафіку), раз на кожен наступний рух; на 2G/Save-Data/reduced-motion сюди не доходимо (return вище).
    var pref=-1;
    function prefetchNext(){
      try{
        if(!canPlay() || typeof fetch!=="function") return;
        fillOrder(); var i=order[0];
        if(i===undefined || pref===i) return;
        pref=i;
        var p=fetch(clips[i], {credentials:"omit"}); if(p && p.then) p.then(function(r){ return r && r.blob ? r.blob() : null; }).catch(function(){});
      }catch(e){}
    }
    function play(){
      if(playing || busy) return;
      if(!canPlay()){ schedule(wait()); return; }
      busy=true;   // від початку підвантаження до старту руху: друга реакція/таймер не запускає паралельний кліп
      var idx=next(), url=clips[idx], ms=(durs[idx]>0?durs[idx]:4000)+400;      // запас 400 мс: декодування перших кадрів на слабкому телефоні не зрізає хвіст
      var pre=new Image();
      pre.onload=function(){
        busy=false;
        if(!canPlay()){ schedule(wait()); return; }      // поки вантажився — вкладка сховалась / Плутус зник з екрана
        playing=true; clipImg.src=url;
        // Жорстка заміна, не кросфейд (SMM 12.10: крізь прозорий кліп просвічував poster → двійник): poster ховаємо ТІЛЬКИ коли перший кадр кліпу вже декодовано
        // (інакше блимне порожнє місце); перший і останній кадри кліпів = poster, тож стик непомітний.
        function show(){
          if(wrap && wrap.classList) wrap.classList.add("clip-on");
          clipImg.classList.add("on");
        }
        function go(){
          if(!playing) return;
          dipNow = dips.indexOf(idx)>=0;
          if(dipNow && wrap && wrap.classList){
            wrap.classList.add("dip");                                  // гасимо poster, під «нулем» міняємо на кліп, проявляємо — час кліпу стартує з підміни
            setTimeout(function(){ if(playing){ show(); } wrap.classList.remove("dip"); }, 170);
          } else { show(); }
          endTimer=setTimeout(function(){ stop(true); schedule(wait()); prefetchNext(); }, ms);
        }
        if(clipImg.decode){ clipImg.decode().then(go, go); } else { go(); }
      };
      pre.onerror=function(){ busy=false; schedule(wait()); };
      pre.src=url;
    }
    // Реакція на курсор/дотик (власник 17.10, варіант 1): наведення мишею або клік/тап по Плутусу — одразу наступний рух із ротації, не чекаючи 12–20 с.
    // Не під час іншого руху, не частіше ніж раз на REACT_COOL після кінця попереднього; вимкнено при reduced-motion/saveData/2g (вище return) і коли Плутуса не видно.
    var pokeT=null;
    function poke(){ if(!wrap || !wrap.classList) return; wrap.classList.remove("poke"); void wrap.offsetWidth; wrap.classList.add("poke"); clearTimeout(pokeT); pokeT=setTimeout(function(){ wrap.classList.remove("poke"); }, 320); }   // «вдавлення» 0,3 с: людина бачить, що тап/наведення зараховано, навіть коли рух ще вантажиться або триває
    function react(touch){
      if(document.hidden || !visible) return;
      poke();
      if(playing || busy) return;
      if(lastEnd && Date.now()-lastEnd<(touch ? 1200 : REACT_COOL)) return;
      clearTimeout(timer);   // скасувати відкладений автопоказ — після реакції стартує звичайна пауза
      play();
    }
    if(wrap && wrap.addEventListener){
      wrap.addEventListener("pointerenter", function(e){ if(!e.pointerType || e.pointerType==="mouse") react(false); });
      wrap.addEventListener("pointerdown", function(e){ if(e.pointerType && e.pointerType!=="mouse") react(true); else if(!e.pointerType) react(false); });
      wrap.addEventListener("click", function(){ react(false); });
    }
    document.addEventListener("visibilitychange", function(){ if(document.hidden){ var was=playing; stop(); if(was) schedule(wait()); } });
    function start(){ schedule(3000); }      // перший кліп — не раніше ніж через 3 с після load
    if(document.readyState==="complete") start(); else window.addEventListener("load", start);
  }
  // ── /nabory.html: чипи-фільтри за типом набору (SMM 12.10). Усі картки вже в HTML (data-g), чип лише ховає/показує — без запитів і без перезавантаження; без JS чипи сховані, сітка повна ──
  function initNaboryChips(){
    var box=document.getElementById("nabory-list"), bar=document.getElementById("nb-chips");
    if(!box || !bar) return;
    bar.classList.add("js");
    var cards=Array.prototype.slice.call(box.querySelectorAll("a.card"));
    var btns=Array.prototype.slice.call(bar.querySelectorAll("button[data-g]"));
    bar.addEventListener("click", function(e){
      var b=e.target.closest ? e.target.closest("button[data-g]") : null; if(!b) return;
      var g=b.getAttribute("data-g");
      btns.forEach(function(x){ var on=(x===b); x.classList.toggle("active", on); x.setAttribute("aria-pressed", on ? "true" : "false"); });
      cards.forEach(function(a){ a.hidden = !(g==="all" || a.getAttribute("data-g")===g); });
    });
  }
  // ── Прихований вхід для тестової оплати LiqPay (запит Коду 12.10): ?liqpay_test=1 розкриває радіо «Оплата карткою онлайн (ТЕСТОВИЙ РЕЖИМ)» на кошику; прапорець живе в sessionStorage лише цієї вкладки.
  //   Покупці адреси не знають; бекенд у sandbox не передає такі замовлення в Toysi й не фіскалізує. Без елемента #pay-test (збірка з SITE_LIQPAY_LIVE=1) нічого не робить. ──
  function initLiqpayTestEntry(){
    var el=document.getElementById("pay-test-slot"); if(!el) return;
    var on=false;
    try{ if(/[?&]liqpay_test=1(&|$)/.test(location.search)){ sessionStorage.setItem("pt_liqpay_test","1"); } on = sessionStorage.getItem("pt_liqpay_test")==="1"; }catch(e){ on = /[?&]liqpay_test=1(&|$)/.test(location.search); }
    if(on) el.innerHTML='<label class="pay" id="pay-test"><input type="radio" name="payment" value="prepaid" data-test="1"> Оплата карткою онлайн (ТЕСТОВИЙ РЕЖИМ — реальних списань немає)</label>';
  }
  // ── Клік по пошті (mailto): на комп'ютері без поштового клієнта браузер «нічого не робить» (власник 15.10). Лишаємо mailto (де клієнт є — відкриється), а адресу додатково копіюємо в буфер і показуємо підтвердження ──
  function initMailCopy(){
    var toast=null, tm=null;
    function show(msg){
      if(!toast){ toast=document.createElement("div"); toast.className="pt-toast"; toast.setAttribute("role","status"); toast.hidden=true; document.body.appendChild(toast); }
      toast.textContent=msg; toast.hidden=false; clearTimeout(tm); tm=setTimeout(function(){ toast.hidden=true; }, 3500);
    }
    function copy(text, done){
      try{
        if(navigator.clipboard && navigator.clipboard.writeText){ navigator.clipboard.writeText(text).then(function(){ done(true); }, function(){ done(fallback()); }); return; }
      }catch(e){}
      done(fallback());
      function fallback(){
        try{ var ta=document.createElement("textarea"); ta.value=text; ta.setAttribute("readonly",""); ta.style.position="fixed"; ta.style.opacity="0"; document.body.appendChild(ta); ta.select(); var ok=document.execCommand && document.execCommand("copy"); document.body.removeChild(ta); return !!ok; }catch(e){ return false; }
      }
    }
    document.addEventListener("click", function(e){
      var a=e.target.closest ? e.target.closest('a[href^="mailto:"]') : null; if(!a) return;
      var addr=(a.getAttribute("href")||"").replace(/^mailto:/i,"").split("?")[0]; if(!addr) return;
      copy(addr, function(ok){ show(ok ? "Адресу "+addr+" скопійовано" : "Пошта: "+addr); });
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

  // ── Знижка тижня: зворотний відлік «залишилось …» до кінця акції (дата з site/sale_week.json через data-sale-until; без JS лишається статичний підпис «до неділі 23:59») ──
  function initSaleTimer(){
    var els=document.querySelectorAll("[data-sale-until]");
    if(!els.length) return;
    function pad(n){ return (n<10?"0":"")+n; }
    function tick(){
      Array.prototype.forEach.call(els, function(el){
        var end=Date.parse(el.getAttribute("data-sale-until")||""); if(!isFinite(end)) return;
        var ms=end-Date.now();
        if(ms<=0){ el.textContent="акцію завершено"; return; }
        var d=Math.floor(ms/86400000), h=Math.floor(ms%86400000/3600000), m=Math.floor(ms%3600000/60000);
        el.textContent="До кінця акції: "+(d>0 ? d+" д " : "")+h+" год "+pad(m)+" хв";
      });
    }
    tick(); setInterval(tick, 30000);
    window.PT_saleTick=tick;   // для тесту: примусове оновлення відліку
  }

  // ── Кнопка «Написати» (меню месенджерів у шапці, власник 14.10) ──
  function initChatMenu(){
    var btn=document.getElementById("chat-toggle"), menu=document.getElementById("chat-menu");
    if(!btn || !menu) return;
    function setOpen(on){ menu.hidden=!on; btn.setAttribute("aria-expanded", on ? "true" : "false"); }
    btn.addEventListener("click", function(e){ e.stopPropagation(); setOpen(menu.hidden); });
    document.addEventListener("click", function(e){
      var it=e.target.closest ? e.target.closest("a.chat-item") : null;
      if(it){ trackRaw("contact_click", {method:it.getAttribute("data-chat")||"chat", link_url:it.getAttribute("href")||"", page_path:location.pathname}, "Contact", {method:it.getAttribute("data-chat")||"chat"}, false); setOpen(false); return; }
      if(!menu.hidden && !(e.target.closest && e.target.closest(".chatwrap"))) setOpen(false);
    });
    document.addEventListener("keydown", function(e){ if((e.key==="Escape" || e.key==="Esc") && !menu.hidden){ setOpen(false); btn.focus(); } });
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
        if(ev.key==="Escape"){ box.innerHTML=""; return; }
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
      if(cities.length && acCity.scrollIntoView){   // тестувальник 12.10 + аудит #697 F2: на телефоні список з'являвся нижче видимої зони; скрол — ПІСЛЯ renderAc (раніше список порожній і схований) і після розкладки
        var raf=window.requestAnimationFrame||function(f){ return setTimeout(f,0); };
        raf(function(){ try{ acCity.scrollIntoView({block:"nearest", behavior:"smooth"}); }catch(e){} });
      }
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
      fetch("api/np/warehouse?city_ref="+encodeURIComponent(selectedCityRef)+"&q="+encodeURIComponent(q)).then(function(r){ if(!r.ok) throw new Error("np"); return r.json(); }).then(function(d){
        if(myWh!==whSeq || myCity!==selectedCityRef) return;     // запізніла відповідь іншого міста/запиту (аудит #682 р.2, F5): склад чужого міста не пропонуємо
        renderAc(acWh, (d.warehouses||[]).map(function(w){ return {label:w.description, name:w.description, number:w.number}; }),
          function(pick){ wh.value=pick.name; selectedWarehouseNumber=pick.number||""; acWh.innerHTML=""; });
      }).catch(function(){
        // збій довідника НП (502/мережа) не маскуємо під «відділення нема» (знахідка тестувальника 17, Code 16.10): один рядок і ручний ввід номера
        if(myWh!==whSeq || myCity!==selectedCityRef) return;
        acWh.innerHTML='<div class="np-note" role="status">Довідник відділень тимчасово недоступний, введіть номер відділення вручну</div>';
      });
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
      // тестувальник 12.10: type=tel/required пропускали «123», «abcdefghij», «   » — сервер відсікав би, але покупець бачить помилку лише після відправки. Та сама перевірка, що в site_order_api.
      var nm=(document.getElementById("f-name").value||"").trim();
      if(nm.length<3 || !/\s/.test(nm)){ fail("Вкажіть ім’я та прізвище отримувача (через пробіл)."); var fn_=document.getElementById("f-name"); if(fn_ && fn_.focus) fn_.focus(); return; }
      var dg=(document.getElementById("f-phone").value||"").replace(/\D/g,""); if(dg.length===10 && dg.charAt(0)==="0") dg="38"+dg;
      if(!(dg.length===12 && dg.indexOf("380")===0)){ fail("Вкажіть коректний номер телефону (+380…)."); var fp_=document.getElementById("f-phone"); if(fp_ && fp_.focus) fp_.focus(); return; }
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
      if(Object.keys(read()).some(function(k){ return /^rt-/.test(k); })) payment="prepaid";   // RoyalToys (rt-…): лише повна передоплата, явно (сервер без prepaid відхиляє кошик, Код 19.10)
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
        payment_method:payment,
        comment:(document.getElementById("f-comment") ? (document.getElementById("f-comment").value||"").trim().slice(0,300) : "")
      };
      // сума, яку ПОКАЗАВ кошик у момент сабміту (ціле грн): якщо index.json змінився між «У кошик» і оплатою, сервер відповість 409 price_changed і НЕ створить замовлення (Code-Agent #759); без поля — поведінка як раніше
      var shownTotal=total(); if(isFinite(shownTotal)){ payload.expected_total=Math.round(shownTotal); }
      if(btn){ btn.disabled=true; btn.textContent="Обробляємо…"; }

      fetch("api/order", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)})
        .then(function(r){ return r.json().then(function(j){ return {ok:r.ok, status:r.status, j:j}; }); })
        .then(function(res){
          if(!res.ok){
            if(res.status===409 && res.j && res.j.code==="stock_changed" && Array.isArray(res.j.lines)){ stockConflict(res.j.lines); return; }
            if(res.status===409 && res.j && res.j.code==="price_changed"){ priceChanged(res.j); return; }
            fail(res.j && res.j.error ? res.j.error : "Не вдалося оформити замовлення."); return;
          }
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
            // тестове радіо веде на LiqPay лише коли бекенд відповів sandbox=true: у бойовому режимі під написом «без списань» реальних грошей не береться (аудит 13.10)
            if(payEl && payEl.getAttribute("data-test")==="1" && !(d.liqpay && d.liqpay.sandbox===true)){ fail("Тестову оплату вимкнено. Оберіть «Оплата при отриманні»."); return; }
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
