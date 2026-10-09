"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { profileFor, isInternal, isAllowedExternal } = require("../profile.cjs");
test("Windows client app IDs, data stores and updater feeds never overlap", () => {
  const a = profileFor("customer"), b = profileFor("staff");
  assert.notEqual(a.appId,b.appId);
  assert.notEqual(a.userData,b.userData);
  assert.notEqual(a.windowsFeed,b.windowsFeed);
  assert.notEqual(a.startUrl,b.startUrl);
});
test("reject invalid app channel", () => {
  assert.throws(() => profileFor("merchant"));
  assert.throws(() => profileFor("__proto__"));
});
test("staff renderer cannot navigate to customer or arbitrary hosts", () => {
  const staff = profileFor("staff");
  assert.equal(isInternal(staff,"https://staff.kofadimpex.com/workspace/"), true);
  assert.equal(isInternal(staff,"https://market.kofadimpex.com/market/"), false);
  assert.equal(isInternal(staff,"https://staff.kofadimpex.com.evil.test/workspace/"), false);
  assert.equal(isInternal(staff,"http://staff.kofadimpex.com/workspace/"), false);
  assert.equal(isInternal(staff,"https://user:pass@staff.kofadimpex.com/"), false);
});
test("customer client only accepts official market and permitted public paths", () => {
  const app = profileFor("customer");
  assert.equal(isInternal(app,"https://market.kofadimpex.com/market/cart/"), true);
  assert.equal(isInternal(app,"https://kofadimpex.com/terms/"), true);
  assert.equal(isInternal(app,"https://staff.kofadimpex.com/login/"), false);
  assert.equal(isInternal(app,"https://kofadimpex.com/workspace/"), false);
});
test("external destinations strictly limited to trusted identity and checkout services", () => {
  assert.equal(isAllowedExternal("https://accounts.google.com/"), true);
  assert.equal(isAllowedExternal("https://checkout.paystack.com/test"), true);
  assert.equal(isAllowedExternal("https://checkout.paystack.com.evil.test/"), false);
  assert.equal(isAllowedExternal("file:///etc/passwd"), false);
  assert.equal(isAllowedExternal("javascript:alert(1)"), false);
});
