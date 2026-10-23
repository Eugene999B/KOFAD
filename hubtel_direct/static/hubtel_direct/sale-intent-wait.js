/* CSP-safe, read-only status refresh for the issued wallet preapproval.
 * Never POST, reissue an authorization request, or record a payment from here.
 */
(() => {
  "use strict";
  const target = document.querySelector("[data-v2-status-url]");
  if (!target) return;
  const url = target.dataset.v2StatusUrl;
  let last = target.dataset.v2Status || "";
  if (!url || !url.startsWith("/") || url.startsWith("//")) return;
  async function refresh() {
    if (document.hidden) return;
    try {
      const response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return;
      const current = await response.json();
      if (typeof current.status === "string" && current.status !== last) {
        last = current.status;
        location.reload();
      }
    } catch (_) { /* Network failures do not repeat authorization. */ }
  }
  window.setInterval(refresh, 10000);
})();
