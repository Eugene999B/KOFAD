import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";

const root=resolve(import.meta.dirname,"..");
const read=path=>readFileSync(resolve(root,path),"utf8");

test("sync screen is packaged locally with separate explicit safe account controls",()=>{
  const html=read("app-shell/index.html");
  const cart=read("app-shell/basket.js");
  const auth=read("app-shell/mobile-auth.js");
  for(const id of ["native-basket-account-load","native-basket-account-save",
                    "native-basket-sync-status","native-basket-server-estimate"]) {
    assert.ok(html.includes('id="'+id+'"'),id);
  }
  assert.match(cart,/features\?\.mobile_cart/);
  assert.match(cart,/async function accountReady/);
  assert.match(cart,/async function loadAccount/);
  assert.match(cart,/async function syncAccount/);
  assert.match(cart,/Math\.max\(previous\.quantity, item\.quantity\)/);
  assert.match(auth,/async function saveMobileCart/);
  assert.match(auth,/method: "PUT"/);
  assert.match(auth,/credentials: "omit"/);
  assert.match(auth,/Authorization: "Bearer " \+ access/);
  assert.match(auth,/resource === "cart\/"/);
  assert.doesNotMatch(cart,/(?:innerHTML\s*=|eval\(|document\.write\(|payment-intent|createOrder)/);
  assert.doesNotMatch(auth,/(?:localStorage|sessionStorage)\s*\.\s*setItem/);
  assert.doesNotMatch(cart,/\/market\/checkout\//);
});

test("account-linked basket cannot silently activate without native auth and API readiness",()=>{
  const cart=read("app-shell/basket.js");
  assert.match(cart,/isAuthenticated\?\.\(\)/);
  assert.match(cart,/if \(syncing \|\| !await accountReady\(\)\) return;/);
  assert.match(cart,/KofadMobileAuth\.readMobile\("cart\/"\)/);
  assert.match(cart,/KofadMobileAuth\.saveMobileCart/);
  assert.match(cart,/Your device basket was not deleted/);
  assert.match(cart,/No order or payment has been placed/);
});
