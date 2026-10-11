import {readFileSync,writeFileSync,mkdirSync} from "node:fs";
import {resolve,dirname} from "node:path";
import {createRequire} from "node:module";
const require=createRequire(import.meta.url);
const {profileFor}=require("../profile.cjs");

/* The Firebase Android client configuration is project-scoped, not a server
 * service-account secret. It MUST match the signed app channel. Never commit
 * it in source control or print its contents in CI logs.
 */
export function checkFirebaseConfig(config,expectedPackage){
  if(!config || typeof config!=="object" || Array.isArray(config)){
    throw new Error("Invalid Firebase Android configuration");
  }
  const p=config.project_info;
  if(!p || typeof p.project_id!=="string"
     || !/^[a-z0-9][a-z0-9-]{4,64}$/.test(p.project_id)
     || typeof p.project_number!=="string"
     || !/^[0-9]{6,25}$/.test(p.project_number)) {
    throw new Error("Missing Firebase project identity");
  }
  if("private_key" in config || "client_email" in config || config.type==="service_account"){
    throw new Error("A Firebase service account must never be packaged inside Android");
  }
  if(!Array.isArray(config.client) || config.client.length===0) {
    throw new Error("Firebase Android client list is missing");
  }
  const matches=config.client.filter(x=>x?.client_info?.android_client_info?.package_name===expectedPackage);
  if(matches.length!==1) throw new Error("Firebase Android package does not match the selected KOFAD app");
  const client=matches[0];
  if(typeof client.client_info.mobilesdk_app_id!=="string"
     || !/^1:[0-9]+:android:[0-9a-f]+$/.test(client.client_info.mobilesdk_app_id)
     || !Array.isArray(client.api_key) || client.api_key.length===0
     || !client.api_key.every(x=>typeof x.current_key==="string"&&x.current_key.length>=15)){
    throw new Error("Firebase Android configuration is incomplete");
  }
  return {projectId:p.project_id,packageName:expectedPackage};
}

export function decodeConfig(base64,expectedPackage){
  if(typeof base64!=="string" || base64.length>160000 || base64.length<40
     || !/^[A-Za-z0-9+/]+={0,2}$/.test(base64)) {
    throw new Error("Firebase config environment variable is not valid Base64");
  }
  const bytes=Buffer.from(base64,"base64");
  if(bytes.length>110000 || bytes.length<60 || bytes.toString("base64")!==base64){
    throw new Error("Firebase config Base64 payload is not canonical");
  }
  let config;
  try{config=JSON.parse(bytes.toString("utf8"));}catch(_){
    throw new Error("Firebase config JSON is invalid");
  }
  checkFirebaseConfig(config,expectedPackage);
  return bytes;
}

if(process.argv[1] && resolve(process.argv[1])===resolve(import.meta.filename)){
  const profile=profileFor(process.env.KOFAD_NATIVE_CHANNEL);
  const encoded=process.env.KOFAD_FIREBASE_CONFIG_B64||"";
  if(!encoded){
    if(process.env.KOFAD_REQUIRE_FIREBASE==="1")
      throw new Error("Signed Android release requires a protected, channel-specific Firebase configuration");
    console.log("Firebase Android client config not supplied; background FCM is disabled in this internal QA build.");
    process.exit(0);
  }
  const bytes=decodeConfig(encoded,profile.appId);
  const path=resolve("android/app/google-services.json");
  mkdirSync(dirname(path),{recursive:true});
  writeFileSync(path,bytes,{mode:0o600,flag:"w"});
  console.log("Validated Firebase config for "+profile.appId+"; project credentials are not logged.");
}
