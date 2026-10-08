document.addEventListener("DOMContentLoaded", () => {
  const panel = document.querySelector("[data-payment-progress]");
  if (!panel) return;
  const message = panel.querySelector("[data-payment-progress-message]");
  const started = Date.now();
  let timer;
  let inFlight = false;
  let stopped = false;
  const schedule = () => {
    clearTimeout(timer);
    if (!stopped) timer = setTimeout(check, 5000);
  };
  const check = async () => {
    if (stopped || inFlight) return;
    if (Date.now() - started >= 10 * 60 * 1000) {
      stopped = true;
      if (message) message.textContent = "Confirmation is taking longer than expected. We will keep checking in the background. If money was deducted, do not pay again; contact KOFAD for help.";
      return;
    }
    if (document.hidden) { schedule(); return; }
    inFlight = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(panel.dataset.statusUrl, {
        credentials:"same-origin", cache:"no-store", signal:controller.signal,
        headers:{"Accept":"application/json"},
      });
      if (response.redirected || response.status === 401 || response.status === 403) {
        stopped = true;
        if (message) message.textContent = "Sign in again to see your latest order status.";
        return;
      }
      if (!response.ok) return;
      const data = await response.json();
      if (message && data.message) message.textContent = data.message;
      if (data.attention) {
        stopped = true;
        if (message) message.textContent = "Your payment needs review. If money was deducted, do not pay again; contact KOFAD.";
        return;
      }
      if (data.paid || data.order_status === "cancelled" || !data.waiting) {
        stopped = true;
        location.reload();
      }
    } catch (_) {
      // A network failure is not a payment failure. The worker continues verification.
    } finally {
      clearTimeout(timeout);
      inFlight = false;
      schedule();
    }
  };
  document.addEventListener("visibilitychange", () => { if (!document.hidden) check(); });
  window.addEventListener("pagehide", () => { stopped = true; clearTimeout(timer); });
  check();
});
