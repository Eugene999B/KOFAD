(() => {
  "use strict";
  const script = document.currentScript;
  const channel = script?.dataset?.pwaChannel;
  if (!["customer", "staff"].includes(channel)) return;
  const isStaff = channel === "staff";
  const scope = isStaff ? "/" : "/market/";
  const sw = isStaff ? "/staff/app/sw.js" : "/market/app/sw.js";
  const buttons = document.querySelectorAll("[data-pwa-install]");
  const label = document.querySelector("[data-pwa-install-state]");
  const steps = document.querySelector("[data-pwa-install-steps]");
  const update = document.querySelector("[data-pwa-update]");
  const updateButton = document.querySelector("[data-pwa-update-apply]");
  let deferredInstall = null;
  let acceptedUpdate = false;
  const agent = navigator.userAgent || "";
  const platform = /iPhone|iPad|iPod/i.test(agent) ? "iPhone"
    : /Android/i.test(agent) ? "Android"
    : /Macintosh|Mac OS X/i.test(agent) ? "Mac"
    : /Windows/i.test(agent) ? "Windows" : "this device";
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
      return "Open this page in Safari. Tap Share (the square with an arrow), choose Add to Home Screen, keep Open as Web App on, and tap Add.";
    if (platform === "Mac")
      return "In Safari, choose File → Add to Dock. Or in Chrome, open the browser menu and choose Install page as app.";
    return "Use your browser menu to choose Install app or Add to Home Screen.";
  };
  const refresh = () => {
    const mainButton = document.querySelector(".kf-download-btn-main[data-pwa-install]");
    if (isStandalone()) {
      setStatus("Installed on your device");
      buttons.forEach(b => { b.disabled = true; });
      if (mainButton) mainButton.textContent = "App installed";
    } else if (deferredInstall) {
      setStatus("Ready to install");
      if (mainButton) mainButton.textContent = "Install KOFAD " + (isStaff ? "Staff" : "Market");
    } else {
      setStatus(platform === "iPhone" ? "Install using Safari's Share menu."
        : platform === "Mac" ? "Install from Safari or Chrome."
        : "Install from your browser in a few taps.");
      if (mainButton) mainButton.textContent =
        platform === "iPhone" ? "Add to Home Screen"
        : platform === "Mac" ? "Add KOFAD to Dock"
        : platform === "Windows" ? "Install on Windows" : "Install KOFAD " + (isStaff ? "Staff" : "Market");
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
  document.querySelector("[data-pwa-close-steps]")?.addEventListener("click", () => {
    if (steps) steps.hidden = true;
  });
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (acceptedUpdate) window.location.reload();
    });
    navigator.serviceWorker.register(sw, {scope, updateViaCache: "none"})
    .then(reg => {
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
    }).catch(() => setStatus("You can still add KOFAD from your browser menu."));
  }
  refresh();
  if (platform === "iPhone" && new URLSearchParams(location.search).get("device") === "iphone")
    showSteps();
})();