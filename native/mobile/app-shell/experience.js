"use strict";
(() => {
  const CUSTOMER = "__CHANNEL__" === "customer";
  const $ = id => document.getElementById(id);
  const screens = ["home","product","saved","notifications","account"];
  const key = "kofad-" + (CUSTOMER?"customer":"staff") + "-welcome-v3";
  const favoritesKey = "kofad-customer-favorites-v3";
  let products=[],selected=null,favorites=[];
  try { const v=JSON.parse(localStorage.getItem(favoritesKey)||"[]");if(Array.isArray(v))favorites=v.filter(x=>Number.isSafeInteger(x)&&x>0).slice(0,100); }catch(_){}
  function open(path) { window.KofadNativeBridge?.openOfficial(path); }
  const login = () => open(CUSTOMER?"/market/access/":"/workspace/");
  function view(name) {
    screens.forEach(s => $("screen-"+s).hidden=s!==name);
    const active = name==="product"?"catalog":name==="saved"?"account":name==="notifications"?"notices":name;
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
  $("tab-catalog").addEventListener("click",browse);
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
    if(selected)open("/market/products/"+selected.id+"/");
  });
  $("native-account-login").addEventListener("click",login);
  $("staff-secure-entry").addEventListener("click",()=>open("/workspace/"));
  if(CUSTOMER){
    accountRow("Saved products",()=>{saved();view("saved");});
    accountRow("Your orders ↗",()=>open("/market/orders/"));
    accountRow("Customer support ↗",()=>open("/market/messages/"));
  }else{
    accountRow("Staff dashboard ↗",()=>open("/workspace/"));
    accountRow("Staff account ↗",()=>open("/account/"));
    accountRow("Communications ↗",()=>open("/email/"));
  }
  window.KofadNativeExperience=Object.freeze({
    showProduct:detail,
    catalogLoaded(items,append){
      if(!CUSTOMER||!Array.isArray(items))return;
      products=append?[...products,...items].slice(0,150):items.slice(0,20);
    },
  });
})();
