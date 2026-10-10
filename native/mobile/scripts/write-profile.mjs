import { createRequire } from "node:module";
import { mkdirSync, readFileSync, writeFileSync, copyFileSync } from "node:fs";
import { resolve } from "node:path";
const require = createRequire(import.meta.url);
const { profileFor } = require("../profile.cjs");
const channel = process.env.KOFAD_NATIVE_CHANNEL;
const profile = profileFor(channel);
const sourceDir = resolve("app-shell");
const outputDir = resolve("www");
mkdirSync(outputDir, {recursive: true});
const copyWithProfile = (filename) => {
  let source = readFileSync(resolve(sourceDir, filename), "utf8");
  source = source.replaceAll("__APP_ORIGIN__", new URL(profile.startUrl).origin)
    .replaceAll("__APP_NAME__", profile.appName)
    .replaceAll("__CHANNEL__", channel)
    .replaceAll("__WELCOME_TITLE__", channel === "customer"
      ? "Discover, shop, stay connected."
      : "Your business, one secure starting point.")
    .replaceAll("__WELCOME_SUBTITLE__", channel === "customer"
      ? "Explore live public products here. Sign in and complete orders securely through KOFAD Market."
      : "Open sales, inventory and approvals on the authorized staff domain. Your existing permissions stay in control.")
    .replaceAll("__MAIN_ACTION__", channel === "customer"
      ? "Continue to secure Market" : "Open staff workspace");
  writeFileSync(resolve(outputDir, filename), source, "utf8");
};
copyWithProfile("index.html");
copyWithProfile("native.js");
copyFileSync(resolve(sourceDir, "native.css"), resolve(outputDir, "native.css"));
console.log(`Bundled offline-capable native ${profile.appName} (${profile.appId}) with local UI, not server.url.`);
