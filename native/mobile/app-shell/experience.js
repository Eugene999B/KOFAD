"use strict";
(() => {
  const $ = selector => document.querySelector(selector);
  const channel = document.body.dataset.channel === "staff" ? "staff" : "customer";
  const customer = channel === "customer";
  const storagePrefix = "kofad-v2-" + channel + "-";
  const key = name => storagePrefix + name;
  const get = name => { try { return localStorage.getItem(key(name)); } catch (_) { return null; } };
  const set = (name, value) => { try { localStorage.setItem(key(name), value); } catch (_) {} };
  const shell = () => window.KofadNativeShell;
  const originalTitle = document.title;
  const notice = $("#native-notifications");
  const bell = $("#native-bell");
  const gate = $("#welcome-gate");
  const detail = $("#native-product-detail");
  let selectedProduct = null;
  let lastProductButton = null;
  let saved = {};
  let liveProducts = [];
  let lastCategories = [];
  try { const parsed = JSON.parse(get("saved") || "{}"); if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) saved = parsed; } catch (_) {}

  function escapeRestoredFocus(element) {
    if (element?.isConnected) element.focus({preventScroll:true});
    else $("#search")?.focus({preventScroll:true});
  }
  const visible = (node, show) => { if (node) node.hidden = !show; };
  function dismissGate() {
    visible(gate, false);
    set("welcome-complete", "1");
    document.body.classList.remove("native-gate-active");
  }
  function startWelcome() {
    if (get("welcome-complete") === "1") return;
    visible(gate, true);
    document.body.classList.add("native-gate-active");
    $("#gate-signin")?.addEventListener("click", () => {
      dismissGate();
      shell()?.openOfficial(customer ? "/market/access/" : "/workspace/");
    });
    $("#gate-skip")?.addEventListener("click", () => {
      dismissGate();
      if (customer) navigate("explore");
      else navigate("work");
    });
  }

  function setTab(active) {
    const nodes = {
      home: $("#tab-home"), explore: $("#tab-explore"),
      saved: $("#tab-account"), account: $("#tab-support"),
    };
    for (const [tab, el] of Object.entries(nodes)) {
      if (!el) continue;
      const current = tab === active;
      el.classList.toggle("selected", current);
      if (current) el.setAttribute("aria-current", "page");
      else el.removeAttribute("aria-current");
    }
  }
  function navigate(tab) {
    const hero = $(".native-hero");
    const catalog = $("#customer-content");
    const staff = $("#staff-content");
    const savedView = $("#saved-view");
    const account = $("#native-account-panel");
    if (customer) {
      const isBrowse = tab === "home" || tab === "explore";
      visible(hero, tab === "home");
      visible(catalog, isBrowse || tab === "saved");
      visible(savedView, tab === "saved");
      visible($("#products"), isBrowse);
      visible($("#category-pills"), isBrowse);
      visible($("#search"), isBrowse);
      visible($("#catalog-status"), isBrowse);
      visible($("#more-products"), isBrowse && Boolean(shell()?.nextPage?.()));
      visible(account, tab === "account");
      if (tab === "saved") renderSaved();
    } else {
      visible(hero, tab === "home");
      visible(staff, tab === "home" || tab === "explore");
      visible(account, tab === "account");
      if (tab === "saved") openNotices();
    }
    setTab(tab);
    const target = tab === "explore" ? (customer ? $("#customer-content") : $("#staff-content"))
      : tab === "saved" ? $("#saved-view") : tab === "account" ? $("#native-account-panel") : $("#main");
    if (target?.scrollIntoView) target.scrollIntoView({behavior:"smooth",block:"start"});
    if (tab === "explore" && customer) $("#search")?.focus({preventScroll:true});
  }
  function openNotices() {
    visible(notice, true);
    bell?.setAttribute("aria-expanded","true");
    $("#native-notice-close")?.focus({preventScroll:true});
    document.body.classList.add("native-modal-open");
    $("#native-bell-dot")?.setAttribute("hidden","");
  }
  function closeNotices() {
    visible(notice, false);
    bell?.setAttribute("aria-expanded","false");
    document.body.classList.remove("native-modal-open");
    bell?.focus({preventScroll:true});
  }
  bell?.addEventListener("click", () => notice?.hidden ? openNotices() : closeNotices());
  $("#native-notice-close")?.addEventListener("click", closeNotices);
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    if (!detail?.hidden) closeProduct();
    else if (!notice?.hidden) closeNotices();
  });

  function safeProduct(product) {
    if (!product || !Number.isSafeInteger(Number(product.id)) || Number(product.id) <= 0) return null;
    return {
      id:Number(product.id), name:String(product.name||"Product").slice(0,160),
      category:String(product.category||"General").slice(0,70),
      description:String(product.description||"Available from KOFAD Market.").slice(0,1300),
      price:String(product.price||"0.00").slice(0,30),
      unit:String(product.selling_unit||"").slice(0,50),
      image:/^\/market\/products\/\d+\/image\/thumb\/$/.test(String(product.image_path||"")) ? product.image_path : "",
      available:product.in_stock_snapshot === true,
    };
  }
  function createThumb(p) {
    const wrap = document.createElement("div");
    wrap.className="native-detail-thumb";
    if (p.image) {
      const image = document.createElement("img");
      image.src="https://market.kofadimpex.com"+p.image;
      image.alt=p.name;
      image.onerror=()=>{wrap.textContent="KOFAD";};
      wrap.append(image);
    } else wrap.textContent="KOFAD";
    return wrap;
  }
  function openProduct(product, source) {
    const p = safeProduct(product);
    if (!p || !detail) return;
    selectedProduct = p;
    lastProductButton = source || document.activeElement;
    $("#native-detail-image").replaceChildren(createThumb(p));
    $("#native-detail-category").textContent=p.category;
    $("#native-detail-title").textContent=p.name;
    $("#native-detail-description").textContent=p.description;
    $("#native-detail-price").textContent="GH₵ "+p.price+" · "+(p.unit||"item");
    $("#native-detail-stock").textContent=p.available ? "Available in the current catalogue · confirmed at checkout" : "Check current availability before ordering";
    $("#native-detail-purchase").textContent="Sign in to buy securely →";
    $("#native-detail-save").textContent=saved[p.id] ? "♥ Saved" : "♡ Save";
    visible(detail,true);
    document.body.classList.add("native-modal-open");
    $(".native-sheet-close")?.focus({preventScroll:true});
  }
  function closeProduct() {
    visible(detail,false);
    document.body.classList.remove("native-modal-open");
    escapeRestoredFocus(lastProductButton);
    selectedProduct=null;
  }
  document.querySelectorAll("[data-close-detail]").forEach(el=>el.addEventListener("click",closeProduct));
  $("#native-detail-save")?.addEventListener("click",()=>{
    if (!selectedProduct) return;
    const p=selectedProduct;
    if (saved[p.id]) delete saved[p.id];
    else saved[p.id]={...p, savedAt:Date.now()};
    // This is on-device convenience, not the server wishlist or an order.
    set("saved",JSON.stringify(saved));
    $("#native-detail-save").textContent=saved[p.id]?"♥ Saved":"♡ Save";
    renderSaved();
  });
  $("#native-detail-purchase")?.addEventListener("click",()=>{
    if (!selectedProduct) return;
    const path="/market/access/?next="+encodeURIComponent("/market/products/"+selectedProduct.id+"/");
    shell()?.openOfficial(path);
  });
  function renderSaved() {
    const root=$("#saved-products");
    if (!root) return;
    root.replaceChildren();
    const items=Object.values(saved).filter(p=>safeProduct(p)).slice(0,30);
    if (!items.length) {
      const empty=document.createElement("p");
      empty.className="native-empty";
      empty.textContent="No saved products yet. Explore the Market and tap the heart on a product.";
      root.append(empty);
      return;
    }
    for (const item of items) {
      const p=safeProduct(item);
      if (!p) continue;
      const card=document.createElement("button");
      card.type="button";card.className="native-saved-card";
      const thumb=createThumb(p);
      const info=document.createElement("span");
      const title=document.createElement("strong");
      title.textContent=p.name;
      const price=document.createElement("small");
      price.textContent="GH₵ "+p.price;
      info.append(title,price);
      card.append(thumb,info);
      card.addEventListener("click",()=>openProduct(p,card));
      root.append(card);
    }
  }
  function showCategories(categories) {
    if (!customer) return;
    const root=$("#category-pills");
    if (!root) return;
    const list=Array.isArray(categories)?categories.filter(v=>typeof v==="string"&&v.length<65).slice(0,12):[];
    lastCategories=list;
    root.replaceChildren();
    const choices=["All",...list];
    const selected=shell()?.currentCategory?.()||"";
    for (const label of choices) {
      const value=label==="All"?"":label;
      const button=document.createElement("button");
      button.type="button";
      button.className="native-category-pill";
      button.textContent=label;
      button.setAttribute("aria-pressed",String(selected===value));
      button.addEventListener("click",()=>{
        shell()?.setCategory(value);
        showCategories(lastCategories);
      });
      root.append(button);
    }
  }
  function setupNav() {
    if (!customer) {
      const explore=$("#tab-explore"), account=$("#tab-account"), profile=$("#tab-support");
      if (explore) explore.querySelector("span:last-child").textContent="Work";
      if (account) account.querySelector("span:last-child").textContent="Notices";
      if (profile) profile.querySelector("span:last-child").textContent="Account";
    }
    $("#tab-home")?.addEventListener("click",()=>navigate("home"));
    $("#tab-explore")?.addEventListener("click",()=>navigate("explore"));
    $("#tab-account")?.addEventListener("click",()=>navigate(customer?"saved":"saved"));
    $("#tab-support")?.addEventListener("click",()=>navigate("account"));
    $("#staff-workspace-access")?.addEventListener("click",()=>shell()?.openOfficial("/workspace/"));
    $("#native-account-signin")?.addEventListener("click",()=>shell()?.openOfficial(customer?"/market/access/":"/workspace/"));
    $("#native-account-orders")?.addEventListener("click",()=>shell()?.openOfficial("/market/orders/"));
    $("#native-account-help")?.addEventListener("click",()=>shell()?.openOfficial(customer?"/market/messages/":"/email/"));
    $("#native-account-security")?.addEventListener("click",()=>shell()?.openOfficial(customer?"/market/account/security/":"/account/"));
  }
  // Calls are UI-only; protected accounts/orders/payments stay in the secured KOFAD backend.
  window.KofadMobileExperience=Object.freeze({openProduct,showCategories,navigate});
  document.title=originalTitle;
  setupNav();
  startWelcome();
})();
