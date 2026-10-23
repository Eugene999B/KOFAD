/* CSP-safe confirmation gates for sensitive staff actions.
 * Prevent submission/click unless the staff member explicitly confirms.
 * Never infer permission from a browser prompt; server-side controls remain authoritative.
 */
(() => {
  "use strict";
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const question = form.dataset.confirmSubmit;
    if (question && !window.confirm(question)) event.preventDefault();
  }, true);
  document.addEventListener("click", (event) => {
    const button = event.target instanceof Element
      ? event.target.closest("button[data-confirm-click]") : null;
    if (!button) return;
    const question = button.dataset.confirmClick;
    if (question && !window.confirm(question)) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);
})();
