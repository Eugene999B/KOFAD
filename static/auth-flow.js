(() => {
  "use strict";

  const body = document.body;
  if (!body || !(
    body.classList.contains("login-page") ||
    body.classList.contains("mfa-page") ||
    body.classList.contains("market-access-page")
  )) return;

  const lockForm = form => {
    let submitting = false;
    const submits = [...form.querySelectorAll('button[type="submit"], input[type="submit"], button:not([type])')];

    form.addEventListener("submit", event => {
      if (submitting) {
        event.preventDefault();
        return;
      }
      submitting = true;
      form.setAttribute("aria-busy", "true");
      submits.forEach(button => {
        button.disabled = true;
        button.dataset.authOriginalLabel = button.textContent || button.value || "";
      });
    });

    window.addEventListener("pageshow", event => {
      if (!event.persisted) return;
      submitting = false;
      form.removeAttribute("aria-busy");
      submits.forEach(button => { button.disabled = false; });
    });
  };

  document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(lockForm);
})();
