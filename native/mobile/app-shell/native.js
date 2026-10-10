"use strict";
(() => {
  const CHANNEL = "__CHANNEL__";
  const OFFICIAL_MARKET = "https://market.kofadimpex.com";
  const OFFICIAL_STAFF = "https://staff.kofadimpex.com";
  const OFFICIAL_ROOT = CHANNEL === "customer" ? OFFICIAL_MARKET : OFFICIAL_STAFF;
  const CACHE_KEY = "kofad-" + CHANNEL + "-public-catalog-v1";
  const $ = (selector) => document.querySelector(selector);
  const native = window.Capacitor?.Plugins || {};
  const main = $("#open-main");
  const status = $("#catalog-status");
  let requestSerial = 0;
  let activeRequest = null;
  let nextPage = null;
  let online = navigator.onLine !== false;

  function approvedUrl(path) {
    if (!/^\/(?!\/)[A-Za-z0-9_/?=&%.-]*$/.test(path)) return "";
    const origin = CHANNEL === "customer" ? OFFICIAL_MARKET : OFFICIAL_STAFF;
    if (CHANNEL === "customer" && !path.startsWith("/market/")) return "";
    if (CHANNEL === "staff" && ![
      "/workspace/", "/sales/new/", "/inventory/", "/approvals/",
      "/online-orders/", "/email/", "/account/"
    ].includes(path)) return "";
    return origin + path;
  }

  async function openOfficial(path) {
    const url = approvedUrl(path);
    if (!url) return;
    try {
      // Capacitor Browser uses Chrome Custom Tabs / SFSafariViewController.
      // This isolates credentials, payment redirects and Google OAuth from
      // our local app WebView. The Django server remains authoritative.
      if (typeof native.Browser?.open === "function") {
        await native.Browser.open({url, presentationStyle: "fullscreen"});
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
      }
    } catch (_) {
      // Do not navigate to arbitrary/scheme-specific URLs on plugin failure.
      window.open(url, "_blank", "noopener,noreferrer");
    }
  }

  function setNetwork(state) {
    online = Boolean(state);
    const badge = $("#network-status");
    badge.classList.toggle("online", online);
    badge.classList.toggle("offline", !online);
    $("#network-label").textContent = online ? "Connected" : "Offline";
    $("#offline-panel").hidden = online;
  }

  function productCard(product) {
    const item = document.createElement("article");
    item.className = "native-product";
    const thumb = document.createElement("div");
    thumb.className = "native-product-thumb";
    const imagePath = product.image_path || "";
    if (/^\/market\/products\/\d+\/image\/thumb\/$/.test(imagePath)) {
      const image = document.createElement("img");
      image.src = OFFICIAL_MARKET + imagePath;
      image.alt = "";
      image.loading = "lazy";
      image.onerror = () => {thumb.textContent = "K";};
      thumb.appendChild(image);
    } else {
      thumb.textContent = "K";
    }
    const category = document.createElement("span");
    category.className = "native-product-category";
    category.textContent = String(product.category || "KOFAD Market").slice(0, 70);
    const title = document.createElement("h3");
    title.textContent = String(product.name || "Product").slice(0, 160);
    const price = document.createElement("div");
    price.className = "native-product-price";
    price.textContent = "GH₵ " + String(product.price || "0.00");
    const unit = document.createElement("small");
    unit.textContent = String(product.selling_unit || "").slice(0, 60);
    price.appendChild(unit);
    const action = document.createElement("button");
    action.className = "native-product-view";
    action.type = "button";
    action.textContent = "View product ↗";
    const id = Number(product.id);
    if (Number.isSafeInteger(id) && id > 0) {
      action.addEventListener("click", () => openOfficial("/market/products/" + id + "/"));
    } else {
      action.disabled = true;
    }
    item.append(thumb, category, title, price, action);
    return item;
  }

  function showProducts(items, append) {
    const list = $("#products");
    if (!append) list.replaceChildren();
    for (const item of items) list.appendChild(productCard(item));
  }

  function readCachedCatalog() {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      if (!raw) return null;
      const val = JSON.parse(raw);
      if (!Array.isArray(val.items) || Date.now() - val.savedAt > 24 * 3600 * 1000) return null;
      return val;
    } catch (_) {
      return null;
    }
  }

  function updateStatus(message) {
    if (status) status.textContent = message;
  }

  async function loadCatalog({append = false, page = 1} = {}) {
    if (CHANNEL !== "customer") return;
    const query = $("#search").value.trim().slice(0, 70);
    const serial = ++requestSerial;
    activeRequest?.abort();
    activeRequest = new AbortController();
    const url = new URL(OFFICIAL_MARKET + "/market/app/catalog.json");
    url.searchParams.set("page", String(page));
    if (query) url.searchParams.set("q", query);
    updateStatus("Refreshing official KOFAD products…");
    try {
      const response = await fetch(url, {
        mode: "cors", credentials: "omit",
        cache: "no-cache", signal: activeRequest.signal,
      });
      if (!response.ok) throw Error("Catalog unavailable");
      const value = await response.json();
      if (serial !== requestSerial) return;
      if (value.schema !== 1 || !Array.isArray(value.items) || value.items.length > 20) {
        throw Error("Unexpected catalog response");
      }
      nextPage = Number.isSafeInteger(value.next_page) && value.next_page > 0 ? value.next_page : null;
      showProducts(value.items, append);
      $("#more-products").hidden = !nextPage;
      updateStatus(
        value.items.length
          ? "Live public prices · confirmed again at checkout"
          : append ? "All products shown." : "No products match this search."
      );
      if (!append && !query) {
        try {
          localStorage.setItem(CACHE_KEY, JSON.stringify({
            savedAt: Date.now(), items: value.items,
          }));
        } catch (_) { /* Storage may be unavailable. */ }
      }
    } catch (error) {
      if (error.name === "AbortError" || serial !== requestSerial) return;
      nextPage = null;
      $("#more-products").hidden = true;
      const cached = !query && !append ? readCachedCatalog() : null;
      if (cached) {
        showProducts(cached.items, false);
        updateStatus("Showing a saved public catalog · prices and availability may have changed.");
      } else {
        if (!append) $("#products").replaceChildren();
        updateStatus("The catalog is temporarily unavailable. Check your connection and refresh.");
      }
    }
  }

  function setupCustomer() {
    $("#customer-content").hidden = false;
    main.textContent = "Shop securely ↗";
    main.addEventListener("click", () => openOfficial("/market/"));
    $("#refresh").addEventListener("click", () => loadCatalog());
    $("#more-products").addEventListener("click", () => {
      if (nextPage) loadCatalog({append: true, page: nextPage});
    });
    let delay;
    $("#search").addEventListener("input", () => {
      clearTimeout(delay);
      delay = setTimeout(() => loadCatalog(), 280);
    });
    $("#tab-account").addEventListener("click", () => openOfficial("/market/account/"));
    $("#tab-support").addEventListener("click", () => openOfficial("/market/messages/"));
    void loadCatalog();
  }

  function setupStaff() {
    $("#staff-content").hidden = false;
    main.textContent = "Open secure workspace ↗";
    main.addEventListener("click", () => openOfficial("/workspace/"));
    document.querySelectorAll(".native-shortcut").forEach(el => {
      const path = el.dataset.path;
      if (approvedUrl(path)) el.addEventListener("click", () => openOfficial(path));
    });
    $("#tab-account").addEventListener("click", () => openOfficial("/account/"));
    $("#tab-support").addEventListener("click", () => openOfficial("/email/"));
  }

  function newerStableVersion(installed, available) {
    const valid = /^\d+\.\d+\.\d+$/;
    if (!valid.test(installed || "") || !valid.test(available || "")) return false;
    const a = installed.split(".").map(Number);
    const b = available.split(".").map(Number);
    for (let i = 0; i < 3; i++) {
      if (!Number.isSafeInteger(a[i]) || !Number.isSafeInteger(b[i])) return false;
      if (b[i] > a[i]) return true;
      if (b[i] < a[i]) return false;
    }
    return false;
  }

  async function openUpdateHub() {
    // The update manifest cannot provide a redirect or download URL.
    // App installation is always delegated to KOFAD's fixed official hub.
    const url = CHANNEL === "staff"
      ? "https://staff.kofadimpex.com/staff/app/"
      : "https://kofadimpex.com/apps/";
    try {
      if (typeof native.Browser?.open === "function") {
        await native.Browser.open({url});
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
      }
    } catch (_) {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  }

  async function checkForMobileUpdate() {
    const platform = window.Capacitor?.getPlatform?.();
    if (!["android", "ios"].includes(platform) || typeof native.App?.getInfo !== "function") return;
    const route = CHANNEL === "staff"
      ? "/staff/app/native-version.json" : "/market/app/native-version.json";
    try {
      const [installed, response] = await Promise.all([
        native.App.getInfo(),
        fetch(OFFICIAL_ROOT + route, {
          mode: "cors", credentials: "omit", cache: "no-store",
        }),
      ]);
      if (!response.ok) return;
      const published = await response.json();
      if (published.channel !== CHANNEL) return;
      window.KofadNativeAlerts?.show(published.notices);
      if (!published.platforms?.[platform]
          || !newerStableVersion(installed.version, published.version)) return;
      const criticalMinimum = published.android_policy?.minimum_version || "";
      const isCritical = platform === "android" &&
        newerStableVersion(installed.version, criticalMinimum);
      if (isCritical) {
        const banner = $("#native-update");
        banner.setAttribute("role", "alert");
        banner.setAttribute("aria-live", "assertive");
        $("#native-update-details").textContent =
          (published.android_policy?.reason || "An important compatibility update is required.") +
          " Update the app before continuing.";
        $("#native-update-action").textContent = "Update required ↗";
        $("#native-update-action").onclick = openUpdateHub;
        $("#native-update-dismiss").hidden = true;
        $("#main").hidden = true;
        document.querySelector(".native-bottom-nav").hidden = true;
        banner.hidden = false;
        return;
      }
      const key = "kofad-app-update-dismissed-" + CHANNEL + "-" + published.version;
      try { if (sessionStorage.getItem(key)) return; } catch (_) {}
      const banner = $("#native-update");
      $("#native-update-details").textContent =
        "Version " + published.version + " is ready. Finish any open work before installing an update.";
      $("#native-update-action").onclick = openUpdateHub;
      $("#native-update-dismiss").onclick = () => {
        banner.hidden = true;
        try { sessionStorage.setItem(key, "1"); } catch (_) {}
      };
      banner.hidden = false;
    } catch (_) {
      // On network problems never suggest a guessed version or install source.
    }
  }

  async function setupConnectivity() {
    setNetwork(online);
    window.addEventListener("online", () => {
      setNetwork(true);
      if (CHANNEL === "customer") void loadCatalog();
    });
    window.addEventListener("offline", () => setNetwork(false));
    if (typeof native.Network?.getStatus === "function") {
      try { setNetwork((await native.Network.getStatus()).connected); } catch (_) {}
    }
    if (typeof native.Network?.addListener === "function") {
      try {
        await native.Network.addListener("networkStatusChange", evt => {
          const previous = online;
          setNetwork(evt.connected);
          if (!previous && evt.connected && CHANNEL === "customer") void loadCatalog();
        });
      } catch (_) {}
    }
  }

  $("#tab-home").addEventListener("click", () => window.scrollTo({top: 0, behavior: "smooth"}));
  if (CHANNEL === "customer") setupCustomer();
  else setupStaff();
  void setupConnectivity();
  void checkForMobileUpdate();
  if (typeof native.App?.addListener === "function") {
    native.App.addListener("appStateChange", evt => {
      if (evt.isActive) void checkForMobileUpdate();
    }).catch(() => {});
  }
})();
