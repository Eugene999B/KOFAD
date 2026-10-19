import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
import {androidDeepLink, iosDeepLink} from "../scripts/configure-auth-deeplink.mjs";

const sampleAndroid = `<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application><activity android:name=".MainActivity" android:exported="true"><intent-filter><action android:name="android.intent.action.MAIN" /></intent-filter></activity></application></manifest>`;
const sampleIos = `<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>CFBundleName</key><string>App</string></dict></plist>`;

for (const [channel,scheme] of [["customer","kofadmarket"],["staff","kofadstaff"]]) {
  test(channel + " Android uses a separate host-scoped OAuth callback intent", () => {
    const output=androidDeepLink(sampleAndroid,scheme);
    assert.match(output, new RegExp('android:scheme="' + scheme + '"'));
    assert.match(output,/android:host="auth"/);
    assert.match(output,/android:path="\/callback"/);
    assert.match(output,/android.intent.category.BROWSABLE/);
    assert.equal(androidDeepLink(output,scheme),output);
    const other=scheme==="kofadmarket"?"kofadstaff":"kofadmarket";
    assert.doesNotMatch(output,new RegExp('android:scheme="' + other + '"'));
  });

  test(channel + " iOS registers only its channel-specific return scheme", () => {
    const output=iosDeepLink(sampleIos,scheme);
    assert.match(output,/CFBundleURLTypes/);
    assert.match(output,new RegExp("<string>" + scheme + "</string>"));
    assert.equal(iosDeepLink(output,scheme),output);
  });
}

test("Mobile identity bundle uses browser PKCE and never stores credentials in web storage", () => {
  const root=resolve(import.meta.dirname,"..");
  const client=readFileSync(resolve(root,"app-shell/mobile-auth.js"),"utf8");
  const html=readFileSync(resolve(root,"app-shell/index.html"),"utf8");
  const packager=readFileSync(resolve(root,"scripts/write-profile.mjs"),"utf8");
  const nativeWorkflows=[
    ".github/workflows/native-qa.yml",
    ".github/workflows/native-android-signed.yml",
    ".github/workflows/native-ios-signed.yml",
    ".github/workflows/android-offline-release.yml",
  ];
  assert.match(client,/crypto\.subtle\.digest\("SHA-256"/);
  assert.match(client,/crypto\.getRandomValues/);
  assert.match(client,/appUrlOpen/);
  assert.match(client,/kofadmarket:\/\/auth\/callback/);
  assert.match(client,/kofadstaff:\/\/auth\/callback/);
  assert.match(client,/credentials: "omit"/);
  assert.match(client,/KofadMobileAuth/);
  assert.doesNotMatch(client,/(?:localStorage|sessionStorage)\s*\.\s*(?:setItem|getItem)|document\s*\.\s*cookie\s*=|indexedDB\s*\./);
  assert.match(html,/src="mobile-auth.js"/);
  assert.match(packager,/copyWithProfile\("mobile-auth\.js"\)/);
  for (const path of nativeWorkflows){
    const yml=readFileSync(resolve(root,"../..",path),"utf8");
    assert.match(yml,/node scripts\/configure-auth-deeplink\.mjs (android|ios)/);
  }
});
