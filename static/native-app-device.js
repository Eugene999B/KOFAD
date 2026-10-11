(() => {
  "use strict";
  // Put the visitor's own platform first; do not fake or enable any release.
  const grid = document.querySelector(".native-platform-grid");
  if (!grid) return;
  const ua = navigator.userAgent || "";
  const isiPadOS = navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
  const id = /Android/i.test(ua) ? "android"
    : (/iPhone|iPad|iPod/i.test(ua) || isiPadOS) ? "ios"
    : /Windows/i.test(ua) ? "windows" : "";
  if (!id) return;
  const first = grid.querySelector('[data-native-platform="' + id + '"]');
  if (!first) return;
  // DOM reorder ensures keyboard order matches the visual order.
  if (grid.firstElementChild !== first) grid.insertBefore(first, grid.firstElementChild);
  first.classList.add("native-device-match");
  const details = first.querySelector(".native-platform-details");
  if (!details) return;
  const hint = document.createElement("span");
  hint.className = "native-device-hint";
  hint.textContent = "Your device";
  details.insertBefore(hint, details.firstChild);
})();
