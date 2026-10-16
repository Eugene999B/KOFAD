/* A display-only status monitor. Never calls a money-moving endpoint. */
(() => {
  'use strict';
  for (const root of document.querySelectorAll('[data-kpay]')) {
    const url = root.dataset.statusUrl;
    const title = root.querySelector('[data-kpay-title]');
    const detail = root.querySelector('[data-kpay-detail]');
    const mark = root.querySelector('[data-kpay-mark]');
    const checkButton = root.querySelector('[data-kpay-check]');
    const receipt = root.querySelector('[data-kpay-receipt]');
    const otp = root.querySelector('[data-kpay-otp]');
    const mode = root.dataset.kpayMode || 'payment';
    const initialState = root.dataset.initialState || '';
    let knownState = initialState;
    let busy = false;
    let timer = null;
    let stopped = false;
    const titles = mode === 'authorization'
      ? {pending:'Awaiting wallet approval', success:'Wallet approval verified', failed:'Approval not completed', review:'Approval needs review'}
      : mode === 'transfer'
      ? {pending:'Transfer processing', success:'Transfer confirmed', failed:'Transfer unsuccessful', review:'Transfer needs review'}
      : {pending:'Waiting for payment', success:'Payment confirmed', failed:'Payment unsuccessful', review:'Payment needs review'};
    function phaseOf(data) {
      const state = String(data.status || data.payment_status || '').toLowerCase();
      if (data.attention || ['attention','unknown','submission_unknown','suspended','cancel_pending'].includes(state)) return 'review';
      if (data.failed || ['failed','rejected','expired','expired_review','reversed','abandoned','cancelled'].includes(state)) return 'failed';
      if (data.paid === true || (mode === 'authorization' && data.provider_confirmed === true) ||
          (mode === 'authorization' && data.provider_approved === true) ||
          (mode === 'authorization' && data.status === 'approved' && data.provider_confirmed !== false) ||
          ['success','issued','settled'].includes(state)) return 'success';
      return 'pending';
    }
    function apply(data) {
      if (!data || typeof data !== 'object' || !data.status) return;
      const state = String(data.status);
      if (root.dataset.reloadOnChange === 'true' && knownState && state !== knownState) {
        location.reload();
        return;
      }
      knownState = state;
      const phase = phaseOf(data);
      root.dataset.phase = phase;
      if (title) title.textContent = mode === 'authorization' && state === 'draft'
        ? 'Review wallet agreement'
        : mode === 'transfer' && state === 'approval_pending'
        ? 'Awaiting transfer approval' : titles[phase];
      if (mark) mark.textContent = phase === 'success' ? '✓' : phase === 'failed' ? '×' : phase === 'review' ? '!' : '••';
      if (detail && data.message && phase !== 'success') detail.textContent = String(data.message).slice(0,240);
      if (phase === 'success' && detail) detail.textContent = mode === 'authorization'
        ? 'Your wallet authorization is verified. This is not a payment or a receipt.'
        : mode === 'transfer' ? 'The transfer has been verified.' : 'KOFAD has independently confirmed this payment.';
      if (receipt) {
        receipt.hidden = phase !== 'success';
        for (const [name, selector] of [['receipt','[data-kpay-receipt-link]'],
                                        ['receipt_thermal','[data-kpay-print-link]']]) {
          const link = receipt.querySelector(selector);
          if (link && typeof data[name] === 'string' && data[name].startsWith('/documents/')) link.href = data[name];
        }
      }
      if (otp) otp.hidden = !data.needs_otp;
      if (phase === 'success' || phase === 'failed') stopped = true;
      if (phase === 'review') stopped = false; // Remain checkable without retrying the payment.
    }
    async function check() {
      if (!url || busy || document.hidden) return;
      busy = true;
      if (checkButton) checkButton.disabled = true;
      try {
        const response = await fetch(url, {credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});
        if (!response.ok) {
          if (response.status === 401 || response.status === 403) {
            if (detail) detail.textContent = 'Sign in again to view this request.';
            stopped = true;
          } else if (detail) detail.textContent = 'Status temporarily unavailable. Your original request is preserved.';
          return;
        }
        apply(await response.json());
      } catch (_) {
        if (detail) detail.textContent = 'Connection interrupted. The original request is still being checked; do not pay twice.';
      } finally {
        busy = false;
        if (checkButton) checkButton.disabled = false;
        clearTimeout(timer);
        if (!stopped) timer = setTimeout(check, 7000);
      }
    }
    root.querySelector('[data-kpay-pos-otp]')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const field = form.querySelector('[name=otp]');
      const button = form.querySelector('[type=submit]');
      const code = String(field?.value || '').trim();
      const reference = root.dataset.reference || '';
      if (!/^[0-9]{4,8}$/.test(code) || !reference) {
        if (detail) detail.textContent = 'Enter the transaction-specific code, never a wallet PIN.';
        return;
      }
      button.disabled = true;
      try {
        const response = await fetch('/api/pos/paystack-momo/' + encodeURIComponent(reference) + '/otp/', {
          method:'POST', credentials:'same-origin', redirect:'error',
          headers:{'Content-Type':'application/json','X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]')?.value || ''},
          body:JSON.stringify({otp:code})
        });
        const data = await response.json();
        if (field) field.value = '';
        if (detail) detail.textContent = data.error || data.message || 'Code submitted. Checking confirmation.';
        if (response.ok) { stopped = false; void check(); }
      } catch (_) {
        if (detail) detail.textContent = 'The code response is unknown. Check the original payment status before retrying.';
      } finally { button.disabled = false; }
    });
    checkButton?.addEventListener('click', () => { clearTimeout(timer); void check(); });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && !stopped) { clearTimeout(timer); void check(); }
    });
    apply({status:initialState,
      provider_confirmed:root.dataset.confirmed==='true',
      provider_approved:root.dataset.confirmed==='true'});
    if (url && !['success','failed'].includes(root.dataset.phase)) timer = setTimeout(check, 1300);
  }
})();
