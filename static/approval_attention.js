(() => {
  "use strict";
  const launcher = document.querySelector("#approval-attention");
  const badges = [...document.querySelectorAll("[data-approval-count]")];
  if (!launcher && !badges.length) return;

  let previous = null;
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
        if (previous !== null && count > previous) {
          launcher.classList.remove("approval-new");
          void launcher.offsetWidth;
          launcher.classList.add("approval-new");
          window.setTimeout(() => launcher.classList.remove("approval-new"), 4200);
        }
      }
      previous = count;
    } catch (_) {
      // The attention control must never interrupt normal KOFAD work.
    }
  }

  refresh();
  window.setInterval(refresh, 12000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
})();
