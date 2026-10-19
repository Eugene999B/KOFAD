(() => {
  "use strict";
  // Only public paths ever send a minimal, consented event to KOFAD's own
  // backend. No PostHog JS SDK, browser cookies or private page data is sent.
  const publicPaths = new Set([
    "/", "/market/", "/about/", "/faq/", "/contact/", "/delivery/",
    "/returns-policy/", "/terms/", "/privacy/"
  ]);
  if (!publicPaths.has(location.pathname)) return;
  const allowedHost = location.hostname === "kofadimpex.com"
    || location.hostname === "www.kofadimpex.com"
    || location.hostname === "market.kofadimpex.com";
  if (!allowedHost) return;
  if ((location.pathname === "/market/") !== (location.hostname === "market.kofadimpex.com")) return;

  let sent = false;
  function sendIfConsented() {
    if (sent || !window.KofadPrivacy?.allows("analytics")) return;
    const csrf = document.querySelector('meta[name="kofad-analytics-csrf"]')?.content;
    if (!csrf) return;
    sent = true;
    fetch("/market/analytics/page-view/", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrf},
      body: JSON.stringify({path: location.pathname}),
      keepalive: true
    }).catch(() => {});
  }
  document.addEventListener("DOMContentLoaded", sendIfConsented);
  window.addEventListener("kofad:cookie-choice", sendIfConsented);
})();