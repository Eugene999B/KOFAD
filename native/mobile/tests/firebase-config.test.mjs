import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {checkFirebaseConfig,decodeConfig} from "../scripts/configure-firebase.mjs";

const root=resolve(import.meta.dirname,"..");
const read=path=>readFileSync(resolve(root,path),"utf8");
const create=packageName=>({
  project_info:{project_id:"kofad-test-firebase",project_number:"123456789012"},
  client:[{
    client_info:{mobilesdk_app_id:"1:123456789012:android:abcdef123456",android_client_info:{
      package_name:packageName,
    }},
    api_key:[{current_key:"TEST_KEY_NOT_A_REAL_FIREBASE_KEY_12345"}],
  }],
});
test("Firebase Android configuration validates against each signed channel identity",()=>{
  for(const pkg of ["com.kofadimpex.market","com.kofadimpex.staff"]){
    const source=create(pkg);
    assert.equal(checkFirebaseConfig(source,pkg).packageName,pkg);
    const b64=Buffer.from(JSON.stringify(source)).toString("base64");
    assert.equal(JSON.parse(decodeConfig(b64,pkg).toString("utf8")).client[0].client_info.android_client_info.package_name,pkg);
    const other=pkg.endsWith(".market")?"com.kofadimpex.staff":"com.kofadimpex.market";
    assert.throws(()=>checkFirebaseConfig(source,other),/does not match/);
  }
  assert.throws(()=>decodeConfig("not base64", "com.kofadimpex.market"));
  const wrong=create("com.kofadimpex.market");
  wrong.private_key="FAKE_PRIVATE_KEY_MUST_NOT_SHIP";
  assert.throws(()=>checkFirebaseConfig(wrong,"com.kofadimpex.market"),/service account/);
  const absent=create("com.kofadimpex.market");
  absent.client[0].api_key=[];
  assert.throws(()=>checkFirebaseConfig(absent,"com.kofadimpex.market"),/incomplete/);
  const multiple=create("com.kofadimpex.market");
  multiple.client.push({...multiple.client[0]});
  assert.throws(()=>checkFirebaseConfig(multiple,"com.kofadimpex.market"),/does not match/);
});
test("Firebase delivery dependency is linked to actual Capacitor Android package",()=>{
  const manifest=JSON.parse(read("package.json"));
  assert.match(manifest.dependencies["@capacitor/push-notifications"],/^\^7\./);
  const yml=read(resolve(root,"../..",".github/workflows/native-android-signed.yml"));
  assert.match(yml,/KOFAD_CUSTOMER_FIREBASE_CONFIG_B64/);
  assert.match(yml,/KOFAD_STAFF_FIREBASE_CONFIG_B64/);
  assert.match(yml,/KOFAD_REQUIRE_FIREBASE: "1"/);
  assert.match(yml,/node scripts\/configure-firebase\.mjs/);
  const qa=read(resolve(root,"../..",".github/workflows/native-qa.yml"));
  assert.match(qa,/node scripts\/configure-firebase\.mjs/);
  const push=read("app-shell/push.js");
  assert.match(push,/isPluginAvailable\?\.\("PushNotifications"\)/);
  assert.match(push,/native_mobile_push===true/);
});
