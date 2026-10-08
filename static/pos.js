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
  let selectedSupplier = null;
  let newCustomerMode = false;
  let restoredState = null;
  let hydrating = true;
  let selectedPaymentMethod = paymentMethods.includes("cash") ? "cash" : (paymentMethods[0] || "");
  let lastCompletedSale = null;
  let momoReference = null;
  let momoPollTimer = null;

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
      momoReference = restoredState.momoReference || null;
      selectedCustomer = restoredState.selectedCustomer || null;
      selectedSupplier = restoredState.selectedSupplier || null;
      newCustomerMode = Boolean(restoredState.newCustomerMode);
      if (
        restoredState.selectedPaymentMethod === "split" ||
        paymentMethods.includes(restoredState.selectedPaymentMethod)
      ) {
        selectedPaymentMethod = restoredState.selectedPaymentMethod;
      }
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
  const customerEmail = document.querySelector("#customer-email");
  const supplierSearch = document.querySelector("#supplier-search");
  const supplierResults = document.querySelector("#supplier-results");
  const selectedSupplierBox = document.querySelector("#selected-supplier");
  const clearSupplierButton = document.querySelector("#clear-supplier");
  const purchaseReference = document.querySelector("#purchase-reference");
  const purchaseDocumentDate = document.querySelector("#purchase-document-date");
  const purchaseNote = document.querySelector("#purchase-note");
  const purchaseSourceButtons = [...document.querySelectorAll("[data-purchase-source]")];
  const purchaseNewBuilder = document.querySelector("#purchase-new-builder");
  const purchaseAddNewProduct = document.querySelector("#purchase-add-new-product");
  const paymentPlan = document.querySelector("#payment-plan");
  const creditFields = document.querySelector("#credit-fields");
  const dueDate = document.querySelector("#due-date");
  const customerConsent = document.querySelector("#customer-consent");
  const customerWhatsApp = document.querySelector("#customer-whatsapp");
  const paystackMomoPanel = document.querySelector("#paystack-momo-panel");
  const paystackMomoProvider = document.querySelector("#paystack-momo-provider");
  const paystackMomoPhone = document.querySelector("#paystack-momo-phone");
  const paystackMomoEmail = document.querySelector("#paystack-momo-email");
  const paystackMomoStatus = document.querySelector("#paystack-momo-status");
  const completeButton = document.querySelector("#complete");
  const completeSaleHint = document.querySelector("#complete-sale-hint");
  const paystackMomoReady = root.dataset.paystackPosMomoReady === "true";
  const momoGateway = root.dataset.momoGateway === "hubtel" ? "hubtel" : "paystack";
  const paymentDialog = document.querySelector("#sale-payment-dialog");
  const openPaymentButton = document.querySelector("#open-payment");
  const closePaymentButton = document.querySelector("#close-payment");
  const paymentErrorBox = document.querySelector("#payment-error");
  const paymentMethodPicker = document.querySelector("#payment-method-picker");
  const paymentMethodButtons = [...document.querySelectorAll("[data-payment-method]")];
  const splitPaymentGrid = document.querySelector("#split-payment-grid");
  const singlePaymentWrap = document.querySelector("#single-payment-wrap");
  const singlePaymentValue = document.querySelector("#single-payment-value");
  const heldSalesDialog = document.querySelector("#held-sales-dialog");
  const successDialog = document.querySelector("#sale-success-dialog");
  const receiptSmsButton = document.querySelector("#receipt-sms");
  const successMessageStatus = document.querySelector("#success-message-status");

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
    if (paymentErrorBox && paymentDialog?.open) {
      paymentErrorBox.textContent = msg;
      paymentErrorBox.classList.remove("hidden");
      paymentErrorBox.scrollIntoView({block: "nearest"});
      return;
    }
    errorBox.scrollIntoView({block: "nearest"});
  };
  const clearError = () => {
    errorBox.classList.add("hidden");
    if (paymentErrorBox) paymentErrorBox.classList.add("hidden");
  };
  const changed = () => {
    if (hydrating) return;
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
  function renderCheckoutSummary() {
    const grandTotal = total();
    let paidNow = 0;
    try { paidNow = paymentTotal(); } catch (_) { paidNow = 0; }
    const balance = Math.max(grandTotal - paidNow, 0);
    const due = document.querySelector("#checkout-total-due");
    const paid = document.querySelector("#checkout-paid-now");
    const balanceNode = document.querySelector("#checkout-balance");
    const progress = document.querySelector("#checkout-progress-fill");
    if (due) due.textContent = formatted(grandTotal);
    if (paid) paid.textContent = formatted(paidNow);
    if (balanceNode) balanceNode.textContent = formatted(balance);
    if (progress) {
      const percent = grandTotal > 0 ? Math.min(100, Math.round((paidNow / grandTotal) * 100)) : 0;
      progress.style.width = percent + "%";
    }
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
        cart, requestKey, pendingBody, heldId, momoReference, selectedCustomer, selectedSupplier, newCustomerMode,
        customerName: customerName?.value || "",
        customerPhone: customerPhone?.value || "",
        customerEmail: customerEmail?.value || "",
        paystackMomoProvider: paystackMomoProvider?.value || "",
        paystackMomoPhone: paystackMomoPhone?.value || "",
        paystackMomoEmail: paystackMomoEmail?.value || "",
        paymentPlan: paymentPlan?.value || "",
        dueDate: dueDate?.value || "",
        customerConsent: Boolean(customerConsent?.checked),
        customerWhatsApp: Boolean(customerWhatsApp?.checked),
        purchaseReference: purchaseReference?.value || "",
        purchaseDocumentDate: purchaseDocumentDate?.value || "",
        purchaseNote: purchaseNote?.value || "",
        selectedPaymentMethod
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
    const paymentTotalNode = document.querySelector("#payment-total");
    const dialogTotal = document.querySelector("#payment-dialog-total");
    if (paymentTotalNode) paymentTotalNode.textContent = formatted(total());
    if (dialogTotal) dialogTotal.textContent = formatted(total());
    if (
      singlePaymentValue &&
      selectedPaymentMethod !== "split" &&
      selectedPaymentMethod &&
      (!paymentPlan || paymentPlan.value === "full")
    ) {
      singlePaymentValue.value = formatted(total());
      zeroPaymentInputs();
      const selectedInput = document.querySelector("#pay-" + selectedPaymentMethod);
      if (selectedInput) selectedInput.value = singlePaymentValue.value;
    }
    renderCheckoutSummary();
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

  function optionalMoney(id) {
    const value = document.querySelector(id)?.value.trim() || "";
    if (!value) return null;
    cents(value);
    return value;
  }

  function addNewPurchaseProduct() {
    if (!purchase) return;
    const name = document.querySelector("#purchase-new-name")?.value.trim() || "";
    const sku = document.querySelector("#purchase-new-sku")?.value.trim().toUpperCase() || "";
    const barcode = document.querySelector("#purchase-new-barcode")?.value.trim() || "";
    const category = document.querySelector("#purchase-new-category")?.value.trim() || "";
    const baseUnit = document.querySelector("#purchase-new-base-unit")?.value.trim() || "piece";
    const packName = document.querySelector("#purchase-new-pack-name")?.value.trim() || "carton";
    const packSize = Number(document.querySelector("#purchase-new-pack-size")?.value || 1);
    const reorderLevel = Number(document.querySelector("#purchase-new-reorder")?.value || 0);
    const mode = document.querySelector("#purchase-new-mode")?.value || "retail_unit";
    const quantity = Number(document.querySelector("#purchase-new-quantity")?.value || 0);
    const price = document.querySelector("#purchase-new-cost")?.value.trim() || "";

    if (name.length < 2) throw new Error("Enter the new product name.");
    if (!/^[A-Z0-9][A-Z0-9._/-]{1,39}$/.test(sku)) {
      throw new Error("Enter a unique SKU using letters, numbers, dot, dash, slash or underscore.");
    }
    if (!Number.isInteger(packSize) || packSize < 1 || packSize > 1000000) {
      throw new Error("Units per pack must be a whole number between 1 and 1,000,000.");
    }
    if (!Number.isInteger(reorderLevel) || reorderLevel < 0 || reorderLevel > 1000000000) {
      throw new Error("Reorder level must be a valid whole number.");
    }
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 1000000) {
      throw new Error("Purchase quantity must be a whole number between 1 and 1,000,000.");
    }
    if (mode === "retail_pack" && packSize <= 1) {
      throw new Error("Set units per pack above 1 before buying full packs.");
    }
    cents(price);
    const newProduct = {
      name, sku, barcode, category,
      base_unit: baseUnit,
      pack_name: packName,
      pack_size: packSize,
      reorder_level: reorderLevel,
      retail_unit: optionalMoney("#purchase-new-retail-unit"),
      retail_pack: optionalMoney("#purchase-new-retail-pack"),
      wholesale_unit: optionalMoney("#purchase-new-wholesale-unit"),
      wholesale_pack: optionalMoney("#purchase-new-wholesale-pack"),
    };
    if (cart.some(line => line.newProduct?.sku === sku)) {
      throw new Error("This new SKU is already in the current purchase.");
    }
    const factor = mode.endsWith("pack") ? packSize : 1;
    cart.push({
      product: null,
      newProduct,
      name,
      mode,
      quantity,
      factor,
      price,
      listPrice: price,
      discount: "0",
      packName,
      baseUnit,
    });
    changed();
    render();
    [
      "#purchase-new-name","#purchase-new-sku","#purchase-new-barcode","#purchase-new-category",
      "#purchase-new-cost","#purchase-new-retail-unit","#purchase-new-retail-pack",
      "#purchase-new-wholesale-unit","#purchase-new-wholesale-pack"
    ].forEach(selector => {
      const node = document.querySelector(selector);
      if (node) node.value = "";
    });
    const qty = document.querySelector("#purchase-new-quantity");
    if (qty) qty.value = "1";
    document.querySelector("#purchase-new-name")?.focus();
  }

  const productGrid = document.querySelector("#catalog");
  const catalogStatus = document.querySelector("#catalog-status");
  const productQuery = document.querySelector("#product-query");

  purchaseSourceButtons.forEach(button => button.addEventListener("click", () => {
    const source = button.dataset.purchaseSource;
    purchaseSourceButtons.forEach(item => item.classList.toggle("active", item === button));
    purchaseNewBuilder?.classList.toggle("hidden", source !== "new");
    document.querySelector(".sale-search-shell")?.classList.toggle("hidden", source === "new");
    productGrid?.classList.toggle("hidden", source === "new");
    catalogStatus?.classList.toggle("hidden", source === "new");
    if (source === "new") document.querySelector("#purchase-new-name")?.focus();
    else productQuery?.focus();
  }));
  purchaseAddNewProduct?.addEventListener("click", () => {
    try {
      clearError();
      addNewPurchaseProduct();
    } catch (error) {
      fail(error.message);
    }
  });

  let openComposerId = null;
  let searchTimer = null;
  let searchSerial = 0;

  function priceSummary(product) {
    if (purchase) return root.dataset.currency + " " + formatted(cents(product.cost));
    const entries = [];
    if (product.prices.retail_unit !== undefined) entries.push("Retail " + product.prices.retail_unit);
    if (product.prices.wholesale_unit !== undefined) entries.push("Wholesale " + product.prices.wholesale_unit);
    return entries.join(" · ") || "No unit selling price";
  }

  function setCatalogStatus(mode, text) {
    if (!catalogStatus) return;
    catalogStatus.classList.toggle("hidden", mode === "results");
    catalogStatus.classList.toggle("loading", mode === "loading");
    const heading = catalogStatus.querySelector("h3");
    const copy = catalogStatus.querySelector("p");
    if (mode === "loading") {
      if (heading) heading.textContent = "Searching…";
      if (copy) copy.textContent = "Finding the closest product matches.";
    } else if (mode === "empty") {
      if (heading) heading.textContent = "No matching product";
      if (copy) copy.textContent = text || "Try another product name, SKU, barcode or category.";
    } else if (mode === "idle") {
      if (heading) heading.textContent = "Find a product";
      if (copy) copy.textContent = "Search, choose the item, and it goes straight into the current sale.";
    }
  }

  function clearCatalogAfterAdd() {
    clearTimeout(searchTimer);
    searchTimer = null;
    searchSerial += 1;
    catalog = [];
    openComposerId = null;
    if (productQuery) {
      productQuery.value = "";
      productQuery.focus();
    }
    renderCatalog();
  }

  function renderCatalog() {
    productGrid.replaceChildren();
    const hasQuery = Boolean(productQuery?.value.trim());
    if (!catalog.length) {
      setCatalogStatus(hasQuery ? "empty" : "idle");
      return;
    }
    setCatalogStatus("results");

    catalog.forEach(product => {
      const card = el("article", undefined, "search-result-card");
      const summary = el("div", undefined, "search-result-summary");
      const symbol = el("span", (product.category || product.base_unit).slice(0, 2).toUpperCase(), "search-result-symbol");
      const identity = el("div", undefined, "search-result-identity");
      identity.append(
        el("strong", product.name),
        el("small", product.sku + (product.category ? " · " + product.category : ""), "muted"),
        el("span", stockLabel(product) + " available", product.stock ? "stock-copy" : "stock-copy danger")
      );
      const commercial = el("div", undefined, "search-result-commercial");
      commercial.append(el("small", purchase ? "Current cost" : "From", "muted"), el("strong", priceSummary(product)));
      const choose = el("button", openComposerId === product.id ? "Close" : "Choose", "button secondary choose-product");
      choose.type = "button";
      choose.addEventListener("click", () => {
        openComposerId = openComposerId === product.id ? null : product.id;
        renderCatalog();
        if (openComposerId) {
          requestAnimationFrame(() => productGrid.querySelector("[data-composer='" + product.id + "'] select")?.focus());
        }
      });
      summary.append(symbol, identity, commercial, choose);
      card.append(summary);

      if (openComposerId === product.id) {
        const composer = el("div", undefined, "product-composer");
        composer.dataset.composer = String(product.id);
        const prices = purchase
          ? {retail_unit: product.cost, retail_pack: formatted(cents(product.cost) * product.pack_size)}
          : product.prices;
        const tiers = purchase
          ? [{value: "purchase", label: "Purchase"}]
          : [
              ...((prices.retail_unit !== undefined || prices.retail_pack !== undefined) ? [{value: "retail", label: "Retail"}] : []),
              ...((prices.wholesale_unit !== undefined || prices.wholesale_pack !== undefined) ? [{value: "wholesale", label: "Wholesale"}] : [])
            ];

        if (!tiers.length) {
          composer.append(el("p", "No selling price is configured for this product.", "danger"));
        } else {
          const tierWrap = el("label", undefined, "composer-tier");
          tierWrap.append(el("small", purchase ? "Receiving mode" : "Selling tier"));
          const tierSelect = el("select");
          tiers.forEach(tier => {
            const option = el("option", tier.label);
            option.value = tier.value;
            tierSelect.append(option);
          });
          tierWrap.append(tierSelect);

          const quantityArea = el("div", undefined, "composer-quantities");
          const addButton = el("button", purchase ? "＋ Add to delivery" : "＋ Add to sale", "button");
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
              const wrap = el("label", undefined, "composer-quantity");
              wrap.append(el("small", "Full " + product.pack_name + "s"), el("strong", root.dataset.currency + " " + packPrice));
              packInput = el("input");
              packInput.type = "number";
              packInput.min = "0";
              packInput.step = "1";
              packInput.value = "1";
              wrap.append(packInput);
              quantityArea.append(wrap);
            }
            if (unitPrice !== undefined) {
              const wrap = el("label", undefined, "composer-quantity");
              wrap.append(el("small", (product.pack_size > 1 ? "Loose " : "") + product.base_unit + "s"), el("strong", root.dataset.currency + " " + unitPrice));
              looseInput = el("input");
              looseInput.type = "number";
              looseInput.min = "0";
              looseInput.step = "1";
              looseInput.value = packInput ? "0" : "1";
              if (product.pack_size > 1) looseInput.max = String(product.pack_size - 1);
              wrap.append(looseInput);
              quantityArea.append(wrap);
            }

            addButton.onclick = () => {
              try {
                clearError();
                const packs = packInput ? Number(packInput.value || 0) : 0;
                const loose = looseInput ? Number(looseInput.value || 0) : 0;
                if (!Number.isInteger(packs) || packs < 0 || !Number.isInteger(loose) || loose < 0) {
                  throw new Error("Pack and loose quantities must be whole numbers.");
                }
                if (product.pack_size > 1 && loose >= product.pack_size) {
                  throw new Error("Loose units must be less than one full " + product.pack_name + ".");
                }
                if (packs === 0 && loose === 0) throw new Error("Enter at least one pack or loose unit.");
                const requested = packs * product.pack_size + loose;
                const already = cart.filter(line => line.product === product.id)
                  .reduce((sum, line) => sum + line.quantity * line.factor, 0);
                if (!purchase && requested + already > product.stock) {
                  throw new Error("Not enough sellable stock. Available: " + stockLabel(product) + ".");
                }
                changed();
                if (packs) addCartLine(product, packMode, packs, packPrice);
                if (loose) addCartLine(product, unitMode, loose, unitPrice);
                render();
                clearCatalogAfterAdd();
              } catch (error) {
                fail(error.message);
              }
            };
          }

          tierSelect.addEventListener("change", drawQuantityControls);
          drawQuantityControls();
          composer.append(tierWrap, quantityArea, addButton);
        }
        card.append(composer);
      }
      productGrid.append(card);
    });
  }

  async function searchCatalog(query, {silent = false} = {}) {
    const q = String(query || "").trim();
    if (pendingBody) return;
    if (!q) {
      catalog = [];
      openComposerId = null;
      renderCatalog();
      return;
    }
    const serial = ++searchSerial;
    if (!silent) setCatalogStatus("loading");
    try {
      const response = await fetch(location.pathname + "?format=json&q=" + encodeURIComponent(q));
      if (!response.ok || !(response.headers.get("Content-Type") || "").includes("application/json")) {
        throw new Error("Product search failed. Check your connection or sign in again.");
      }
      const data = await response.json();
      if (serial !== searchSerial) return;
      catalog = data.catalog || [];
      openComposerId = catalog.length === 1 ? catalog[0].id : null;
      renderCatalog();
    } catch (error) {
      if (serial === searchSerial) {
        catalog = [];
        renderCatalog();
        fail(error.message);
      }
    }
  }

  async function ensureProducts(ids) {
    const missing = ids.filter(id => !catalog.some(item => item.id === id));
    if (!missing.length) return;
    const response = await fetch(location.pathname + "?format=json&ids=" + encodeURIComponent(missing.join(",")));
    if (!response.ok) throw new Error("Could not refresh products for the held sale.");
    const data = await response.json();
    catalog = [...catalog, ...(data.catalog || []).filter(item => !catalog.some(existing => existing.id === item.id))];
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
    if (paystackMomoPhone && customer.phone) paystackMomoPhone.value = customer.phone;
    if (paystackMomoEmail && customer.email) paystackMomoEmail.value = customer.email;
    changed();
    updateConsentAvailability();
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
    if (customerEmail) customerEmail.value = "";
    changed();
    updateConsentAvailability();
    persist();
  }

  function beginNewCustomer() {
    clearCustomer();
    newCustomerMode = true;
    newCustomerFields?.classList.remove("hidden");
    newCustomerToggle?.classList.add("hidden");
    clearCustomerButton?.classList.remove("hidden");
    updateConsentAvailability();
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
            el("small", customer.phone + " · " + customer.purchase_count + " purchases · owes " + root.dataset.currency + " " + customer.outstanding + (customer.consent ? " · SMS enabled" : ""), "muted")
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
  function showSelectedSupplier(supplier) {
    selectedSupplier = supplier;
    if (partyInput) partyInput.value = supplier.id;
    selectedSupplierBox?.classList.remove("hidden");
    if (selectedSupplierBox) {
      selectedSupplierBox.replaceChildren();
      selectedSupplierBox.append(
        el("strong", supplier.name),
        el("small", supplier.phone + " · Outstanding " + root.dataset.currency + " " + supplier.outstanding + " · " + supplier.purchase_count + " purchases", "muted")
      );
    }
    supplierResults?.replaceChildren();
    clearSupplierButton?.classList.remove("hidden");
    if (supplierSearch) supplierSearch.value = "";
    changed();
    persist();
  }

  function clearSupplier() {
    selectedSupplier = null;
    if (partyInput) partyInput.value = "";
    selectedSupplierBox?.classList.add("hidden");
    selectedSupplierBox?.replaceChildren();
    supplierResults?.replaceChildren();
    clearSupplierButton?.classList.add("hidden");
    changed();
    persist();
  }

  let supplierTimer = null;
  supplierSearch?.addEventListener("input", () => {
    clearTimeout(supplierTimer);
    const q = supplierSearch.value.trim();
    supplierResults?.replaceChildren();
    if (q.length < 2) return;
    supplierTimer = setTimeout(async () => {
      try {
        const response = await fetch("/api/suppliers/?q=" + encodeURIComponent(q));
        if (!response.ok) throw new Error("Supplier search failed.");
        const data = await response.json();
        supplierResults?.replaceChildren();
        data.suppliers.forEach(supplier => {
          const button = el("button", undefined, "customer-result");
          button.type = "button";
          button.setAttribute("role", "option");
          button.append(
            el("strong", supplier.name),
            el("small", supplier.phone + " · owes " + root.dataset.currency + " " + supplier.outstanding + " · " + supplier.purchase_count + " purchases", "muted")
          );
          button.addEventListener("click", () => showSelectedSupplier(supplier));
          supplierResults?.append(button);
        });
        if (!data.suppliers.length) supplierResults?.append(el("div", "No saved supplier found. Add a new supplier first.", "empty"));
      } catch (error) { fail(error.message); }
    }, 180);
  });
  clearSupplierButton?.addEventListener("click", clearSupplier);

  function hasAttachedCustomer() {
    return Boolean(
      partyInput?.value ||
      (newCustomerMode && customerName?.value.trim() && customerPhone?.value.trim())
    );
  }

  function updateConsentAvailability() {
    if (!customerConsent) return;
    const available = hasAttachedCustomer();
    // Keep the receipt-SMS choice user-controlled at all times. It starts ON,
    // but cashiers must be able to turn it OFF before or after attaching a
    // customer. The backend still sends only when a valid recipient exists.
    customerConsent.disabled = false;
    customerConsent.closest("#customer-consent-wrap")?.classList.toggle("no-recipient", !available);
    // A customer lookup must never overwrite the cashier\u0027s choice.
  }

  customerConsent?.addEventListener("change", () => {
    changed();
    persist();
  });
  customerWhatsApp?.addEventListener("change", () => { changed(); persist(); });
  customerName?.addEventListener("input", updateConsentAvailability);
  customerPhone?.addEventListener("input", () => {
    updateConsentAvailability();
    if (paystackMomoPhone && !momoReference) paystackMomoPhone.value = customerPhone.value;
    persist();
  });
  customerEmail?.addEventListener("input", () => {
    if (paystackMomoEmail && !momoReference) paystackMomoEmail.value = customerEmail.value;
    persist();
  });
  paystackMomoProvider?.addEventListener("change", persist);
  paystackMomoPhone?.addEventListener("input", persist);
  paystackMomoEmail?.addEventListener("input", persist);

  function zeroPaymentInputs() {
    paymentMethods.forEach(method => {
      const input = document.querySelector("#pay-" + method);
      if (input) input.value = "0";
    });
  }

  function syncSinglePayment() {
    if (!singlePaymentValue || selectedPaymentMethod === "split" || !selectedPaymentMethod) return;
    zeroPaymentInputs();
    const input = document.querySelector("#pay-" + selectedPaymentMethod);
    if (input) input.value = singlePaymentValue.value || "0";
    renderCheckoutSummary();
  }

  function directMomoSelected() {
    return !purchase
      && selectedPaymentMethod === "momo"
      && (paymentPlan?.value || "full") === "full";
  }

  function syncPaystackMomoPanel() {
    const active = directMomoSelected();
    paystackMomoPanel?.classList.toggle("hidden", !active);
    if (active) {
      if (paystackMomoPhone && !paystackMomoPhone.value) {
        paystackMomoPhone.value = selectedCustomer?.phone || customerPhone?.value || "";
      }
      if (paystackMomoEmail && !paystackMomoEmail.value) {
        paystackMomoEmail.value = selectedCustomer?.email || customerEmail?.value || "";
      }
    }
    if (completeButton && !purchase) {
      completeButton.textContent = active && paystackMomoReady
        ? (momoGateway === "hubtel" ? "Create secure Hubtel checkout" : "Send MoMo Approval Request")
        : "Complete Sale & Generate Receipt";
    }
    if (completeSaleHint && !purchase) {
      completeSaleHint.textContent = active && paystackMomoReady
        ? (momoGateway === "hubtel"
          ? "The customer pays on Hubtel's secure checkout. KOFAD waits for independently verified payment before posting the sale and issuing a receipt."
          : "KOFAD waits for Paystack to verify the payment before posting stock, recording the sale or issuing the receipt.")
        : "One click posts the transaction. After a sale, the receipt and its Print / PDF / SMS actions appear immediately.";
    }
  }

  function selectPaymentMethod(method, {preserveAmount = false} = {}) {
    if (!method || (method !== "split" && !paymentMethods.includes(method))) return;
    selectedPaymentMethod = method;
    paymentMethodButtons.forEach(button => {
      const active = button.dataset.paymentMethod === method;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    const split = method === "split";
    splitPaymentGrid?.classList.toggle("hidden", !split);
    singlePaymentWrap?.classList.toggle("hidden", split || paymentPlan?.value === "credit");
    if (!split) {
      if (!preserveAmount && singlePaymentValue) {
        singlePaymentValue.value = (!paymentPlan || paymentPlan.value === "full")
          ? formatted(total())
          : "0";
      }
      syncSinglePayment();
    }
    syncPaystackMomoPanel();
    persist();
  }

  function applyPaymentPlan() {
    const plan = purchase ? "purchase" : (paymentPlan?.value || "full");
    const credit = plan === "credit";
    const full = plan === "full";
    paymentMethodPicker?.classList.toggle("hidden", credit);
    if (credit) {
      zeroPaymentInputs();
      singlePaymentWrap?.classList.add("hidden");
      splitPaymentGrid?.classList.add("hidden");
      creditFields?.classList.remove("hidden");
    } else {
      if (purchase) creditFields?.classList.remove("hidden");
      else creditFields?.classList.toggle("hidden", full);
      if (full && dueDate) dueDate.value = "";
      if (!selectedPaymentMethod) selectedPaymentMethod = paymentMethods.includes("cash") ? "cash" : (paymentMethods[0] || "");
      selectPaymentMethod(selectedPaymentMethod, {preserveAmount: plan === "part"});
      if (full && selectedPaymentMethod !== "split" && singlePaymentValue) {
        singlePaymentValue.value = formatted(total());
        syncSinglePayment();
      }
    }
    updateConsentAvailability();
    renderCheckoutSummary();
    syncPaystackMomoPanel();
    persist();
  }

  paymentMethodButtons.forEach(button => button.addEventListener("click", () => {
    changed();
    selectPaymentMethod(button.dataset.paymentMethod);
  }));
  singlePaymentValue?.addEventListener("input", () => {
    changed();
    syncSinglePayment();
    persist();
  });
  paymentMethods.forEach(method => {
    document.querySelector("#pay-" + method)?.addEventListener("input", () => {
      changed();
      renderCheckoutSummary();
      persist();
    });
  });
  paymentPlan?.addEventListener("change", () => {
    changed();
    applyPaymentPlan();
  });

  document.querySelector("#exact-payment")?.addEventListener("click", () => {
    changed();
    if (paymentPlan && paymentPlan.value !== "full") paymentPlan.value = "full";
    if (selectedPaymentMethod === "split") {
      selectedPaymentMethod = paymentMethods.includes("cash") ? "cash" : (paymentMethods[0] || "");
    }
    applyPaymentPlan();
    if (singlePaymentValue) {
      singlePaymentValue.value = formatted(total());
      syncSinglePayment();
    }
    renderCheckoutSummary();
    persist();
  });

  document.querySelector("#clear-payment")?.addEventListener("click", () => {
    changed();
    if (singlePaymentValue) singlePaymentValue.value = "0";
    zeroPaymentInputs();
    renderCheckoutSummary();
    persist();
  });

  function openPayment() {
    clearError();
    if (!cart.length) return fail("Add a product first.");
    applyPaymentPlan();
    if (!paymentDialog) {
      const checkoutPanel = document.querySelector("#checkout-panel");
      checkoutPanel?.scrollIntoView({block: "start", behavior: "smooth"});
      checkoutPanel?.focus({preventScroll: true});
      return;
    }
    if (typeof paymentDialog.showModal === "function") paymentDialog.showModal();
    else paymentDialog.setAttribute("open", "");
    requestAnimationFrame(() => {
      const target = paymentMethodButtons.find(button => button.classList.contains("active")) || paymentPlan;
      target?.focus();
    });
  }
  function closePayment() {
    if (!paymentDialog?.open) return;
    if (typeof paymentDialog.close === "function") paymentDialog.close();
    else paymentDialog.removeAttribute("open");
  }
  openPaymentButton?.addEventListener("click", openPayment);
  closePaymentButton?.addEventListener("click", closePayment);
  paymentDialog?.addEventListener("click", event => {
    if (event.target === paymentDialog) closePayment();
  });
  paymentDialog?.addEventListener("cancel", event => {
    event.preventDefault();
    closePayment();
  });

  document.querySelector("#open-held-sales")?.addEventListener("click", () => {
    if (typeof heldSalesDialog?.showModal === "function") heldSalesDialog.showModal();
    else heldSalesDialog?.setAttribute("open", "");
  });
  document.querySelector("#close-held-sales")?.addEventListener("click", () => heldSalesDialog?.close?.());
  heldSalesDialog?.addEventListener("click", event => {
    if (event.target === heldSalesDialog) heldSalesDialog.close();
  });

  document.querySelector("#cart-jump").addEventListener("click", () => {
    const panel = document.querySelector("#checkout-panel");
    panel.scrollIntoView({block: "start"});
    panel.focus({preventScroll: true});
  });

  renderCatalog();

  document.querySelector("#catalog-search").addEventListener("submit", event => {
    event.preventDefault();
    searchCatalog(productQuery.value);
  });
  productQuery?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = productQuery.value.trim();
    if (!q) {
      catalog = [];
      openComposerId = null;
      renderCatalog();
      return;
    }
    searchTimer = setTimeout(() => searchCatalog(q, {silent: true}), q.length === 1 ? 280 : 150);
  });
  document.addEventListener("keydown", event => {
    const editing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || "");
    if (!editing && event.key === "/") {
      event.preventDefault();
      productQuery?.focus();
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
        const rawName = customerName?.value.trim() || "";
        const rawPhone = customerPhone?.value.trim() || "";
        const wantsNamedCustomer = newCustomerMode || rawName || rawPhone;
        if (wantsNamedCustomer) {
          if (rawName.length < 2) throw new Error("Enter the new customer's name.");
          newName = rawName;
          newPhone = normalizeGhanaPhone(rawPhone);
        }
      }
      const plan = paymentPlan?.value || "full";
      if (!allowCredit && plan !== "full") throw new Error("Credit sales are disabled by company policy.");
      if ((plan === "part" || plan === "credit") && !party && !newName) {
        throw new Error("Choose or enter a customer for a sale that leaves a balance.");
      }
      if (plan === "full" && paidNow !== grandTotal) {
        throw new Error("The payment amount must exactly equal the sale total.");
      }
      if (plan === "part" && !(paidNow > 0 && paidNow < grandTotal)) {
        throw new Error("Part payment must be greater than zero and less than the sale total.");
      }
      if (plan === "credit" && paidNow !== 0) {
        throw new Error("Credit / pay later starts with zero payment.");
      }
      if ((plan === "part" || plan === "credit") && !dueDate?.value) {
        throw new Error("Choose a due date for the unpaid balance.");
      }
    } else {
      if (!party) throw new Error("Search and choose a supplier before posting this purchase.");
      if (!purchaseDocumentDate?.value) throw new Error("Choose the supplier invoice date.");
      if (paidNow < grandTotal && !dueDate?.value) {
        throw new Error("Choose a due date for the unpaid supplier balance.");
      }
    }

    return {
      kind: root.dataset.kind,
      items: cart.map(({product, newProduct, mode, quantity, price, discount}) => ({
        product, mode, quantity,
        ...(newProduct ? {new_product: newProduct} : {}),
        ...(purchase || allowPriceOverrides ? {price} : {}),
        ...(!purchase && allowDiscounts ? {discount: discount || "0"} : {})
      })),
      party,
      ...(!purchase && !party && newName ? {
        customer_name: newName,
        customer_phone: newPhone,
        customer_email: customerEmail?.value.trim() || ""
      } : {}),
      ...(!purchase ? {
        customer_consent: Boolean((party || newName) && (customerConsent?.checked || customerWhatsApp?.checked)),
        send_sms: Boolean((party || newName) && customerConsent?.checked),
        send_whatsapp: Boolean((party || newName) && customerWhatsApp?.checked)
      } : {}),
      due_date: dueDate?.value || "",
      ...(purchase ? {external_reference: purchaseReference?.value.trim() || "", document_date: purchaseDocumentDate?.value || "", note: purchaseNote?.value.trim() || ""} : {}),
      override_reason: document.querySelector("#override-reason")?.value || "",
      payments: paymentMethods.map(method => ({
        method,
        amount: document.querySelector("#pay-" + method)?.value || "0"
      }))
    };
  }

  function setMomoStatus(message, tone = "subtle") {
    if (!paystackMomoStatus) return;
    paystackMomoStatus.textContent = message || "";
    paystackMomoStatus.classList.toggle("error", tone === "error");
  }

  function syncHubtelCheckoutLink(result) {
    const link = document.querySelector("#hubtel-momo-checkout-link");
    if (!link) return;
    const url = typeof result?.authorization_url === "string" ? result.authorization_url : "";
    let safe = false;
    try {
      const parsed = new URL(url);
      safe = momoGateway === "hubtel" && parsed.protocol === "https:"
        && parsed.hostname === "pay.hubtel.com" && !parsed.username && !parsed.password;
    } catch (_) { /* A missing or malformed link is never rendered. */ }
    if (safe) link.href = url;
    else link.removeAttribute("href");
    link.classList.toggle("hidden", !safe);
  }

  function syncMomoChallenge(result) {
    const panel = document.querySelector("#paystack-momo-challenge");
    if (!panel) return;
    const needsCode = Boolean(momoReference && result?.needs_otp);
    const newlyRequested = needsCode && panel.classList.contains("hidden");
    panel.classList.toggle("hidden", !needsCode);
    panel.querySelectorAll("input,button").forEach(control => { control.disabled = !needsCode; });
    if (newlyRequested) {
      setMomoStatus("Ask the customer for the one-time Paystack verification code sent for this payment. Never ask for their MoMo PIN.");
      panel.querySelector("input")?.focus({preventScroll: true});
      panel.scrollIntoView({behavior: "smooth", block: "nearest"});
    }
  }

  document.querySelector("#paystack-momo-otp-submit")?.addEventListener("click", async () => {
    const input = document.querySelector("#paystack-momo-otp");
    const button = document.querySelector("#paystack-momo-otp-submit");
    if (!momoReference || !/^[0-9]{4,8}$/.test(input?.value || "")) {
      setMomoStatus("Enter the one-time payment code. Never enter a MoMo PIN.", "error");
      return;
    }
    button.disabled = true;
    try {
      const result = await api("/api/pos/paystack-momo/" + encodeURIComponent(momoReference) + "/otp/", {otp: input.value});
      input.value = "";
      syncMomoChallenge(result);
      setMomoStatus(result.message || "Code submitted. Waiting for verified payment.");
      pollMomoPayment();
    } catch (error) {
      input.value = "";
      setMomoStatus(error.message, "error");
      button.disabled = false;
    }
  });

  function lockCheckoutForMomo(locked) {
    root.querySelectorAll("input,select,textarea,button").forEach(control => {
      // Keep a provider-requested one-time code available while the sale is locked.
      // No other cart or payment details can be changed until verification finishes.
      if (control.closest("#paystack-momo-challenge")) return;
      control.disabled = locked;
    });
    if (!locked) {
      syncMomoChallenge(null);
      applyPaymentPlan();
    }
  }

  async function fetchMomoStatus() {
    if (!momoReference) return null;
    const response = await fetch(
      "/api/pos/paystack-momo/" + encodeURIComponent(momoReference) + "/status/",
      {credentials: "same-origin", cache: "no-store", headers: {"Accept": "application/json"}}
    );
    const contentType = response.headers.get("Content-Type") || "";
    if (!contentType.includes("application/json")) throw new Error("Sign in again to check this payment.");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not check the MoMo payment.");
    return result;
  }

  async function pollMomoPayment({immediate = false} = {}) {
    clearTimeout(momoPollTimer);
    if (!momoReference) return;
    if (!immediate) {
      momoPollTimer = setTimeout(() => pollMomoPayment({immediate: true}), 5000);
      return;
    }
    try {
      const result = await fetchMomoStatus();
      if (!result || !momoReference) return;
      setMomoStatus(result.display_text || result.message || "Checking payment…");
      syncMomoChallenge(result);
      syncHubtelCheckoutLink(result);
      if (result.paid && result.sale) {
        completed = true;
        momoReference = null;
        pendingBody = null;
        syncHubtelCheckoutLink(null);
        clearStored();
        lockCheckoutForMomo(false);
        showSaleSuccess(result.sale);
        return;
      }
      if (result.failed) {
        const message = result.message || "The MoMo request was not successful.";
        momoReference = null;
        pendingBody = null;
        requestKey = crypto.randomUUID();
        lockCheckoutForMomo(false);
        persist();
        setMomoStatus(message, "error");
        fail(message);
        return;
      }
      if (result.attention) {
        const message = result.message || "This payment needs manager review. Do not request another payment.";
        setMomoStatus(message, "error");
        persist();
        fail(message);
        return;
      }
      persist();
      momoPollTimer = setTimeout(() => pollMomoPayment({immediate: true}), result.status === "not_confirmed" ? 10000 : 5000);
    } catch (error) {
      setMomoStatus(error.message + " KOFAD will keep checking in the background.", "error");
      momoPollTimer = setTimeout(() => pollMomoPayment({immediate: true}), 10000);
    }
  }

  async function startPaystackMomo() {
    if (!directMomoSelected() || !paystackMomoReady) return false;
    if (!pendingBody) pendingBody = buildCheckoutBody();
    const phone = paystackMomoPhone?.value.trim() || selectedCustomer?.phone || customerPhone?.value.trim() || "";
    const email = paystackMomoEmail?.value.trim() || selectedCustomer?.email || customerEmail?.value.trim() || "";
    const provider = paystackMomoProvider?.value || "mtn";
    if (!phone) throw new Error("Enter the customer's Mobile Money number.");
    if (!email || !email.includes("@")) throw new Error("Enter the customer's email for the " + (momoGateway === "hubtel" ? "Hubtel checkout." : "Paystack payment request."));

    setMomoStatus(momoGateway === "hubtel" ? "Creating your secure Hubtel checkout…" : "Sending Mobile Money approval request…");
    lockCheckoutForMomo(true);
    try {
      const result = await api("/api/pos/paystack-momo/start/", {
        sale: pendingBody,
        phone,
        email,
        provider,
        payment_gateway: momoGateway,
        request_key: requestKey
      }, requestKey);
      momoReference = result.reference || momoReference;
      if (!momoReference) throw new Error("The payment provider did not return a payment reference.");
      setMomoStatus(result.message || result.display_text || "Approve the payment on the customer's phone.");
      syncMomoChallenge(result);
      syncHubtelCheckoutLink(result);
      persist();
      if (result.paid && result.sale) {
        completed = true;
        momoReference = null;
        pendingBody = null;
        clearStored();
        lockCheckoutForMomo(false);
        showSaleSuccess(result.sale);
        return true;
      }
      if (result.failed) {
        const message = result.message || "The MoMo request was not successful.";
        momoReference = null;
        pendingBody = null;
        requestKey = crypto.randomUUID();
        lockCheckoutForMomo(false);
        persist();
        throw new Error(message);
      }
      pollMomoPayment();
      return true;
    } catch (error) {
      if (!momoReference) {
        pendingBody = null;
        requestKey = crypto.randomUUID();
        lockCheckoutForMomo(false);
        persist();
      }
      throw error;
    }
  }

  function setSuccessStatus(message, tone = "subtle") {
    if (!successMessageStatus) return;
    message = [message, successMessageStatus.dataset.whatsappResult].filter(Boolean).join(" ");
    successMessageStatus.textContent = message || "";
    successMessageStatus.classList.toggle("hidden", !message);
    successMessageStatus.classList.toggle("error", tone === "error");
    successMessageStatus.classList.toggle("subtle", tone !== "error");
  }

  function showSaleSuccess(result) {
    lastCompletedSale = result;
    closePayment();
    root.querySelectorAll("input,select,textarea,button").forEach(control => control.disabled = false);
    document.querySelector("#success-reference").textContent = result.reference || "Receipt";
    document.querySelector("#success-total").textContent = Number(result.total || 0).toFixed(2);
    const successPaid = document.querySelector("#success-paid");
    const successBalance = document.querySelector("#success-balance");
    const successPayment = document.querySelector("#success-payment");
    if (successPaid) successPaid.textContent = Number(result.paid || 0).toFixed(2);
    if (successBalance) successBalance.textContent = Math.max(Number(result.total || 0) - Number(result.paid || 0), 0).toFixed(2);
    if (successPayment) {
      successPayment.textContent = selectedPaymentMethod === "split"
        ? "Mixed payment"
        : (selectedPaymentMethod ? selectedPaymentMethod.toUpperCase() + " payment" : "Payment recorded");
    }
    document.querySelector("#success-customer").textContent = result.customer
      ? result.customer.name + (result.customer.phone ? " · " + result.customer.phone : "")
      : "Walk-in customer";

    const successItems = document.querySelector("#success-items");
    if (successItems) {
      successItems.replaceChildren();
      cart.forEach(line => {
        const row = el("div", undefined, "success-receipt-line");
        const copy = el("span");
        copy.append(el("strong", line.name), el("small", line.quantity + " × " + formatted(effectiveUnit(line))));
        row.append(copy, el("strong", formatted(effectiveUnit(line) * line.quantity)));
        successItems.append(row);
      });
    }

    const printLink = document.querySelector("#receipt-print");
    const a4Link = document.querySelector("#receipt-a4");
    const viewLink = document.querySelector("#receipt-view");
    if (result.document_id) {
      printLink.href = "/documents/" + result.document_id + "/pdf/thermal80/";
      a4Link.href = "/documents/" + result.document_id + "/pdf/a4/";
      viewLink.href = result.url;
    }

    if (receiptSmsButton) {
      const smsDone = ["accepted", "delivered", "simulated", "sending"].includes(result.sms_status);
      const smsFailed = ["failed", "undelivered", "expired", "unknown"].includes(result.sms_status);
      receiptSmsButton.disabled = smsDone || !result.can_send_sms;
      receiptSmsButton.textContent = result.sms_status === "delivered"
        ? "Delivered ✓"
        : smsDone
        ? "SMS sent ✓"
        : smsFailed
        ? "Retry SMS"
        : result.can_send_sms
        ? "Send SMS"
        : "SMS unavailable";
    }
    if (result.whatsapp_requested && successMessageStatus) {
      successMessageStatus.dataset.whatsappResult = result.whatsapp_message || "WhatsApp receipt requested.";
    } else if (successMessageStatus) { delete successMessageStatus.dataset.whatsappResult; }
    if (result.sms_requested) {
      setSuccessStatus(
        result.sms_message || (result.sms_status === "delivered" ? "Receipt SMS delivered." : ""),
        ["failed", "undelivered", "expired", "unknown"].includes(result.sms_status) ? "error" : "subtle"
      );
    } else {
      setSuccessStatus(result.can_send_sms ? "" : (result.sms_reason || ""));
    }
    if (typeof successDialog?.showModal === "function") successDialog.showModal();
    else successDialog?.setAttribute("open", "");
  }

  function resetForNextSale() {
    successDialog?.close?.();
    completed = false;
    lastCompletedSale = null;
    pendingBody = null;
    heldId = null;
    momoReference = null;
    clearTimeout(momoPollTimer);
    requestKey = crypto.randomUUID();
    cart.splice(0, cart.length);
    selectedCustomer = null;
    newCustomerMode = false;
    if (partyInput) partyInput.value = "";
    selectedCustomerBox?.classList.add("hidden");
    selectedCustomerBox?.replaceChildren();
    customerResults?.replaceChildren();
    newCustomerFields?.classList.add("hidden");
    newCustomerToggle?.classList.remove("hidden");
    clearCustomerButton?.classList.add("hidden");
    if (customerSearch) customerSearch.value = "";
    if (customerName) customerName.value = "";
    if (customerPhone) customerPhone.value = "";
    if (customerEmail) customerEmail.value = "";
    if (paystackMomoPhone) paystackMomoPhone.value = "";
    if (paystackMomoEmail) paystackMomoEmail.value = "";
    if (paystackMomoProvider) paystackMomoProvider.value = "mtn";
    setMomoStatus("When you complete the sale, KOFAD will send the MoMo approval request and wait for verified payment.");
    if (customerConsent) customerConsent.checked = true;
    if (customerWhatsApp) customerWhatsApp.checked = false;
    if (paymentPlan) paymentPlan.value = "full";
    if (dueDate) dueDate.value = "";
    selectedPaymentMethod = paymentMethods.includes("cash") ? "cash" : (paymentMethods[0] || "");
    zeroPaymentInputs();
    if (singlePaymentValue) singlePaymentValue.value = "0";
    const reason = document.querySelector("#override-reason");
    if (reason) reason.value = "";
    applyPaymentPlan();
    render();
    clearStored();
    clearError();
    productQuery?.focus();
  }

  async function sendReceiptSmsNow() {
    if (!lastCompletedSale?.document_id || !receiptSmsButton) return;
    setSuccessStatus("");
    receiptSmsButton.disabled = true;
    receiptSmsButton.textContent = "Sending SMS…";
    try {
      const result = await api("/api/documents/" + lastCompletedSale.document_id + "/send-sms/", {});
      receiptSmsButton.textContent = result.status === "delivered" ? "Delivered ✓" : "SMS sent ✓";
      setSuccessStatus(result.message || (result.status === "delivered" ? "Receipt SMS delivered." : "Receipt SMS sent."));
    } catch (error) {
      receiptSmsButton.disabled = false;
      receiptSmsButton.textContent = "Retry SMS";
      setSuccessStatus(error.message, "error");
    }
  }

  receiptSmsButton?.addEventListener("click", sendReceiptSmsNow);
  document.querySelector("#new-sale-after-success")?.addEventListener("click", resetForNextSale);
  successDialog?.addEventListener("cancel", event => {
    event.preventDefault();
  });

  document.querySelector("#checkout").addEventListener("submit", async event => {
    event.preventDefault();
    if (!cart.length) return fail("Add a product first.");
    const button = document.querySelector("#complete");
    clearError();
    try {
      if (!pendingBody) pendingBody = buildCheckoutBody();
      persist();

      if (!purchase) {
        const momoAmount = cents(document.querySelector("#pay-momo")?.value || "0");
        if (momoAmount > 0) {
          if (!paystackMomoReady) {
            throw new Error("Mobile Money payments through " + (momoGateway === "hubtel" ? "Hubtel" : "Paystack") + " are awaiting activation.");
          }
          if (!directMomoSelected()) {
            throw new Error("Verified Mobile Money is currently available only as a full single-method payment. Remove split or part-payment amounts and try again.");
          }
          await startPaystackMomo();
          return;
        }
      }

      root.querySelectorAll("input,select,textarea,button").forEach(control => control.disabled = true);
      button.disabled = false;
      const result = await api("/api/trades/", pendingBody, requestKey);
      if (heldId) {
        try { await api("/api/held/" + heldId + "/", {}); } catch (_) {}
      }
      completed = true;
      clearStored();
      if (purchase) {
        location.href = result.url;
        return;
      }
      showSaleSuccess(result);
    } catch (error) {
      if (error.rejected || !pendingBody) {
        pendingBody = null;
        requestKey = crypto.randomUUID();
        root.querySelectorAll("input,select,textarea,button").forEach(control => control.disabled = false);
        applyPaymentPlan();
        persist();
      }
      const locked = Boolean(pendingBody || momoReference);
      fail(error.message + (locked ? " Do not start another payment until this request is resolved." : ""));
    } finally {
      if (!completed && !momoReference) button.disabled = false;
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
        customer_email: customerEmail?.value.trim() || "",
        customer_consent: Boolean(customerConsent?.checked),
        send_whatsapp: Boolean(customerWhatsApp?.checked),
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
      await ensureProducts(saved.items.map(line => line.product));
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
        if (customerEmail) customerEmail.value = saved.customer_email || "";
      }
      if (paymentPlan && saved.payment_plan) paymentPlan.value = saved.payment_plan;
      if (dueDate) dueDate.value = saved.due_date || "";
      if (customerWhatsApp) customerWhatsApp.checked = saved.send_whatsapp === true;
      if (customerConsent) {
        if (Object.prototype.hasOwnProperty.call(saved, "customer_consent")) {
                customerConsent.checked = Boolean(saved.customer_consent);
        } else {
                customerConsent.checked = true;
        }
      }
      applyPaymentPlan();
      render();
      heldSalesDialog?.close?.();
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
  if (selectedSupplier?.id) showSelectedSupplier(selectedSupplier);
  if (restoredState) {
    if (customerName && restoredState.customerName) customerName.value = restoredState.customerName;
    if (customerPhone && restoredState.customerPhone) customerPhone.value = restoredState.customerPhone;
    if (customerEmail && restoredState.customerEmail) customerEmail.value = restoredState.customerEmail;
    if (paystackMomoProvider && restoredState.paystackMomoProvider) paystackMomoProvider.value = restoredState.paystackMomoProvider;
    if (paystackMomoPhone && restoredState.paystackMomoPhone) paystackMomoPhone.value = restoredState.paystackMomoPhone;
    if (paystackMomoEmail && restoredState.paystackMomoEmail) paystackMomoEmail.value = restoredState.paystackMomoEmail;
    if (paymentPlan && restoredState.paymentPlan) paymentPlan.value = restoredState.paymentPlan;
    if (dueDate && restoredState.dueDate) dueDate.value = restoredState.dueDate;
    if (customerWhatsApp) customerWhatsApp.checked = restoredState.customerWhatsApp === true;
    if (customerConsent) {
      if (Object.prototype.hasOwnProperty.call(restoredState, "customerConsent")) {
            customerConsent.checked = Boolean(restoredState.customerConsent);
      } else {
            customerConsent.checked = true;
      }
    }
    if (purchaseReference) purchaseReference.value = restoredState.purchaseReference || "";
    if (purchaseDocumentDate && restoredState.purchaseDocumentDate) purchaseDocumentDate.value = restoredState.purchaseDocumentDate;
    if (purchaseNote) purchaseNote.value = restoredState.purchaseNote || "";
    if (
      restoredState.selectedPaymentMethod === "split" ||
      paymentMethods.includes(restoredState.selectedPaymentMethod)
    ) {
      selectedPaymentMethod = restoredState.selectedPaymentMethod;
    }
  }
  applyPaymentPlan();
  render();
  hydrating = false;

  if (pendingBody) {
    if (partyInput) partyInput.value = pendingBody.party || "";
    if (pendingBody.customer_name && customerName) {
      beginNewCustomer();
      customerName.value = pendingBody.customer_name;
      customerPhone.value = pendingBody.customer_phone || "";
      if (customerEmail) customerEmail.value = pendingBody.customer_email || "";
    }
    if (customerConsent && Object.prototype.hasOwnProperty.call(pendingBody, "customer_consent")) {
        customerConsent.checked = Boolean(pendingBody.send_sms ?? pendingBody.customer_consent);
      updateConsentAvailability();
    }
    if (customerWhatsApp) customerWhatsApp.checked = pendingBody.send_whatsapp === true;
    if (dueDate) dueDate.value = pendingBody.due_date || "";
    if (purchaseReference) purchaseReference.value = pendingBody.external_reference || "";
    if (purchaseDocumentDate) purchaseDocumentDate.value = pendingBody.document_date || purchaseDocumentDate.value;
    if (purchaseNote) purchaseNote.value = pendingBody.note || "";
    pendingBody.payments.forEach(payment => {
      const input = document.querySelector("#pay-" + payment.method);
      if (input) input.value = payment.amount;
    });
    const nonZeroPayments = pendingBody.payments.filter(payment => {
      try { return cents(payment.amount) > 0; } catch (_) { return false; }
    });
    if (nonZeroPayments.length === 1) {
      selectedPaymentMethod = nonZeroPayments[0].method;
      if (singlePaymentValue) singlePaymentValue.value = nonZeroPayments[0].amount;
    } else if (nonZeroPayments.length > 1) {
      selectedPaymentMethod = "split";
    }
    selectPaymentMethod(selectedPaymentMethod, {preserveAmount: true});
    const reason = document.querySelector("#override-reason");
    if (reason) reason.value = pendingBody.override_reason || "";
    root.querySelectorAll("input,select,textarea,button").forEach(control => control.disabled = true);
    openPayment();
    if (momoReference) {
      syncPaystackMomoPanel();
      setMomoStatus("Restored pending MoMo payment. KOFAD is checking Paystack automatically.");
      pollMomoPayment({immediate: true});
    } else {
      document.querySelector("#complete").disabled = false;
      fail("A checkout was interrupted. Review the unchanged checkout and click Complete Sale & Generate Receipt to recover the original result.");
    }
  } else if (momoReference) {
    setMomoStatus("Restored pending MoMo payment. KOFAD is checking Paystack automatically.");
    lockCheckoutForMomo(true);
    pollMomoPayment({immediate: true});
  }
})();