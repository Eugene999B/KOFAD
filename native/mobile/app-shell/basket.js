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
