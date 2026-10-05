(() => {
  "use strict";
  const arm = form => {
    if (!form || form.dataset.submitGuard === "ready") return;
    form.dataset.submitGuard = "ready";
    let submitting = false;
    form.addEventListener("submit", event => {
      if (submitting) {
        event.preventDefault();
        return;
      }
      submitting = true;
      form.setAttribute("aria-busy", "true");
      form.querySelectorAll('button[type="submit"],input[type="submit"],button:not([type])').forEach(button => {
        button.disabled = true;
      });
    });
    window.addEventListener("pageshow", event => {
      if (!event.persisted) return;
      submitting = false;
      form.removeAttribute("aria-busy");
      form.querySelectorAll('button[type="submit"],input[type="submit"],button:not([type])').forEach(button => {
        button.disabled = false;
      });
    });
  };
  document.querySelectorAll("form[method='post'],form[method='POST']").forEach(arm);
})();
