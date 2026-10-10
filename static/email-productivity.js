/* Lightweight progressive enhancement; the forms work without JavaScript. */
document.addEventListener("DOMContentLoaded", () => {
  const bulk = document.querySelector(".pro-bulk-form");
  if (bulk) {
    const all = bulk.querySelector("[data-select-all]");
    const rows = Array.from(bulk.querySelectorAll("[data-select-row]"));
    const counter = bulk.querySelector("[data-selection-counter]");
    const sync = () => {
      const count = rows.filter(x => x.checked).length;
      if (counter) counter.textContent = count + " selected";
      if (all) { all.checked = Boolean(count && count === rows.length); all.indeterminate = count > 0 && count < rows.length; }
    };
    if (all) all.addEventListener("change", () => { rows.forEach(x => { x.checked = all.checked; }); sync(); });
    rows.forEach(x => x.addEventListener("change", sync));
    bulk.addEventListener("submit", event => {
      if (!rows.some(x => x.checked)) { event.preventDefault(); alert("Select at least one conversation."); }
    });
    sync();
  }
  document.querySelectorAll("[data-copy-reply]").forEach(button => button.addEventListener("click", () => {
    const section = document.getElementById("reply-" + button.getAttribute("data-copy-reply"));
    if (!section) return;
    const value = section.textContent || "";
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(value).then(() => { button.textContent = "Copied"; });
    } else {
      const control = document.createElement("textarea");
      control.value = value; control.style.position = "fixed"; control.style.opacity = "0";
      document.body.appendChild(control); control.select();
      document.execCommand("copy"); document.body.removeChild(control);
      button.textContent = "Copied";
    }
  }));
  document.querySelectorAll("[data-confirm-delete]").forEach(button => button.addEventListener("click", event => {
    if (!window.confirm("Delete this saved item? This cannot be undone.")) event.preventDefault();
  }));
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
