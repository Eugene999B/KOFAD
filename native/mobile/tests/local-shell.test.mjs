import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
const root = resolve(import.meta.dirname, "..");
const read = path => readFileSync(resolve(root, path), "utf8");

test("Mobile config packages local native UI and does not use dev server.url", () => {
  const config = read("capacitor.config.ts");
  assert.match(config, /webDir:\s*"www"/);
  assert.doesNotMatch(config, /^\s*url:\s*profile\.startUrl/m);
  assert.doesNotMatch(config, /allowNavigation/);
  assert.match(config, /cleartext:\s*false/);
});

test("Customer and staff are independently built, including branded pages", () => {
  for (const channel of ["customer", "staff"]) {
    const stdout = execFileSync("node", ["scripts/write-profile.mjs"], {
      cwd: root, env: {...process.env, KOFAD_NATIVE_CHANNEL: channel},
      encoding: "utf8",
    });
    assert.match(stdout, /Bundled offline-capable native/);
    const html = read("www/index.html");
    const js = read("www/native.js");
    assert.ok(existsSync(resolve(root, "www/native.css")));
    assert.match(html, new RegExp(channel === "customer" ? "KOFAD Market" : "KOFAD Staff"));
    assert.match(html, /Content-Security-Policy/);
    assert.match(js, new RegExp('const CHANNEL = "' + channel + '"'));
    assert.doesNotMatch(html, /__APP_NAME__|__APP_ORIGIN__|__CHANNEL__|__WELCOME_/);
    assert.match(html, new RegExp("connect-src https://" + (channel === "customer" ? "market" : "staff") + "\\.kofadimpex\\.com"));
    assert.doesNotMatch(html, new RegExp("connect-src https://" + (channel === "customer" ? "staff" : "market") + "\\.kofadimpex\\.com"));
    assert.doesNotMatch(js, /__CHANNEL__/);
    assert.doesNotMatch(html, /password|api_key|secret_key/i);
  }
});

test("Only official HTTPS origins can be opened for protected operations", () => {
  const js = read("app-shell/native.js");
  assert.match(js, /https:\/\/market\.kofadimpex\.com/);
  assert.match(js, /https:\/\/staff\.kofadimpex\.com/);
  assert.match(js, /function approvedUrl/);
  assert.match(js, /native\.Browser\.open/);
  assert.doesNotMatch(js, /eval\(|document\.write\(/);
});

test("No offline transaction queue or unsafe application bridge", () => {
  const local = read("app-shell/native.js");
  const cfg = read("capacitor.config.ts");
  assert.doesNotMatch(local, /localStorage\.setItem\([^,]*payment/i);
  assert.doesNotMatch(cfg, /^\s*url:\s*profile\.startUrl/m);
  assert.doesNotMatch(cfg, /allowMixedContent:\s*true/);
});

test("Native app updates compare installed versions and use only fixed official hubs", () => {
  const src = read("app-shell/native.js");
  const html = read("app-shell/index.html");
  assert.match(src, /native\.App\.getInfo/);
  assert.match(src, /native-version\.json/);
  assert.match(src, /newerStableVersion/);
  assert.match(src, /appStateChange/);
  assert.match(src, /https:\/\/kofadimpex\.com\/apps\//);
  assert.match(src, /https:\/\/staff\.kofadimpex\.com\/staff\/app\//);
  assert.match(html, /id="native-update"/);
  assert.match(src, /cache: "no-store"/);
  assert.doesNotMatch(src, /eval\(|document\.write\(/);
});

test("Native mandatory Android updates and opt-in app notices have isolated controls", () => {
  const js = read("app-shell/native.js");
  const alerts = read("app-shell/alerts.js");
  const html = read("app-shell/index.html");
  const packager = read("scripts/write-profile.mjs");
  assert.match(js, /criticalMinimum/);
  assert.match(js, /Update required/);
  assert.match(js, /native-update-dismiss/);
  assert.match(js, /KofadNativeAlerts/);
  assert.match(alerts, /OS.requestPermissions/);
  assert.match(alerts, /OS.checkPermissions/);
  assert.match(alerts, /document.createElement/);
  assert.match(html, /id="native-alerts-toggle"/);
  assert.ok(packager.includes('copyWithProfile("alerts.js")'));
  assert.doesNotMatch(alerts, /Contacts.getContacts|READ_CONTACTS|READ_MEDIA_IMAGES/);
});

test("Both Android channels bundle a genuine native home, welcome and separate notifications screen", () => {
  const html = read("app-shell/index.html");
  const experience = read("app-shell/experience.js");
  const native = read("app-shell/native.js");
  const alerts = read("app-shell/alerts.js");
  const packager = read("scripts/write-profile.mjs");
  for (const id of ["native-welcome", "native-welcome-signin", "native-welcome-guest", "native-bell", "screen-home", "screen-product", "screen-saved", "screen-notifications", "screen-account"]) {
    assert.ok(html.includes('id="' + id + '"'), id);
  }
  assert.ok(html.includes('id="native-alerts-toggle"'));
  assert.ok(html.includes('id="native-notice-list"'));
  assert.match(experience, /KofadNativeExperience/);
  assert.match(experience, /showProduct:detail/);
  assert.match(native, /KofadNativeExperience\?\.showProduct/);
  assert.match(native, /KofadNativeBridge/);
  assert.match(alerts, /OS.requestPermissions/);
  assert.doesNotMatch(experience, /READ_CONTACTS|READ_MEDIA_IMAGES|READ_EXTERNAL_STORAGE|eval\(/);
  assert.ok(packager.includes('copyWithProfile("experience.js")'));
  assert.ok(packager.includes('kofad-original-logo.png'));
  for (const channel of ["customer", "staff"]) {
    execFileSync("node", ["scripts/write-profile.mjs"], {cwd:root,env:{...process.env,KOFAD_NATIVE_CHANNEL:channel}});
    const out = read("www/index.html");
    assert.doesNotMatch(out, /__(WELCOME|MAIN_ACTION|HERO_KICKER|SIGNIN_ACTION|SECONDARY_ACTION|APP_NAME)/);
    assert.ok(existsSync(resolve(root, "www/kofad-logo.png")));
    assert.ok(existsSync(resolve(root, "www/experience.js")));
  }
});
