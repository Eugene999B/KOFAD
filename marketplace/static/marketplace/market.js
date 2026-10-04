document.addEventListener("DOMContentLoaded", () => {
  const mediaDark = window.matchMedia("(prefers-color-scheme: dark)");
  const getTheme = () => {
    try { return localStorage.getItem("kofad-theme") || "light"; }
    catch (_) { return "light"; }
  };
  const applyTheme = preference => {
    const resolved = preference === "system" ? (mediaDark.matches ? "dark" : "light") : preference;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.themePreference = preference;
    document.querySelectorAll("[data-theme-toggle]").forEach(button => {
      const dark = resolved === "dark";
      const icon = button.querySelector("[data-theme-icon]");
      const label = button.querySelector("[data-theme-label]");
      if (icon) icon.textContent = dark ? "☀" : "☾";
      if (label) label.textContent = dark ? "Light" : "Dark";
      button.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
    });
  };
  applyTheme(getTheme());
  mediaDark.addEventListener?.("change", () => {
    if (getTheme() === "system") applyTheme("system");
  });
  document.querySelectorAll("[data-theme-toggle]").forEach(button => button.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("kofad-theme", next); } catch (_) {}
    applyTheme(next);
  }));

  const fulfilment = document.querySelector("[data-checkout-form] select[name='fulfilment']");
  const deliveryFields = document.querySelector("[data-delivery-fields]");
  const syncFulfilment = () => {
    if (!fulfilment || !deliveryFields) return;
    deliveryFields.hidden = fulfilment.value !== "delivery";
  };
  fulfilment?.addEventListener("change", syncFulfilment);
  syncFulfilment();

  const locationButton = document.querySelector("[data-capture-location]");
  const locationResult = document.querySelector("[data-location-result]");
  locationButton?.addEventListener("click", () => {
    if (!navigator.geolocation) {
      if (locationResult) locationResult.textContent = "This browser cannot capture your location. Enter your written address instead.";
      return;
    }
    locationButton.disabled = true;
    if (locationResult) locationResult.textContent = "Getting your location…";
    navigator.geolocation.getCurrentPosition(
      position => {
        const lat = position.coords.latitude.toFixed(6);
        const lng = position.coords.longitude.toFixed(6);
        const latInput = document.querySelector("#id_latitude");
        const lngInput = document.querySelector("#id_longitude");
        if (latInput) latInput.value = lat;
        if (lngInput) lngInput.value = lng;
        if (locationResult) {
          locationResult.textContent = "Location captured: " + lat + ", " + lng;
          locationResult.classList.add("success");
        }
        locationButton.disabled = false;
      },
      () => {
        if (locationResult) locationResult.textContent = "KOFAD could not access your location. You can still use GhanaPost GPS, landmark and written address.";
        locationButton.disabled = false;
      },
      {enableHighAccuracy:true, timeout:12000, maximumAge:60000}
    );
  });
});
