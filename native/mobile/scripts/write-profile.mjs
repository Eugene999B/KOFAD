import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
const require = createRequire(import.meta.url);
const { profileFor } = require("../profile.cjs");
const channel = process.env.KOFAD_NATIVE_CHANNEL;
const profile = profileFor(channel);
mkdirSync(resolve("www"), { recursive: true });
// Offline local screen is packaged in every build, rather than a white page.
writeFileSync(resolve("www/index.html"), `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#10374c"><title>${profile.appName}</title><style>body{margin:0;font:16px system-ui;background:#0b3048;color:white;display:grid;place-items:center;min-height:100svh;padding:26px;text-align:center}main{max-width:440px}b{display:grid;place-items:center;background:#1e8c8a;border-radius:20px;width:64px;height:64px;margin:auto;font-size:38px}p{line-height:1.7;color:#deeff1}a{display:inline-block;background:#f6d59b;color:#17374a;border-radius:12px;padding:14px 18px;font-weight:bold}</style><main><b>K</b><h1>${profile.appName}</h1><p>Secure connection is needed to access KOFAD accounts, stock and payment information. Reconnect to the internet and open the app again.</p><a href="${profile.startUrl}">Try secure KOFAD connection</a></main></html>`, "utf8");
console.log(`Prepared ${profile.appName} (${profile.appId}). Generate Android/iOS native projects with 'npx cap add android' and 'npx cap add ios' once, using this channel. Existing generated native project identifiers MUST match the selected channel before publishing.`);
