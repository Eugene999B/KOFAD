/* Existing order-session CSRF; no payment phone/amount is ever editable by browser. */
(() => {
  'use strict';
  for (const card of document.querySelectorAll('[data-hubtel-direct]')) {
    if (card.dataset.wired === '1') continue;
    card.dataset.wired = '1';
    const form = card.querySelector('[data-hubtel-direct-form]');
    const message = card.querySelector('[data-hubtel-direct-message]');
    const order = card.dataset.orderId;
    const token = form.querySelector('[name=csrfmiddlewaretoken]')?.value;
    if (!order || !token) continue;
    const base = '/market/orders/' + encodeURIComponent(order) + '/hubtel-direct/';
    let busy = false;
    async function post(url, payload) {
      if (busy) return;
      busy = true;
      for (const button of card.querySelectorAll('button')) button.disabled = true;
      message.textContent = 'Submitting securely…';
      try {
        const response = await fetch(url, {
          method: 'POST', credentials: 'same-origin', redirect: 'error',
          headers: {'X-CSRFToken': token, 'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
        });
        const json = await response.json();
        if (!response.ok) throw Error(json.error || 'Your request was not accepted.');
        if (json.verified) {
          message.textContent = 'Your phone has been verified. Reloading payment options.';
          window.location.reload();
        } else if (json.reference) {
          message.textContent = 'Payment request recorded. Approve the request on your phone and wait for independent confirmation; do not pay again.';
          window.location.reload();
        } else {
          message.textContent = json.message || 'Request received.';
        }
      } catch (_error) {
        message.textContent = 'The result of this request could not be confirmed. Check this order status before trying again.';
      } finally {
        busy = false;
        for (const button of card.querySelectorAll('button')) button.disabled = false;
      }
    }
    card.querySelector('[data-hubtel-verify-send]')?.addEventListener('click', () => {
      void post(base + 'verify-phone/', {action: 'send'});
    });
    card.querySelector('[data-hubtel-verify-code]')?.addEventListener('click', () => {
      const value = form.querySelector('[name=verification_code]')?.value || '';
      if (!/^\d{6}$/.test(value)) {
        message.textContent = 'Enter the six-digit SMS verification code.';
        return;
      }
      void post(base + 'verify-phone/', {action: 'verify', code: value});
    });
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const channel = form.querySelector('[name=channel]')?.value;
      if (!['mtn-gh', 'vodafone-gh', 'tigo-gh'].includes(channel)) {
        message.textContent = 'Select the correct Mobile Money network.';
        return;
      }
      void post(base, {channel});
    });
  }
})();
