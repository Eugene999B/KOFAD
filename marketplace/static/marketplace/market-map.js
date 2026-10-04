document.addEventListener("DOMContentLoaded", () => {
  const number = value => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const decodePolyline = encoded => {
    if (!encoded) return [];
    let index = 0, lat = 0, lng = 0;
    const points = [];
    while (index < encoded.length) {
      let result = 0, shift = 0, byte;
      do {
        byte = encoded.charCodeAt(index++) - 63;
        result |= (byte & 0x1f) << shift;
        shift += 5;
      } while (byte >= 0x20 && index < encoded.length + 1);
      const dlat = (result & 1) ? ~(result >> 1) : (result >> 1);
      lat += dlat;
      result = 0;
      shift = 0;
      do {
        byte = encoded.charCodeAt(index++) - 63;
        result |= (byte & 0x1f) << shift;
        shift += 5;
      } while (byte >= 0x20 && index < encoded.length + 1);
      const dlng = (result & 1) ? ~(result >> 1) : (result >> 1);
      lng += dlng;
      points.push([lat / 1e5, lng / 1e5]);
    }
    return points;
  };

  const icon = className => window.L ? L.divIcon({
    className: "kofad-map-icon-wrap",
    html: '<span class="kofad-map-icon ' + className + '"></span>',
    iconSize: [24, 24],
    iconAnchor: [12, 12],
  }) : null;

  document.querySelectorAll("[data-location-map]").forEach(mapEl => {
    const shell = mapEl.closest("[data-map-shell]") || mapEl.parentElement || document;
    const latInput = document.getElementById(mapEl.dataset.latInput || "");
    const lngInput = document.getElementById(mapEl.dataset.lngInput || "");
    const addressInput = document.getElementById(mapEl.dataset.addressInput || "");
    const resultNode = shell.querySelector("[data-location-result]");
    const quoteNode = shell.querySelector("[data-delivery-quote]");
    const feeNode = document.querySelector("[data-delivery-fee]");
    const totalNode = document.querySelector("[data-checkout-total]");
    const subtotal = number(document.querySelector("[data-checkout-subtotal]")?.dataset.checkoutSubtotal);
    const googleLink = shell.querySelector("[data-google-map-link]");
    const directionsLink = shell.querySelector("[data-google-directions-link]");

    let latitude = number(mapEl.dataset.lat) ?? number(latInput?.value);
    let longitude = number(mapEl.dataset.lng) ?? number(lngInput?.value);
    const originLat = number(mapEl.dataset.originLat);
    const originLng = number(mapEl.dataset.originLng);
    const driverLat = number(mapEl.dataset.driverLat);
    const driverLng = number(mapEl.dataset.driverLng);
    const picker = mapEl.dataset.picker === "true";

    let map = null;
    let destinationMarker = null;
    let routeLayer = null;

    const syncLinks = () => {
      if (googleLink && latitude !== null && longitude !== null) {
        googleLink.href = "https://www.google.com/maps/search/?api=1&query=" +
          encodeURIComponent(latitude + "," + longitude);
        googleLink.hidden = false;
      }
      if (directionsLink && originLat !== null && originLng !== null && latitude !== null && longitude !== null) {
        directionsLink.href = "https://www.google.com/maps/dir/?api=1&origin=" +
          encodeURIComponent(originLat + "," + originLng) + "&destination=" +
          encodeURIComponent(latitude + "," + longitude) + "&travelmode=driving";
        directionsLink.hidden = false;
      }
    };

    const updateResult = label => {
      if (!resultNode || latitude === null || longitude === null) return;
      resultNode.textContent = label || ("Pinned at " + latitude.toFixed(6) + ", " + longitude.toFixed(6));
      resultNode.classList.add("success");
    };

    const drawFallbackRoute = () => {
      if (!map || originLat === null || originLng === null || latitude === null || longitude === null) return;
      if (routeLayer) map.removeLayer(routeLayer);
      const encoded = mapEl.dataset.routePolyline || "";
      const routePoints = decodePolyline(encoded);
      routeLayer = L.polyline(
        routePoints.length ? routePoints : [[originLat, originLng], [latitude, longitude]],
        routePoints.length ? {weight: 5, opacity: .75} : {weight: 3, opacity: .55, dashArray: "7 7"},
      ).addTo(map);
    };

    const moveDestinationMarker = () => {
      if (!map || latitude === null || longitude === null) return;
      if (!destinationMarker) {
        destinationMarker = L.marker([latitude, longitude], {
          draggable: picker,
          icon: icon("customer"),
          title: picker ? "Delivery pin" : "Customer delivery location",
        }).addTo(map);
        if (picker) {
          destinationMarker.on("dragend", event => {
            const point = event.target.getLatLng();
            setLocation(point.lat, point.lng, "", true);
          });
        }
      } else {
        destinationMarker.setLatLng([latitude, longitude]);
      }
      drawFallbackRoute();
      syncLinks();
    };

    const reverseLookup = async () => {
      const url = mapEl.dataset.reverseUrl;
      if (!url || latitude === null || longitude === null) return;
      try {
        const response = await fetch(url + "?lat=" + encodeURIComponent(latitude) + "&lng=" + encodeURIComponent(longitude), {
          credentials: "same-origin",
          headers: {"Accept": "application/json"},
        });
        const payload = await response.json();
        if (response.ok && payload.label) {
          updateResult(payload.label);
          if (addressInput && !addressInput.value.trim()) addressInput.value = payload.label;
        }
      } catch (_) {}
    };

    const refreshQuote = async () => {
      const url = mapEl.dataset.quoteUrl;
      if (!url || latitude === null || longitude === null) return;
      if (quoteNode) quoteNode.innerHTML = "<span>Calculating delivery…</span>";
      try {
        const response = await fetch(url + "?lat=" + encodeURIComponent(latitude) + "&lng=" + encodeURIComponent(longitude), {
          credentials: "same-origin",
          headers: {"Accept": "application/json"},
        });
        const payload = await response.json();
        if (!response.ok) {
          if (quoteNode) quoteNode.innerHTML = "<strong>Delivery unavailable</strong><span>" + (payload.error || "Choose another location.") + "</span>";
          return;
        }
        const fee = Number(payload.fee || 0);
        const distance = payload.distance_km !== null ? Number(payload.distance_km) : null;
        if (quoteNode) {
          const distanceText = distance !== null ? distance.toFixed(2) + " km" : "Flat delivery";
          quoteNode.innerHTML = "<strong>Delivery: GHS " + fee.toFixed(2) + "</strong><span>" + distanceText + "</span>";
        }
        if (feeNode) feeNode.textContent = "GHS " + fee.toFixed(2);
        if (totalNode && subtotal !== null) totalNode.textContent = "GHS " + (subtotal + fee).toFixed(2);
      } catch (_) {
        if (quoteNode) quoteNode.innerHTML = "<strong>Could not calculate delivery</strong><span>Try again or choose pickup.</span>";
      }
    };

    const setLocation = (lat, lng, label = "", doReverse = false) => {
      const parsedLat = number(lat);
      const parsedLng = number(lng);
      if (parsedLat === null || parsedLng === null) return;
      latitude = parsedLat;
      longitude = parsedLng;
      if (latInput) latInput.value = latitude.toFixed(6);
      if (lngInput) lngInput.value = longitude.toFixed(6);
      if (label) {
        updateResult(label);
        if (addressInput) addressInput.value = label;
      } else {
        updateResult("");
      }
      moveDestinationMarker();
      if (map) map.setView([latitude, longitude], Math.max(map.getZoom(), 16));
      if (doReverse) reverseLookup();
      refreshQuote();
    };

    if (window.L) {
      const defaultLat = latitude ?? originLat ?? 7.9465;
      const defaultLng = longitude ?? originLng ?? -1.0232;
      map = L.map(mapEl, {scrollWheelZoom: false}).setView([defaultLat, defaultLng], latitude !== null ? 16 : 7);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
      }).addTo(map);

      if (originLat !== null && originLng !== null) {
        L.marker([originLat, originLng], {icon: icon("origin"), title: "Company dispatch point"}).addTo(map);
      }
      if (driverLat !== null && driverLng !== null) {
        L.marker([driverLat, driverLng], {icon: icon("driver"), title: "Latest delivery team location"}).addTo(map);
      }
      if (latitude !== null && longitude !== null) moveDestinationMarker();
      if (picker) {
        map.on("click", event => setLocation(event.latlng.lat, event.latlng.lng, "", true));
      }

      const bounds = [];
      if (originLat !== null && originLng !== null) bounds.push([originLat, originLng]);
      if (latitude !== null && longitude !== null) bounds.push([latitude, longitude]);
      if (driverLat !== null && driverLng !== null) bounds.push([driverLat, driverLng]);
      if (bounds.length > 1) map.fitBounds(bounds, {padding: [35, 35], maxZoom: 16});
      setTimeout(() => map.invalidateSize(), 80);
    }

    syncLinks();

    shell.querySelectorAll("[data-capture-location]").forEach(button => {
      button.addEventListener("click", () => {
        if (!navigator.geolocation) {
          if (resultNode) resultNode.textContent = "This browser cannot provide GPS location. Search or tap the map instead.";
          return;
        }
        button.disabled = true;
        if (resultNode) resultNode.textContent = "Getting your precise location…";
        navigator.geolocation.getCurrentPosition(
          position => {
            setLocation(position.coords.latitude, position.coords.longitude, "", true);
            button.disabled = false;
          },
          error => {
            if (resultNode) {
              resultNode.textContent = error.code === 1
                ? "Location permission is blocked. Allow location for this site, or search and pin the map."
                : "Could not read your GPS location. Search and pin the map instead.";
            }
            button.disabled = false;
          },
          {enableHighAccuracy: true, timeout: 20000, maximumAge: 15000},
        );
      });
    });

    const searchBox = shell.querySelector("[data-location-search]");
    if (searchBox) {
      const input = searchBox.querySelector("input");
      const button = searchBox.querySelector("button");
      const results = shell.querySelector("[data-location-search-results]");
      const runSearch = async () => {
        const query = input?.value.trim() || "";
        if (query.length < 3) {
          if (results) results.innerHTML = "<p>Enter at least 3 characters.</p>";
          return;
        }
        button.disabled = true;
        if (results) results.innerHTML = "<p>Searching…</p>";
        try {
          const response = await fetch((mapEl.dataset.searchUrl || "/market/location/search/") + "?q=" + encodeURIComponent(query), {
            credentials: "same-origin",
            headers: {"Accept": "application/json"},
          });
          const payload = await response.json();
          if (!response.ok || !(payload.results || []).length) {
            if (results) results.innerHTML = "<p>" + (payload.error || "No matching location found.") + "</p>";
            return;
          }
          results.replaceChildren();
          payload.results.forEach(row => {
            const choice = document.createElement("button");
            choice.type = "button";
            choice.className = "location-search-choice";
            choice.textContent = row.label;
            choice.addEventListener("click", () => {
              setLocation(row.latitude, row.longitude, row.label, false);
              results.replaceChildren();
              if (input) input.value = row.label;
            });
            results.appendChild(choice);
          });
        } catch (_) {
          if (results) results.innerHTML = "<p>Location search is temporarily unavailable.</p>";
        } finally {
          button.disabled = false;
        }
      };
      button?.addEventListener("click", runSearch);
      input?.addEventListener("keydown", event => {
        if (event.key === "Enter") {
          event.preventDefault();
          runSearch();
        }
      });
    }

    const fulfilment = document.querySelector("[data-checkout-form] select[name='fulfilment']");
    fulfilment?.addEventListener("change", () => {
      if (fulfilment.value === "pickup") {
        if (feeNode) feeNode.textContent = "GHS 0.00";
        if (totalNode && subtotal !== null) totalNode.textContent = "GHS " + subtotal.toFixed(2);
      } else if (latitude !== null && longitude !== null) {
        refreshQuote();
      }
    });

    if (latitude !== null && longitude !== null && mapEl.dataset.quoteUrl) refreshQuote();
  });

  document.querySelectorAll("[data-delivery-policy]").forEach(form => {
    const mode = form.querySelector("[name='delivery_pricing_mode']");
    const sync = () => {
      form.querySelectorAll("[data-price-mode]").forEach(row => {
        row.hidden = !row.dataset.priceMode.split(" ").includes(mode?.value || "");
      });
    };
    mode?.addEventListener("change", sync);
    sync();
  });
});
