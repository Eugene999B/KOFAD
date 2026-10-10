import test from "node:test";
import assert from "node:assert/strict";
import {createRequire} from "node:module";

const {profileFor} = createRequire(import.meta.url)("../profile.cjs");

test("the public KOFAD Android client keeps a stable customer-only package identity", () => {
  const customer = profileFor("customer");
  const staff = profileFor("staff");
  assert.equal(customer.appId, "com.kofadimpex.market");
  assert.equal(customer.appName, "KOFAD Market");
  assert.equal(customer.hostname, "market.kofadimpex.com");
  assert.equal(customer.startUrl, "https://market.kofadimpex.com/market/");
  assert.notEqual(customer.appId, staff.appId);
  assert.notEqual(customer.startUrl, staff.startUrl);
});
