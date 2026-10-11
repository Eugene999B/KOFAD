(() => {
"use strict";
const root = document.querySelector("#momo-monitor");
if (!root) return;
const reference = root.dataset.reference;
const status = document.querySelector("#payment-stage");
const label = document.querySelector("#payment-state-label");
const message = document.querySelector("#payment-stage-message");
const verify = document.querySelector("#payment-verify-button");
const receipt = document.querySelector("#payment-receipt");
const otp = document.querySelector("#payment-otp");
const tx = document.querySelector("#payment-transaction-id");
const csrf = document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
let timer, busy = false;
const update = data => {
  if (!data || data.reference !== reference) return;
  status.dataset.state = data.status;
  label.textContent = data.display_status || "Pending";
  message.textContent = data.message || "Awaiting Paystack verification.";
  tx.textContent = data.transaction_id || "Not yet confirmed";
  verify.disabled = Boolean(!data.pending || data.paid || data.attention);
  otp.classList.toggle("hidden", !data.needs_otp);
  receipt.classList.toggle("hidden", !data.paid);
  if (data.paid) {
    for (const [id, key] of [["payment-print","receipt_thermal"],["payment-a4","receipt_a4"],["payment-document","receipt"]]) {
      const link = document.getElementById(id);
      if (link && data[key] && data[key].startsWith("/documents/")) link.href = data[key];
    }
  }
  if (data.paid || data.attention || !data.pending) clearTimeout(timer);
};
const check = async () => {
  if (busy || document.hidden) return;
  busy = true;
  try {
    const r = await fetch(window.location.pathname+"?format=json", {credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json"}});
    if (r.ok) update(await r.json());
    else if (r.status === 403 || r.status === 401) message.textContent = "Sign in again to monitor payment.";
  } catch (_) { message.textContent = "Connection interrupted. Background verification continues; refresh when online."; }
  finally { busy = false; if (!verify.disabled) timer = setTimeout(check, 5000); }
};
document.addEventListener("visibilitychange", () => { if (!document.hidden) {clearTimeout(timer);check();} });
document.querySelector("#payment-code-form")?.addEventListener("submit", async e => {
  e.preventDefault();
  const input = document.querySelector("#payment-code");
  if (!/^[0-9]{4,8}$/.test(input?.value || "")) return;
  const btn = e.currentTarget.querySelector("button");
  btn.disabled = true;
  try {
    const r = await fetch("/api/pos/paystack-momo/"+encodeURIComponent(reference)+"/otp/", {method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-CSRFToken":csrf},body:JSON.stringify({otp:input.value})});
    const data = await r.json();
    input.value = "";
    message.textContent = data.message || data.error || "Payment code submitted.";
    if (r.ok) check();
  } catch (_) { message.textContent = "Unable to submit code. Try again."; }
  finally {btn.disabled = false;}
});
timer = setTimeout(check, 1500);
})();