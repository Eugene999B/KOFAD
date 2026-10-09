(() => {
  "use strict";
  const bridge = window.Capacitor;
  if (!bridge?.isNativePlatform?.() || !["android", "ios"].includes(bridge.getPlatform?.())) return;
  const official = location.hostname === "market.kofadimpex.com" || location.hostname === "staff.kofadimpex.com";
  if (!official) return;
  const { Network, Share } = bridge.Plugins || {};

  // No offline posting queue for money or stock. Show connection status and do
  // NOT replay POST requests when the connection returns.
  const notice = document.createElement("div");
  notice.className = "native-connection-notice";
  notice.setAttribute("role", "status");
  notice.textContent = "You are offline. KOFAD cannot confirm new transactions until connection is restored.";
  notice.hidden = true;
  const setup = () => {
    document.body.appendChild(notice);
    const showNetwork = status => {
      notice.hidden = Boolean(status.connected);
    };
    if (Network) {
      if (typeof Network.getStatus === "function") Network.getStatus().then(showNetwork).catch(() => {});
      if (typeof Network.addListener === "function") Network.addListener("networkStatusChange", showNetwork).catch(() => {});
    }
    if (location.hostname !== "market.kofadimpex.com") return;
    const shareButton = document.querySelector("[data-native-share-product]");
    if (shareButton && Share?.share) {
      shareButton.hidden = false;
      shareButton.addEventListener("click", async () => {
        const target = shareButton.dataset.nativeShareProduct;
        if (!target || !target.startsWith("https://market.kofadimpex.com/market/products/")) return;
        try {
          await Share.share({
            title: shareButton.dataset.nativeShareTitle || "KOFAD Market product",
            text: "Have a look at this product in KOFAD Market.",
            url: target,
          });
        } catch (_) { /* Dismissed native share sheet; no network write. */ }
      });
    }
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup);
  else setup();
})();
