document.addEventListener("DOMContentLoaded", () => {
  const panel = document.querySelector("[data-payment-launch]");
  if (!panel) return;

  const message = panel.querySelector("[data-payment-launch-message]");
  const raw = panel.dataset.checkoutUrl || "";
  let url;
  try {
    url = new URL(raw, window.location.href);
  } catch (_) {
    if (message) message.textContent = "The secure payment link could not be opened. Please use the button below or return to your order.";
    return;
  }

  const allowed = new Set(["pay.hubtel.com", "checkout.paystack.com"]);
  if (url.protocol !== "https:" || !allowed.has(url.hostname)) {
    if (message) message.textContent = "KOFAD blocked an invalid payment destination.";
    return;
  }

  // The previous page may have been a POST form. Starting this navigation from a
  // normal same-origin GET page avoids mobile browsers suppressing the external handoff.
  window.setTimeout(() => {
    window.location.replace(url.href);
  }, 120);
});
