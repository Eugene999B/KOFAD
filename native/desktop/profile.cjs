"use strict";
const profiles = Object.freeze({
  customer: Object.freeze({
    appId: "com.kofadimpex.market.windows", productName: "KOFAD Market",
    userData: "KOFAD-Market", startUrl: "https://market.kofadimpex.com/market/",
    hosts: ["market.kofadimpex.com", "kofadimpex.com"],
    windowsFeed: "https://downloads.kofadimpex.com/windows/customer/",
  }),
  staff: Object.freeze({
    appId: "com.kofadimpex.staff.windows", productName: "KOFAD Staff",
    userData: "KOFAD-Staff", startUrl: "https://staff.kofadimpex.com/login/",
    hosts: ["staff.kofadimpex.com"],
    windowsFeed: "https://downloads.kofadimpex.com/windows/staff/",
  }),
});
function profileFor(channel) {
  if (!Object.prototype.hasOwnProperty.call(profiles, channel)) {
    throw Error("Invalid native application channel");
  }
  return profiles[channel];
}
function isInternal(profile, value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.port) return false;
    if (!profile.hosts.includes(url.hostname.toLowerCase())) return false;
    if (profile === profiles.staff) return true; // staff domain, server enforces auth
    if (url.hostname === "market.kofadimpex.com") return url.pathname.startsWith("/market/");
    return new Set(["/", "/apps/", "/about/", "/faq/", "/contact/",
      "/delivery/", "/returns-policy/", "/terms/", "/privacy/"]).has(url.pathname);
  } catch (_) { return false; }
}
function isAllowedExternal(value) {
  try {
    const u = new URL(value);
    if (u.protocol !== "https:" || u.username || u.password || u.port) return false;
    // OAuth/hosted payment links in the user's default secure browser.
    return ["accounts.google.com", "checkout.paystack.com",
      "paystack.com", "paystack.shop", "paylink.hubtel.com",
      "app.hubtel.com", "apps.apple.com", "play.google.com"].includes(u.hostname);
  } catch (_) { return false; }
}
module.exports = { profileFor, isInternal, isAllowedExternal };
