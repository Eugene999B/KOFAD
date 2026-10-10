(() => {
  "use strict";
  const script = document.currentScript;
  const channel = script?.dataset?.pwaChannel;
  if (!["customer", "staff"].includes(channel) ||
      !("serviceWorker" in navigator)) return;
  const isStaff = channel === "staff";
  const scope = isStaff ? "/" : "/market/";
  const sw = isStaff ? "/staff/app/sw.js" : "/market/app/sw.js";
  const buttons = document.querySelectorAll("[data-pwa-install]");
  const label = document.querySelector("[data-pwa-install-state]");
  const steps = document.querySelector("[data-pwa-install-steps]");
  const update = document.querySelector("[data-pwa-update]");
  const updateButton = document.querySelector("[data-pwa-update-apply]");
  let deferredInstall = null;
  let registration = null;
  let acceptedUpdate = false;
  const platform = /Android/i.test(navigator.userAgent) ? "Android"
    : /Windows/i.test(navigator.userAgent) ? "Windows"
    : /iPhone|iPad/i.test(navigator.userAgent) ? "iPhone" : "this device";
  const isStandalone = () =>
    window.matchMedia("(display-mode: standalone)").matches ||
    navigator.standalone === true;
  const setStatus = (message) => {
    if (label) label.textContent = message;
  };
  const advice = () => {
    if (isStandalone()) return "KOFAD is already open as an installed app.";
    if (platform === "Android")
      return "In Chrome, open the browser menu (⋮) and select Install app or Add to Home screen.";
    if (platform === "Windows")
      return "In Chrome or Edge, use the Install icon near the address bar, or choose Install this site as an app from the browser menu.";
    if (platform === "iPhone")
      return "In Safari, choose Share → Add to Home Screen. The Android/Windows installer is separate.";
    return "Open this page in Chrome or Edge and choose Install app from the browser menu.";
  };
  const refresh = () => {
    if (isStandalone()) {
      setStatus("Already installed on this device.");
      buttons.forEach(b => { b.textContent = "App installed"; b.disabled = true; });
    } else if (deferredInstall) {
      setStatus("Your browser supports one-tap installation.");
      buttons.forEach(b => { b.textContent = "Install KOFAD " + (isStaff ? "Staff" : "Market"); b.disabled = false; });
    } else {
      setStatus("Install through your browser — no APK download needed.");
      buttons.forEach(b => { b.textContent = "Show installation steps"; b.disabled = false; });
    }
  };
  const showSteps = () => {
    if (steps) {
      steps.hidden = false;
      const item = steps.querySelector("[data-pwa-device-steps]");
      if (item) item.textContent = advice();
      steps.scrollIntoView({behavior: "smooth", block: "nearest"});
    }
  };
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredInstall = event;
    refresh();
  });
  window.addEventListener("appinstalled", () => {
    deferredInstall = null;
    if (steps) steps.hidden = true;
    setStatus("KOFAD has been added to your device.");
    refresh();
  });
  buttons.forEach(b => b.addEventListener("click", async () => {
    if (!deferredInstall) { showSteps(); return; }
    const prompt = deferredInstall;
    deferredInstall = null;
    try {
      await prompt.prompt();
      const choice = await prompt.userChoice;
      setStatus(choice?.outcome === "accepted"
        ? "Installation accepted. Check your device's apps."
        : "You can install later using your browser.");
    } catch (_) { showSteps(); }
    refresh();
  }));
  const notifyUpdate = (reg) => {
    if (!reg.waiting || !update || !updateButton || !document.querySelector("[data-pwa-install-page]"))
      return;
    update.hidden = false;
    updateButton.onclick = () => {
      acceptedUpdate = true;
      reg.waiting?.postMessage({type: "SKIP_WAITING"});
    };
  };
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (acceptedUpdate) window.location.reload();
  });
  navigator.serviceWorker.register(sw, {scope, updateViaCache: "none"})
    .then(reg => {
      registration = reg;
      if (reg.waiting && navigator.serviceWorker.controller) notifyUpdate(reg);
      reg.addEventListener("updatefound", () => {
        const worker = reg.installing;
        worker?.addEventListener("statechange", () => {
          if (worker.state === "installed" && navigator.serviceWorker.controller) notifyUpdate(reg);
        });
      });
      if (document.querySelector("[data-pwa-install-page]")) {
        reg.update().catch(() => {});
      }
    }).catch(() => setStatus("Installation is temporarily unavailable. Refresh this secure page."));
  refresh();
})();