import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";

const root=resolve(import.meta.dirname,"..");
const read=path=>readFileSync(resolve(root,path),"utf8");

test("KOFAD native market orders and staff branch overview have real app screens",()=>{
  const html=read("app-shell/index.html");
  const experience=read("app-shell/experience.js");
  const client=read("app-shell/mobile-auth.js");
  for(const id of ["screen-orders","native-orders-list","native-order-detail","screen-work","native-work-summary"]) {
    assert.ok(html.includes('id="' + id + '"'),id);
  }
  assert.match(experience,/async function showOrders/);
  assert.match(experience,/async function showOrderDetails/);
  assert.match(experience,/async function showWork/);
  assert.match(experience,/KofadMobileAuth\?\.readMobile/);
  assert.match(client,/async function readMobile/);
  assert.match(client,/headers: \{Authorization: "Bearer " \+ access\}/);
  assert.match(client,/credentials: "omit"/);
  assert.doesNotMatch(experience,/innerHTML\s*=|document\.write\(|eval\(/);
  assert.doesNotMatch(client,/(?:localStorage|sessionStorage)\s*\.\s*setItem/);
  assert.doesNotMatch(experience,/localStorage\.setItem\(.*(?:orders|staff|permission|access)/);
});
