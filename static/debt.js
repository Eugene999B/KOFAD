document.addEventListener("DOMContentLoaded", () => {
  const box = document.querySelector("#debt-payment-box");
  if (!box) return;

  const amount = document.querySelector("#debt-amount");
  const payFull = document.querySelector("#debt-pay-full");
  const title = document.querySelector("#debt-payment-title");
  const submit = document.querySelector("#debt-payment-submit");
  const preview = document.querySelector("#debt-allocation-lines");
  const outstanding = Number(box.dataset.outstanding || 0);
  const invoices = [...document.querySelectorAll(".debt-invoice-row")].map(row => ({
    reference: row.dataset.reference,
    outstanding: Number(row.dataset.outstanding || 0),
  }));

  function money(value) {
    return new Intl.NumberFormat(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(value || 0);
  }

  function renderPreview() {
    const requested = payFull.value === "1" ? outstanding : Number(amount.value || 0);
    preview.replaceChildren();
    if (!requested || requested <= 0) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "Enter an amount to preview receipt allocation.";
      preview.append(p);
      return;
    }
    let remaining = Math.min(requested, outstanding);
    invoices.forEach(invoice => {
      if (remaining <= 0) return;
      const applied = Math.min(invoice.outstanding, remaining);
      const row = document.createElement("div");
      row.className = "debt-allocation-line";
      const left = document.createElement("span");
      left.textContent = invoice.reference;
      const right = document.createElement("strong");
      right.textContent = money(applied);
      row.append(left, right);
      preview.append(row);
      remaining -= applied;
    });
  }

  function open(mode) {
    const full = mode === "full";
    payFull.value = full ? "1" : "";
    amount.value = full ? outstanding.toFixed(2) : "";
    amount.readOnly = full;
    title.textContent = full ? "Pay the complete customer balance" : "Record a partial customer payment";
    submit.textContent = full ? "Receive " + money(outstanding) + " & issue receipt" : "Save partial payment & issue receipt";
    if (typeof box.showModal === "function") box.showModal();
    else box.setAttribute("open", "");
    document.body.classList.add("dialog-open");
    renderPreview();
    if (!full) window.setTimeout(() => amount.focus(), 30);
  }

  document.querySelectorAll("[data-debt-payment-mode]").forEach(button => {
    button.addEventListener("click", () => open(button.dataset.debtPaymentMode));
  });
  document.querySelectorAll("[data-debt-payment-close]").forEach(button => button.addEventListener("click", () => {
    if (typeof box.close === "function") box.close(); else box.removeAttribute("open");
  }));
  box.addEventListener("close", () => document.body.classList.remove("dialog-open"));
  box.addEventListener("cancel", () => document.body.classList.remove("dialog-open"));
  box.addEventListener("click", event => {
    const rect = box.getBoundingClientRect();
    const outside = event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
    if (outside && typeof box.close === "function") box.close();
  });
  amount?.addEventListener("input", renderPreview);
  renderPreview();
});
