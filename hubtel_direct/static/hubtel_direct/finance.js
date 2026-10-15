(() => {
  'use strict';
  const form = document.querySelector('[data-direct-payout-request]');
  if (form) {
    let attempted = false;
    // Submission is always clickable; an unmet verification requirement must
    // explain itself instead of looking like a frozen button.
    form.noValidate = true;
    const kind = form.querySelector('[name=kind]');
    const network = form.querySelector('[data-momo-network]');
    const bank = form.querySelector('[data-bank-choice]');
    const output = form.querySelector('[data-direct-payout-message]');
    const verifyButton = form.querySelector('[data-verify-recipient]');
    const verifiedStatus = form.querySelector('[data-recipient-verification]');
    const confirmIdentity = form.querySelector('[data-recipient-confirm]');
    const submitButton = form.querySelector('[data-submit-transfer]');
    const nameInput = form.elements.recipient_name;
    let verifiedSnapshot = '';
    const identityKey = () => [
      kind.value, form.elements.destination.value.trim(),
      kind.value === 'send_bank' ? form.elements.bank_code.value : form.elements.channel.value,
    ].join('|');
    const invalidate = () => {
      verifiedSnapshot = '';
      if (nameInput) nameInput.value = '';
      if (confirmIdentity) { confirmIdentity.checked = false; confirmIdentity.disabled = true; }
      if (submitButton) submitButton.setAttribute('aria-disabled', 'true');
      if (verifiedStatus) verifiedStatus.textContent = 'Not verified';
    };
    for (const input of [kind, form.elements.destination, form.elements.channel, form.elements.bank_code]) {
      input?.addEventListener('change', invalidate);
      if (input === form.elements.destination) input.addEventListener('input', invalidate);
    }
    confirmIdentity?.addEventListener('change', () => {
      if (submitButton) submitButton.setAttribute('aria-disabled', String(!(confirmIdentity.checked && verifiedSnapshot === identityKey())));
    });
    verifyButton?.addEventListener('click', async () => {
      invalidate();
      const destination = form.elements.destination.value.trim();
      if (!destination) { verifiedStatus.textContent = 'Enter the recipient number first.'; return; }
      verifyButton.disabled = true;
      verifiedStatus.textContent = 'Checking registered name…';
      const snapshot = identityKey();
      const payload = kind.value === 'send_bank'
        ? {kind:'bank', bank_code:form.elements.bank_code.value, account:destination}
        : {kind:'momo', phone:destination, channel:form.elements.channel.value};
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetch('/finance/hubtel-direct/verify-beneficiary/', {
          method:'POST', credentials:'same-origin', redirect:'error', signal:controller.signal,
          headers:{'X-CSRFToken':form.elements.csrfmiddlewaretoken.value,
                   'Content-Type':'application/json'},
          body:JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok || !data.verified || !data.registered_name) {
          verifiedStatus.textContent = data.error || 'Name could not be verified. No transfer was submitted.';
          return;
        }
        if (snapshot !== identityKey()) { invalidate(); return; }
        nameInput.value = data.registered_name;
        verifiedSnapshot = snapshot;
        verifiedStatus.textContent = 'Verified: ' + data.registered_name + ' · ****' + data.destination_suffix;
        confirmIdentity.disabled = false;
      } catch (_) {
        verifiedStatus.textContent = 'Could not verify the account. Check the connection and try again. No transfer was submitted.';
      } finally { clearTimeout(timeout); verifyButton.disabled = false; }
    });
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
      if (!verifiedSnapshot || verifiedSnapshot !== identityKey() || !confirmIdentity?.checked) {
        output.textContent = 'First enter the recipient, tap Verify recipient, and confirm the name before requesting a transfer.';
        verifyButton?.focus();
        return;
      }
      if (!form.reportValidity()) {
        output.textContent = 'Enter the amount and reason to continue.';
        return;
      }
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
        output.textContent = 'Request status unknown. Check transfer history before trying again to prevent duplicates.';
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
        output.textContent = 'Approval recorded. Transfer confirmation is pending.';
        location.reload();
      } catch (_) {
        output.textContent = 'Approval outcome unknown. Check the transfer history; do not approve again blindly.';
      }
    });
  }
})();
