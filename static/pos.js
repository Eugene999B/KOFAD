(() => {
  "use strict";
  const root = document.querySelector("#pos");
  if (!root) return;

  let catalog = JSON.parse(document.querySelector("#catalog-data").textContent);
  const paymentMethods = JSON.parse(document.querySelector("#payment-methods-data").textContent);
  const purchase = root.dataset.kind === "purchase";
  const allowDiscounts = root.dataset.allowDiscounts === "true";
  const allowPriceOverrides = root.dataset.allowPriceOverrides === "true";
  const allowCredit = root.dataset.allowCredit === "true";
  const maxDiscount = Number(root.dataset.maxDiscount || 0);
  const csrf = document.querySelector('[name="csrfmiddlewaretoken"]').value;
  const cart = [];
  let requestKey = root.dataset.key;
  let pendingBody = null;
  let heldId = null;
  let completed = false;
  let selectedCustomer = null;
  let newCustomerMode = false;
  let restoredState = null;

  const storageKey = "kofad-cart:" + root.dataset.user + ":" + root.dataset.branch + ":" + root.dataset.kind;
  try {
    restoredState = JSON.parse(sessionStorage.getItem(storageKey) || "null");
    if (restoredState && Array.isArray(restoredState.cart) && restoredState.cart.length <= 100) {
      cart.push(...restoredState.cart);
      cart.forEach(line => {
        if (line.listPrice === undefined) line.listPrice = line.price;
        if (line.discount === undefined) line.discount = "0";
      });
      requestKey = restoredState.requestKey || requestKey;
      pendingBody = restoredState.pendingBody || null;
      heldId = restoredState.heldId || null;
      selectedCustomer = restoredState.selectedCustomer || null;
      newCustomerMode = Boolean(restoredState.newCustomerMode);
    }
  } catch (_) { restoredState = null; }

  const errorBox = document.querySelector("#pos-error");
  const partyInput = document.querySelector("#party");
  const customerSearch = document.querySelector("#customer-search");
  const customerResults = document.querySelector("#customer-results");
  const selectedCustomerBox = document.querySelector("#selected-customer");
  const newCustomerFields = document.querySelector("#new-customer-fields");
  const newCustomerToggle = document.querySelector("#new-customer-toggle");
  const clearCustomerButton = document.querySelector("#clear-customer");
  const customerName = document.querySelector("#customer-name");
  const customerPhone = document.querySelector("#customer-phone");
  const paymentPlan = document.querySelector("#payment-plan");
  const creditFields = document.querySelector("#credit-fields");
  const dueDate = document.querySelector("#due-date");

  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const cents = value => {
    const v = String(value ?? "0").trim() || "0";
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
  const fail = msg => {
    errorBox.textContent = msg;
    errorBox.classList.remove("hidden");
    errorBox.scrollIntoView({block: "nearest"});
  };
  const clearError = () => errorBox.classList.add("hidden");
  const changed = () => {
    if (pendingBody) {
      pendingBody = null;
      requestKey = crypto.randomUUID();
    }
  };
  function total() {
    return cart.reduce((sum, line) => sum + effectiveUnit(line) * line.quantity, 0);
  }
  function paymentTotal() {
    return paymentMethods.reduce((sum, method) => sum + cents(document.querySelector("#pay-" + method)?.value || "0"), 0);
  }
  function normalizeGhanaPhone(value) {
    let digits = String(value || "").replace(/\D/g, "");
    if (digits.startsWith("233")) digits = digits.slice(3);
    else if (digits.startsWith("0")) digits = digits.slice(1);
    if (digits.length !== 9) throw new Error("Enter 9 Ghana digits after +233, or a 10-digit local number beginning with 0.");
    return "+233" + digits;
  }
  function stockLabel(product) {
    if (product.pack_size <= 1) return product.stock + " " + product.base_unit;
    const packs = Math.floor(product.stock / product.pack_size);
    const loose = product.stock % product.pack_size;
    const equivalent = (product.stock / product.pack_size).toFixed(2).replace(/\.00$/, "");
    return loose
      ? packs + " " + product.pack_name + " + " + loose + " " + product.base_unit + " (" + equivalent + " " + product.pack_name + " equivalent)"
      : packs + " " + product.pack_name + " (" + product.stock + " " + product.base_unit + ")";
  }
  function modeLabel(line) {
    const tier = line.mode.startsWith("wholesale") ? "Wholesale" : purchase ? "Purchase" : "Retail";
    const unit = line.mode.endsWith("pack") ? (line.packName || "pack") : (line.baseUnit || "unit");
    return tier + " · " + unit;
  }
  function persist() {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({
        cart, requestKey, pendingBody, heldId, selectedCustomer, newCustomerMode,
        customerName: customerName?.value || "",
        customerPhone: customerPhone?.value || "",
        paymentPlan: paymentPlan?.value || "",
        dueDate: dueDate?.value || ""
      }));
    } catch (_) {}
  }
  function clearStored() {
    try { sessionStorage.removeItem(storageKey); } catch (_) {}
  }

  function render() {
    const container = document.querySelector("#cart");
    container.replaceChildren();
    if (!cart.length) container.append(el("div", "Choose a product to start.", "empty cart-empty"));
    cart.forEach((line, index) => {
      const row = el("div", undefined, "cart-line");
      const top = el("div", undefined, "section-heading");
      top.append(el("strong", line.name));
      const remove = el("button", "×", "text-button");
      remove.type = "button";
      remove.setAttribute("aria-label", "Remove " + line.name);
      remove.addEventListener("click", () => {
        changed();
        cart.splice(index, 1);
        render();
      });
      top.append(remove);
      row.append(
        top,
        el("small", modeLabel(line) + " · " + line.factor + " base unit" + (line.factor === 1 ? "" : "s") + " each", "muted")
      );
      const controls = el("div", undefined, "cart-controls");
      const quantity = el("input");
      quantity.type = "number";
      quantity.min = "1";
      quantity.max = "1000000";
      quantity.step = "1";
      quantity.value = line.quantity;
      quantity.setAttribute("aria-label", "Quantity for " + line.name);
      quantity.addEventListener("change", () => {
        const n = Number(quantity.value);
        if (!Number.isInteger(n) || n < 1 || n > 1000000) {
          quantity.value = line.quantity;
          return;
        }
        changed();
        line.quantity = n;
        render();
      });
      controls.append(quantity);

      if (purchase || allowPriceOverrides) {
        const priceWrap = el("label", undefined, "cart-control-field");
        priceWrap.append(el("small", purchase ? "Purchase price" : "Selling price"));
        const price = el("input");
        price.type = "number";
        price.min = "0";
        price.step = ".01";
        price.value = line.price;
        price.setAttribute("aria-label", (purchase ? "Purchase price for " : "Selling price for ") + line.name);
        price.addEventListener("change", () => {
          try {
            cents(price.value);
            if (!purchase && line.discount && Number(line.discount) > 0 && cents(price.value) !== cents(line.listPrice)) {
              throw new Error("Use either a custom selling price or a discount on a line, not both.");
            }
            changed();
            line.price = price.value;
            render();
          } catch (error) {
            price.value = line.price;
            fail(error.message);
          }
        });
        priceWrap.append(price);
        controls.append(priceWrap);
      }

      if (!purchase && allowDiscounts) {
        const discountWrap = el("label", undefined, "cart-control-field");
        discountWrap.append(el("small", "Discount %"));
        const discount = el("input");
        discount.type = "number";
        discount.min = "0";
        discount.max = String(maxDiscount);
        discount.step = ".01";
        discount.value = line.discount || "0";
        discount.setAttribute("aria-label", "Discount percent for " + line.name);
        discount.addEventListener("change", () => {
          try {
            const points = percentBasisPoints(discount.value);
            if (points > Math.round(maxDiscount * 100)) throw new Error("Discount exceeds the maximum configured in Settings.");
            if (points > 0 && cents(line.price) !== cents(line.listPrice)) throw new Error("Use either a custom selling price or a discount on a line, not both.");
            changed();
            line.discount = discount.value;
            render();
          } catch (error) {
            discount.value = line.discount || "0";
            fail(error.message);
          }
        });
        discountWrap.append(discount);
        controls.append(discountWrap);
      }
      controls.append(el("strong", formatted(effectiveUnit(line) * line.quantity)));
      row.append(controls);
      container.append(row);
    });
    document.querySelector("#total").textContent = formatted(total());
    document.querySelector("#cart-count").textContent = cart.length + " lines";
    document.querySelector("#mobile-cart-count").textContent = cart.length;
    document.querySelector("#mobile-cart-total").textContent = formatted(total());
    persist();
  }

  function addCartLine(product, mode, quantity, price) {
    if (!quantity) return;
    const factor = mode.endsWith("pack") ? product.pack_size : 1;
    const existing = cart.find(line =>
      line.product === product.id &&
      line.mode === mode &&
      cents(line.price) === cents(price) &&
      (!line.discount || Number(line.discount) === 0)
    );
    if (existing) existing.quantity += quantity;
    else cart.push({
      product: product.id,
      name: product.name,
      mode,
      quantity,
      factor,
      price,
      listPrice: price,
      discount: "0",
      packName: product.pack_name,
      baseUnit: product.base_unit
    });
  }

  const productGrid = document.querySelector("#catalog");
  function renderCatalog() {
    productGrid.replaceChildren();
    catalog.forEach(product => {
      const card = el("article", undefined, "product-card");
      card.append(
        el("div", (product.category || product.base_unit).slice(0, 2).toUpperCase(), "product-symbol"),
        el("small", product.sku, "muted"),
        el("h3", product.name),
        el("p", stockLabel(product) + " available", product.stock ? "muted" : "danger")
      );

      const prices = purchase
        ? {
            retail_unit: product.cost,
            retail_pack: formatted(cents(product.cost) * product.pack_size)
          }
        : product.prices;

      const tiers = purchase
        ? [{value: "purchase", label: "Purchase"}]
        : [
            ...((prices.retail_unit !== undefined || prices.retail_pack !== undefined) ? [{value: "retail", label: "Retail"}] : []),
            ...((prices.wholesale_unit !== undefined || prices.wholesale_pack !== undefined) ? [{value: "wholesale", label: "Wholesale"}] : [])
          ];

      if (!tiers.length) {
        card.append(el("p", "No selling price configured.", "danger"));
        productGrid.append(card);
        return;
      }

      const tierSelect = el("select");
      tierSelect.setAttribute("aria-label", "Retail or wholesale for " + product.name);
      tiers.forEach(tier => {
        const option = el("option", tier.label);
        option.value = tier.value;
        tierSelect.append(option);
      });

      const quantityArea = el("div", undefined, "pack-quantity-grid");
      const addButton = el("button", "＋ Add to sale", "button secondary");
      addButton.type = "button";

      function drawQuantityControls() {
        quantityArea.replaceChildren();
        const tier = tierSelect.value;
        const unitMode = purchase ? "retail_unit" : tier + "_unit";
        const packMode = purchase ? "retail_pack" : tier + "_pack";
        const unitPrice = prices[unitMode];
        const packPrice = prices[packMode];

        let packInput = null;
        let looseInput = null;

        if (product.pack_size > 1 && packPrice !== undefined) {
          const wrap = el("label", undefined, "cart-control-field");
          wrap.append(el("small", "Full " + product.pack_name + "s · " + packPrice));
          packInput = el("input");
          packInput.type = "number";
          packInput.min = "0";
          packInput.step = "1";
          packInput.value = "0";
          packInput.setAttribute("aria-label", "Full " + product.pack_name + " quantity for " + product.name);
          wrap.append(packInput);
          quantityArea.append(wrap);
        }

        if (unitPrice !== undefined) {
          const wrap = el("label", undefined, "cart-control-field");
          wrap.append(el("small", (product.pack_size > 1 ? "Loose " : "") + product.base_unit + "s · " + unitPrice));
          looseInput = el("input");
          looseInput.type = "number";
          looseInput.min = "0";
          looseInput.step = "1";
          looseInput.value = "0";
          if (product.pack_size > 1) looseInput.max = String(product.pack_size - 1);
          looseInput.setAttribute("aria-label", "Loose unit quantity for " + product.name);
          wrap.append(looseInput);
          quantityArea.append(wrap);
        }

        if (product.pack_size === 1 && looseInput) looseInput.value = "1";
        else if (packInput && !looseInput) packInput.value = "1";
        else if (looseInput && !packInput) looseInput.value = "1";

        addButton.onclick = () => {
          try {
            clearError();
            const packs = packInput ? Number(packInput.value || 0) : 0;
            const loose = looseInput ? Number(looseInput.value || 0) : 0;
            if (!Number.isInteger(packs) || packs < 0 || !Number.isInteger(loose) || loose < 0) {
              throw new Error("Pack and loose quantities must be whole numbers.");
            }
            if (product.pack_size > 1 && loose >= product.pack_size) {
              throw new Error("Loose units must be less than one full " + product.pack_name + ". Put the extra quantity into full packs.");
            }
            if (packs === 0 && loose === 0) throw new Error("Enter at least one pack or loose unit.");

            const requested = packs * product.pack_size + loose;
            const alreadyInCart = cart.filter(line => line.product === product.id)
              .reduce((sum, line) => sum + line.quantity * line.factor, 0);
            if (!purchase && requested + alreadyInCart > product.stock) {
              throw new Error("Not enough sellable stock. Available: " + stockLabel(product) + ".");
            }

            changed();
            if (packs) addCartLine(product, packMode, packs, packPrice);
            if (loose) addCartLine(product, unitMode, loose, unitPrice);
            render();
            if (packInput) packInput.value = "0";
            if (looseInput) looseInput.value = product.pack_size === 1 ? "1" : "0";
          } catch (error) {
            fail(error.message);
          }
        };
      }

      tierSelect.addEventListener("change", drawQuantityControls);
      drawQuantityControls();
      card.append(tierSelect, quantityArea, addButton);
      productGrid.append(card);
    });

    if (!catalog.length) {
      productGrid.append(el("div", "No matching products. Add products in Inventory or refine your search.", "empty"));
    }
  }

  function showSelectedCustomer(customer) {
    selectedCustomer = customer;
    newCustomerMode = false;
    if (partyInput) partyInput.value = customer.id;
    selectedCustomerBox?.classList.remove("hidden");
    if (selectedCustomerBox) {
      selectedCustomerBox.replaceChildren();
      selectedCustomerBox.append(
        el("strong", customer.name),
        el("small", customer.phone + " · Outstanding " + root.dataset.currency + " " + customer.outstanding, "muted")
      );
    }
    customerResults?.replaceChildren();
    newCustomerFields?.classList.add("hidden");
    newCustomerToggle?.classList.add("hidden");
    clearCustomerButton?.classList.remove("hidden");
    if (customerSearch) customerSearch.value = "";
    changed();
    persist();
  }

  function clearCustomer() {
    selectedCustomer = null;
    newCustomerMode = false;
    if (partyInput) partyInput.value = "";
    selectedCustomerBox?.classList.add("hidden");
    selectedCustomerBox?.replaceChildren();
    customerResults?.replaceChildren();
    newCustomerFields?.classList.add("hidden");
    newCustomerToggle?.classList.remove("hidden");
    clearCustomerButton?.classList.add("hidden");
    changed();
    persist();
  }

  function beginNewCustomer() {
    clearCustomer();
    newCustomerMode = true;
    newCustomerFields?.classList.remove("hidden");
    newCustomerToggle?.classList.add("hidden");
    clearCustomerButton?.classList.remove("hidden");
    customerName?.focus();
    persist();
  }

  let customerTimer = null;
  customerSearch?.addEventListener("input", () => {
    clearTimeout(customerTimer);
    const q = customerSearch.value.trim();
    customerResults.replaceChildren();
    if (q.length < 2) return;
    customerTimer = setTimeout(async () => {
      try {
        const response = await fetch("/api/customers/?q=" + encodeURIComponent(q));
        if (!response.ok) throw new Error("Customer search failed.");
        const data = await response.json();
        customerResults.replaceChildren();
        data.customers.forEach(customer => {
          const button = el("button", undefined, "customer-result");
          button.type = "button";
          button.setAttribute("role", "option");
          button.append(
            el("strong", customer.name),
            el("small", customer.phone + " · " + customer.purchase_count + " purchases · owes " + root.dataset.currency + " " + customer.outstanding, "muted")
          );
          button.addEventListener("click", () => showSelectedCustomer(customer));
          customerResults.append(button);
        });
        if (!data.customers.length) {
          customerResults.append(el("div", "No saved customer found. Use “Enter new customer”.", "empty"));
        }
      } catch (error) {
        fail(error.message);
      }
    }, 180);
  });
  newCustomerToggle?.addEventListener("click", beginNewCustomer);
  clearCustomerButton?.addEventListener("click", clearCustomer);

  function applyPaymentPlan() {
    if (purchase || !paymentPlan) return;
    const plan = paymentPlan.value;
    const inputs = paymentMethods.map(method => document.querySelector("#pay-" + method)).filter(Boolean);
    if (plan === "credit") {
      inputs.forEach(input => {
        input.value = "0";
        input.disabled = true;
      });
      creditFields?.classList.remove("hidden");
    } else {
      inputs.forEach(input => input.disabled = false);
      creditFields?.classList.toggle("hidden", plan === "full");
      if (plan === "full" && dueDate) dueDate.value = "";
    }
    persist();
  }
  paymentPlan?.addEventListener("change", () => {
    changed();
    applyPaymentPlan();
  });

  document.querySelector("#cart-jump").addEventListener("click", () => {
    const panel = document.querySelector("#checkout-panel");
    panel.scrollIntoView({block: "start"});
    panel.focus({preventScroll: true});
  });

  renderCatalog();

  document.querySelector("#catalog-search").addEventListener("submit", async event => {
    event.preventDefault();
    if (pendingBody) return fail("Resolve the pending checkout before searching.");
    try {
      const q = document.querySelector("#product-query").value;
      const response = await fetch(location.pathname + "?format=json&q=" + encodeURIComponent(q));
      if (!response.ok || !(response.headers.get("Content-Type") || "").includes("application/json")) {
        throw new Error("Search failed. Check your connection or sign in again.");
      }
      catalog = (await response.json()).catalog;
      renderCatalog();
    } catch (error) {
      fail(error.message);
    }
  });

  document.querySelector("#exact-cash")?.addEventListener("click", () => {
    changed();
    if (paymentPlan) paymentPlan.value = "full";
    paymentMethods.filter(method => method !== "cash").forEach(method => {
      const input = document.querySelector("#pay-" + method);
      if (input) input.value = "0";
    });
    const cash = document.querySelector("#pay-cash");
    if (cash) {
      cash.disabled = false;
      cash.value = formatted(total());
    }
    applyPaymentPlan();
  });

  document.querySelector("#checkout").addEventListener("change", () => {
    changed();
    persist();
  });

  async function api(path, body, key) {
    const response = await fetch(path, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrf, ...(key ? {"Idempotency-Key": key} : {})},
      body: JSON.stringify(body)
    });
    const contentType = response.headers.get("Content-Type") || "";
    if (!contentType.includes("application/json")) throw new Error("Your session may have expired. Sign in again before retrying.");
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || "Request rejected. Check your permissions.");
      error.rejected = response.status >= 400 && response.status < 500;
      throw error;
    }
    return result;
  }

  function buildCheckoutBody() {
    const paidNow = paymentTotal();
    const grandTotal = total();
    let party = partyInput?.value || null;
    let newName = "";
    let newPhone = "";

    if (!purchase) {
      if (!party) {
        newName = customerName?.value.trim() || "";
        if (!newCustomerMode && !newName) throw new Error("Choose a returning customer or enter a new customer.");
        if (newName.length < 2) throw new Error("Enter the new customer's name.");
        newPhone = normalizeGhanaPhone(customerPhone?.value || "");
      }
      const plan = paymentPlan?.value || "full";
      if (!allowCredit && plan !== "full") throw new Error("Credit sales are disabled by company policy.");
      if (plan === "full" && paidNow !== grandTotal) {
        throw new Error("For “Paid in full”, the channel split must exactly equal the sale total.");
      }
      if (plan === "part" && !(paidNow > 0 && paidNow < grandTotal)) {
        throw new Error("Part payment must be greater than zero and less than the sale total.");
      }
      if (plan === "credit" && paidNow !== 0) {
        throw new Error("Credit / pay later starts with zero payment. Choose Part payment if money is received now.");
      }
      if ((plan === "part" || plan === "credit") && !dueDate?.value) {
        throw new Error("Choose a due date for the unpaid balance.");
      }
    } else if (paidNow < grandTotal && !dueDate?.value) {
      throw new Error("Choose a due date for the unpaid supplier balance.");
    }

    return {
      kind: root.dataset.kind,
      items: cart.map(({product, mode, quantity, price, discount}) => ({
        product, mode, quantity,
        ...(purchase || allowPriceOverrides ? {price} : {}),
        ...(!purchase && allowDiscounts ? {discount: discount || "0"} : {})
      })),
      party,
      ...(!purchase && !party ? {customer_name: newName, customer_phone: newPhone} : {}),
      due_date: dueDate?.value || "",
      override_reason: document.querySelector("#override-reason")?.value || "",
      payments: paymentMethods.map(method => ({
        method,
        amount: document.querySelector("#pay-" + method)?.value || "0"
      }))
    };
  }

  document.querySelector("#checkout").addEventListener("submit", async event => {
    event.preventDefault();
    if (!cart.length) return fail("Add a product first.");
    const button = document.querySelector("#complete");
    clearError();
    try {
      if (!pendingBody) pendingBody = buildCheckoutBody();
      persist();
      root.querySelectorAll("input,select,textarea,button").forEach(control => control.disabled = true);
      button.disabled = false;
      const result = await api("/api/trades/", pendingBody, requestKey);
      if (heldId) {
        try { await api("/api/held/" + heldId + "/", {}); } catch (_) {}
      }
      completed = true;
      clearStored();
      location.href = result.url;
    } catch (error) {
      if (error.rejected || !pendingBody) {
        pendingBody = null;
        requestKey = crypto.randomUUID();
        root.querySelectorAll("input,select,textarea,button").forEach(control => control.disabled = false);
        applyPaymentPlan();
        persist();
      }
      fail(error.message + (pendingBody ? " Retry this unchanged request to recover the same transaction. Editing is locked until its outcome is known." : ""));
    } finally {
      if (!completed) button.disabled = false;
    }
  });

  document.querySelector("#hold")?.addEventListener("click", async () => {
    if (!cart.length) return fail("Add a product before holding.");
    if (pendingBody) return fail("Resolve the pending checkout before holding this cart.");
    try {
      const body = {
        label: selectedCustomer?.name || customerName?.value.trim() || "Counter sale",
        items: cart,
        party: partyInput?.value || null,
        customer: selectedCustomer,
        customer_name: customerName?.value.trim() || "",
        customer_phone: customerPhone?.value || "",
        payment_plan: paymentPlan?.value || "full",
        due_date: dueDate?.value || ""
      };
      await api("/api/held/", body);
      if (heldId) await api("/api/held/" + heldId + "/", {});
      completed = true;
      clearStored();
      location.reload();
    } catch (error) {
      fail(error.message);
    }
  });

  document.querySelectorAll(".held-item").forEach(button => button.addEventListener("click", async () => {
    if (cart.length || pendingBody) return fail("Complete or hold the current cart before resuming another.");
    try {
      const response = await fetch("/api/held/" + button.dataset.id + "/");
      if (!response.ok) throw new Error("Cannot load held sale.");
      const saved = await response.json();
      const refreshed = saved.items.map(line => {
        const product = catalog.find(item => item.id === line.product);
        if (!product || !(line.mode in product.prices)) throw new Error("A held product is unavailable. Clear the catalog search or check its selling modes.");
        const standard = product.prices[line.mode];
        return {
          ...line,
          name: product.name,
          price: allowPriceOverrides && line.price !== undefined ? line.price : standard,
          listPrice: standard,
          discount: allowDiscounts ? (line.discount || "0") : "0",
          factor: line.mode.endsWith("pack") ? product.pack_size : 1,
          packName: product.pack_name,
          baseUnit: product.base_unit
        };
      });
      cart.push(...refreshed);
      heldId = button.dataset.id;
      if (saved.customer?.id) showSelectedCustomer(saved.customer);
      else if (saved.customer_name || saved.customer_phone) {
        beginNewCustomer();
        if (customerName) customerName.value = saved.customer_name || "";
        if (customerPhone) customerPhone.value = saved.customer_phone || "";
      }
      if (paymentPlan && saved.payment_plan) paymentPlan.value = saved.payment_plan;
      if (dueDate) dueDate.value = saved.due_date || "";
      applyPaymentPlan();
      render();
    } catch (error) {
      fail(error.message);
    }
  }));

  window.addEventListener("beforeunload", event => {
    if (cart.length && !completed) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  if (selectedCustomer?.id) showSelectedCustomer(selectedCustomer);
  else if (newCustomerMode) beginNewCustomer();
  if (restoredState) {
    if (customerName && restoredState.customerName) customerName.value = restoredState.customerName;
    if (customerPhone && restoredState.customerPhone) customerPhone.value = restoredState.customerPhone;
    if (paymentPlan && restoredState.paymentPlan) paymentPlan.value = restoredState.paymentPlan;
    if (dueDate && restoredState.dueDate) dueDate.value = restoredState.dueDate;
  }
  applyPaymentPlan();
  render();

  if (pendingBody) {
    if (partyInput) partyInput.value = pendingBody.party || "";
    if (pendingBody.customer_name && customerName) {
      beginNewCustomer();
      customerName.value = pendingBody.customer_name;
      customerPhone.value = pendingBody.customer_phone || "";
    }
    if (dueDate) dueDate.value = pendingBody.due_date || "";
    pendingBody.payments.forEach(payment => {
      const input = document.querySelector("#pay-" + payment.method);
      if (input) input.value = payment.amount;
    });
    const reason = document.querySelector("#override-reason");
    if (reason) reason.value = pendingBody.override_reason || "";
    root.querySelectorAll("input,select,textarea,button").forEach(control => control.disabled = true);
    document.querySelector("#complete").disabled = false;
    fail("A checkout was interrupted. Retry to recover its original result before making changes.");
  }
})();