(() => {
  "use strict";

  const downloadForm = document.querySelector("[data-backup-download]");
  const statusNode = document.querySelector("[data-backup-state]");
  const badge = document.querySelector("[data-backup-ready-badge]");
  const guardedButtons = [...document.querySelectorAll("[data-requires-recent-backup]")];
  if (!downloadForm || !statusNode || !guardedButtons.length) return;

  const statusUrl = downloadForm.dataset.backupStatusUrl;
  let expiryTimer = null;
  let polling = false;

  const setLocked = (locked, seconds = 0) => {
    guardedButtons.forEach(button => {
      button.disabled = locked;
      if (locked) {
        button.setAttribute("title", "Download a fresh backup first");
      } else {
        button.removeAttribute("title");
      }
    });
    badge.hidden = locked;
    statusNode.classList.toggle("ready", !locked);
    if (locked) {
      statusNode.innerHTML =
        "<strong>Safety backup required.</strong><span>Download the backup above. " +
        "Restore and reset will unlock automatically after the server confirms it.</span>";
    } else {
      const minutes = Math.max(1, Math.ceil(seconds / 60));
      statusNode.innerHTML =
        "<strong>Safety backup confirmed.</strong><span>Restore and reset are unlocked for this browser session" +
        (seconds ? " for about " + minutes + " minute" + (minutes === 1 ? "" : "s") + "." : ".") +
        "</span>";
    }
  };

  const scheduleExpiry = seconds => {
    if (expiryTimer) window.clearTimeout(expiryTimer);
    if (!seconds) return;
    expiryTimer = window.setTimeout(() => setLocked(true), (seconds + 1) * 1000);
  };

  const readStatus = async () => {
    if (!statusUrl) return null;
    const response = await fetch(statusUrl, {
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
    });
    if (!response.ok) throw new Error("Could not confirm safety backup status.");
    return response.json();
  };

  const syncStatus = async () => {
    try {
      const data = await readStatus();
      if (!data) return false;
      setLocked(!data.recent, Number(data.expires_in_seconds || 0));
      if (data.recent) scheduleExpiry(Number(data.expires_in_seconds || 0));
      return Boolean(data.recent);
    } catch (_) {
      return false;
    }
  };

  const pollUntilReady = async () => {
    if (polling) return;
    polling = true;
    const started = Date.now();
    statusNode.classList.remove("ready");
    statusNode.innerHTML =
      "<strong>Preparing your safety backup…</strong><span>The download can continue while KOFAD verifies the safety marker.</span>";
    try {
      while (Date.now() - started < 30000) {
        await new Promise(resolve => window.setTimeout(resolve, 450));
        if (await syncStatus()) return;
      }
      statusNode.innerHTML =
        "<strong>Backup download started.</strong><span>If Restore and Reset stay locked, refresh this page after the download finishes.</span>";
    } finally {
      polling = false;
    }
  };

  downloadForm.addEventListener("submit", () => {
    window.setTimeout(pollUntilReady, 250);
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) syncStatus();
  });

  window.addEventListener("pageshow", syncStatus);
  syncStatus();
})();
