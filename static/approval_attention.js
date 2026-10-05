(() => {
  "use strict";
  const launcher = document.querySelector("#approval-attention");
  const badges = [...document.querySelectorAll("[data-approval-count]")];
  if (!launcher && !badges.length) return;

  const POSITION_KEY = "kofad-approval-position-v1";
  let previous = null;
  let dragged = false;
  let dragState = null;

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  const applySavedPosition = () => {
    if (!launcher) return;
    try {
      const saved = JSON.parse(localStorage.getItem(POSITION_KEY) || "null");
      if (!saved || !Number.isFinite(saved.x) || !Number.isFinite(saved.y)) return;
      const rect = launcher.getBoundingClientRect();
      const x = clamp(saved.x, 8, Math.max(8, window.innerWidth - rect.width - 8));
      const y = clamp(saved.y, 8, Math.max(8, window.innerHeight - rect.height - 8));
      launcher.style.left = x + "px";
      launcher.style.top = y + "px";
      launcher.style.right = "auto";
      launcher.style.bottom = "auto";
    } catch (_) {}
  };

  const savePosition = () => {
    if (!launcher) return;
    const rect = launcher.getBoundingClientRect();
    try {
      localStorage.setItem(POSITION_KEY, JSON.stringify({x: rect.left, y: rect.top}));
    } catch (_) {}
  };

  if (launcher) {
    launcher.draggable = false;
    launcher.addEventListener("dragstart", event => event.preventDefault());

    const beginDrag = (clientX, clientY, pointerId) => {
      const rect = launcher.getBoundingClientRect();
      dragState = {
        pointerId,
        startX: clientX,
        startY: clientY,
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      };
      dragged = false;
      launcher.style.left = rect.left + "px";
      launcher.style.top = rect.top + "px";
      launcher.style.right = "auto";
      launcher.style.bottom = "auto";
    };

    const moveDrag = (clientX, clientY, pointerId, event) => {
      if (!dragState || dragState.pointerId !== pointerId) return;
      const dx = clientX - dragState.startX;
      const dy = clientY - dragState.startY;
      if (!dragged && Math.hypot(dx, dy) < 5) return;
      dragged = true;
      launcher.classList.add("is-dragging");
      const x = clamp(
        dragState.left + dx,
        8,
        Math.max(8, window.innerWidth - dragState.width - 8),
      );
      const y = clamp(
        dragState.top + dy,
        8,
        Math.max(8, window.innerHeight - dragState.height - 8),
      );
      launcher.style.left = x + "px";
      launcher.style.top = y + "px";
      event?.preventDefault?.();
    };

    const finishDrag = pointerId => {
      if (!dragState || dragState.pointerId !== pointerId) return;
      dragState = null;
      launcher.classList.remove("is-dragging");
      if (dragged) savePosition();
    };

    // Pointer events cover touch, pen and modern mouse input.
    launcher.addEventListener("pointerdown", event => {
      if (event.button !== 0) return;
      beginDrag(event.clientX, event.clientY, event.pointerId);
      event.preventDefault();
    });
    document.addEventListener("pointermove", event => {
      moveDrag(event.clientX, event.clientY, event.pointerId, event);
    }, {passive: false});
    document.addEventListener("pointerup", event => finishDrag(event.pointerId));
    document.addEventListener("pointercancel", event => finishDrag(event.pointerId));

    // Mouse fallback is deliberate. Some browser automation, embedded browsers
    // and older desktop engines don't preserve pointer capture consistently.
    launcher.addEventListener("mousedown", event => {
      if (event.button !== 0) return;
      beginDrag(event.clientX, event.clientY, "mouse");
      event.preventDefault();
    });
    document.addEventListener("mousemove", event => {
      moveDrag(event.clientX, event.clientY, "mouse", event);
    });
    document.addEventListener("mouseup", () => finishDrag("mouse"));

    launcher.addEventListener("click", event => {
      if (!dragged) return;
      event.preventDefault();
      event.stopPropagation();
      dragged = false;
    });
    launcher.addEventListener("dblclick", event => {
      event.preventDefault();
      try { localStorage.removeItem(POSITION_KEY); } catch (_) {}
      launcher.style.left = "";
      launcher.style.top = "";
      launcher.style.right = "";
      launcher.style.bottom = "";
    });
    window.addEventListener("resize", applySavedPosition);
  }

  async function refresh() {
    try {
      const response = await fetch("/api/approvals/summary/", {
        headers: {"X-Requested-With": "XMLHttpRequest"},
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const data = await response.json();
      const count = Number(data.actionable ?? data.pending ?? 0);
      badges.forEach(badge => {
        badge.textContent = String(count);
        badge.hidden = count <= 0;
      });
      if (launcher) {
        launcher.hidden = count <= 0;
        launcher.classList.toggle("has-approvals", count > 0);
        if (count > 0) requestAnimationFrame(applySavedPosition);
        if (previous !== null && count > previous) {
          launcher.classList.remove("approval-new");
          void launcher.offsetWidth;
          launcher.classList.add("approval-new");
          window.setTimeout(() => launcher.classList.remove("approval-new"), 2600);
        }
      }
      previous = count;
    } catch (_) {
      // Approval awareness must never interrupt normal KOFAD work.
    }
  }

  refresh();
  window.setInterval(refresh, 12000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
})();