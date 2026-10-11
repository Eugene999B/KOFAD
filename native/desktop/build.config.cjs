"use strict";
const { profileFor } = require("./profile.cjs");
const profile = profileFor(process.env.KOFAD_NATIVE_CHANNEL || "customer");
module.exports = {
  appId: profile.appId,
  productName: profile.productName,
  directories: { output: "dist/" + (process.env.KOFAD_NATIVE_CHANNEL || "customer") },
  files: ["main.cjs", "profile.cjs", "package.json"],
  asar: true,
  forceCodeSigning: process.env.KOFAD_NATIVE_SIGNED_RELEASE === "1",
  win: {
    icon: "build/icon.ico",
    target: [{ target: "nsis", arch: ["x64"] }],
    signtoolOptions: process.env.KOFAD_WINDOWS_CERT_SUBJECT
      ? { publisherName: process.env.KOFAD_WINDOWS_CERT_SUBJECT }
      : undefined,
    verifyUpdateCodeSignature: true,
  },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: "always",
    createStartMenuShortcut: true,
  },
  publish: [{ provider: "generic", url: profile.windowsFeed }],
};
