"use strict";
/* Real background pushes require KOFAD Firebase configuration and signed
 * Capacitor PushNotifications installation. NEVER simulate background
 * delivery with polling, a browser permission, or local notifications.
 * The user must explicitly tap Enable; default is no subscription.
 */
(() => {
  const CHANNEL="__CHANNEL__";
  const CUSTOMER=CHANNEL==="customer";
  const ROOT=CUSTOMER?"https://market.kofadimpex.com/market/mobile/v1/"
                     :"https://staff.kofadimpex.com/staff/mobile/v1/";
  const $=id=>document.getElementById(id);
  const plugin=window.Capacitor?.Plugins?.PushNotifications;
  let pending=false;
  let registered=false;
  let listenersReady=false;
  function message(text) {
    const status=$("native-push-status");
    if(status)status.textContent=text;
  }
  function busy(value) {
    pending=value;
    if($("native-push-enable"))$("native-push-enable").disabled=value;
    if($("native-push-disable"))$("native-push-disable").disabled=value;
  }
  async function serverReady() {
    try {
      const result=await fetch(ROOT+"capabilities/",{
        method:"GET",mode:"cors",credentials:"omit",cache:"no-store",
      });
      if(!result.ok)return false;
      const flags=await result.json();
      return flags.channel===CHANNEL &&
        flags.native_mobile_token_login===true &&
        flags.native_mobile_push===true;
    }catch(_){return false;}
  }
  async function listeners() {
    if (listenersReady)return;
    await plugin.addListener("registration",async result=>{
      if(!pending || !result?.value)return;
      const marketing=CUSTOMER && $("native-push-marketing")?.checked===true;
      const saved=await window.KofadMobileAuth?.pushDeviceRegistration?.(
        result.value,true,marketing
      );
      if(saved?.registered) {
        registered=true;
        message("Device registered with KOFAD for your chosen categories. Background delivery is pending Firebase activation.");
      }else {
        message("Could not register this device with KOFAD. No background messages are enabled.");
      }
      busy(false);
    });
    await plugin.addListener("registrationError",()=>{
      message("Android could not obtain a Firebase device token. Try again after configuration.");
      busy(false);
    });
    await plugin.addListener("pushNotificationActionPerformed",()=>{
      $("tab-notices")?.click();
    });
    listenersReady=true;
  }
  async function enable() {
    if(pending)return;
    if(!plugin || typeof plugin.register!=="function" ||
       typeof plugin.checkPermissions!=="function") {
      message("Real Android background push is not included in this app build yet.");
      return;
    }
    if(!window.KofadMobileAuth?.isAuthenticated?.()) {
      message("Sign in securely in your Account tab before enabling device notifications.");
      return;
    }
    if(!await serverReady()){
      message("KOFAD has not activated verified Firebase device notifications yet.");
      return;
    }
    busy(true);
    try {
      await listeners();
      let permission=await plugin.checkPermissions();
      if(permission.receive==="prompt"||permission.receive==="prompt-with-rationale")
        permission=await plugin.requestPermissions();
      if(permission.receive!=="granted"){
        message("Android notification permission was not granted. You can enable it in system settings.");
        busy(false);
        return;
      }
      message("Registering this Android device securely…");
      await plugin.register();
      // Capacitor emits registration or registrationError asynchronously.
    }catch(_){
      message("Unable to register Android notifications. Your account was not changed.");
      busy(false);
    }
  }
  async function disable() {
    if(pending)return;
    busy(true);
    try {
      const reply=await window.KofadMobileAuth?.pushDeviceUnsubscribe?.();
      if(reply?.revoked) {
        registered=false;
        message("This KOFAD device subscription was removed from your account.");
      }else {
        message("Could not confirm removal. Retry when connected or revoke your mobile session.");
      }
    }finally{busy(false);}
  }
  const enableButton=$("native-push-enable");
  const disableButton=$("native-push-disable");
  if(CUSTOMER) $("native-push-marketing-wrap").hidden=false;
  if(!window.Capacitor || window.Capacitor.getPlatform?.()!=="android") {
    message("Device background push is only available in the configured Android app.");
  }else if(!plugin){
    message("Firebase background messaging is not configured for this Android release.");
  }else{
    message("Background alerts are optional. Choose Enable after signing in.");
  }
  enableButton?.addEventListener("click",()=>void enable());
  disableButton?.addEventListener("click",()=>void disable());
  window.KofadNativePush=Object.freeze({isRegistered:()=>registered});
})();
