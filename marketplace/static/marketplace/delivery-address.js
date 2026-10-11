document.addEventListener("DOMContentLoaded", () => {
  const shell = document.querySelector("[data-address-finder]");
  if (!shell) return;
  const form = document.querySelector("[data-checkout-form]");
  const input = document.getElementById("delivery-landmark-query");
  const suggestions = shell.querySelector("[data-address-suggestions]");
  const state = shell.querySelector("[data-address-state]");
  const attribution = shell.querySelector("[data-google-attribution]");
  const gpsInput = document.getElementById("id_ghana_post_gps");
  const latInput = document.getElementById("id_latitude");
  const lngInput = document.getElementById("id_longitude");
  const street = document.getElementById("id_address_line");
  const landmark = document.getElementById("id_landmark");
  const town = document.getElementById("id_town");
  const quote = document.querySelector("[data-delivery-quote]");
  const fee = document.querySelector("[data-delivery-fee]");
  const total = document.querySelector("[data-checkout-total]");
  const subtotal = Number(document.querySelector("[data-checkout-subtotal]")?.dataset.checkoutSubtotal || 0);
  const mapsLink = shell.querySelector("[data-address-maps-link]");
  const token = typeof crypto?.randomUUID === "function" ? crypto.randomUUID() : "";
  let queryTimeout, currentSearch, sequence = 0, quoteSequence = 0;
  const csrf = form?.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
  const message = (text, ok = false) => {
    state.textContent = text;
    state.classList.toggle("success", ok);
  };
  const hideSuggestions = () => {
    suggestions.replaceChildren();
    attribution.hidden = true;
  };
  const setCoordinates = (latitude, longitude, description, kind = "landmark") => {
    const lat = Number(latitude), lng = Number(longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || lat < 4.5 || lat > 11.3 || lng < -3.5 || lng > 1.3) {
      message("That place could not be confirmed in Ghana. Please choose another.");
      return;
    }
    latInput.value = lat.toFixed(6);
    lngInput.value = lng.toFixed(6);
    if (kind === "landmark" && landmark && !landmark.value.trim()) landmark.value = description;
    if (street && !street.value.trim()) street.value = description;
    mapsLink.href = "https://www.google.com/maps/search/?api=1&query=" +
      encodeURIComponent(lat.toFixed(6) + "," + lng.toFixed(6));
    mapsLink.hidden = false;
    message(description + " — location confirmed for delivery.", true);
    updateQuote();
  };
  const post = async (url, values) => {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {"X-CSRFToken": csrf, "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
      body: new URLSearchParams(values).toString(),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "We could not confirm this location.");
    return data;
  };
  const updateQuote = async () => {
    const id = ++quoteSequence;
    if (!latInput.value || !lngInput.value) return;
    if (form?.querySelector("[name=fulfilment]")?.value === "pickup") return;
    quote.replaceChildren();
    quote.textContent = "Calculating delivery charge…";
    try {
      const search = new URLSearchParams({lat:latInput.value, lng:lngInput.value});
      const response = await fetch(shell.dataset.quoteUrl + "?" + search, {credentials:"same-origin"});
      const data = await response.json();
      if (id !== quoteSequence) return;
      if (!response.ok) throw new Error(data.error || "Delivery is not available for this location.");
      const charge = Number(data.fee);
      if (!Number.isFinite(charge)) throw new Error("Delivery pricing is unavailable.");
      quote.replaceChildren();
      const heading = document.createElement("strong");
      heading.textContent = "Delivery: GHS " + charge.toFixed(2);
      const detail = document.createElement("span");
      detail.textContent = data.distance_km !== null ? data.distance_km + " km" : "Configured delivery fee";
      quote.append(heading, detail);
      if (fee) fee.textContent = "GHS " + charge.toFixed(2);
      if (total) total.textContent = "GHS " + (subtotal + charge).toFixed(2);
    } catch (error) {
      if (id !== quoteSequence) return;
      quote.replaceChildren();
      quote.textContent = error.message || "Delivery pricing unavailable. Please try again.";
    }
  };
  const search = async () => {
    const query = input.value.trim();
    if (query.length < 3) { hideSuggestions(); return; }
    currentSearch?.abort();
    const controller = new AbortController();
    currentSearch = controller;
    const id = ++sequence;
    hideSuggestions();
    const pending = document.createElement("p");
    pending.textContent = "Finding places in Ghana…";
    suggestions.appendChild(pending);
    try {
      const params = new URLSearchParams({q:query, session:token});
      const response = await fetch(shell.dataset.suggestUrl + "?" + params, {
        credentials:"same-origin", signal:controller.signal, headers:{"Accept":"application/json"},
      });
      const data = await response.json();
      if (id !== sequence) return;
      hideSuggestions();
      if (!response.ok) throw new Error(data.error || "Search is temporarily unavailable.");
      if (!data.results?.length) {
        message("No matches yet. Try a nearby business, school, junction or area.");
        return;
      }
      for (const item of data.results) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "address-suggestion";
        button.textContent = item.label;
        button.addEventListener("click", async () => {
          hideSuggestions();
          input.value = item.label;
          if (!item.place_id) {
            setCoordinates(item.latitude, item.longitude, item.label);
            return;
          }
          message("Confirming this landmark…");
          try {
            const place = await post(shell.dataset.selectUrl, {place_id:item.place_id, session:token});
            setCoordinates(place.latitude, place.longitude, place.label);
          } catch (error) { message(error.message); }
        });
        suggestions.appendChild(button);
        if (item.source === "google_places") attribution.hidden = false;
      }
    } catch (error) {
      if (error.name === "AbortError" || id !== sequence) return;
      hideSuggestions();
      message(error.message || "Location search is unavailable. Try your current location.");
    }
  };
  input?.addEventListener("input", () => {
    clearTimeout(queryTimeout);
    currentSearch?.abort();
    hideSuggestions();
    if (input.value.trim().length >= 3) queryTimeout = setTimeout(search, 420);
  });
  input?.addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); clearTimeout(queryTimeout); search(); }
    if (event.key === "ArrowDown") suggestions.querySelector("button")?.focus();
    if (event.key === "Escape") hideSuggestions();
  });
  shell.querySelector("[data-address-search]")?.addEventListener("click", search);
  shell.querySelector("[data-gps-lookup]")?.addEventListener("click", async event => {
    const button = event.currentTarget;
    const code = (gpsInput?.value || "").trim().toUpperCase().replace(/\s/g, "");
    gpsInput.value = code;
    if (!/^[A-Z]{2,3}-\d{3,4}-\d{4}$/.test(code)) {
      message("Enter your full GhanaPost GPS code, for example AK-039-5028.");
      gpsInput.focus();
      return;
    }
    button.disabled = true;
    message("Checking your GhanaPost GPS code…");
    try {
      const result = await post(shell.dataset.gpsUrl, {code});
      gpsInput.value = result.code;
      setCoordinates(result.latitude, result.longitude, result.label, "ghana_post");
      if (town && !town.value.trim() && result.area) town.value = result.area;
    } catch (error) { message(error.message); }
    finally { button.disabled = false; }
  });
  shell.querySelector("[data-address-current]")?.addEventListener("click", event => {
    const button = event.currentTarget;
    if (!navigator.geolocation) { message("Your browser cannot access location. Search a landmark instead."); return; }
    button.disabled = true;
    message("Getting your location. Allow precise location if prompted…");
    navigator.geolocation.getCurrentPosition(async position => {
      const {latitude, longitude, accuracy} = position.coords;
      setCoordinates(latitude, longitude, "Your current location (±" + Math.round(accuracy) + " m)", "current");
      try {
        const params = new URLSearchParams({lat:latitude, lng:longitude});
        const response = await fetch(shell.dataset.reverseUrl + "?" + params, {credentials:"same-origin"});
        const data = await response.json();
        if (response.ok && data.label && street && !street.value.trim()) street.value = data.label;
      } catch (_) {}
      button.disabled = false;
    }, () => {
      message("Location permission was denied or unavailable. Search a landmark or enter a GhanaPost GPS code.");
      button.disabled = false;
    }, {enableHighAccuracy:true,timeout:12000,maximumAge:0});
  });
  const fulfilment = form?.querySelector("[name=fulfilment]");
  fulfilment?.addEventListener("change", () => {
    const pickup = fulfilment.value === "pickup";
    document.querySelector("[data-delivery-fields]").hidden = pickup;
    const pickupFields = document.querySelector("[data-pickup-fields]");
    if (pickupFields) pickupFields.hidden = !pickup;
    if (pickup) {
      quoteSequence++;
      if (fee) fee.textContent = "GHS 0.00";
      if (total) total.textContent = "GHS " + subtotal.toFixed(2);
    } else if (latInput.value && lngInput.value) updateQuote();
  });
  if (latInput?.value && lngInput?.value) {
    setCoordinates(latInput.value, lngInput.value, "Saved delivery location", "saved");
  }
});
