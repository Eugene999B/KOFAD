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

  // Mobile CSS intentionally anchors the approval control above the bottom dock
  // with !important. Once the user drags it, release those anchors with the same
  // priority and freeze the control's measured box so moving can never resize it.
  const pinLauncher = (x, y, width, height) => {
    if (!launcher) return;
    launcher.style.setProperty("left", x + "px", "important");
    launcher.style.setProperty("top", y + "px", "important");
    launcher.style.setProperty("right", "auto", "important");
    launcher.style.setProperty("bottom", "auto", "important");
    launcher.style.setProperty("width", width + "px", "important");
    launcher.style.setProperty("height", height + "px", "important");
    launcher.style.setProperty("min-width", width + "px", "important");
    launcher.style.setProperty("max-width", width + "px", "important");
    launcher.style.setProperty("box-sizing", "border-box", "important");
  };

  const resetLauncherGeometry = () => {
    if (!launcher) return;
    ["left", "top", "right", "bottom", "width", "height", "min-width", "max-width", "box-sizing"]
      .forEach(name => launcher.style.removeProperty(name));
  };

  const applySavedPosition = () => {
    if (!launcher) return;
    try {
      const saved = JSON.parse(localStorage.getItem(POSITION_KEY) || "null");
      if (!saved || !Number.isFinite(saved.x) || !Number.isFinite(saved.y)) return;
      const rect = launcher.getBoundingClientRect();
      const x = clamp(saved.x, 8, Math.max(8, window.innerWidth - rect.width - 8));
      const y = clamp(saved.y, 8, Math.max(8, window.innerHeight - rect.height - 8));
      pinLauncher(x, y, rect.width, rect.height);
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
    const dragHandle = launcher.querySelector(".approval-drag-handle");

    const beginDrag = event => {
      if (!dragHandle || event.button !== undefined && event.button !== 0) return;
      const rect = launcher.getBoundingClientRect();
      dragState = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      };
      dragged = false;
      pinLauncher(rect.left, rect.top, rect.width, rect.height);
      event.preventDefault();
    };

    const moveDrag = event => {
      if (!dragState || event.pointerId !== dragState.pointerId) return;
      const dx = event.clientX - dragState.startX;
      const dy = event.clientY - dragState.startY;
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
      pinLauncher(x, y, dragState.width, dragState.height);
      event.preventDefault();
    };

    const finishDrag = event => {
      if (!dragState || event.pointerId !== dragState.pointerId) return;
      if (dragged) {
        moveDrag(event);
        savePosition();
      }
      dragState = null;
      launcher.classList.remove("is-dragging");
    };

    dragHandle?.addEventListener("pointerdown", beginDrag);
    document.addEventListener("pointermove", moveDrag, {passive: false});
    document.addEventListener("pointerup", finishDrag);
    document.addEventListener("pointercancel", finishDrag);

    dragHandle?.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
    });
    dragHandle?.addEventListener("dblclick", event => {
      event.preventDefault();
      event.stopPropagation();
      try { localStorage.removeItem(POSITION_KEY); } catch (_) {}
      resetLauncherGeometry();
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