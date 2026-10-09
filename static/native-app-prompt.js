(() => {
  "use strict";
  const suggestion = document.querySelector("[data-native-suggest]");
  if (!suggestion) return;
  if (window.Capacitor?.isNativePlatform?.()) return; // Already in the app.
  const agent = navigator.userAgent || "";
  const platform = /Android/i.test(agent) ? "android"
    : /iPad|iPhone|iPod/i.test(agent) ? "ios"
    : /Windows/i.test(agent) ? "windows" : "";
  // Do not advertise Android if only the Windows edition is released, etc.
  if (!platform || suggestion.dataset[platform] !== "yes") return;
  // The prompt is marketing, not a security or permission check. Show at most
  // once per browser session, never over checkout/login or private staff pages.
  const path = window.location.pathname;
  if (path.startsWith("/market/checkout/") || path.includes("/account/") ||
      path.includes("/payment/") || path.startsWith("/staff/") ||
      path.startsWith("/apps/")) return;
  let shown = false;
  try {
    shown = window.sessionStorage.getItem("kofad-native-market-suggested-v1") === "yes";
  } catch (_) { shown = true; } // Fail closed if storage is restricted.
  if (shown) return;
  try { window.sessionStorage.setItem("kofad-native-market-suggested-v1", "yes"); } catch (_) {}
  const close = suggestion.querySelector("[data-native-suggest-dismiss]");
  if (close) close.addEventListener("click", () => { suggestion.hidden = true; });
  window.setTimeout(() => { suggestion.hidden = false; }, 2500);
})();
