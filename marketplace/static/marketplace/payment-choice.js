document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-payment-choice]").forEach(panel => {
    const method = panel.querySelector("[name=payment_method]");
    const fields = panel.querySelector("[data-momo-fields]");
    if (!method || !fields) return;
    const sync = () => {
      const active = method.value === "momo";
      fields.hidden = !active;
      fields.querySelectorAll("input, select").forEach(input => {
        input.disabled = !active; input.required = active;
      });
    };
    method.addEventListener("change", sync); sync();
  });
});
