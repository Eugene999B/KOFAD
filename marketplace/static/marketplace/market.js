document.addEventListener("DOMContentLoaded", () => {
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
