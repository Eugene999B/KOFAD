(() => {
  "use strict";
  const root = document.querySelector("#closing-workspace");
  if (!root) return;
  const controls = ["opening-cash", "closing-cash-in", "closing-cash-out"].map(id => document.getElementById(id));
  const output = document.getElementById("expected-closing-cash");
  const cents = value => {
    const number = Number(value || 0);
    return Number.isFinite(number) ? Math.round(number * 100) : 0;
  };
  const netCash = cents(root.dataset.netCash);
  function update() {
    const [opening, incoming, outgoing] = controls.map(input => Math.max(0, cents(input?.value)));
    output.textContent = ((opening + netCash + incoming - outgoing) / 100).toFixed(2);
  }
  controls.forEach(input => input?.addEventListener("input", update));
  const counts = [...root.querySelectorAll("[data-denomination]")];
  const status = root.querySelector("[data-denomination-status]");
  function countTotal() {
    return counts.reduce((total, input) => total + Number(input.dataset.denomination) * Number(input.value || 0), 0);
  }
  counts.forEach(input => input.addEventListener("input", () => {
    const valid = counts.every(field => field.checkValidity());
    root.querySelector("[data-denomination-total]").textContent = valid ? (countTotal() / 100).toFixed(2) : "Check quantities";
    status.textContent = "";
  }));
  root.querySelector("[data-apply-denominations]").addEventListener("click", () => {
    if (!counts.every(input => input.reportValidity())) return;
    const cash = root.querySelector('input[name="cash"]');
    cash.value = (countTotal() / 100).toFixed(2);
    cash.dispatchEvent(new Event("input", {bubbles: true}));
    status.textContent = "Applied to physical cash counted. Review before submitting.";
  });
  update();
})();
