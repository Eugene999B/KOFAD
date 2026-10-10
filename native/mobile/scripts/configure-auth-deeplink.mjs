import {readFileSync, writeFileSync} from "node:fs";
import {resolve} from "node:path";
import {createRequire} from "node:module";

const require = createRequire(import.meta.url);
const {profileFor} = require("../profile.cjs");
export function androidDeepLink(xml, appScheme) {
  if (xml.includes('android:scheme="' + appScheme + '"')) return xml;
  if (!xml.includes("</activity>")) throw new Error("Capacitor Android activity missing; cannot set up KOFAD login callback");
  const filter = `        <intent-filter>
            <action android:name="android.intent.action.VIEW" />
            <category android:name="android.intent.category.DEFAULT" />
            <category android:name="android.intent.category.BROWSABLE" />
            <data android:scheme="${appScheme}" android:host="auth" android:path="/callback" />
        </intent-filter>`;
  return xml.replace("</activity>",filter + "\n    </activity>");
}

export function iosDeepLink(plist, appScheme) {
  if (plist.includes("<string>" + appScheme + "</string>")) return plist;
  const last = plist.lastIndexOf("</dict>");
  if (last < 0 || !plist.includes("</plist>")) {
    throw new Error("Capacitor iOS Info.plist is missing; cannot set up KOFAD login callback");
  }
  const entry = `    <key>CFBundleURLTypes</key>
    <array>
        <dict>
            <key>CFBundleURLName</key>
            <string>com.kofadimpex.mobile-auth</string>
            <key>CFBundleURLSchemes</key>
            <array><string>${appScheme}</string></array>
        </dict>
    </array>
`;
  return plist.slice(0,last) + entry + plist.slice(last);
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(import.meta.filename)) {
  const channel = process.env.KOFAD_NATIVE_CHANNEL;
  const profile = profileFor(channel);
  const scheme = channel === "customer" ? "kofadmarket" : "kofadstaff";
  const platform = process.argv[2];
  const file = platform === "android"
    ? resolve("android/app/src/main/AndroidManifest.xml")
    : platform === "ios" ? resolve("ios/App/App/Info.plist") : null;
  if (!file) throw new Error("Select android or ios as the first argument");
  const original = readFileSync(file,"utf8");
  const updated = platform === "android" ? androidDeepLink(original,scheme)
    : iosDeepLink(original,scheme);
  if (updated !== original) writeFileSync(file,updated,"utf8");
  console.log("Configured " + platform + " login callback for " + profile.appId + " (" + scheme + "://auth/callback)");
}
