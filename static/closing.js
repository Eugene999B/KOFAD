(() => {
  "use strict";
  const root = document.querySelector("#closing-workspace");
  if (!root) return;
  const opening = document.querySelector("#opening-cash");
  const cashIn = document.querySelector("#closing-cash-in");
  const cashOut = document.querySelector("#closing-cash-out");
  const output = document.querySelector("#expected-closing-cash");
  const netCash = Number(root.dataset.netCash || 0);

  function amount(input) {
    const value = Number(input?.value || 0);
    return Number.isFinite(value) && value >= 0 ? value : 0;
  }

  function update() {
    const expected = amount(opening) + netCash + amount(cashIn) - amount(cashOut);
    output.textContent = expected.toFixed(2);
  }

  [opening, cashIn, cashOut].forEach(input => input?.addEventListener("input", update));
  update();
})();