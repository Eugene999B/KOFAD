import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {runInNewContext} from "node:vm";

const root=resolve(import.meta.dirname,"..");
const read=p=>readFileSync(resolve(root,p),"utf8");

test("Both Android clients bundle strictly opt-in remote push controls",()=>{
  const html=read("app-shell/index.html"),js=read("app-shell/push.js");
  const auth=read("app-shell/mobile-auth.js"),packer=read("scripts/write-profile.mjs");
  for(const id of ["native-push-enable","native-push-disable","native-push-status",
                   "native-push-marketing","native-push-marketing-wrap"]){
    assert.ok(html.includes('id="'+id+'"'),id);
  }
  assert.match(html,/src="push\.js"/);
  assert.match(packer,/copyWithProfile\("push\.js"\)/);
  assert.match(js,/native_mobile_push===true/);
  assert.match(js,/checkPermissions\(\)/);
  assert.match(js,/requestPermissions\(\)/);
  assert.match(js,/pushDeviceRegistration/);
  assert.match(js,/pushDeviceUnsubscribe/);
  assert.match(auth,/method:"POST",mode:"cors",credentials:"omit"/);
  assert.match(auth,/method:"DELETE",mode:"cors",credentials:"omit"/);
  assert.match(auth,/Authorization:"Bearer "\+access/);
  assert.doesNotMatch(js,/(?:localStorage|sessionStorage|document\.cookie|innerHTML\s*=|eval\()/);
  assert.doesNotMatch(auth,/localStorage\s*\.\s*setItem|sessionStorage\s*\.\s*setItem/);
});

test("No background permission or push registration occurs automatically",()=>{
  const calls=[],handlers={};
  const elems=new Map();
  const element=id=>{
    if(!elems.has(id))elems.set(id,{
      hidden:true,disabled:false,textContent:"",checked:false,
      addEventListener(name,fn){handlers[id+":"+name]=fn;},
    });
    return elems.get(id);
  };
  const platform={
    register:async()=>calls.push("register"),
    checkPermissions:async()=>{calls.push("check");return {receive:"granted"};},
    requestPermissions:async()=>{calls.push("request");return {receive:"granted"};},
    addListener:async()=>{},
  };
  const sandbox={
    window:{Capacitor:{getPlatform:()=>"android",Plugins:{PushNotifications:platform}},
            KofadMobileAuth:{isAuthenticated:()=>false}},
    document:{getElementById:element},
    navigator:{},
    fetch:async()=>{throw Error("No automatic network requests");},
  };
  runInNewContext(read("app-shell/push.js").replaceAll("__CHANNEL__","customer"),sandbox);
  assert.deepEqual(calls,[]);
  assert.equal(element("native-push-marketing-wrap").hidden,false);
  assert.equal(typeof handlers["native-push-enable:click"],"function");
  handlers["native-push-enable:click"]();
  assert.deepEqual(calls,[]);
  assert.match(element("native-push-status").textContent,/Sign in securely/);
});
