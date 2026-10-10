"use strict";
/* Local, public-product shopping draft only. No order, payment, stock or
 * authenticated customer information is persisted or submitted here. */
(() => {
  if ("__CHANNEL__" !== "customer") return;
  const KEY = "kofad-market-local-basket-v1";
  const MAX_LINES = 40, MAX_QUANTITY = 20;
  const $ = id => document.getElementById(id);
  const safe = value => typeof value === "string" ? value.trim().slice(0, 110) : "";
  const validId = value => Number.isSafeInteger(value) && value > 0;
  function normalize(input) {
    if (!Array.isArray(input)) return [];
    const rows = new Map();
    for (const entry of input.slice(0, 100)) {
      const id = Number(entry?.id), name = safe(entry?.name);
      const quantity = Number(entry?.quantity);
      if (!validId(id) || !name || !Number.isInteger(quantity) || quantity < 1 || quantity > MAX_QUANTITY || rows.has(id)) continue;
      rows.set(id, {id, name, quantity});
      if (rows.size >= MAX_LINES) break;
    }
    return [...rows.values()];
  }
  let rows = [];
  try { rows = normalize(JSON.parse(localStorage.getItem(KEY) || "[]")); } catch (_) {}
  const count = () => rows.reduce((n, row) => n + row.quantity, 0);
  function persist() {
    try { localStorage.setItem(KEY, JSON.stringify(rows)); } catch (_) {}
    const badge = $("native-basket-count");
    if (badge) {
      const n = count();
      badge.textContent = String(n);
      badge.hidden = n === 0;
    }
  }
  function button(label, action, description) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.setAttribute("aria-label", description);
    b.addEventListener("click", action);
    return b;
  }
  function change(id, quantity) {
    const item = rows.find(r => r.id === id);
    if (!item) return;
    if (quantity <= 0) rows = rows.filter(r => r.id !== id);
    else if (quantity <= MAX_QUANTITY) item.quantity = quantity;
    persist();
    render();
  }
  function render() {
    const root = $("native-basket-items");
    if (!root) return;
    root.replaceChildren();
    const summary = $("native-basket-summary");
    const clear = $("native-basket-clear");
    const shop = $("native-basket-secure-site");
    if (clear) clear.disabled = rows.length === 0;
    if (shop) shop.disabled = navigator.onLine === false;
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "native-basket-empty";
      const icon = document.createElement("span");
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = "♡";
      const heading = document.createElement("strong");
      heading.textContent = "Your basket is empty";
      const message = document.createElement("p");
      message.textContent = "Add products as you browse. Your choices remain on this device.";
      empty.append(icon, heading, message);
      root.append(empty);
      if (summary) summary.textContent = "No products added";
      return;
    }
    for (const item of rows) {
      const card = document.createElement("article");
      card.className = "native-basket-item";
      const title = document.createElement("strong");
      title.textContent = item.name;
      const detail = document.createElement("small");
      detail.textContent = "Product " + item.id + " · Final price and stock confirmed by KOFAD";
      const controls = document.createElement("div");
      controls.className = "native-quantity-controls";
      const minus = button("−", () => change(item.id, item.quantity - 1), "Decrease " + item.name);
      const amount = document.createElement("output");
      amount.textContent = String(item.quantity);
      amount.setAttribute("aria-label", item.name + " quantity");
      const plus = button("+", () => change(item.id, item.quantity + 1), "Increase " + item.name);
      plus.disabled = item.quantity >= MAX_QUANTITY;
      const remove = button("Remove", () => change(item.id, 0), "Remove " + item.name);
      controls.append(minus, amount, plus, remove);
      card.append(title, detail, controls);
      root.append(card);
    }
    if (summary) summary.textContent = count() + " item" + (count() === 1 ? "" : "s") + " in your local basket";
  }
  function addProduct(product) {
    const id = Number(product?.id), name = safe(product?.name);
    if (!validId(id) || !name || product?.in_stock_snapshot === false) return false;
    const existing = rows.find(r => r.id === id);
    if (existing) {
      if (existing.quantity >= MAX_QUANTITY) return false;
      existing.quantity++;
    } else {
      if (rows.length >= MAX_LINES) return false;
      rows.push({id, name, quantity: 1});
    }
    persist();
    render();
    return true;
  }
  let syncing = false;
  const message = text => {
    const status = $("native-basket-sync-status");
    if (status) status.textContent = text;
  };
  const estimate = data => {
    const node = $("native-basket-server-estimate");
    if (!node) return;
    if (data?.currency !== "GHS" || typeof data?.estimated_subtotal !== "string") {
      node.hidden = true;
      return;
    }
    node.textContent = "Account basket estimate: GH₵ " + data.estimated_subtotal +
      ". Delivery, stock and payment are confirmed at checkout.";
    node.hidden = false;
  };
  function merge(remoteItems) {
    if (!Array.isArray(remoteItems)) return null;
    const source = normalize(remoteItems);
    const combined = new Map(source.map(item => [item.id, item]));
    for (const item of rows) {
      const previous = combined.get(item.id);
      // Take the larger saved quantity. Addition here would double item counts
      // on every repeated manual sync from the same device.
      if (previous) previous.quantity = Math.max(previous.quantity, item.quantity);
      else combined.set(item.id, {...item});
    }
    return combined.size <= MAX_LINES ? [...combined.values()] : null;
  }
  async function accountReady() {
    const auth = window.KofadMobileAuth;
    if (!auth?.isAuthenticated?.()) {
      message("Sign in through your Account tab to synchronize this basket.");
      return false;
    }
    try {
      const reply = await fetch("https://market.kofadimpex.com/market/mobile/v1/bootstrap/", {
        method: "GET", mode: "cors", credentials: "omit", cache: "no-store",
      });
      if (!reply.ok || (await reply.json())?.features?.mobile_cart !== true) {
        message("Secure account basket synchronization is not available yet.");
        return false;
      }
    } catch (_) {
      message("Cannot check KOFAD account sync right now. Your device basket is safe.");
      return false;
    }
    return true;
  }
  function busy(value) {
    syncing = value;
    for (const id of ["native-basket-account-load", "native-basket-account-save"]) {
      const button = $(id);
      if (button) button.disabled = value;
    }
  }
  async function loadAccount() {
    if (syncing || !await accountReady()) return;
    busy(true);
    message("Loading your verified KOFAD account basket…");
    try {
      const data = await window.KofadMobileAuth.readMobile("cart/");
      if (!data || data.error || !Array.isArray(data.items)) {
        message("Account basket could not be loaded. Your device basket is unchanged.");
        return;
      }
      const combined = merge(data.items);
      if (!combined) {
        message("Both baskets exceed 40 different products. Remove items before merging.");
        return;
      }
      rows = combined;
      persist();
      render();
      estimate(data);
      message("Account items loaded and merged on this device. Choose 'Merge & save' to synchronize both devices.");
    } finally { busy(false); }
  }
  async function syncAccount() {
    if (syncing || !await accountReady()) return;
    busy(true);
    message("Checking your saved account basket…");
    try {
      const remote = await window.KofadMobileAuth.readMobile("cart/");
      if (!remote || remote.error || !Array.isArray(remote.items)) {
        message("The saved account basket is unavailable. Nothing was changed.");
        return;
      }
      const combined = merge(remote.items);
      if (!combined) {
        message("Too many different products to synchronize. Remove items before trying again.");
        return;
      }
      const saved = await window.KofadMobileAuth.saveMobileCart(
        combined.map(item => ({id:item.id, quantity:item.quantity})),
      );
      if (!saved || saved.error || !Array.isArray(saved.items)) {
        message(saved?.error === "listing_unavailable"
          ? "A product is no longer available. Remove it and try again."
          : "KOFAD could not save the basket. Your device basket was not deleted.");
        return;
      }
      rows = normalize(saved.items);
      persist();
      render();
      estimate(saved);
      message("Basket synchronized with your KOFAD account. No order or payment has been placed.");
    } finally { busy(false); }
  }
  $("native-basket-account-load")?.addEventListener("click",()=>void loadAccount());
  $("native-basket-account-save")?.addEventListener("click",()=>void syncAccount());

  $("native-basket-clear")?.addEventListener("click", () => {
    rows = [];
    persist();
    render();
  });
  $("native-basket-secure-site")?.addEventListener("click", () =>
    window.KofadNativeBridge?.openOfficial("/market/")
  );
  persist();
  render();
  window.KofadNativeBasket = Object.freeze({addProduct, render, count});
})();
