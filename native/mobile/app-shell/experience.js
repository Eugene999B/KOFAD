"use strict";
(() => {
  const CUSTOMER = "__CHANNEL__" === "customer";
  const $ = id => document.getElementById(id);
  const screens = ["home","product","saved","notifications","account","orders","work"];
  const key = "kofad-" + (CUSTOMER?"customer":"staff") + "-welcome-v3";
  const favoritesKey = "kofad-customer-favorites-v3";
  let products=[],selected=null,favorites=[];
  try { const v=JSON.parse(localStorage.getItem(favoritesKey)||"[]");if(Array.isArray(v))favorites=v.filter(x=>Number.isSafeInteger(x)&&x>0).slice(0,100); }catch(_){}
  function open(path) { window.KofadNativeBridge?.openOfficial(path); }
  const login = async () => {
    const started = await window.KofadMobileAuth?.start?.();
    if (!started) open(CUSTOMER?"/market/access/":"/workspace/");
  };
  function view(name) {
    screens.forEach(s => $("screen-"+s).hidden=s!==name);
    const active = name==="product"?"catalog":name==="saved"||name==="orders"?"account":name==="work"?"catalog":name==="notifications"?"notices":name;
    ["home","catalog","notices","account"].forEach(id=>{
      const node=$("tab-"+id);
      node.classList.toggle("selected",id===active);
      if(id===active)node.setAttribute("aria-current","page");else node.removeAttribute("aria-current");
    });
    window.scrollTo(0,0);
  }
  function browse() {
    view("home");
    (CUSTOMER ? $("customer-content") : $("staff-content")).scrollIntoView({behavior:"smooth",block:"start"});
  }
  function thumb(p) {
    const wrap=document.createElement("div");wrap.className="native-detail-image";
    if(/^\/market\/products\/\d+\/image\/thumb\/$/.test(p.image_path||"")){
      const img=document.createElement("img");img.src="https://market.kofadimpex.com"+p.image_path;img.loading="lazy";img.alt="";
      img.onerror=()=>{img.remove();wrap.textContent="K";};wrap.append(img);
    }else wrap.textContent="K";
    return wrap;
  }
  function detail(p) {
    if(!CUSTOMER||!Number.isSafeInteger(p?.id)||p.id<=0)return;
    selected=p;
    $("native-detail-photo").replaceChildren(thumb(p));
    $("native-detail-category").textContent=String(p.category||"KOFAD Market").slice(0,70);
    $("native-detail-name").textContent=String(p.name||"Product").slice(0,160);
    $("native-detail-price").textContent="GH₵ "+String(p.price||"0.00")+" · "+String(p.selling_unit||"");
    $("native-detail-description").textContent=String(p.description||"Browse this product from KOFAD Market.").slice(0,500);
    $("native-detail-stock").textContent=p.in_stock_snapshot===false?"Please check availability before ordering":"Stock is confirmed when you order";
    $("native-detail-save").textContent=favorites.includes(p.id)?"♥ Saved":"♡ Save item";
    view("product");
    // The v1 endpoint enriches a public card without transmitting cookies or
    // authentication. Order/payment actions never use a cached price.
    if (navigator.onLine !== false) {
      const detailId = p.id;
      void fetch("https://market.kofadimpex.com/market/mobile/v1/products/" + detailId + "/", {
        mode: "cors", credentials: "omit", cache: "no-store",
      }).then(response => response.ok ? response.json() : null).then(data => {
        if (!data || data.version !== 1 || selected?.id !== detailId || $("screen-product").hidden) return;
        const fresh = data.product;
        if (!fresh || fresh.id !== detailId) return;
        $("native-detail-description").textContent=String(fresh.description||"Browse this product from KOFAD Market.").slice(0,1600);
        $("native-detail-price").textContent="GH₵ "+String(fresh.price||"0.00")+" · "+String(fresh.selling_unit||"");
        $("native-detail-stock").textContent=String(fresh.availability_note||"Stock is confirmed when you order").slice(0,150);
      }).catch(() => { /* Keep the locally displayed public preview when offline. */ });
    }
  }
  function saved() {
    const root=$("native-saved-items");root.replaceChildren();
    const found=products.filter(x=>favorites.includes(x.id));
    if(!found.length){const p=document.createElement("p");p.textContent="No saved items from the loaded catalogue. Browse and save products you like.";root.append(p);return;}
    found.forEach(p=>{
      const button=document.createElement("button");button.type="button";button.className="native-saved-card";
      const label=document.createElement("strong");label.textContent=String(p.name||"Product").slice(0,160);
      const amount=document.createElement("small");amount.textContent="GH₵ "+String(p.price);
      button.append(thumb(p),label,amount);button.addEventListener("click",()=>detail(p));root.append(button);
    });
  }
  function stateText(root, text) {
    root.replaceChildren();
    const message=document.createElement("p");
    message.className="native-caption";
    message.textContent=text;
    root.append(message);
  }
  function metric(label, count) {
    const card=document.createElement("div");
    card.className="native-metric-card";
    const title=document.createElement("small");
    title.textContent=label;
    const value=document.createElement("strong");
    value.textContent=String(count);
    card.append(title,value);
    return card;
  }
  async function showOrderDetails(id) {
    if (!/^[0-9a-fA-F-]{36}$/.test(id)) return;
    const box=$("native-order-detail");
    const list=$("native-orders-list");
    list.hidden=true;box.hidden=false;
    stateText(box,"Loading your order…");
    const data=await window.KofadMobileAuth?.readMobile?.("orders/"+id+"/");
    if ($("screen-orders").hidden || box.hidden) return;
    if (!data || data.error || !data.order) {
      stateText(box,"This order could not be loaded right now.");
      return;
    }
    box.replaceChildren();
    const back=document.createElement("button");
    back.type="button";back.className="native-back-link";back.textContent="← All orders";
    back.addEventListener("click",()=>{box.hidden=true;list.hidden=false;});
    const h=document.createElement("h2");h.textContent=String(data.order.reference||"Order").slice(0,40);
    const status=document.createElement("p");status.className="native-caption";
    status.textContent="Order: "+String(data.order.status||"")+
      " · Payment: "+String(data.order.payment_status||"");
    const amount=document.createElement("h3");amount.textContent="GH₵ "+String(data.order.total||"0.00");
    box.append(back,h,status,amount);
    for(const item of (Array.isArray(data.order.items)?data.order.items.slice(0,100):[])){
      const row=document.createElement("div");row.className="native-order-line";
      const name=document.createElement("strong");name.textContent=String(item.name||"Product").slice(0,180);
      const price=document.createElement("span");price.textContent=String(item.quantity||0)+" × GH₵ "+String(item.unit_price||"0.00");
      row.append(name,price);box.append(row);
    }
  }
  async function showOrders() {
    if (!CUSTOMER) return;
    view("orders");
    $("native-order-detail").hidden=true;
    const list=$("native-orders-list");list.hidden=false;
    stateText(list,"Checking your orders…");
    if (!window.KofadMobileAuth?.isAuthenticated?.()) {
      stateText(list,"Sign in to KOFAD Market to view your orders privately.");
      return;
    }
    const data=await window.KofadMobileAuth?.readMobile?.("orders/");
    if ($("screen-orders").hidden) return;
    if (!data || data.error || !Array.isArray(data.items)) {
      stateText(list,"Could not load your order history. Check your connection and try again.");
      return;
    }
    list.replaceChildren();
    if (!data.items.length){stateText(list,"You have no orders yet. Explore KOFAD Market to get started.");return;}
    for(const order of data.items.slice(0,30)){
      const id=String(order.id||"");
      if (!/^[0-9a-fA-F-]{36}$/.test(id)) continue;
      const row=document.createElement("button");row.type="button";row.className="native-order-row";
      const reference=document.createElement("strong");reference.textContent=String(order.reference||"Order").slice(0,60);
      const summary=document.createElement("small");
      summary.textContent=String(order.status||"")+" · "+String(order.payment_status||"")+
        " · GH₵ "+String(order.total||"0.00");
      row.append(reference,summary);
      row.addEventListener("click",()=>void showOrderDetails(id));list.append(row);
    }
  }
  async function showWork() {
    if (CUSTOMER) return;
    view("work");
    const list=$("native-work-summary");stateText(list,"Loading your assigned branch…");
    if (!window.KofadMobileAuth?.isAuthenticated?.()) {
      stateText(list,"Sign in securely to view live staff operations.");
      return;
    }
    const data=await window.KofadMobileAuth?.readMobile?.("overview/");
    if ($("screen-work").hidden) return;
    if (!data || data.error || !data.modules) {
      stateText(list,"Your live operations are unavailable. Verify your account, permissions and connection.");
      return;
    }
    list.replaceChildren();
    const branch=document.createElement("h2");branch.textContent=String(data.branch?.name||"Assigned branch").slice(0,100);
    list.append(branch);
    const modules=data.modules||{};
    if (modules.inventory) list.append(metric("Products needing stock attention",modules.inventory.low_stock_items??0));
    if (modules.orders) {
      list.append(metric("Awaiting payment",modules.orders.awaiting_payment??0));
      list.append(metric("Preparing orders",modules.orders.preparing??0));
      list.append(metric("Out for delivery",modules.orders.out_for_delivery??0));
    }
    if (!modules.inventory && !modules.orders) {
      stateText(list,"No operational modules are assigned to this account.");
    }
  }

  function accountRow(title,fn){
    const button=document.createElement("button");button.type="button";button.className="native-account-link";button.textContent=title;
    button.addEventListener("click",fn);$("native-account-actions").append(button);
  }
  function finish(signIn) {
    $("native-welcome").hidden=true;document.body.classList.remove("native-welcome-open");
    try{localStorage.setItem(key,"yes");}catch(_){}
    if(signIn)login();
    else view("home");
  }
  $("native-welcome-signin").addEventListener("click",()=>finish(true));
  $("native-welcome-guest").addEventListener("click",()=>finish(false));
  let started=false;try{started=localStorage.getItem(key)==="yes";}catch(_){}
  if(!started){$("native-welcome").hidden=false;document.body.classList.add("native-welcome-open");}
  if(!CUSTOMER){
    $("native-welcome-guest").textContent="Preview app features";
    $("native-account-title").textContent="Staff access";
    $("native-account-head").textContent="Your secure workspace";
    $("native-account-copy").textContent="KOFAD verifies your role, branch and identity before protected work.";
    $("native-account-login").textContent="Staff sign in →";
    document.querySelector("[data-catalog-label]").textContent="Work";
  }
  $("tab-home").addEventListener("click",()=>view("home"));
  $("tab-catalog").addEventListener("click",()=>{if(CUSTOMER)browse();else void showWork();});
  $("tab-notices").addEventListener("click",()=>view("notifications"));
  $("native-bell").addEventListener("click",()=>view("notifications"));
  $("tab-account").addEventListener("click",()=>view("account"));
  $("native-detail-back").addEventListener("click",browse);
  $("native-detail-save").addEventListener("click",()=>{
    if(!selected)return;
    favorites=favorites.includes(selected.id)?favorites.filter(x=>x!==selected.id):[...favorites,selected.id].slice(0,100);
    try{localStorage.setItem(favoritesKey,JSON.stringify(favorites));}catch(_){}
    $("native-detail-save").textContent=favorites.includes(selected.id)?"♥ Saved":"♡ Save item";
  });
  $("native-detail-buy").addEventListener("click",()=>{
    if(selected)open("/market/access/?next="+encodeURIComponent("/market/products/"+selected.id+"/"));
  });
  $("native-account-login").addEventListener("click",async()=>{
    if (window.KofadMobileAuth?.isAuthenticated?.()) {
      await window.KofadMobileAuth.signOut();
    } else await login();
  });
  $("staff-secure-entry").addEventListener("click",()=>void login());
  $("native-orders-back").addEventListener("click",()=>view("account"));
  $("native-work-back").addEventListener("click",()=>view("home"));
  if(CUSTOMER){
    accountRow("Saved products",()=>{saved();view("saved");});
    accountRow("My orders",()=>void showOrders());
    accountRow("Customer support ↗",()=>open("/market/messages/"));
  }else{
    accountRow("Live operations",()=>void showWork());
    accountRow("Staff account ↗",()=>open("/account/"));
    accountRow("Communications ↗",()=>open("/email/"));
  }
  function showCategories(names) {
    if (!CUSTOMER) return;
    const root = $("native-category-filters");
    if (!root || !Array.isArray(names)) return;
    const selected = window.KofadNativeBridge?.currentCategory?.() || "";
    const options = ["",...new Set(names.filter(x => typeof x === "string" && x.trim().length < 65).slice(0,16))];
    root.replaceChildren();
    options.forEach(name => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "native-filter-pill";
      button.textContent = name || "All";
      button.setAttribute("aria-pressed", String(name === selected));
      button.addEventListener("click", () => {
        window.KofadNativeBridge?.setCategory(name);
        showCategories(names);
      });
      root.append(button);
    });
  }
  window.KofadNativeExperience=Object.freeze({
    showProduct:detail,
    showCategories,
    catalogLoaded(items,append){
      if(!CUSTOMER||!Array.isArray(items))return;
      products=append?[...products,...items].slice(0,150):items.slice(0,20);
    },
  });
})();
