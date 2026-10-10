import type { CapacitorConfig } from "@capacitor/cli";
import { profileFor } from "./profile.cjs";

// Native UI is bundled inside the signed app; it no longer boots a remote
// website using Capacitor's development-only live-reload server.url option.
// Authenticated sales / checkout open official HTTPS sites through the native
// secure-browser plugin; account/payment sessions are not held in the app.
const profile = profileFor(process.env.KOFAD_NATIVE_CHANNEL || "customer");
const config: CapacitorConfig = {
  appId: profile.appId,
  appName: profile.appName,
  webDir: "www",
  server: {
    androidScheme: "https",
    cleartext: false,
  },
  ios: {
    scheme: "capacitor",
    contentInset: "automatic",
  },
  android: { allowMixedContent: false },
  plugins: {
    StatusBar: { style: "DARK", backgroundColor: "#10374c" },
  },
};
export default config;
