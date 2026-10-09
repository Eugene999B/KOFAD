"use strict";
const { profileFor } = require("../profile.cjs");
const profile = profileFor(process.env.KOFAD_NATIVE_CHANNEL);
if (!process.env.KOFAD_WINDOWS_CERT_SUBJECT ||
    !process.env.WIN_CSC_LINK ||
    !process.env.WIN_CSC_KEY_PASSWORD ||
    process.env.KOFAD_NATIVE_SIGNED_RELEASE !== "1") {
  throw Error("Release blocked: Windows code-signing certificate, certificate password, verified publisher subject and explicit signed-release mode are required.");
}
if (!profile.windowsFeed.startsWith("https://downloads.kofadimpex.com/windows/")) {
  throw Error("Release blocked: unknown updater origin");
}
console.log(`Signing ${profile.productName} for trusted Windows release from ${profile.windowsFeed}`);
