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
