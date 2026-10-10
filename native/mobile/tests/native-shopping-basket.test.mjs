import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {runInNewContext} from "node:vm";

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
  assert.doesNotMatch(js,/(?:Authorization|Bearer|payment-intent|createOrder|checkout\\/quote|innerHTML\\s*=|document\\.write\\()/);
  assert.match(js,/method: "GET", mode: "cors", credentials: "omit"/);
  // This module only reads public capability flags; account writes are
  // delegated to the allowlisted native bearer client under explicit consent.
  assert.doesNotMatch(js,/fetch\\([^)]*method: "PUT"/);
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

test("a real local basket add is quantity-bounded and never persists prices or tokens", () => {
  const storage = new Map();
  function node() {
    return {
      textContent:"", hidden:false, disabled:false, children:[],
      setAttribute(){}, addEventListener(){},
      append(...items){this.children.push(...items);},
      replaceChildren(...items){this.children=items;},
    };
  }
  const elements=new Map();
  const getElementById=id=>{
    if(!elements.has(id))elements.set(id,node());
    return elements.get(id);
  };
  const sandbox={
    window:{},
    document:{getElementById,createElement:()=>node()},
    navigator:{onLine:true},
    localStorage:{
      getItem:key=>storage.get(key)??null,
      setItem:(key,value)=>storage.set(key,value),
    },
  };
  runInNewContext(read("app-shell/basket.js").replaceAll("__CHANNEL__","customer"),sandbox);
  const cart=sandbox.window.KofadNativeBasket;
  assert.ok(cart);
  assert.equal(cart.count(),0);
  assert.equal(cart.addProduct({id:7,name:"Public cotton",price:"109.99"}),true);
  assert.equal(cart.addProduct({id:7,name:"Public cotton"}),true);
  assert.equal(cart.count(),2);
  assert.equal(cart.addProduct({id:8,name:"Out of stock",in_stock_snapshot:false}),false);
  assert.equal(cart.addProduct({id:-3,name:"Bad product"}),false);
  assert.equal(cart.count(),2);
  const stored=storage.get("kofad-market-local-basket-v1");
  assert.deepEqual(JSON.parse(stored),[{id:7,name:"Public cotton",quantity:2}]);
  assert.ok(!stored.includes("109.99") && !stored.includes("token"));
  for(let i=0;i<18;i++)assert.equal(cart.addProduct({id:7,name:"Public cotton"}),true);
  assert.equal(cart.count(),20);
  assert.equal(cart.addProduct({id:7,name:"Public cotton"}),false);
});
