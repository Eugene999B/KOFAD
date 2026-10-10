"use strict";
/* PKCE mobile account connection for KOFAD's bundled, local Capacitor UI.
 * No passwords, bearer tokens, or refresh tokens are written to web storage.
 * The production server advertises readiness; until enabled we retain the
 * existing official browser sign-in without exposing a broken login screen.
 */
(() => {
  const CHANNEL = "__CHANNEL__";
  const CUSTOMER = CHANNEL === "customer";
  const ORIGIN = CUSTOMER ? "https://market.kofadimpex.com" : "https://staff.kofadimpex.com";
  const ROOT = CUSTOMER ? "/market/mobile/v1/" : "/staff/mobile/v1/";
  const CLIENT = CUSTOMER ? "kofad-market" : "kofad-staff";
  const CALLBACK = CUSTOMER ? "kofadmarket://auth/callback" : "kofadstaff://auth/callback";
  const NATIVE = window.Capacitor?.Plugins || {};
  let pending = null;
  let session = null;
  let supported = null;
  let busy = false;

  const encode64 = bytes => {
    let raw = "";
    for (const value of bytes) raw += String.fromCharCode(value);
    return btoa(raw).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  };
  const random = bytes => encode64(crypto.getRandomValues(new Uint8Array(bytes)));

  async function readiness() {
    if (supported !== null) return supported;
    if (typeof NATIVE.Browser?.open !== "function" || typeof NATIVE.App?.addListener !== "function"
        || !window.crypto?.subtle || !window.crypto?.getRandomValues) return false;
    try {
      const reply = await fetch(ORIGIN + ROOT + "capabilities/", {
        method: "GET", mode: "cors", credentials: "omit", cache: "no-store",
      });
      if (!reply.ok) return false;
      const result = await reply.json();
      supported = result?.native_mobile_token_login === true
        && result.channel === CHANNEL && result.client_id === CLIENT && result.redirect_uri === CALLBACK;
      return supported;
    } catch (_) {
      return false;
    }
  }

  async function start() {
    if (busy) return true;
    if (session) {
      if (!session.profile) await profile();
      updateAccount();
      return true;
    }
    if (!await readiness()) return false;
    busy = true;
    try {
      const verifier = random(48);
      const state = random(24);
      const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
      pending = {verifier, state, created: Date.now()};
      const url = new URL(ORIGIN + ROOT + "authorize/");
      url.searchParams.set("client_id", CLIENT);
      url.searchParams.set("redirect_uri", CALLBACK);
      url.searchParams.set("code_challenge", encode64(hash));
      url.searchParams.set("code_challenge_method", "S256");
      url.searchParams.set("state", state);
      await NATIVE.Browser.open({url: url.href, presentationStyle: "fullscreen"});
      return true;
    } catch (_) {
      pending = null;
      return false;
    } finally {
      busy = false;
    }
  }

  async function token(body) {
    const response = await fetch(ORIGIN + ROOT + "token/", {
      method: "POST", mode: "cors", credentials: "omit", cache: "no-store",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({...body, client_id: CLIENT}),
    });
    if (!response.ok) throw new Error("Please sign in again.");
    const data = await response.json();
    if (data.token_type !== "Bearer" || typeof data.access_token !== "string"
        || !/^[A-Za-z0-9_-]{43}$/.test(data.access_token)
        || typeof data.refresh_token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(data.refresh_token)) {
      throw new Error("Invalid mobile session response.");
    }
    return data;
  }

  function adopt(issued) {
    // Memory only. Persisting a refresh token requires a reviewed Android Keystore
    // or iOS Keychain implementation. Never use localStorage for credentials.
    session = {
      access: issued.access_token, refresh: issued.refresh_token,
      expires: Date.now() + (Number(issued.expires_in) || 900) * 1000,
      profile: null,
    };
  }

  function clear() {
    session = null;
    pending = null;
    updateAccount();
  }

  async function accessToken() {
    if (!session) return "";
    if (Date.now() + 30000 >= session.expires) {
      try {
        const renewed = await token({grant_type: "refresh_token", refresh_token: session.refresh});
        adopt(renewed);
      } catch (_) {clear();return "";}
    }
    return session?.access || "";
  }

  async function profile() {
    const access = await accessToken();
    if (!access) return null;
    try {
      const response = await fetch(ORIGIN + ROOT + "me/", {
        method: "GET", mode: "cors", credentials: "omit", cache: "no-store",
        headers: {Authorization: "Bearer " + access},
      });
      if (!response.ok) {if (response.status === 401 || response.status === 403) clear();return null;}
      const data = await response.json();
      if (data.channel !== CHANNEL) return null;
      if (session) session.profile = data;
      updateAccount();
      return data;
    } catch (_) {return null;}
  }

  function updateAccount() {
    const title = document.getElementById("native-account-title");
    const name = document.getElementById("native-account-head");
    const hint = document.getElementById("native-account-copy");
    const button = document.getElementById("native-account-login");
    if (!title || !name || !hint || !button) return;
    if (session?.profile) {
      title.textContent = CUSTOMER ? "My KOFAD Market" : "My KOFAD Staff";
      name.textContent = String(session.profile.display_name || "KOFAD member").slice(0, 100);
      hint.textContent = CUSTOMER ? "Signed in to KOFAD Market on this device."
        : "Signed in · " + String(session.profile.branch?.name || "Staff").slice(0, 70);
      button.textContent = "Sign out of this device";
    } else {
      title.textContent = CUSTOMER ? "Account" : "Staff access";
      name.textContent = CUSTOMER ? "Make KOFAD yours" : "Your secure workspace";
      hint.textContent = CUSTOMER ? "Sign in to access your KOFAD account securely."
        : "Verify your identity and branch permissions to enter your workspace.";
      button.textContent = CUSTOMER ? "Sign in securely →" : "Staff sign in →";
    }
  }

  async function callback(raw) {
    if (!pending || typeof raw !== "string" || Date.now() - pending.created > 180000) {
      pending = null; return;
    }
    let url;
    try {url = new URL(raw);} catch (_) {return;}
    if (url.protocol !== new URL(CALLBACK).protocol
        || url.hostname !== "auth" || url.pathname !== "/callback"
        || url.searchParams.get("state") !== pending.state) return;
    const code = url.searchParams.get("code");
    if (!code || !/^[A-Za-z0-9_-]{43}$/.test(code)) return;
    const current = pending;
    pending = null;
    try {
      const issued = await token({
        grant_type: "authorization_code", code,
        code_verifier: current.verifier, redirect_uri: CALLBACK,
      });
      adopt(issued);
      try {await NATIVE.Browser?.close?.();} catch (_) {}
      await profile();
      const tab = document.getElementById("tab-account");
      tab?.click();
    } catch (_) {clear();}
  }

  async function readMobile(resource) {
    const allowed = CUSTOMER
      ? /^orders\/(?:[0-9a-fA-F-]{36}\/)?$/.test(resource)
      : resource === "overview/";
    if (!allowed) return {error: "unsupported_mobile_resource"};
    const access = await accessToken();
    if (!access) return {error: "authentication_required"};
    try {
      const reply = await fetch(ORIGIN + ROOT + resource, {
        method: "GET", mode: "cors", credentials: "omit", cache: "no-store",
        headers: {Authorization: "Bearer " + access},
      });
      if (reply.status === 401 || reply.status === 403) {
        clear();
        return {error: "session_expired"};
      }
      if (!reply.ok) return {error: "temporarily_unavailable"};
      return await reply.json();
    } catch (_) {return {error: "connection_unavailable"};}
  }

  async function signOut() {
    const access = await accessToken();
    clear();
    if (!access) return;
    try {
      await fetch(ORIGIN + ROOT + "revoke/", {
        method: "POST", mode: "cors", credentials: "omit", cache: "no-store",
        headers: {Authorization: "Bearer " + access, "Content-Type": "application/json"}, body: "{}",
      });
    } catch (_) {
      // Tokens still expire server-side. If offline, try again from the server's
      // account/device management before treating remote session as revoked.
    }
  }

  if (typeof NATIVE.App?.addListener === "function") {
    void NATIVE.App.addListener("appUrlOpen", event => void callback(event?.url));
    void NATIVE.App.getLaunchUrl?.().then(event => {if(event?.url) void callback(event.url);}).catch(() => {});
  }

  window.KofadMobileAuth = Object.freeze({
    start, profile, signOut, readMobile,
    isAuthenticated: () => Boolean(session?.profile),
    isSupported: readiness,
  });
})();
