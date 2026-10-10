import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {runInNewContext} from "node:vm";
import {webcrypto} from "node:crypto";

const root=resolve(import.meta.dirname,"..");
const read=path=>readFileSync(resolve(root,path),"utf8");
const {patchActivity,patchManifest,packageSource} =
  await import("../scripts/configure-keystore.mjs");

test("Generated Android app reliably registers separately packaged secure vault",()=>{
  const activity='package com.kofadimpex.market;\nimport com.getcapacitor.BridgeActivity;\npublic class MainActivity extends BridgeActivity {\n}\n';
  const registered=patchActivity(activity);
  assert.match(registered,/registerPlugin\(KofadVault.class\)/);
  assert.ok(registered.indexOf("registerPlugin(")<registered.indexOf("super.onCreate("));
  assert.equal(patchActivity(registered),registered);
  assert.throws(()=>patchActivity("public class MainActivity extends Activity {}"));
  assert.throws(()=>patchActivity('public class MainActivity extends BridgeActivity {void onCreate() {}}'));
  const manifest='<manifest><application android:allowBackup="true"><activity/></application></manifest>';
  const safe=patchManifest(manifest);
  assert.match(safe,/android:allowBackup="false"/);
  assert.equal(patchManifest(safe),safe);
  assert.match(patchManifest("<manifest><application /></manifest>"),/android:allowBackup="false"/);
  for(const pkg of ["com.kofadimpex.market","com.kofadimpex.staff"]){
    const java=packageSource(read("native-android/KofadVault.java.template"),pkg);
    assert.ok(java.includes("package "+pkg+";"));
    assert.ok(java.includes('KeyStore.getInstance("AndroidKeyStore")'));
    assert.ok(java.includes("KeyProperties.BLOCK_MODE_GCM"));
    assert.ok(java.includes("GCMParameterSpec(128, iv)"));
    assert.ok(java.includes(".commit()"));
    assert.ok(!java.includes("android.util.Log"));
  }
  assert.throws(()=>packageSource("package __PACKAGE__;","com.attacker"));
  for(const path of [".github/workflows/native-qa.yml",
                     ".github/workflows/native-android-signed.yml",
                     ".github/workflows/android-offline-release.yml"]){
    assert.match(read(resolve(root,"../..",path)),/configure-keystore\.mjs/);
  }
});

const flush=()=>new Promise(done=>setTimeout(done,20));

function nativeClient(channel, saved="") {
  const stored={value:saved,saves:[],removes:0,revoked:0};
  let handler=null,opened="";
  let exchanges=0;
  const elements=new Map();
  function node(id){
    if(!elements.has(id)) elements.set(id,{textContent:"",click(){},});
    return elements.get(id);
  }
  const origin=channel==="customer"?"https://market.kofadimpex.com":"https://staff.kofadimpex.com";
  const callback=channel==="customer"?"kofadmarket://auth/callback":"kofadstaff://auth/callback";
  const client=channel==="customer"?"kofad-market":"kofad-staff";
  const plugins={
    App:{
      addListener:async(name,fn)=>{if(name==="appUrlOpen")handler=fn;},
      getLaunchUrl:async()=>null,
    },
    Browser:{
      open:async({url})=>{opened=url;},
      close:async()=>{},
    },
    KofadVault:{
      load:async()=>({value:stored.value||null}),
      save:async({value})=>{stored.value=value;stored.saves.push(value);},
      remove:async()=>{stored.value="";stored.removes++;},
    },
  };
  const fakeFetch=async(url,opts={})=>{
    if(!String(url).startsWith(origin+"/"))throw Error("Nonofficial URL");
    if(url.endsWith("capabilities/"))return {ok:true,json:async()=>({
      native_mobile_token_login:true,channel,client_id:client,redirect_uri:callback,
    })};
    if(url.endsWith("token/")){
      exchanges++;
      const body=JSON.parse(opts.body);
      assert.equal(body.client_id,client);
      return {ok:true,json:async()=>({
        token_type:"Bearer",
        access_token:String(exchanges%2?"A":"B").repeat(43),
        refresh_token:String(exchanges%2?"R":"S").repeat(43),
        expires_in:900,
      })};
    }
    if(url.endsWith("me/"))return {ok:true,json:async()=>({
      channel,display_name:"KOFAD testing member",branch:{name:"HQ"},
    })};
    if(url.endsWith("revoke/")){stored.revoked++;return {ok:true,json:async()=>({revoked:true})};}
    throw Error("Unexpected URL "+url);
  };
  const sandbox={
    window:{Capacitor:{Plugins:plugins,getPlatform:()=>"android"}},
    crypto:webcrypto,
    TextEncoder,
    URL,
    btoa:s=>Buffer.from(s,"binary").toString("base64"),
    fetch:fakeFetch,
    navigator:{},
    document:{getElementById:node},
    console:{log:()=>{},warn:()=>{}},
  };
  runInNewContext(read("app-shell/mobile-auth.js").replaceAll("__CHANNEL__",channel),sandbox);
  return {api:sandbox.window.KofadMobileAuth,stored,callback,
    handler:()=>handler,opened:()=>opened,exchanges:()=>exchanges,
  };
}

for(const channel of ["customer","staff"]){
  test(channel+" saves refresh tokens only in Android vault and wipes on sign-out",async()=>{
    const native=nativeClient(channel);
    await flush(); // cold-start vault was empty
    assert.equal(await native.api.start(),true);
    const url=new URL(native.opened());
    assert.match(url.href,/\/authorize\//);
    assert.equal(url.searchParams.get("client_id"),channel==="customer"?"kofad-market":"kofad-staff");
    native.handler()({url:native.callback+"?code="+("C".repeat(43))+"&state="+url.searchParams.get("state")});
    await flush();
    assert.equal(native.api.isAuthenticated(),true);
    assert.equal(native.stored.value,"R".repeat(43));
    assert.deepEqual(native.stored.saves,["R".repeat(43)]);
    assert.equal(await native.api.signOut(),true);
    assert.equal(native.stored.value,"");
    assert.equal(native.stored.revoked,1);
    assert.equal(native.api.isAuthenticated(),false);
  });

  test(channel+" restores a saved refresh token by server rotation after restart",async()=>{
    const native=nativeClient(channel,"T".repeat(43));
    await flush();
    assert.equal(native.api.isAuthenticated(),true);
    assert.equal(native.exchanges(),1);
    assert.equal(native.stored.value,"R".repeat(43));
    assert.equal(native.stored.saves.length,1);
    assert.equal(native.opened(),"");
  });
}
