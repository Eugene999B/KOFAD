/* Keep the payment-pricing input numeric without silently rewriting pasted values. */
(() => {
  const field = document.querySelector("#online-markup-percent");
  if (!field) return;
  const shape = /^[0-9]*\.?[0-9]*$/;
  field.addEventListener("beforeinput", event => {
    if (event.isComposing || typeof event.data !== "string") return;
    const begin = field.selectionStart ?? field.value.length;
    const end = field.selectionEnd ?? field.value.length;
    const candidate = field.value.slice(0, begin) + event.data + field.value.slice(end);
    if (!shape.test(candidate)) event.preventDefault();
  });
  field.addEventListener("input", () => {
    field.setCustomValidity(shape.test(field.value) ? "" :
      "Use numbers and one decimal point only. Do not use commas, plus/minus signs or percent symbols.");
  });
})();
