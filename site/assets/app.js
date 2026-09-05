/* PlutusToys — клієнтська логіка: кошик, лічильник, пошук, checkout.
   Статичний сайт: кошик у localStorage, оформлення POST → /api/order (бекенд крок 3). */
(function(){
  "use strict";
  var CART_KEY = "pt_cart_v1";
  var DELIVERY_HINT = 70;           // орієнтир доставки НП для підсумку (уточнюється при оформленні)

  function read(){ try{ return JSON.parse(localStorage.getItem(CART_KEY)) || {}; }catch(e){ return {}; } }
  function write(c){ try{ localStorage.setItem(CART_KEY, JSON.stringify(c)); }catch(e){} updateBadge(); }
  function count(){ var c=read(),n=0; for(var k in c){ n+=c[k].qty; } return n; }
  function total(){ var c=read(),s=0; for(var k in c){ s+=c[k].qty*c[k].price; } return s; }

  window.PT = {
    add:function(p){                 // p={id,name,price,photo}
      var c=read();
      if(c[p.id]){ c[p.id].qty++; } else { c[p.id]={id:p.id,name:p.name,price:+p.price,photo:p.photo,qty:1}; }
      write(c);
      flash("Додано в кошик");
    },
    setQty:function(id,q){ var c=read(); if(c[id]){ c[id].qty=Math.max(0,q); if(c[id].qty===0){delete c[id];} write(c); } },
    remove:function(id){ var c=read(); delete c[id]; write(c); },
    clear:function(){ write({}); },
    read:read, count:count, total:total, DELIVERY_HINT:DELIVERY_HINT
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
  function runSearch(q){
    var res=document.querySelector("#search-overlay .results");
    if(!res) return;
    q=(q||"").trim().toLowerCase();
    if(q.length<2){ res.innerHTML=""; return; }
    ensureIndex(function(data){
      var out=[];
      for(var i=0;i<data.length && out.length<40;i++){
        if(data[i].n.toLowerCase().indexOf(q)>=0) out.push(data[i]);
      }
      res.innerHTML = out.length ? out.map(function(p){
        return '<a class="sr" href="product-'+p.id+'.html">'+
          '<img src="'+p.p+'" loading="lazy" alt="">'+
          '<div><div class="srn">'+esc(p.n)+'</div><div class="srp">'+p.pr+' ₴</div></div></a>';
      }).join("") : '<div class="empty" style="padding:40px 0">Нічого не знайдено за «'+esc(q)+'»</div>';
    });
  }
  function esc(s){ return String(s).replace(/[&<>"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];}); }

  // ── Рендер кошика (сторінка cart.html) ──
  function renderCart(){
    var box=document.getElementById("cart-body");
    if(!box) return;
    var c=read(), ids=Object.keys(c);
    var checkoutBtn=document.getElementById("to-checkout");
    if(ids.length===0){
      box.innerHTML='<div class="empty"><div class="fox">🦊</div>Кошик порожній.<br>Оберіть іграшки в <a href="catalog.html" style="color:var(--accent)">каталозі</a>.</div>';
      var s=document.getElementById("cart-summary"); if(s) s.style.display="none";
      if(checkoutBtn) checkoutBtn.style.display="none";
      return;
    }
    var html="";
    ids.forEach(function(id){
      var it=c[id];
      html+='<div class="cart-item" data-id="'+id+'">'+
        '<img src="'+it.photo+'" loading="lazy" alt="">'+
        '<div style="flex:1"><div class="ci-nm">'+esc(it.name)+'</div>'+
        '<div class="ci-pr">'+it.price+' ₴</div>'+
        '<div class="qty"><button data-act="dec">−</button><span class="q">'+it.qty+'</span><button data-act="inc">+</button></div>'+
        '</div><button class="ci-rm" data-act="rm">Прибрати</button></div>';
    });
    box.innerHTML=html;
    renderSummary();
  }
  function renderSummary(){
    var goods=total();
    var g=document.getElementById("sum-goods"), d=document.getElementById("sum-delivery"), t=document.getElementById("sum-total");
    if(g) g.textContent=goods+" ₴";
    if(d) d.textContent="≈ "+PT.DELIVERY_HINT+" ₴";
    if(t) t.textContent=(goods+PT.DELIVERY_HINT)+" ₴";
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
      var ci=e.target.closest(".cart-item [data-act]");
      if(ci){
        var wrap=ci.closest(".cart-item"), id=wrap.getAttribute("data-id"), act=ci.getAttribute("data-act");
        var c=read();
        if(act==="inc") PT.setQty(id, (c[id]?c[id].qty:0)+1);
        else if(act==="dec") PT.setQty(id, (c[id]?c[id].qty:0)-1);
        else if(act==="rm") PT.remove(id);
        renderCart();
      }
    });

    var so=document.querySelector("#search-overlay input");
    if(so){ so.addEventListener("input", function(){ runSearch(so.value); }); }

    renderCart();

    // оформлення (checkout) — поки заглушка бекенду (крок 3)
    var form=document.getElementById("checkout-form");
    if(form){
      form.addEventListener("submit", function(e){
        e.preventDefault();
        var msg=document.getElementById("checkout-msg");
        if(count()===0){ if(msg) msg.textContent="Кошик порожній."; return; }
        // TODO(крок 3): POST payload → /api/order → LiqPay redirect. Зараз демонструємо збір даних.
        if(msg){ msg.style.color="var(--success)";
          msg.textContent="Дані зібрано. Оплата LiqPay та відправка НП підключаються на кроці бекенду."; }
      });
    }
  });
})();
