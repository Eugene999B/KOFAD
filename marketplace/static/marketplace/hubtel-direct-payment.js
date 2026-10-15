/* Hubtel customer checkout: per-account first-use SMS, no MoMo PIN is collected. */
(() => {
  'use strict';
  function normalizePhone(raw) {
    let digits = String(raw || '').replace(/\D/g, '');
    if (digits.startsWith('233')) digits = digits.slice(3);
    else if (digits.startsWith('0')) digits = digits.slice(1);
    if (!/^\d{9}$/.test(digits)) throw Error('Enter a valid Ghana Mobile Money number.');
    return '+233' + digits;
  }
  for (const card of document.querySelectorAll('[data-hubtel-direct]')) {
    if (card.dataset.wired === '1') continue;
    card.dataset.wired = '1';
    const form = card.querySelector('[data-hubtel-direct-form]');
    const phoneInput = form?.querySelector('[name=phone]');
    const channelInput = form?.querySelector('[name=channel]');
    const codeInput = form?.querySelector('[name=verification_code]');
    const verifyPanel = form?.querySelector('[data-direct-verify-panel]');
    const verifiedMessage = form?.querySelector('[data-direct-verified]');
    const message = form?.querySelector('[data-hubtel-direct-message]');
    const payButton = form?.querySelector('[data-direct-pay]');
    const nameReview = form?.querySelector('[data-direct-name-review]');
    const registeredName = form?.querySelector('[data-direct-registered-name]');
    const confirmName = form?.querySelector('[data-direct-name-confirm]');
    const token = form?.querySelector('[name=csrfmiddlewaretoken]')?.value;
    const order = card.dataset.orderId;
    if (!order || !token || !phoneInput || !channelInput || !message) continue;
    const base = '/market/orders/' + encodeURIComponent(order) + '/hubtel-direct/';
    let verifiedPhone = '';
    let verifiedName = '';
    let verifiedChannel = '';
    let busy = false;
    let generation = 0;
    function selectedPhone() { return normalizePhone(phoneInput.value); }
    function notify(text) { message.textContent = text; }
    function reset() {
      generation++;
      verifiedPhone = '';
      verifiedName = '';
      verifiedChannel = '';
      if (nameReview) nameReview.hidden = true;
      if (registeredName) registeredName.textContent = '';
      if (confirmName) confirmName.checked = false;
      verifyPanel.hidden = true;
      verifiedMessage.hidden = true;
      payButton.disabled = true;
      notify('');
    }
    phoneInput.addEventListener('input', reset);
    channelInput.addEventListener('change', reset);
    async function post(url, payload) {
      const response = await fetch(url, {
        method: 'POST', credentials: 'same-origin', redirect: 'error',
        headers: {'X-CSRFToken': token, 'Content-Type': 'application/json', 'Accept': 'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) throw Error(data.error || 'The request could not be completed.');
      return data;
    }
    async function verifyRegisteredName(phone, channel) {
      if (verifiedPhone !== phone) return false;
      const result = await post(base + 'verify-phone/', {action:'name', phone, channel});
      if (!result.verified || !result.registered_name) throw Error('Wallet name could not be verified.');
      if (selectedPhone() !== phone || channelInput.value !== channel) return false;
      verifiedName = result.registered_name;
      verifiedChannel = channel;
      registeredName.textContent = result.registered_name;
      nameReview.hidden = false;
      confirmName.checked = false;
      payButton.disabled = true;
      notify('Check the registered name, then confirm it belongs to you.');
      return true;
    }
    confirmName?.addEventListener('change', () => {
      payButton.disabled = !(confirmName.checked && verifiedName && verifiedPhone === selectedPhone() &&
        verifiedChannel === channelInput.value);
    });
    payButton.disabled = true;
    async function checkWallet() {
      const phone = selectedPhone();
      const current = ++generation;
      const result = await post(base + 'verify-phone/', {action: 'check', phone});
      if (current !== generation || selectedPhone() !== phone) return false;
      verifiedPhone = result.verified ? phone : '';
      verifyPanel.hidden = Boolean(result.verified);
      verifiedMessage.hidden = !result.verified;
      if (result.verified) return verifyRegisteredName(phone, channelInput.value);
      notify(result.message || 'Verify your wallet by SMS to continue.');
      return false;
    }
    phoneInput.addEventListener('blur', () => {
      if (phoneInput.value.trim()) void checkWallet().catch(err => notify(err.message));
    });
    async function controlled(fn) {
      if (busy) return;
      busy = true;
      for (const button of form.querySelectorAll('button')) button.disabled = true;
      try { await fn(); }
      catch (error) { notify(error.message || 'Unable to complete request. Check status before retrying.'); }
      finally {
        busy = false;
        for (const button of form.querySelectorAll('button')) {
          if (button !== payButton) button.disabled = false;
        }
        // Never leave the payment action frozen after a verification error.
        let sameWallet = false;
        try {
          sameWallet = verifiedPhone === selectedPhone() && verifiedChannel === channelInput.value;
        } catch (_) { /* Invalid input stays blocked. */ }
        payButton.disabled = !(sameWallet && verifiedName && confirmName?.checked);
      }
    }
    form.querySelector('[data-hubtel-verify-send]')?.addEventListener('click', () => {
      void controlled(async () => {
        const phone = selectedPhone();
        const response = await post(base + 'verify-phone/', {action: 'send', phone});
        notify(response.message || 'SMS code sent.');
      });
    });
    form.querySelector('[data-hubtel-verify-code]')?.addEventListener('click', () => {
      void controlled(async () => {
        const phone = selectedPhone();
        const code = (codeInput?.value || '').trim();
        if (!/^\d{6}$/.test(code)) throw Error('Enter the six-digit SMS code.');
        const data = await post(base + 'verify-phone/', {action: 'verify', phone, code});
        if (data.verified) {
          verifiedPhone = phone;
          verifyPanel.hidden = true;
          verifiedMessage.hidden = false;
          if (codeInput) codeInput.value = '';
          await verifyRegisteredName(phone, channelInput.value);
        }
      });
    });
    form.addEventListener('submit', event => {
      event.preventDefault();
      void controlled(async () => {
        const phone = selectedPhone();
        const channel = channelInput.value;
        if (!['mtn-gh', 'vodafone-gh', 'tigo-gh'].includes(channel))
          throw Error('Choose your Mobile Money network.');
        if (verifiedPhone !== phone) {
          const ok = await checkWallet();
          if (!ok) return;
        }
        const destination = '/market/orders/' + encodeURIComponent(order) + '/payment/verification/';
        try {
          const data = await post(base, {phone, channel, customer_authorized: true});
          if (data.reference) {
            // Stay on KOFAD. Poll only the persisted reference.
            window.location.assign(destination);
          }
        } catch (error) {
          // A lost HTTP response can conceal an accepted money request. Check
          // the saved original reference; NEVER send another provider POST.
          try {
            const check = await fetch(base + 'status/', {
              method:'GET', credentials:'same-origin', cache:'no-store',
              headers:{'Accept':'application/json'}
            });
            if (check.ok) {
              const saved = await check.json();
              if (saved.reference) {
                window.location.assign(destination);
                return;
              }
            }
          } catch (_) { /* Network still unavailable; don't resubmit. */ }
          notify('Payment status could not be confirmed. Open this order again and check for a pending request before retrying. ' + (error.message || ''));
        }
      });
    });
  }
})();
