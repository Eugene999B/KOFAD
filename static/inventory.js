document.addEventListener("DOMContentLoaded", () => {
  const dialog = document.querySelector("#restock-dialog");
  if (!dialog) return;

  const productInput = document.querySelector("#restock-product");
  const name = document.querySelector("#restock-name");
  const current = document.querySelector("#restock-current");
  const packs = document.querySelector("#restock-packs");
  const loose = document.querySelector("#restock-loose");
  const added = document.querySelector("#restock-added");
  const newBalance = document.querySelector("#restock-new-balance");
  const packLabel = document.querySelector("#restock-pack-label");
  const unitLabel = document.querySelector("#restock-unit-label");
  const cost = document.querySelector("#restock-cost");
  let packSize = 1;
  let stock = 0;
  let packName = "pack";
  let baseUnit = "unit";

  function updatePreview() {
    const fullPacks = Math.max(0, Number(packs.value || 0));
    const looseUnits = Math.max(0, Number(loose.value || 0));
    const quantity = fullPacks * packSize + looseUnits;
    added.textContent = quantity + " " + baseUnit;
    newBalance.textContent = (stock + quantity) + " " + baseUnit;
  }

  function open(button) {
    productInput.value = button.dataset.product || "";
    name.textContent = button.dataset.name || "Choose a product";
    packSize = Math.max(1, Number(button.dataset.packSize || 1));
    stock = Math.max(0, Number(button.dataset.stock || 0));
    packName = button.dataset.packName || "pack";
    baseUnit = button.dataset.baseUnit || "unit";
    current.textContent = stock + " " + baseUnit;
    packs.value = "0";
    loose.value = "0";
    cost.value = button.dataset.cost || "";
    unitLabel.textContent = baseUnit;
    packLabel.firstChild.textContent = "Full " + packName + "s ";
    packLabel.classList.toggle("hidden", packSize <= 1);
    loose.max = packSize > 1 ? String(packSize - 1) : "";
    updatePreview();
    if (typeof dialog.showModal === "function") dialog.showModal();
    else dialog.setAttribute("open", "");
  }

  document.querySelectorAll("[data-restock-open]").forEach(button => {
    button.addEventListener("click", () => {
      if (!button.dataset.product) {
        const first = document.querySelector("[data-restock-open][data-product]:not([data-product=''])");
        if (first) return open(first);
      }
      open(button);
    });
  });
  document.querySelectorAll("[data-restock-close]").forEach(button => {
    button.addEventListener("click", () => dialog.close());
  });
  [packs, loose].forEach(input => input?.addEventListener("input", updatePreview));
  dialog.addEventListener("click", event => {
    const rect = dialog.getBoundingClientRect();
    const outside = event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
    if (outside) dialog.close();
  });
});
