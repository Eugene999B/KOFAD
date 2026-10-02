(() => {
  "use strict";
  const root = document.querySelector("#pos");
  if (!root) return;
  const catalog = JSON.parse(document.querySelector("#catalog-data").textContent);
  const csrf = document.querySelector('[name="csrfmiddlewaretoken"]').value;
  const cart = [];
  let requestKey = root.dataset.key;
  let pendingBody = null;
  let heldId = null;
  let completed = false;
  const purchase = root.dataset.kind === "purchase";
  const errorBox = document.querySelector("#pos-error");
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
  const cents = value => {
    const v = String(value);
    if (!/^\d+(\.\d{1,2})?$/.test(v)) throw new Error("Enter a nonnegative amount with at most two decimal places.");
    const [whole, fraction = ""] = v.split(".");
    const amount = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
    if (!Number.isSafeInteger(amount)) throw new Error("Amount is too large.");
    return amount;
  };
  const formatted = value => (value / 100).toFixed(2);
  const fail = msg => { errorBox.textContent = msg; errorBox.classList.remove("hidden"); errorBox.scrollIntoView({block:"nearest"}); };
  const changed = () => { if (pendingBody) { pendingBody = null; requestKey = crypto.randomUUID(); } };
  function total() { return cart.reduce((sum, line) => sum + cents(line.price) * line.quantity, 0); }
  function render() {
    const container = document.querySelector("#cart");
    container.replaceChildren();
    if (!cart.length) { container.append(el("div", "Choose a product to start.", "empty cart-empty")); }
    cart.forEach((line, index) => {
      const row = el("div", undefined, "cart-line");
      const top = el("div", undefined, "section-heading");
      top.append(el("strong", line.name));
      const remove = el("button", "×", "text-button"); remove.type = "button";
      remove.setAttribute("aria-label", "Remove " + line.name);
      remove.addEventListener("click", () => { changed(); cart.splice(index, 1); render(); });
      top.append(remove);
      row.append(top, el("small", line.mode.replaceAll("_", " ") + " · " + line.factor + " base units each"));
      const controls = el("div", undefined, "cart-controls");
      const quantity = el("input"); quantity.type = "number"; quantity.min = "1"; quantity.max = "1000000"; quantity.step = "1"; quantity.value = line.quantity;
      quantity.setAttribute("aria-label", "Quantity for " + line.name);
      quantity.addEventListener("change", () => {
        const n = Number(quantity.value);
        if (!Number.isInteger(n) || n < 1 || n > 1000000) { quantity.value = line.quantity; return; }
        changed(); line.quantity = n; render();
      });
      controls.append(quantity);
      if (purchase) {
        const price = el("input"); price.type = "number"; price.min = "0"; price.step = ".01"; price.value = line.price;
        price.setAttribute("aria-label", "Purchase price for " + line.name);
        price.addEventListener("change", () => { try { cents(price.value); changed(); line.price = price.value; render(); } catch(e) { fail(e.message); } });
        controls.append(price);
      }
      controls.append(el("strong", formatted(cents(line.price) * line.quantity)));
      row.append(controls); container.append(row);
    });
    document.querySelector("#total").textContent = formatted(total());
    document.querySelector("#cart-count").textContent = cart.length + " lines";
  }
  const productGrid = document.querySelector("#catalog");
  catalog.forEach(product => {
    const card = el("article", undefined, "product-card");
    const badge = el("div", (product.category || product.base_unit).slice(0, 2).toUpperCase(), "product-symbol");
    card.append(badge, el("small", product.sku, "muted"), el("h3", product.name));
    card.append(el("p", product.stock + " " + product.base_unit + " in stock", product.stock ? "muted" : "danger"));
    const select = el("select");
    select.setAttribute("aria-label", "Selling mode for " + product.name);
    const prices = purchase ? {retail_unit: product.cost, retail_pack: formatted(cents(product.cost) * product.pack_size)} : product.prices;
    Object.entries(prices).forEach(([mode, price]) => {
      const option = el("option", (purchase ? (mode.endsWith("pack") ? product.pack_name : product.base_unit) : mode.replaceAll("_", " ")) + " · " + price);
      option.value = mode; select.append(option);
    });
    const add = el("button", "＋ Add", "button secondary");
    add.addEventListener("click", () => {
      changed(); const mode = select.value; const found = cart.find(l => l.product === product.id && l.mode === mode);
      if (found) found.quantity += 1;
      else cart.push({product:product.id, name:product.name, mode, quantity:1, factor: mode.endsWith("pack") ? product.pack_size : 1, price:prices[mode]});
      render();
    });
    card.append(select, add); productGrid.append(card);
  });
  if (!catalog.length) productGrid.append(el("div", "No matching products. Add products in Inventory or refine your search.", "empty"));
  document.querySelector("#exact-cash").addEventListener("click", () => {
    changed();
    ["momo", "bank", "card"].forEach(m => document.querySelector("#pay-" + m).value = "0");
    document.querySelector("#pay-cash").value = formatted(total());
  });
  document.querySelector("#checkout").addEventListener("change", changed);
  async function api(path, body, key) {
    const response = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json", "X-CSRFToken":csrf, ...(key ? {"Idempotency-Key":key} : {})}, body:JSON.stringify(body)});
    const content = response.headers.get("Content-Type") || "";
    if (!content.includes("application/json")) throw new Error("Your session may have expired. Sign in again before retrying.");
    const result = await response.json();
    if (!response.ok) { const error = new Error(result.error || "Request rejected. Check your permissions."); error.rejected = response.status >= 400 && response.status < 500; throw error; }
    return result;
  }
  document.querySelector("#checkout").addEventListener("submit", async e => {
    e.preventDefault();
    if (!cart.length) return fail("Add a product first.");
    const button = document.querySelector("#complete");
    root.querySelectorAll("input,select,button").forEach(control => control.disabled = true);
    errorBox.classList.add("hidden");
    try {
      if (!pendingBody) pendingBody = {kind:root.dataset.kind, items:cart.map(({product,mode,quantity,price})=>({product,mode,quantity,...(purchase?{price}:{})})),
        party:document.querySelector("#party").value || null, due_date:document.querySelector("#due-date").value,
        payments:["cash","momo","bank","card"].map(method=>({method, amount:document.querySelector("#pay-"+method).value || "0"}))};
      const result = await api("/api/trades/", pendingBody, requestKey);
      if (heldId) {
        try { await api("/api/held/" + heldId + "/", {}); } catch (_) { /* Posted sale remains valid if held-cart cleanup fails. */ }
      }
      completed = true;
      location.href = result.url;
    } catch (error) {
      if (error.rejected) {
        pendingBody = null; requestKey = crypto.randomUUID();
        root.querySelectorAll("input,select,button").forEach(control => control.disabled = false);
      }
      fail(error.message + (pendingBody ? " Retry this unchanged request to recover the same transaction. Editing is locked until its outcome is known." : ""));
    }
    finally { if (!completed) button.disabled = false; }
  });
  document.querySelector("#hold")?.addEventListener("click", async () => {
    if (!cart.length) return fail("Add a product before holding.");
    if (pendingBody) return fail("Resolve the pending checkout before holding this cart.");
    try { await api("/api/held/", {label:"Counter sale", items:cart}); location.reload(); } catch(e) { fail(e.message); }
  });
  document.querySelectorAll(".held-item").forEach(button => button.addEventListener("click", async () => {
    if (cart.length || pendingBody) return fail("Complete or hold the current cart before resuming another.");
    try {
      const response = await fetch("/api/held/" + button.dataset.id + "/");
      if (!response.ok) throw new Error("Cannot load held sale.");
      const saved = await response.json();
      const refreshed = saved.items.map(line => {
        const product = catalog.find(p => p.id === line.product);
        if (!product || !(line.mode in product.prices)) throw new Error("A held product is unavailable. Clear the catalog search or check its selling modes.");
        return {...line, name:product.name, price:product.prices[line.mode], factor:line.mode.endsWith("pack") ? product.pack_size : 1};
      });
      cart.push(...refreshed); heldId = button.dataset.id; render();
    } catch(e) { fail(e.message); }
  }));
  window.addEventListener("beforeunload", e => { if (cart.length && !completed) { e.preventDefault(); e.returnValue = ""; } });
  render();
})();