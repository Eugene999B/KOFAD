(() => {
  "use strict";
  const form = document.querySelector("[data-expense-form]");
  if (!form) return;

  const source = document.querySelector("#expense-funding-source");
  const treatment = document.querySelector("#expense-closing-treatment");
  const method = document.querySelector("#expense-method");
  const methodWrap = document.querySelector("[data-expense-method-wrap]");
  const noteWrap = document.querySelector("[data-expense-funding-note]");
  const note = noteWrap?.querySelector("textarea");
  const preview = document.querySelector("[data-expense-treatment-preview]");

  const labels = {
    today_sales_receipts: "today's sales receipts",
    petty_cash: "petty cash",
    prior_business_funds: "prior business funds",
    owner_manager_funds: "owner / manager funds",
    bank_account: "the business bank account",
    momo_wallet: "the business MoMo wallet",
    unpaid_credit: "unpaid credit",
    other: "another funding source",
  };

  function sync() {
    const value = source?.value || "today_sales_receipts";
    const fromToday = value === "today_sales_receipts";
    const noBusinessChannel = ["owner_manager_funds", "unpaid_credit"].includes(value);

    if (treatment) {
      treatment.value = fromToday ? "1" : "0";
      treatment.disabled = true;
    }

    if (value === "petty_cash" && method) method.value = "cash";
    if (value === "bank_account" && method) method.value = "bank";
    if (value === "momo_wallet" && method) method.value = "momo";

    methodWrap?.classList.toggle("hidden", noBusinessChannel);
    if (method) method.disabled = noBusinessChannel;

    const other = value === "other";
    noteWrap?.classList.toggle("hidden", !other);
    if (note) note.required = other;

    if (!preview) return;
    if (fromToday) {
      preview.innerHTML =
        "<strong>Daily Closing will reduce.</strong><span>This expense comes from today's takings, so KOFAD will subtract it from the selected payment channel's expected settlement.</span>";
      preview.className = "expense-treatment-preview closing";
    } else if (value === "unpaid_credit") {
      preview.innerHTML =
        "<strong>Accounting only · unpaid.</strong><span>The expense reduces profit and creates an amount payable. No Cash, MoMo, Bank or Card balance is reduced today.</span>";
      preview.className = "expense-treatment-preview accounting";
    } else if (value === "owner_manager_funds") {
      preview.innerHTML =
        "<strong>Accounting only · owner funded.</strong><span>The expense reduces profit and is funded as owner capital. Today's business settlement is unchanged.</span>";
      preview.className = "expense-treatment-preview accounting";
    } else {
      preview.innerHTML =
        "<strong>Accounting only for Daily Closing.</strong><span>The expense is funded from " +
        (labels[value] || "another source") +
        ", so it stays in expense/accounting reports without reducing today's expected settlement.</span>";
      preview.className = "expense-treatment-preview accounting";
    }
  }

  source?.addEventListener("change", sync);
  form.addEventListener("submit", () => {
    if (treatment) treatment.disabled = false;
    if (method) method.disabled = false;
  });
  sync();
})();
