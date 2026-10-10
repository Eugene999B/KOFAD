import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";

const root = resolve(import.meta.dirname,"..");
const read = path => readFileSync(resolve(root,path),"utf8");

test("basket is a bounded local draft with no order or payment mutation", () => {
  const html=read("app-shell/index.html");
  const js=read("app-shell/basket.js");
  const shell=read("app-shell/experience.js");
  const profile=read("scripts/write-profile.mjs");
  for(const id of ["screen-basket","tab-basket","native-basket-items","native-basket-count","native-detail-basket","native-detail-share"]) {
    assert.ok(html.includes('id="' + id + '"'),id);
  }
  assert.match(js,/MAX_LINES = 40, MAX_QUANTITY = 20/);
  assert.match(js,/normalize\(JSON\.parse/);
  assert.match(js,/in_stock_snapshot === false/);
  assert.match(js,/KofadNativeBasket/);
  assert.match(shell,/native-detail-share/);
  assert.match(profile,/copyWithProfile\("basket\.js"\)/);
  assert.doesNotMatch(js,/(?:Authorization|fetch\(|Bearer|payment-intent|createOrder|checkout\/quote|innerHTML\s*=|document\.write\()/);
});

test("marketing messages are optional and device permission is requested by Android", () => {
  const notices=read("app-shell/alerts.js");
  const html=read("app-shell/index.html");
  assert.match(html,/native-promotions-toggle/);
  assert.match(notices,/marketing/);
  assert.match(notices,/OS\.requestPermissions/);
  assert.match(notices,/OS\.checkPermissions/);
  assert.doesNotMatch(notices,/innerHTML\s*=|eval\(/);
});
