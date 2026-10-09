import type { CapacitorConfig } from "@capacitor/cli";
import { profileFor } from "./profile.cjs";

// Two discrete installed applications, not two login screens inside one app.
// Build variants separately and never publish the staff package as a public
// consumer app. HTTPS only and app-specific hostname boundaries.
const profile = profileFor(process.env.KOFAD_NATIVE_CHANNEL || "customer");
const config: CapacitorConfig = {
  appId: profile.appId,
  appName: profile.appName,
  webDir: "www",
  server: {
    url: profile.startUrl,
    allowNavigation: [profile.hostname],
    cleartext: false,
    androidScheme: "https",
  },
  ios: {
    scheme: "capacitor",
    contentInset: "automatic",
  },
  android: {
    allowMixedContent: false,
  },
  plugins: {
    StatusBar: { style: "DARK", backgroundColor: "#10374c" },
  },
};
export default config;
