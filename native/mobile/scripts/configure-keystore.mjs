import {readFileSync,writeFileSync,mkdirSync} from "node:fs";
import {resolve,join} from "node:path";
import {createRequire} from "node:module";
const require=createRequire(import.meta.url);
const {profileFor}=require("../profile.cjs");

export function patchActivity(source) {
  if (source.includes("registerPlugin(KofadVault.class)")) return source;
  const pattern=/\bpublic\s+class\s+MainActivity\s+extends\s+BridgeActivity\s*\{/;
  if (!pattern.test(source)) throw new Error("Unexpected generated MainActivity: refusing native credential installation");
  // Fail closed if the generated activity already owns lifecycle behaviour.
  if (/void\s+onCreate\s*\(/.test(source)) throw new Error("MainActivity lifecycle changed; review plugin registration before release");
  return source.replace(pattern,match=>match+`
    @Override
    public void onCreate(android.os.Bundle savedInstanceState) {
        registerPlugin(KofadVault.class);
        super.onCreate(savedInstanceState);
    }
`);
}

export function patchManifest(source) {
  if (!/<application\b/.test(source)) throw new Error("Generated AndroidManifest missing application");
  if (/android:allowBackup="(?:true|false)"/.test(source)) {
    return source.replace(/android:allowBackup="(?:true|false)"/,'android:allowBackup="false"');
  }
  return source.replace(/<application\b/,'<application android:allowBackup="false"');
}

export function packageSource(source,packageName) {
  if (!/^com\.kofadimpex\.(market|staff)$/.test(packageName)) throw new Error("Unexpected package");
  if (!source.includes("package __PACKAGE__;")) throw new Error("Credential vault Java package placeholder missing");
  return source.replaceAll("__PACKAGE__",packageName);
}

if (process.argv[1] && resolve(process.argv[1])===resolve(import.meta.filename)) {
  const profile=profileFor(process.env.KOFAD_NATIVE_CHANNEL);
  const pkgDir=resolve("android/app/src/main/java",...profile.appId.split("."));
  const activity=join(pkgDir,"MainActivity.java");
  const manifest=resolve("android/app/src/main/AndroidManifest.xml");
  const vault=join(pkgDir,"KofadVault.java");
  const template=readFileSync(resolve("native-android/KofadVault.java.template"),"utf8");
  const updatedActivity=patchActivity(readFileSync(activity,"utf8"));
  const updatedManifest=patchManifest(readFileSync(manifest,"utf8"));
  mkdirSync(pkgDir,{recursive:true});
  writeFileSync(vault,packageSource(template,profile.appId),"utf8");
  writeFileSync(activity,updatedActivity,"utf8");
  writeFileSync(manifest,updatedManifest,"utf8");
  console.log("Installed per-app AndroidKeyStore plugin for "+profile.appId+"; Android backup disabled.");
}
