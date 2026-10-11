"use strict";
(() => {
  // All notices are generic. The server never sends personal account data.
  const CHANNEL = "__CHANNEL__";
  const PREFIX = "kofad-mobile-alerts-" + CHANNEL + "-";
  const OS = window.Capacitor?.Plugins?.LocalNotifications;
  const list = document.getElementById("native-notice-list");
  const button = document.getElementById("native-alerts-toggle");
  const explainer = document.getElementById("native-alerts-explainer");
  const promotionsButton = document.getElementById("native-promotions-toggle");
  let notices = [], allNotices = [];
  const key = name => PREFIX + name;
  const get = name => {
    try { return localStorage.getItem(key(name)); } catch (_) { return null; }
  };
  const put = (name, value) => {
    try { localStorage.setItem(key(name), value); } catch (_) { /* Private mode */ }
  };
  const enabled = () => get("permission") === "enabled";
  const promotionsEnabled = () => get("marketing") === "enabled";
  const promotional = n => n.kind === "promotion" || n.category === "marketing";
  function renderPromotions() {
    if (!promotionsButton) return;
    promotionsButton.textContent = promotionsEnabled() ? "Offers enabled" : "Enable offers";
    promotionsButton.setAttribute("aria-pressed", String(promotionsEnabled()));
  }
  const renderToggle = () => {
    if (button) button.textContent = enabled() ? "Device alerts on" : "Enable device alerts";
  };

  function show(entries) {
    allNotices = Array.isArray(entries) ? entries.filter(n =>
      Number.isSafeInteger(n?.id) && n.id > 0 &&
      typeof n.title === "string" && n.title.length <= 90 &&
      typeof n.message === "string" && n.message.length <= 280
    ).slice(0, 20) : [];
    renderNotices();
    void alertNew();
  }

  function renderNotices() {
    notices = allNotices.filter(n => !promotional(n) || promotionsEnabled());
    if (list) {
      list.replaceChildren();
      if (!notices.length) {
        const empty = document.createElement("p");
        empty.textContent = "No announcements at the moment.";
        list.append(empty);
      }
      for (const n of notices) {
        const card = document.createElement("article");
        card.className = "native-announcement";
        const title = document.createElement("strong");
        title.textContent = n.title;
        const detail = document.createElement("p");
        detail.textContent = n.message;
        card.append(title, detail);
        list.append(card);
      }
    }
  }

  async function alertNew() {
    // Local notices only when a user opens/resumes the app. They are NOT
    // Firebase background push notifications.
    if (!enabled() || !OS?.schedule || !OS?.checkPermissions) return;
    try {
      const permission = await OS.checkPermissions();
      if (permission.display !== "granted") return;
      let delivered = [];
      try { delivered = JSON.parse(get("delivered") || "[]"); } catch (_) {}
      if (!Array.isArray(delivered)) delivered = [];
      for (const notice of notices.filter(n => !delivered.includes(n.id)).slice(0, 3)) {
        await OS.schedule({ notifications: [{
          id: notice.id, title: notice.title, body: notice.message,
          schedule: { at: new Date(Date.now() + 1800) },
        }] });
        delivered.push(notice.id);
      }
      put("delivered", JSON.stringify(delivered.slice(-60)));
    } catch (_) { /* The in-app inbox remains available without OS permission. */ }
  }

  if (button) {
    button.addEventListener("click", async () => {
      if (enabled()) {
        put("permission", "disabled");
        renderToggle();
        return;
      }
      if (!OS?.requestPermissions) {
        if (explainer) explainer.textContent = "Device alerts are unavailable; announcements remain visible here.";
        return;
      }
      try {
        const result = await OS.requestPermissions();
        if (result.display === "granted") {
          // No flood of notifications for old announcements on first opt-in.
          put("delivered", JSON.stringify(notices.map(n => n.id)));
          put("permission", "enabled");
        } else if (explainer) {
          explainer.textContent = "Permission was not granted. You can enable notifications in Android Settings.";
        }
      } catch (_) {
        if (explainer) explainer.textContent = "Notification permission is unavailable on this device.";
      }
      renderToggle();
    });
  }
  promotionsButton?.addEventListener("click", () => {
    put("marketing", promotionsEnabled() ? "disabled" : "enabled");
    renderPromotions();
    renderNotices();
    // Marketing notifications need both explicit consent and OS alert permission.
    // Older messages are considered already seen to avoid a burst at opt-in.
    if (promotionsEnabled()) {
      let delivered = [];
      try { delivered = JSON.parse(get("delivered") || "[]"); } catch (_) {}
      if (!Array.isArray(delivered)) delivered = [];
      put("delivered", JSON.stringify([...delivered, ...notices.filter(promotional).map(n=>n.id)].slice(-60)));
    }
  });
  renderPromotions();
  renderToggle();
  window.KofadNativeAlerts = Object.freeze({ show });
})();
