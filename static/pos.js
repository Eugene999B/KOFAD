(() => {
  "use strict";
  const root = document.querySelector("#pos");
  if (!root) return;
  let catalog = JSON.parse(document.querySelector("#catalog-data").textContent);
  const paymentMethods = JSON.parse(document.querySelector("#payment-methods-data").textContent);
  const allowDiscounts = root.dataset.allowDiscounts === "true";
  const allowPriceOverrides = root.dataset.allowPriceOverrides === "true";
  const maxDiscount = Number(root.dataset.maxDiscount || 0);
  const csrf = document.querySelector('[name="csrfmiddlewaretoken"]').value;
  const cart = [];
  let requestKey = root.dataset.key;
  let pendingBody = null;
  let heldId = null;
  let completed = false;
  const storageKey = "kofad-cart:"+root.dataset.user+":"+root.dataset.branch+":"+root.dataset.kind;
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null");
    if (saved && Array.isArray(saved.cart) && saved.cart.length <= 100) {
      cart.push(...saved.cart);
      cart.forEach(line => {
        if (line.listPrice === undefined) line.listPrice = line.price;
        if (line.discount === undefined) line.discount = "0";
      });
      requestKey = saved.requestKey || requestKey;
      pendingBody = saved.pendingBody || null; heldId = saved.heldId || null;
    }
  } catch (_) { /* Storage may be disabled; server idempotency still applies. */ }
  function persist() {
    try { sessionStorage.setItem(storageKey,JSON.stringify({cart,requestKey,pendingBody,heldId})); } catch (_) {}
  }
  function clearStored() { try { sessionStorage.removeItem(storageKey); } catch (_) {} }
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
  const percentBasisPoints = value => {
    const v = String(value ?? "0");
    if (!/^\d+(\.\d{1,2})?$/.test(v)) throw new Error("Enter a percentage from 0 to 100.");
    const [whole, fraction = ""] = v.split(".");
    const points = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
    if (!Number.isSafeInteger(points) || points < 0 || points > 10000) throw new Error("Enter a percentage from 0 to 100.");
    return points;
  };
  const effectiveUnit = line => {
    const price = cents(line.price);
    const discount = percentBasisPoints(line.discount || "0");
    return Math.round(price * (10000 - discount) / 10000);
  };
  const fail = msg => { errorBox.textContent = msg; errorBox.classList.remove("hidden"); errorBox.scrollIntoView({block:"nearest"}); };
  const changed = () => { if (pendingBody) { pendingBody = null; requestKey = crypto.randomUUID(); } };
  function total() { return cart.reduce((sum, line) => sum + effectiveUnit(line) * line.quantity, 0); }
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
      if (purchase || allowPriceOverrides) {
        const priceWrap = el("label", undefined, "cart-control-field");
        priceWrap.append(el("small", purchase ? "Purchase price" : "Selling price"));
        const price = el("input"); price.type = "number"; price.min = "0"; price.step = ".01"; price.value = line.price;
        price.setAttribute("aria-label", (purchase ? "Purchase price for " : "Selling price for ") + line.name);
        price.addEventListener("change", () => {
          try {
            cents(price.value);
            if (!purchase && line.discount && Number(line.discount) > 0 && price.value !== line.listPrice) {
              throw new Error("Use either a custom selling price or a discount on a line, not both.");
            }
            changed(); line.price = price.value; render();
          } catch(e) { price.value = line.price; fail(e.message); }
        });
        priceWrap.append(price); controls.append(priceWrap);
      }
      if (!purchase && allowDiscounts) {
        const discountWrap = el("label", undefined, "cart-control-field");
        discountWrap.append(el("small", "Discount %"));
        const discount = el("input"); discount.type = "number"; discount.min = "0"; discount.max = String(maxDiscount); discount.step = ".01"; discount.value = line.discount || "0";
        discount.setAttribute("aria-label", "Discount percent for " + line.name);
        discount.addEventListener("change", () => {
          try {
            const points = percentBasisPoints(discount.value);
            if (points > Math.round(maxDiscount * 100)) throw new Error("Discount exceeds the maximum configured in Settings.");
            if (points > 0 && line.price !== line.listPrice) throw new Error("Use either a custom selling price or a discount on a line, not both.");
            changed(); line.discount = discount.value; render();
          } catch(e) { discount.value = line.discount || "0"; fail(e.message); }
        });
        discountWrap.append(discount); controls.append(discountWrap);
      }
      controls.append(el("strong", formatted(effectiveUnit(line) * line.quantity)));
      row.append(controls); container.append(row);
    });
    document.querySelector("#total").textContent = formatted(total());
    document.querySelector("#cart-count").textContent = cart.length + " lines";
    document.querySelector("#mobile-cart-count").textContent = cart.length;
    document.querySelector("#mobile-cart-total").textContent = formatted(total());
    persist();
  }
  document.querySelector("#cart-jump").addEventListener("click", () => {
    const panel = document.querySelector("#checkout-panel");
    panel.scrollIntoView({block:"start"}); panel.focus({preventScroll:true});
  });
  const productGrid = document.querySelector("#catalog");
  function renderCatalog() {
  productGrid.replaceChildren();
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
      else cart.push({product:product.id, name:product.name, mode, quantity:1, factor: mode.endsWith("pack") ? product.pack_size : 1, price:prices[mode], listPrice:prices[mode], discount:"0"});
      render();
    });
    card.append(select, add); productGrid.append(card);
  });
  if (!catalog.length) productGrid.append(el("div", "No matching products. Add products in Inventory or refine your search.", "empty"));
  }
  renderCatalog();
  document.querySelector("#catalog-search").addEventListener("submit",async event => {
    event.preventDefault();
    if (pendingBody) return fail("Resolve the pending checkout before searching.");
    try {
      const q = document.querySelector("#product-query").value;
      const response = await fetch(location.pathname+"?format=json&q="+encodeURIComponent(q));
      if (!response.ok || !(response.headers.get("Content-Type") || "").includes("application/json")) throw new Error("Search failed. Check your connection or sign in again.");
      catalog = (await response.json()).catalog;
      renderCatalog();
    } catch(error) { fail(error.message); }
  });
  document.querySelector("#exact-cash")?.addEventListener("click", () => {
    changed();
    paymentMethods.filter(m => m !== "cash").forEach(m => {
      const input = document.querySelector("#pay-" + m);
      if (input) input.value = "0";
    });
    const cash = document.querySelector("#pay-cash");
    if (cash) cash.value = formatted(total());
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
    root.querySelectorAll("input,select,textarea,button").forEach(control => control.disabled = true);
    errorBox.classList.add("hidden");
    try {
      if (!pendingBody) pendingBody = {
        kind:root.dataset.kind,
        items:cart.map(({product,mode,quantity,price,discount})=>({
          product, mode, quantity,
          ...(purchase || allowPriceOverrides ? {price} : {}),
          ...(!purchase && allowDiscounts ? {discount: discount || "0"} : {})
        })),
        party:document.querySelector("#party").value || null,
        due_date:document.querySelector("#due-date").value,
        override_reason:document.querySelector("#override-reason")?.value || "",
        payments:paymentMethods.map(method=>({method, amount:document.querySelector("#pay-"+method)?.value || "0"}))
      };
      persist();
      const result = await api("/api/trades/", pendingBody, requestKey);
      if (heldId) {
        try { await api("/api/held/" + heldId + "/", {}); } catch (_) { /* Posted sale remains valid if held-cart cleanup fails. */ }
      }
      completed = true;
      clearStored();
      location.href = result.url;
    } catch (error) {
      if (error.rejected) {
        pendingBody = null; requestKey = crypto.randomUUID(); persist();
        root.querySelectorAll("input,select,textarea,button").forEach(control => control.disabled = false);
      }
      fail(error.message + (pendingBody ? " Retry this unchanged request to recover the same transaction. Editing is locked until its outcome is known." : ""));
    }
    finally { if (!completed) button.disabled = false; }
  });
  document.querySelector("#hold")?.addEventListener("click", async () => {
    if (!cart.length) return fail("Add a product before holding.");
    if (pendingBody) return fail("Resolve the pending checkout before holding this cart.");
    try { await api("/api/held/", {label:"Counter sale", items:cart}); if (heldId) await api("/api/held/"+heldId+"/", {}); completed = true; clearStored(); location.reload(); } catch(e) { fail(e.message); }
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
        const standard = product.prices[line.mode];
        return {
          ...line,
          name:product.name,
          price:allowPriceOverrides && line.price !== undefined ? line.price : standard,
          listPrice:standard,
          discount:allowDiscounts ? (line.discount || "0") : "0",
          factor:line.mode.endsWith("pack") ? product.pack_size : 1
        };
      });
      cart.push(...refreshed); heldId = button.dataset.id; render();
    } catch(e) { fail(e.message); }
  }));
  window.addEventListener("beforeunload", e => { if (cart.length && !completed) { e.preventDefault(); e.returnValue = ""; } });
  render();
  if (pendingBody) {
    document.querySelector("#party").value = pendingBody.party || "";
    document.querySelector("#due-date").value = pendingBody.due_date || "";
    pendingBody.payments.forEach(payment => {
      const input = document.querySelector("#pay-"+payment.method);
      if (input) input.value = payment.amount;
    });
    const reason = document.querySelector("#override-reason");
    if (reason) reason.value = pendingBody.override_reason || "";
    root.querySelectorAll("input,select,textarea,button").forEach(control => control.disabled = true);
    document.querySelector("#complete").disabled = false;
    fail("A checkout was interrupted. Retry to recover its original result before making changes.");
  }
})();