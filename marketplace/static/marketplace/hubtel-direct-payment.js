/* KOFAD customer checkout: account phone or fresh OTP for a different phone. */
(() => {
  'use strict';
  function normalizePhone(raw) {
    let digits = String(raw || '').replace(/\D/g, '');
    if (digits.startsWith('233')) digits = digits.slice(3);
    else if (digits.startsWith('0')) digits = digits.slice(1);
    if (!/^\d{9}$/.test(digits)) throw Error('Enter a valid Ghana phone number.');
    return '+233' + digits;
  }
  for (const card of document.querySelectorAll('[data-hubtel-direct]')) {
    if (card.dataset.wired === '1') continue;
    card.dataset.wired = '1';
    const form = card.querySelector('[data-hubtel-direct-form]');
    const accountPhone = card.dataset.accountPhone || '';
    const phoneInput = form?.querySelector('[name=phone]');
    const choices = [...(form?.querySelectorAll('[name=wallet_choice]') || [])];
    const differentBox = form?.querySelector('[data-direct-other-phone]');
    const channelInput = form?.querySelector('[name=channel]');
    const codeInput = form?.querySelector('[name=verification_code]');
    const otpPanel = form?.querySelector('[data-direct-verify-panel]');
    const verifiedNotice = form?.querySelector('[data-direct-verified]');
    const message = form?.querySelector('[data-hubtel-direct-message]');
    const payButton = form?.querySelector('[data-direct-pay]');
    const token = form?.querySelector('[name=csrfmiddlewaretoken]')?.value;
    const order = card.dataset.orderId;
    if (!form || !token || !phoneInput || !channelInput || !message || !payButton || !order) continue;
    const base = '/market/orders/' + encodeURIComponent(order) + '/hubtel-direct/';
    const destination = '/market/orders/' + encodeURIComponent(order) + '/payment/verification/';
    let busy = false;
    let locked = false;
    let verifiedPhone = '';
    let verifiedChannel = '';
    let generation = 0;
    function choice() { return choices.find(x => x.checked)?.value || 'different'; }
    function selectedPhone() {
      return normalizePhone(choice() === 'account' && accountPhone ? accountPhone : phoneInput.value);
    }
    function notify(msg) { message.textContent = msg || ''; }
    function reset() {
      generation++;
      verifiedPhone = '';
      verifiedChannel = '';
      if (codeInput) codeInput.value = '';
      if (otpPanel) otpPanel.hidden = true;
      if (verifiedNotice) verifiedNotice.hidden = true;
      if (differentBox) differentBox.hidden = choice() !== 'different';
      phoneInput.disabled = choice() !== 'different';
      phoneInput.required = choice() === 'different';
      payButton.disabled = locked || busy;
      if (!locked) notify('');
    }
    choices.forEach(x => x.addEventListener('change', reset));
    phoneInput.addEventListener('input', reset);
    channelInput.addEventListener('change', reset);
    reset();
    async function post(url, payload) {
      const controller = new AbortController();
      const deadline = setTimeout(() => controller.abort(), 20000);
      let response;
      try {
        response = await fetch(url, {
          method: 'POST', credentials: 'same-origin', redirect: 'error',
          headers: {'X-CSRFToken': token, 'Content-Type': 'application/json',
            'Accept': 'application/json'},
          body: JSON.stringify(payload), signal: controller.signal,
        });
      } finally { clearTimeout(deadline); }
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(data.error || 'Request unsuccessful. Please try again.');
        error.rejected = response.status >= 400 && response.status < 500;
        throw error;
      }
      return data;
    }
    async function run(fn) {
      if (busy || locked) return;
      busy = true;
      form.querySelectorAll('button').forEach(b => b.disabled = true);
      try { await fn(); }
      catch (e) { notify(e.message || 'Unable to complete the request.'); }
      finally {
        busy = false;
        form.querySelectorAll('button').forEach(b => { b.disabled = locked; });
      }
    }
    async function statusOrLock() {
      try {
        const response = await fetch(base + 'status/', {
          method: 'GET', credentials: 'same-origin', cache: 'no-store',
          headers: {'Accept':'application/json'}
        });
        if (response.ok && (await response.json()).reference) {
          window.location.assign(destination);
          return;
        }
      } catch (_) { /* Never treat a missing reply as permission to charge twice. */ }
      locked = true;
      payButton.disabled = true;
      notify('Payment request status is uncertain. Check your order before trying again.');
    }
    form.querySelector('[data-hubtel-verify-send]')?.addEventListener('click', () => {
      void run(async () => {
        const phone = selectedPhone();
        const channel = channelInput.value;
        const data = await post(base + 'verify-phone/', {action:'send', phone, channel});
        verifiedPhone = '';
        verifiedChannel = '';
        if (data.verified) {
          verifiedPhone = phone;
          verifiedChannel = channel;
          otpPanel.hidden = true;
          verifiedNotice.hidden = false;
        }
        notify(data.message || 'SMS code sent.');
      });
    });
    form.querySelector('[data-hubtel-verify-code]')?.addEventListener('click', () => {
      void run(async () => {
        const phone = selectedPhone();
        const channel = channelInput.value;
        const code = (codeInput?.value || '').trim();
        if (!/^\d{6}$/.test(code)) throw Error('Enter the 6-digit SMS code.');
        const seq = generation;
        const data = await post(base + 'verify-phone/', {action:'verify', phone, channel, code});
        if (seq !== generation || selectedPhone() !== phone || channelInput.value !== channel) return;
        if (!data.verified) throw Error('Verification was not completed.');
        verifiedPhone = phone;
        verifiedChannel = channel;
        otpPanel.hidden = true;
        verifiedNotice.hidden = false;
        codeInput.value = '';
        notify('Number confirmed. Select Continue to pay.');
      });
    });
    form.addEventListener('submit', ev => {
      ev.preventDefault();
      void run(async () => {
        const phone = selectedPhone();
        const channel = channelInput.value;
        if (!['mtn-gh','vodafone-gh','tigo-gh'].includes(channel))
          throw Error('Select a Mobile Money network.');
        const seq = generation;
        if (verifiedPhone !== phone || verifiedChannel !== channel) {
          const check = await post(base + 'verify-phone/', {action:'check', phone, channel});
          if (seq !== generation) return;
          if (!check.verified) {
            otpPanel.hidden = false;
            verifiedNotice.hidden = true;
            notify('Verify this number with an SMS code.');
            otpPanel.scrollIntoView({behavior:'smooth', block:'nearest'});
            return;
          }
          verifiedPhone = phone;
          verifiedChannel = channel;
        }
        try {
          const paid = await post(base, {phone, channel, customer_authorized:true});
          if (paid.reference) window.location.assign(destination);
          else await statusOrLock();
        } catch (error) {
          // A provider POST may have happened even if the browser timed out.
          if (!error.rejected) await statusOrLock();
          else {
            verifiedPhone = '';
            verifiedChannel = '';
            if (error.message.toLowerCase().includes('existing payment')) {
              await statusOrLock();
            } else notify(error.message);
          }
        }
      });
    });
  }
})();
