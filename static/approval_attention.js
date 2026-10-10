(() => {
  "use strict";
  const launcher = document.querySelector("#approval-attention");
  const badges = [...document.querySelectorAll("[data-approval-count]")];
  if (!launcher && !badges.length) return;

  const POSITION_KEY = "kofad-approval-position-v2";
  let previous = null;
  let dragState = null;
  let moved = false;
  let suppressClick = false;

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  // Only place the floating approval control inside the reachable content area.
  // iOS Safari's visual viewport shrinks/offsets with browser chrome and zoom,
  // and KOFAD's mobile dock must never cover the Approval Center control.
  const bounds = (width, height) => {
    const vv = window.visualViewport;
    const viewLeft = vv ? vv.offsetLeft : 0;
    const viewTop = vv ? vv.offsetTop : 0;
    const viewRight = vv ? vv.offsetLeft + vv.width : window.innerWidth;
    const viewBottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
    const gap = 12;
    let top = viewTop + gap;
    let bottom = viewBottom - gap;

    const header = document.querySelector(".topbar");
    if (header) {
      const r = header.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.top < viewBottom && r.bottom > viewTop) {
        top = Math.max(top, r.bottom + gap);
      }
    }
    const dock = document.querySelector(".mobile-dock");
    if (dock && getComputedStyle(dock).display !== "none") {
      const r = dock.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.top < viewBottom) {
        bottom = Math.min(bottom, r.top - gap);
      }
    }
    return {
      minX: Math.max(gap, viewLeft + gap),
      maxX: Math.max(gap, Math.min(window.innerWidth, viewRight) - width - gap),
      minY: top,
      maxY: Math.max(top, bottom - height),
    };
  };

  const pinLauncher = (x, y) => {
    if (!launcher) return;
    launcher.style.setProperty("left", x + "px", "important");
    launcher.style.setProperty("top", y + "px", "important");
    launcher.style.setProperty("right", "auto", "important");
    launcher.style.setProperty("bottom", "auto", "important");
    // Never freeze width/height: mobile and desktop have different layouts.
  };

  const keepReachable = () => {
    if (!launcher || launcher.hidden) return;
    const rect = launcher.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const limit = bounds(rect.width, rect.height);
    const x = clamp(rect.left, limit.minX, limit.maxX);
    const y = clamp(rect.top, limit.minY, limit.maxY);
    pinLauncher(x, y);
  };

  const applySavedPosition = () => {
    if (!launcher || launcher.hidden) return;
    const rect = launcher.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    let x = rect.left, y = rect.top;
    try {
      const saved = JSON.parse(localStorage.getItem(POSITION_KEY) || "null");
      if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
        x = saved.x;
        y = saved.y;
      }
    } catch (_) {}
    const limit = bounds(rect.width, rect.height);
    pinLauncher(clamp(x, limit.minX, limit.maxX),
                clamp(y, limit.minY, limit.maxY));
  };

  const savePosition = () => {
    if (!launcher) return;
    keepReachable();
    const rect = launcher.getBoundingClientRect();
    try {
      localStorage.setItem(POSITION_KEY, JSON.stringify({x: rect.left, y: rect.top}));
    } catch (_) {}
  };

  if (launcher) {
    const beginDrag = event => {
      if (event.button !== undefined && event.button !== 0) return;
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
      moved = false;
      launcher.classList.add("is-drag-ready");
    };

    const moveDrag = event => {
      if (!dragState || event.pointerId !== dragState.pointerId) return;
      const dx = event.clientX - dragState.startX;
      const dy = event.clientY - dragState.startY;
      if (!moved && Math.hypot(dx, dy) < 7) return;
      moved = true;
      launcher.classList.remove("is-drag-ready");
      launcher.classList.add("is-dragging");
      const limit = bounds(dragState.width, dragState.height);
      const x = clamp(dragState.left + dx, limit.minX, limit.maxX);
      const y = clamp(dragState.top + dy, limit.minY, limit.maxY);
      pinLauncher(x, y);
      event.preventDefault();
    };

    const finishDrag = event => {
      if (!dragState || event.pointerId !== dragState.pointerId) return;
      if (moved) {
        moveDrag(event);
        savePosition();
        suppressClick = true;
        window.setTimeout(() => { suppressClick = false; }, 0);
      }
      dragState = null;
      launcher.classList.remove("is-drag-ready", "is-dragging");
    };

    launcher.addEventListener("pointerdown", beginDrag);
    document.addEventListener("pointermove", moveDrag, {passive:false});
    document.addEventListener("pointerup", finishDrag);
    document.addEventListener("pointercancel", finishDrag);
    launcher.addEventListener("click", event => {
      if (!suppressClick) return;
      event.preventDefault();
      event.stopPropagation();
    }, true);
    // A bad old saved position is repaired immediately and after rotations,
    // dynamic Safari toolbar changes, or navigation-dock dimension changes.
    window.addEventListener("resize", keepReachable, {passive:true});
    window.addEventListener("pageshow", keepReachable);
    window.visualViewport?.addEventListener("resize", keepReachable, {passive:true});
    window.visualViewport?.addEventListener("scroll", keepReachable, {passive:true});
    const dock = document.querySelector(".mobile-dock");
    if (dock && typeof ResizeObserver !== "undefined") {
      new ResizeObserver(keepReachable).observe(dock);
    }
  }

  async function refresh() {
    try {
      const response = await fetch("/api/approvals/summary/", {
        headers: {"X-Requested-With":"XMLHttpRequest"},
        credentials:"same-origin",
        cache:"no-store",
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
