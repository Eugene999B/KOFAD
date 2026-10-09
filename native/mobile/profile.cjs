"use strict";
const profiles = Object.freeze({
  customer: Object.freeze({
    appId: "com.kofadimpex.market",
    appName: "KOFAD Market",
    hostname: "market.kofadimpex.com",
    startUrl: "https://market.kofadimpex.com/market/",
  }),
  staff: Object.freeze({
    appId: "com.kofadimpex.staff",
    appName: "KOFAD Staff",
    hostname: "staff.kofadimpex.com",
    startUrl: "https://staff.kofadimpex.com/workspace/",
  }),
});
function profileFor(channel) {
  if (!Object.prototype.hasOwnProperty.call(profiles, channel)) {
    throw new Error("Unknown native application channel");
  }
  return profiles[channel];
}
module.exports = { profiles, profileFor };
