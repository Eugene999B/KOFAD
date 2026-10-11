(() => {
  "use strict";
  const secure = location.protocol === "https:";
  const name = secure ? "__Host-kofad_consent" : "kofad_consent";
  const lifetime = 180 * 86400000;
  let choice = null;
  let analyticsStarted = false;
  const preferenceKeys = ["kofad-theme", "kofad-welcome-sound"];
  try {
    const value = document.cookie.split("; ").find(row => row.startsWith(name + "="));
    const saved = value && JSON.parse(decodeURIComponent(value.slice(name.length + 1)));
    if (saved && saved.version === 1 && typeof saved.preferences === "boolean"
        && typeof saved.analytics === "boolean" && Number.isFinite(saved.savedAt)
        && saved.savedAt <= Date.now() && Date.now() - saved.savedAt < lifetime) choice = saved;
  } catch (_) {}
  const allows = category => Boolean(choice && choice[category] === true);
  window.KofadPrivacy = {
    allows,
    getPreference(key, fallback) {
      if (!preferenceKeys.includes(key) || !allows("preferences")) return fallback;
      try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; }
    },
    setPreference(key, value) {
      if (!preferenceKeys.includes(key) || !allows("preferences")) return;
      try { localStorage.setItem(key, value); } catch (_) {}
    }
  };
  function removeAnalyticsCookies() {
    const names = document.cookie.split(";").map(row => row.trim().split("=")[0])
      .filter(key => key === "_ga" || key.startsWith("_ga_") || key === "_gid" || key.startsWith("_gat"));
    for (const key of names) {
      document.cookie = key + "=; Max-Age=0; Path=/; SameSite=Lax" + (secure ? "; Secure" : "");
      if (location.hostname === "kofadimpex.com" || location.hostname.endsWith(".kofadimpex.com"))
        document.cookie = key + "=; Max-Age=0; Path=/; Domain=kofadimpex.com; SameSite=Lax" + (secure ? "; Secure" : "");
    }
  }
  function applyAnalytics(id) {
    if (!/^G-[A-Z0-9]{4,20}$/.test(id)) return;
    window["ga-disable-" + id] = !allows("analytics");
    if (!allows("analytics")) { removeAnalyticsCookies(); return; }
    if (analyticsStarted) return;
    analyticsStarted = true;
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag("js", new Date());
    window.gtag("config", id, {
      allow_google_signals: false, allow_ad_personalization_signals: false,
      send_page_view: false
    });
    // Send only a public page category; never account/order URLs, search terms,
    // referrers, email addresses, payment references or customer identifiers.
    const path = ["/", "/market/", "/about/", "/faq/", "/contact/", "/delivery/",
      "/returns-policy/", "/terms/", "/privacy/"].includes(location.pathname)
      ? location.pathname : "/market/other/";
    window.gtag("event", "page_view", {
      page_location: location.origin + path, page_referrer: "", page_title: "KOFAD website"
    });
    const script = document.createElement("script");
    script.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(id);
    script.async = true;
    document.head.appendChild(script);
  }
  document.addEventListener("DOMContentLoaded", () => {
    const panel = document.querySelector("[data-cookie-panel]");
    if (!panel) return;
    const preferences = panel.querySelector("[name=cookie_preferences]");
    const analytics = panel.querySelector("[name=cookie_analytics]");
    const options = panel.querySelector("[data-cookie-options]");
    const status = document.querySelector("[data-cookie-status]");
    const id = panel.dataset.analyticsId || "";
    const openers = document.querySelectorAll("[data-cookie-open]");
    const analyticsAvailable = /^G-[A-Z0-9]{4,20}$/.test(id);
    analytics.disabled = !analyticsAvailable;
    let returnFocus = null;
    function open(showOptions) {
      returnFocus = document.activeElement;
      preferences.checked = allows("preferences");
      analytics.checked = analyticsAvailable && allows("analytics");
      options.hidden = !showOptions;
      panel.hidden = false;
      panel.querySelector("[data-cookie-reject]").focus({preventScroll: true});
    }
    function save(preferenceChoice, analyticsChoice) {
      choice = {version: 1, preferences: preferenceChoice,
        analytics: analyticsAvailable && analyticsChoice, savedAt: Date.now()};
      document.cookie = name + "=" + encodeURIComponent(JSON.stringify(choice))
        + "; Max-Age=15552000; Path=/; SameSite=Lax" + (secure ? "; Secure" : "");
      if (!choice.preferences) {
        try { preferenceKeys.forEach(key => localStorage.removeItem(key)); } catch (_) {}
      } else {
        window.KofadPrivacy.setPreference("kofad-theme",
          document.documentElement.dataset.themePreference || "light");
      }
      if (!choice.analytics) removeAnalyticsCookies();
      applyAnalytics(id);
      window.dispatchEvent(new CustomEvent("kofad:cookie-choice", {detail: {...choice}}));
      panel.hidden = true;
      if (returnFocus && returnFocus.isConnected) returnFocus.focus({preventScroll: true});
      status.textContent = "Your cookie choices have been saved.";
    }
    openers.forEach(button => button.addEventListener("click", () => open(true)));
    panel.querySelector("[data-cookie-customise]").addEventListener("click", () => {
      options.hidden = false;
      preferences.focus();
    });
    panel.querySelector("[data-cookie-reject]").addEventListener("click", () => save(false, false));
    panel.querySelector("[data-cookie-accept]").addEventListener("click", () => save(true, analyticsAvailable));
    panel.querySelector("[data-cookie-save]").addEventListener("click", () => save(preferences.checked, analytics.checked));
    panel.addEventListener("keydown", event => {
      if (event.key === "Escape") { panel.hidden = true; if (returnFocus) returnFocus.focus(); }
    });
    if (!choice) open(false);
    applyAnalytics(id);
  });
})();
