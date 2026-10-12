(() => {
  'use strict';
  const form = document.querySelector('[data-direct-payout-request]');
  if (form) {
    let attempted = false;
    const kind = form.querySelector('[name=kind]');
    const network = form.querySelector('[data-momo-network]');
    const bank = form.querySelector('[data-bank-choice]');
    const output = form.querySelector('[data-direct-payout-message]');
    const update = () => {
      const isBank = kind.value === 'send_bank';
      network.hidden = isBank; bank.hidden = !isBank;
    };
    kind.addEventListener('change', update);
    update();
    // A stable key for this form prevents double payout intents if the HTTP
    // response goes missing. Never blindly use a new key after an unknown send.
    const key = crypto.randomUUID();
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (attempted) return;
      attempted = true;
      form.querySelector('button[type=submit]').disabled = true;
      output.textContent = 'Recording for a second-person approval…';
      const isBank = kind.value === 'send_bank';
      const body = {
        kind: kind.value,
        amount: form.elements.amount.value,
        destination: form.elements.destination.value.trim(),
        recipient_name: form.elements.recipient_name.value.trim(),
        purpose: form.elements.purpose.value.trim(),
      };
      if (isBank) body.bank_code = form.elements.bank_code.value;
      else body.channel = form.elements.channel.value;
      try {
        const response = await fetch('/finance/hubtel-direct/payouts/', {
          method: 'POST', credentials: 'same-origin', redirect: 'error',
          headers: {'X-CSRFToken': form.elements.csrfmiddlewaretoken.value, 'Idempotency-Key': key, 'Content-Type': 'application/json'},
          body: JSON.stringify(body),
        });
        const data = await response.json();
        if (!response.ok) {
          attempted = false;
          form.querySelector('button[type=submit]').disabled = false;
          output.textContent = data.error || 'Request rejected.';
          return;
        }
        output.textContent = 'Saved for approval. Reference: ' + data.reference;
        location.reload();
      } catch (_) {
        output.textContent = 'Request result unknown. Check the transfer history before creating a new request.';
      }
    });
  }
  for (const form of document.querySelectorAll('[data-direct-payout-approve]')) {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const id = form.dataset.id;
      const output = document.querySelector('[data-direct-approval-message]');
      if (!confirm('Approve this verified recipient and transfer amount? The request may send REAL money when activated.')) return;
      form.querySelector('button').disabled = true;
      output.textContent = 'Submitting approval securely…';
      try {
        const response = await fetch('/finance/hubtel-direct/payouts/' + encodeURIComponent(id) + '/approve/', {
          method: 'POST', credentials: 'same-origin', redirect: 'error',
          headers: {'X-CSRFToken': form.elements.csrfmiddlewaretoken.value, 'Content-Type': 'application/json'},
          body: '{}',
        });
        const data = await response.json();
        if (!response.ok) {
          output.textContent = data.error || 'Approval was not accepted.';
          form.querySelector('button').disabled = false;
          return;
        }
        output.textContent = 'Approval recorded. Hubtel reconciliation is pending.';
        location.reload();
      } catch (_) {
        output.textContent = 'Approval outcome unknown. Check the transfer history; do not approve again blindly.';
      }
    });
  }
})();
