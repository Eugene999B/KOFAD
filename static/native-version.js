(() => {
  "use strict";
  // The native mobile bridge exists only inside the actual Android/iOS build.
  // Never treat a user agent string or query parameter as proof of identity.
  const native = window.Capacitor;
  if (!native || typeof native.isNativePlatform !== "function" || !native.isNativePlatform()) return;
  const platform = native.getPlatform && native.getPlatform();
  if (platform !== "android" && platform !== "ios") return;
  const staff = location.hostname === "staff.kofadimpex.com";
  const customer = location.hostname === "market.kofadimpex.com";
  if (!staff && !customer) return;
  const plugins = native.Plugins || {};
  if (!plugins.App || typeof plugins.App.getInfo !== "function") return;
  const endpoint = staff ? "/staff/app/releases.json" : "/market/app/releases.json";
  const semver = (value) => {
    if (typeof value !== "string" || !/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(value)) return null;
    return value.split(/[+-]/)[0].split(".").map(Number);
  };
  function isNewer(current, latest) {
    const a = semver(current), b = semver(latest);
    if (!a || !b) return false;
    for (let i = 0; i < 3; i++) {
      if (b[i] > a[i]) return true;
      if (b[i] < a[i]) return false;
    }
    return false;
  }
  function trustedStoreLink(raw) {
    try {
      const url = new URL(raw);
      if (url.protocol !== "https:" || url.username || url.password) return false;
      return platform === "ios"
        ? url.hostname === "apps.apple.com"
        : url.hostname === "play.google.com" || url.hostname === "kofadimpex.com" || url.hostname.endsWith(".kofadimpex.com");
    } catch (_) { return false; }
  }
  async function check() {
    try {
      const [installed, response] = await Promise.all([
        plugins.App.getInfo(), fetch(endpoint, { cache: "no-store", credentials: "same-origin" }),
      ]);
      if (!response.ok) return;
      const release = await response.json();
      const platformRelease = release.platforms && release.platforms[platform];
      if (!platformRelease?.available || !trustedStoreLink(platformRelease.url) ||
          !isNewer(installed.version, release.version)) return;
      const marker = "kofad-native-update-dismissed-" + (staff ? "staff-" : "customer-") + release.version;
      if (sessionStorage.getItem(marker)) return;
      if (document.getElementById("kofad-native-version-banner")) return;
      const banner = document.createElement("aside");
      banner.id = "kofad-native-version-banner";
      banner.className = "native-suggest";
      banner.setAttribute("role", "status");
      banner.setAttribute("aria-label", "Native app update available");
      const content = document.createElement("div");
      const headline = document.createElement("strong");
      headline.textContent = "KOFAD app update available";
      const text = document.createElement("p");
      text.textContent = "Version " + release.version + " is ready. Your account and saved server records stay intact.";
      const action = document.createElement("a");
      action.textContent = "View verified update";
      action.href = platformRelease.url;
      action.rel = "noopener noreferrer";
      action.target = "_blank";
      action.addEventListener("click", async event => {
        if (plugins.Browser && typeof plugins.Browser.open === "function") {
          event.preventDefault();
          try { await plugins.Browser.open({ url: platformRelease.url }); }
          catch (_) { window.open(platformRelease.url, "_blank", "noopener"); }
        }
      });
      const dismiss = document.createElement("button");
      dismiss.type = "button";
      dismiss.setAttribute("aria-label", "Dismiss update notice");
      dismiss.textContent = "×";
      dismiss.addEventListener("click", () => {
        try { sessionStorage.setItem(marker, "1"); } catch (_) {}
        banner.remove();
      });
      content.append(headline, text, action);
      banner.append(content, dismiss);
      document.body.appendChild(banner);
    } catch (_) { /* No update notice is safer than an unauthenticated one. */ }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", check);
  else void check();
})();
