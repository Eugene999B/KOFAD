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
    .replaceAll("__MAIN_ACTION__", channel === "customer"
      ? "Explore products" : "Enter secure workspace")
    .replaceAll("__HERO_KICKER__", channel === "customer" ? "THE MARKET, IN YOUR HAND" : "KOFAD STAFF")
    .replaceAll("__SIGNIN_ACTION__", channel === "customer" ? "Sign in" : "Sign in securely")
    .replaceAll("__SECONDARY_ACTION__", channel === "customer" ? "Continue as guest" : "Preview work modules")
    .replaceAll("__WELCOME_FOOTER__", channel === "customer"
      ? "Browse freely. A verified customer sign-in is required before ordering."
      : "Only authorized staff can access business records.")
    .replaceAll("__WELCOME_SUBTITLE__", channel === "customer"
      ? "Products, favourites and your orders, designed for your phone."
      : "Your operations, organized in one secure place.");
  writeFileSync(resolve(outputDir, filename), source, "utf8");
};
copyWithProfile("index.html");
copyWithProfile("native.js");
copyWithProfile("alerts.js");
copyWithProfile("mobile-auth.js");
copyWithProfile("experience.js");
copyFileSync(resolve(sourceDir, "native.css"), resolve(outputDir, "native.css"));
copyFileSync(resolve("../../static/brand/kofad-original-logo.png"), resolve(outputDir, "kofad-logo.png"));
console.log(`Bundled offline-capable native ${profile.appName} (${profile.appId}) with local UI, not server.url.`);
