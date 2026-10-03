document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("#product-setup-form");
  if (!form) return;
  const structure = document.querySelector("#id_pack_enabled");
  const packName = document.querySelector("#id_pack_name");
  const packSize = document.querySelector("#id_pack_size");
  const openingPacks = document.querySelector("#id_opening_packs");
  const openingUnits = document.querySelector("#id_opening_units");
  const packFields = [...document.querySelectorAll("[data-pack-only]")];
  const singleFields = [...document.querySelectorAll("[data-single-only]")];
  const retailLabel = document.querySelector("#retail-unit-label");
  const wholesaleLabel = document.querySelector("#wholesale-unit-label");
  const costLabel = document.querySelector("#cost-label");
  const openingLabel = document.querySelector("#opening-units-label");
  const openingHelp = document.querySelector("#opening-units-help");

  function sync() {
    const packed = structure?.value === "yes";
    packFields.forEach(node => node.classList.toggle("hidden", !packed));
    singleFields.forEach(node => node.classList.toggle("hidden", packed));
    if (retailLabel) retailLabel.textContent = packed ? "Retail price · one loose unit" : "Retail price";
    if (wholesaleLabel) wholesaleLabel.textContent = packed ? "Wholesale price · one loose unit" : "Wholesale price";
    if (costLabel) costLabel.textContent = packed ? "Cost per loose unit" : "Cost per unit";
    if (openingLabel) openingLabel.textContent = packed ? "Opening loose units" : "Opening quantity";
    if (openingHelp) openingHelp.textContent = packed ? "Loose pieces outside a full pack." : "Number of complete single items currently on hand.";
    if (!packed) {
      if (packSize) packSize.value = "1";
      if (packName) packName.value = document.querySelector("#id_base_unit")?.value || "unit";
      if (openingPacks) openingPacks.value = "0";
    }
    const word = (packName?.value || "pack").trim() || "pack";
    document.querySelectorAll("[data-pack-word]").forEach(node => node.textContent = word);
  }

  structure?.addEventListener("change", sync);
  packName?.addEventListener("input", sync);
  document.querySelector("#id_base_unit")?.addEventListener("input", () => {
    if (structure?.value === "no" && packName) packName.value = document.querySelector("#id_base_unit").value || "unit";
  });
  sync();
});