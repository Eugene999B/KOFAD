/* KOFAD support inbox — refresh staff waiting/open/mine queues without
   interrupting a worker typing or navigating inside an active conversation. */
(() => {
  document.addEventListener("DOMContentLoaded", () => {
    const shell = document.querySelector(".support-desk-v3");
    const list = shell?.querySelector(".support-queue-list");
    if (!shell || !list) return;
    let pending = false;
    let lastSeen = "";
    const refresh = async () => {
      if (document.hidden || pending || document.activeElement?.closest(".support-queue-v3")) return;
      pending = true;
      try {
        const res = await fetch(window.location.pathname + window.location.search, {
          credentials: "same-origin",
          headers: {"Accept": "text/html", "X-Requested-With": "XMLHttpRequest"},
          cache: "no-store",
        });
        if (!res.ok || res.redirected) return;
        const html = await res.text();
        const page = new DOMParser().parseFromString(html, "text/html");
        const nextList = page.querySelector(".support-queue-list");
        if (!nextList) return;
        // Avoid unnecessary DOM updates: a staff member can still click a
        // conversation normally while the inbox refreshes in the background.
        const signature = nextList.innerHTML;
        if (signature !== lastSeen && signature !== list.innerHTML) {
          const scroll = list.scrollTop;
          list.replaceChildren(...Array.from(nextList.children).map(node => document.importNode(node, true)));
          list.scrollTop = scroll;
        }
        lastSeen = signature;
        for (const selector of [".support-desk-metrics", ".support-desk-tabs"]) {
          const current = shell.querySelector(selector);
          const updated = page.querySelector(selector);
          if (!current || !updated || current.contains(document.activeElement)) continue;
          if (current.innerHTML !== updated.innerHTML) {
            const scroll = current.scrollLeft;
            current.innerHTML = updated.innerHTML;
            current.scrollLeft = scroll;
          }
        }
      } catch (_) {
        // A short network interruption should not disturb staff replies.
      } finally {
        pending = false;
      }
    };
    window.setInterval(refresh, 8000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) refresh();
    });
  });
})();
