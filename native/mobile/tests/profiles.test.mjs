import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const { profileFor } = createRequire(import.meta.url)("../profile.cjs");
test("customer and staff use unique app IDs and isolated official secure origins", () => {
  const a = profileFor("customer"), b = profileFor("staff");
  assert.notEqual(a.appId, b.appId);
  assert.notEqual(a.hostname, b.hostname);
  assert.match(a.startUrl, /^https:\/\/market\.kofadimpex\.com\/market\//);
  assert.match(b.startUrl, /^https:\/\/staff\.kofadimpex\.com\/workspace\//);
});
test("invalid channel rejected", () => {
  assert.throws(() => profileFor("unknown"));
  assert.throws(() => profileFor("__proto__"));
});
