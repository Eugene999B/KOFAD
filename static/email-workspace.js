/* Progressive enhancement for the KOFAD owner mailbox-permission matrix. */
document.addEventListener('DOMContentLoaded', () => {
  const form = document.querySelector('.mail-bulk-form');
  if (!form) return;
  const readBoxes = Array.from(form.querySelectorAll('input[name="read_mailboxes"]'));
  const replyBoxes = Array.from(form.querySelectorAll('input[name="send_mailboxes"]'));
  const count = form.querySelector('[data-permission-count]');
  const reads = new Map(readBoxes.map(node => [node.value, node]));
  const replies = new Map(replyBoxes.map(node => [node.value, node]));
  const update = () => {
    const readable = readBoxes.filter(box => box.checked && !box.disabled).length;
    const replyable = replyBoxes.filter(box => box.checked && !box.disabled).length;
    if (count) count.textContent = readable + ' readable · ' + replyable + ' reply-enabled';
  };
  for (const box of replyBoxes) {
    box.addEventListener('change', () => {
      if (box.checked) reads.get(box.value).checked = true;
      update();
    });
  }
  for (const box of readBoxes) {
    box.addEventListener('change', () => {
      if (!box.checked) replies.get(box.value).checked = false;
      update();
    });
  }
  const all = form.querySelector('[data-mail-action="read-all"]');
  if (all) all.addEventListener('click', () => {
    readBoxes.forEach(box => { if (!box.disabled) box.checked = true; });
    update();
  });
  const clear = form.querySelector('[data-mail-action="clear"]');
  if (clear) clear.addEventListener('click', () => {
    [...readBoxes, ...replyBoxes].forEach(box => { box.checked = false; });
    update();
  });
  update();
});


/* Works for out-of-form row checkboxes using their explicit form attribute.
   Confirmation is also enforced by the server for mass/permanent actions. */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-mail-bulk-form]').forEach(form => {
    const rows = Array.from(form.elements).filter(el => el.matches?.('[data-mail-select-row]'));
    const all = form.querySelector('[data-mail-select-all]');
    const count = form.querySelector('[data-mail-selected-count]');
    const sync = () => {
      const selected = rows.filter(el => el.checked).length;
      if (count) count.textContent = selected + ' selected';
      if (all) {
        all.checked = rows.length > 0 && selected === rows.length;
        all.indeterminate = selected > 0 && selected < rows.length;
      }
    };
    all?.addEventListener('change', () => {
      rows.forEach(el => { el.checked = all.checked; });
      sync();
    });
    rows.forEach(el => el.addEventListener('change', sync));
    form.addEventListener('submit', event => {
      const button = event.submitter;
      const action = button?.value || form.querySelector('input[name="action"]')?.value || '';
      if (['trash_selected', 'restore_selected', 'purge_selected'].includes(action)
          && !rows.some(el => el.checked)) {
        event.preventDefault();
        alert('Select one or more messages from this page first.');
        return;
      }
      if (action === 'trash_selected' && !confirm('Move the selected emails to Trash? Pending sends will be cancelled.')) event.preventDefault();
      if (action === 'trash_all' && !confirm('Move ALL matching emails across every page of this department to Trash?')) event.preventDefault();
      if (action === 'purge_selected' && !confirm('Permanently delete the selected emails? This cannot be undone.')) event.preventDefault();
      if (action === 'empty_trash' && !confirm('Permanently empty this department’s Trash, except audit-protected records?')) event.preventDefault();
    });
    sync();
  });
});
