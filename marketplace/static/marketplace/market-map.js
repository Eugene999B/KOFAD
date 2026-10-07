document.addEventListener("DOMContentLoaded", () => {
  const number = value => {
    if (value === null || value === undefined || String(value).trim() === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const escapeHtml = value => String(value || "").replace(/[&<>"']/g, char => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[char]));

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
      lat += (result & 1) ? ~(result >> 1) : (result >> 1);
      result = 0;
      shift = 0;
      do {
        byte = encoded.charCodeAt(index++) - 63;
        result |= (byte & 0x1f) << shift;
        shift += 5;
      } while (byte >= 0x20 && index < encoded.length + 1);
      lng += (result & 1) ? ~(result >> 1) : (result >> 1);
      points.push([lat / 1e5, lng / 1e5]);
    }
    return points;
  };

  let googleLoader = null;
  const loadGoogle = key => {
    if (!key) return Promise.reject(new Error("Google Maps browser key is not configured."));
    if (window.google?.maps?.importLibrary) return Promise.resolve(window.google.maps);
    if (googleLoader) return googleLoader;
    googleLoader = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://maps.googleapis.com/maps/api/js?key=" + encodeURIComponent(key) +
        "&v=weekly&loading=async&libraries=places,marker";
      script.async = true;
      script.defer = true;
      script.onload = () => {
        if (window.google?.maps) resolve(window.google.maps);
        else reject(new Error("Google Maps did not initialize."));
      };
      script.onerror = () => reject(new Error("Google Maps failed to load."));
      document.head.appendChild(script);
    });
    return googleLoader;
  };

  const leafletIcon = className => window.L ? L.divIcon({
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
    const accuracyNode = shell.querySelector("[data-location-accuracy]");
    const providerNode = shell.querySelector("[data-map-provider]");
    const quoteNode = shell.querySelector("[data-delivery-quote]");
    const feeNode = document.querySelector("[data-delivery-fee]");
    const totalNode = document.querySelector("[data-checkout-total]");
    const subtotal = number(document.querySelector("[data-checkout-subtotal]")?.dataset.checkoutSubtotal);
    const googleLink = shell.querySelector("[data-google-map-link]");
    const directionsLink = shell.querySelector("[data-google-directions-link]");
    const googleKey = (mapEl.dataset.googleMapsKey || "").trim();
    const googleMapId = (mapEl.dataset.googleMapId || "").trim();

    let latitude = number(mapEl.dataset.lat) ?? number(latInput?.value);
    let longitude = number(mapEl.dataset.lng) ?? number(lngInput?.value);
    const originLat = number(mapEl.dataset.originLat);
    const originLng = number(mapEl.dataset.originLng);
    const driverLat = number(mapEl.dataset.driverLat);
    const driverLng = number(mapEl.dataset.driverLng);
    const picker = mapEl.dataset.picker === "true";

    let provider = "";
    let map = null;
    let destinationMarker = null;
    let routeLayer = null;

    const setProvider = name => {
      provider = name;
      if (!providerNode) return;
      if (name === "google") {
        providerNode.textContent = "Google Maps + Places";
        providerNode.classList.add("google");
      } else {
        providerNode.textContent = "Open map fallback";
        providerNode.classList.remove("google");
      }
    };

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

    const updateAccuracy = accuracy => {
      if (!accuracyNode) return;
      if (!Number.isFinite(accuracy)) {
        accuracyNode.textContent = "";
        return;
      }
      accuracyNode.textContent = "GPS accuracy ±" + Math.max(1, Math.round(accuracy)) + " m";
    };

    let reverseSequence = 0;
    const reverseLookup = async () => {
      const sequence = ++reverseSequence;
      const requestedLat = latitude, requestedLng = longitude;
      const url = mapEl.dataset.reverseUrl;
      if (!url || latitude === null || longitude === null) return;
      try {
        const response = await fetch(url + "?lat=" + encodeURIComponent(latitude) + "&lng=" + encodeURIComponent(longitude), {
          credentials: "same-origin",
          headers: {"Accept": "application/json"},
        });
        const payload = await response.json();
        if (sequence !== reverseSequence || requestedLat !== latitude || requestedLng !== longitude) return;
        if (response.ok && payload.label) {
          updateResult(payload.label);
          if (addressInput) addressInput.value = payload.label;
        }
      } catch (_) {}
    };

    let quoteSequence = 0;
    const refreshQuote = async () => {
      const sequence = ++quoteSequence;
      const requestedLat = latitude, requestedLng = longitude;
      const url = mapEl.dataset.quoteUrl;
      if (!url || latitude === null || longitude === null) return;
      if (quoteNode) quoteNode.innerHTML = "<span>Calculating delivery…</span>";
      try {
        const response = await fetch(url + "?lat=" + encodeURIComponent(latitude) + "&lng=" + encodeURIComponent(longitude), {
          credentials: "same-origin",
          headers: {"Accept": "application/json"},
        });
        const payload = await response.json();
        if (sequence !== quoteSequence || requestedLat !== latitude || requestedLng !== longitude) return;
        if (document.querySelector("[data-checkout-form] select[name='fulfilment']")?.value === "pickup") return;
        if (!response.ok) {
          if (quoteNode) quoteNode.innerHTML = "<strong>Delivery unavailable</strong><span>" +
            escapeHtml(payload.error || "Choose another location.") + "</span>";
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
        if (sequence !== quoteSequence) return;
        if (quoteNode) quoteNode.innerHTML = "<strong>Could not calculate delivery</strong><span>Try again or choose pickup.</span>";
      }
    };

    const drawLeafletRoute = () => {
      if (provider !== "leaflet" || !map || originLat === null || originLng === null || latitude === null || longitude === null) return;
      if (routeLayer) map.removeLayer(routeLayer);
      const routePoints = decodePolyline(mapEl.dataset.routePolyline || "");
      routeLayer = L.polyline(
        routePoints.length ? routePoints : [[originLat, originLng], [latitude, longitude]],
        routePoints.length
          ? {weight: 5, opacity: .78, color: "#188e91"}
          : {weight: 3, opacity: .58, dashArray: "7 7", color: "#188e91"},
      ).addTo(map);
    };

    const drawGoogleRoute = () => {
      if (provider !== "google" || !map || originLat === null || originLng === null || latitude === null || longitude === null) return;
      if (routeLayer) routeLayer.setMap(null);
      const decoded = decodePolyline(mapEl.dataset.routePolyline || "");
      const path = decoded.length
        ? decoded.map(([lat,lng]) => ({lat,lng}))
        : [{lat:originLat,lng:originLng},{lat:latitude,lng:longitude}];
      routeLayer = new google.maps.Polyline({
        path,
        geodesic:false,
        strokeColor:"#168f91",
        strokeOpacity:decoded.length ? .88 : .62,
        strokeWeight:decoded.length ? 5 : 3,
        map,
      });
    };

    const makeGoogleMarker = async (position, kind, title, draggable=false) => {
      if (googleMapId) {
        const markerLib = await google.maps.importLibrary("marker");
        const dot = document.createElement("span");
        dot.className = "kofad-google-marker " + kind;
        const marker = new markerLib.AdvancedMarkerElement({
          map,
          position,
          title,
          content:dot,
          gmpDraggable:draggable,
        });
        return marker;
      }
      return new google.maps.Marker({
        map,
        position,
        title,
        draggable,
      });
    };

    const moveDestinationMarker = async () => {
      if (!map || latitude === null || longitude === null) return;
      if (provider === "google") {
        const position = {lat:latitude,lng:longitude};
        if (!destinationMarker) {
          destinationMarker = await makeGoogleMarker(
            position,
            "customer",
            picker ? "Delivery pin" : "Customer delivery location",
            picker,
          );
          if (picker) {
            destinationMarker.addListener("dragend", event => {
              const pos = event.latLng || destinationMarker.position;
              const lat = typeof pos?.lat === "function" ? pos.lat() : pos?.lat;
              const lng = typeof pos?.lng === "function" ? pos.lng() : pos?.lng;
              setLocation(lat,lng,"",true);
            });
          }
        } else if (destinationMarker.setPosition) {
          destinationMarker.setPosition(position);
        } else {
          destinationMarker.position = position;
        }
        drawGoogleRoute();
      } else if (provider === "leaflet") {
        if (!destinationMarker) {
          destinationMarker = L.marker([latitude, longitude], {
            draggable: picker,
            icon: leafletIcon("customer"),
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
        drawLeafletRoute();
      }
      syncLinks();
    };

    const centerMap = () => {
      if (!map || latitude === null || longitude === null) return;
      if (provider === "google") {
        map.panTo({lat:latitude,lng:longitude});
        if ((map.getZoom?.() || 0) < 16) map.setZoom(16);
      } else if (provider === "leaflet") {
        map.setView([latitude, longitude], Math.max(map.getZoom(), 16));
      }
    };

    const setLocation = (lat, lng, label = "", doReverse = false, accuracy = null) => {
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
      updateAccuracy(accuracy);
      Promise.resolve(moveDestinationMarker()).then(centerMap);
      if (doReverse) reverseLookup();
      refreshQuote();
    };

    const initLeaflet = () => {
      if (!window.L) return false;
      provider = "leaflet";
      setProvider("leaflet");
      const defaultLat = latitude ?? originLat ?? 7.9465;
      const defaultLng = longitude ?? originLng ?? -1.0232;
      map = L.map(mapEl, {scrollWheelZoom:false}).setView([defaultLat, defaultLng], latitude !== null ? 16 : 7);
      const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom:19,
        attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
      }).addTo(map);
      let failures = 0;
      tiles.on("tileerror", () => {
        failures += 1;
        if (failures >= 3 && resultNode) {
          resultNode.textContent = "Map background could not load. Search a location, use GPS, or open the pin in Google Maps.";
          resultNode.classList.remove("success");
        }
      });
      if (originLat !== null && originLng !== null) {
        L.marker([originLat,originLng],{icon:leafletIcon("origin"),title:"Company dispatch point"}).addTo(map);
      }
      if (driverLat !== null && driverLng !== null) {
        L.marker([driverLat,driverLng],{icon:leafletIcon("driver"),title:"Latest delivery team location"}).addTo(map);
      }
      if (picker) map.on("click", event => setLocation(event.latlng.lat,event.latlng.lng,"",true));
      if (latitude !== null && longitude !== null) moveDestinationMarker();
      const bounds=[];
      if(originLat!==null&&originLng!==null) bounds.push([originLat,originLng]);
      if(latitude!==null&&longitude!==null) bounds.push([latitude,longitude]);
      if(driverLat!==null&&driverLng!==null) bounds.push([driverLat,driverLng]);
      if(bounds.length>1) map.fitBounds(bounds,{padding:[35,35],maxZoom:16});
      setTimeout(()=>map.invalidateSize(),80);
      return true;
    };

    const enhanceGooglePlaces = async () => {
      const searchBox = shell.querySelector("[data-location-search]");
      if (!searchBox) return;
      const fallbackInput = searchBox.querySelector("input");
      const fallbackButton = searchBox.querySelector("button");
      try {
        const placesLib = await google.maps.importLibrary("places");
        if (!placesLib.PlaceAutocompleteElement) return;
        const autocomplete = new placesLib.PlaceAutocompleteElement({
          includedRegionCodes:["gh"],
        });
        autocomplete.setAttribute("aria-label", fallbackInput?.getAttribute("aria-label") || "Search location");
        autocomplete.setAttribute("placeholder", fallbackInput?.getAttribute("placeholder") || "Search a location");
        autocomplete.className = "kofad-google-places";
        searchBox.prepend(autocomplete);
        searchBox.classList.add("google-places-active");
        if (fallbackInput) fallbackInput.hidden = true;
        if (fallbackButton) fallbackButton.hidden = true;
        autocomplete.addEventListener("gmp-select", async event => {
          const prediction = event.placePrediction;
          if (!prediction) return;
          const place = prediction.toPlace();
          await place.fetchFields({fields:["displayName","formattedAddress","location"]});
          if (!place.location) return;
          const label = place.formattedAddress || place.displayName || "Google Maps location";
          setLocation(place.location.lat(),place.location.lng(),label,false);
        });
      } catch (_) {
        // Keep the server-backed search UI visible if Places cannot initialize.
      }
    };

    const initGoogle = async () => {
      await loadGoogle(googleKey);
      const mapsLib = await google.maps.importLibrary("maps");
      provider = "google";
      setProvider("google");
      const defaultLat = latitude ?? originLat ?? 7.9465;
      const defaultLng = longitude ?? originLng ?? -1.0232;
      const options = {
        center:{lat:defaultLat,lng:defaultLng},
        zoom:latitude !== null ? 16 : 7,
        mapTypeControl:false,
        streetViewControl:false,
        fullscreenControl:true,
        gestureHandling:"cooperative",
        clickableIcons:true,
      };
      if (googleMapId) options.mapId = googleMapId;
      map = new mapsLib.Map(mapEl, options);
      if (picker) {
        map.addListener("click", event => {
          const point = event.latLng;
          setLocation(point.lat(),point.lng(),"",true);
        });
      }
      if(originLat!==null&&originLng!==null) await makeGoogleMarker({lat:originLat,lng:originLng},"origin","Company dispatch point",false);
      if(driverLat!==null&&driverLng!==null) await makeGoogleMarker({lat:driverLat,lng:driverLng},"driver","Latest delivery team location",false);
      if(latitude!==null&&longitude!==null) await moveDestinationMarker();
      const bounds = new google.maps.LatLngBounds();
      let count=0;
      [[originLat,originLng],[latitude,longitude],[driverLat,driverLng]].forEach(([lat,lng])=>{
        if(lat!==null&&lng!==null){bounds.extend({lat,lng});count+=1;}
      });
      if(count>1) map.fitBounds(bounds,48);
      await enhanceGooglePlaces();
    };

    const searchBox = shell.querySelector("[data-location-search]");
    if (searchBox) {
      const input = searchBox.querySelector("input");
      const button = searchBox.querySelector("button");
      const results = shell.querySelector("[data-location-search-results]");
      let searchController;
      let searchSequence = 0;
      results?.setAttribute("aria-live", "polite");
      const runSearch = async () => {
        searchController?.abort();
        const sequence = ++searchSequence;
        searchController = new AbortController();
        const query = input?.value.trim() || "";
        if (query.length < 3) {
          if (results) results.innerHTML = "<p>Enter at least 3 characters.</p>";
          return;
        }
        button.disabled = true;
        if (results) results.innerHTML = "<p>Searching…</p>";
        try {
          const response = await fetch((mapEl.dataset.searchUrl || "/market/location/search/") + "?q=" + encodeURIComponent(query), {
            credentials:"same-origin",
            signal: searchController.signal,
            headers:{"Accept":"application/json"},
          });
          const payload = await response.json();
          if (sequence !== searchSequence) return;
          if (!response.ok || !(payload.results || []).length) {
            if (results) results.innerHTML = "<p>" + escapeHtml(payload.error || "No matching location found.") + "</p>";
            return;
          }
          results.replaceChildren();
          payload.results.forEach(row => {
            const choice = document.createElement("button");
            choice.type = "button";
            choice.className = "location-search-choice";
            choice.textContent = row.label;
            choice.addEventListener("click", () => {
              setLocation(row.latitude,row.longitude,row.label,false);
              results.replaceChildren();
              if(input) input.value=row.label;
            });
            results.appendChild(choice);
          });
        } catch (error) {
          if (error.name === "AbortError" || sequence !== searchSequence) return;
          if (results) results.innerHTML = "<p>Location search is temporarily unavailable.</p>";
        } finally {
          if (sequence === searchSequence) button.disabled = false;
        }
      };
      results?.addEventListener("keydown", event => {
        const choices = [...results.querySelectorAll("button")];
        const index = choices.indexOf(document.activeElement);
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          choices[(index + (event.key === "ArrowDown" ? 1 : -1) + choices.length) % choices.length]?.focus();
        }
        if (event.key === "Escape") { results.replaceChildren(); input?.focus(); }
      });
      button?.addEventListener("click",runSearch);
      input?.addEventListener("keydown",event=>{
        if(event.key==="Enter"){event.preventDefault();runSearch();}
        if(event.key==="ArrowDown"){event.preventDefault();results?.querySelector("button")?.focus();}
        if(event.key==="Escape"){searchController?.abort();searchSequence++;results?.replaceChildren();if(button) button.disabled=false;}
      });
    }

    shell.querySelectorAll("[data-capture-location]").forEach(button => {
      button.addEventListener("click", () => {
        if (!navigator.geolocation) {
          if (resultNode) resultNode.textContent = "This browser cannot provide GPS location. Search or tap the map instead.";
          return;
        }
        button.disabled = true;
        let best = null;
        let settled = false;
        if (resultNode) resultNode.textContent = "Finding your most accurate location…";
        updateAccuracy(null);
        const finish = () => {
          if (settled) return;
          settled = true;
          if (watchId !== null) navigator.geolocation.clearWatch(watchId);
          clearTimeout(timer);
          button.disabled = false;
          if (best) {
            setLocation(best.latitude,best.longitude,"",true,best.accuracy);
          } else if (resultNode) {
            resultNode.textContent = "Could not read your location. Check location permission, then try again or search the map.";
          }
        };
        let watchId = null;
        const timer = setTimeout(finish,12000);
        watchId = navigator.geolocation.watchPosition(
          position => {
            const candidate = {
              latitude:position.coords.latitude,
              longitude:position.coords.longitude,
              accuracy:position.coords.accuracy,
            };
            if (!best || candidate.accuracy < best.accuracy) best = candidate;
            updateAccuracy(best.accuracy);
            if (best.accuracy <= 20) finish();
          },
          error => {
            if (error.code === 1 && resultNode) {
              resultNode.textContent = "Location permission is blocked. Allow precise location for this site, or search and pin the map.";
            }
            finish();
          },
          {enableHighAccuracy:true,timeout:10000,maximumAge:0},
        );
      });
    });

    const fulfilment = document.querySelector("[data-checkout-form] select[name='fulfilment']");
    fulfilment?.addEventListener("change",()=>{
      if(fulfilment.value==="pickup"){
        quoteSequence++;
        if(feeNode) feeNode.textContent="GHS 0.00";
        if(totalNode&&subtotal!==null) totalNode.textContent="GHS "+subtotal.toFixed(2);
      }else if(latitude!==null&&longitude!==null){
        refreshQuote();
      }
    });

    shell.querySelector("[data-map-details]")?.addEventListener("toggle", () => {
      if (provider === "leaflet" && map) map.invalidateSize();
      if (provider === "google" && map) {
        google.maps.event.trigger(map, "resize");
        if (latitude !== null && longitude !== null) map.setCenter({lat:latitude,lng:longitude});
      }
    });
    syncLinks();
    (async () => {
      if (googleKey) {
        try {
          await initGoogle();
        } catch (error) {
          console.warn("KOFAD Google Maps fallback:", error);
          initLeaflet();
        }
      } else {
        initLeaflet();
      }
      if(latitude!==null&&longitude!==null&&mapEl.dataset.quoteUrl && fulfilment?.value !== "pickup") refreshQuote();
    })();
  });

  document.querySelectorAll("[data-delivery-policy]").forEach(form => {
    const mode = form.querySelector("[name='delivery_pricing_mode']");
    const sync = () => {
      form.querySelectorAll("[data-price-mode]").forEach(row => {
        row.hidden = !row.dataset.priceMode.split(" ").includes(mode?.value || "");
      });
    };
    mode?.addEventListener("change",sync);
    sync();
  });
});
