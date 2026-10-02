document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll('form[action="/logout/"]').forEach(form => form.addEventListener("submit",() => {
    try { Object.keys(sessionStorage).filter(key => key.startsWith("kofad-cart:")).forEach(key => sessionStorage.removeItem(key)); } catch (_) {}
  }));
  const toggle = document.querySelector("#menu-toggle"), sidebar = document.querySelector(".sidebar");
  const backdrop = document.querySelector(".nav-backdrop"), body = document.querySelector(".body"), dock = document.querySelector(".mobile-dock");
  const mobile = matchMedia("(max-width:950px)");
  function menu(open, restore = true) {
    document.body.classList.toggle("nav-open", open);
    if (!toggle) return;
    toggle.setAttribute("aria-expanded", String(open)); backdrop.hidden = !open;
    body.inert = open; if (dock) dock.inert = open;
    if (open) sidebar.querySelector(".nav-close").focus();
    else if (restore) toggle.focus();
  }
  toggle?.addEventListener("click", () => menu(true));
  backdrop?.addEventListener("click", () => menu(false));
  document.querySelector(".nav-close")?.addEventListener("click", () => menu(false));
  mobile.addEventListener("change", () => menu(false, false));
  document.addEventListener("keydown", e => {
    if (!document.body.classList.contains("nav-open")) return;
    if (e.key === "Escape") { e.preventDefault(); menu(false); }
    if (e.key === "Tab") {
      const links = [...sidebar.querySelectorAll("a,button")].filter(el => el.getClientRects().length);
      const first = links[0], last = links[links.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
  document.querySelectorAll(".sidebar nav a,.mobile-dock a").forEach(link => {
    const target = new URL(link.href);
    if (target.pathname === location.pathname && (!target.search || target.search === location.search)) {
      link.classList.add("active"); link.setAttribute("aria-current", "page");
    }
  });
  document.querySelectorAll("[data-password-toggle]").forEach(button => button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.passwordToggle), show = input.type === "password";
    input.type = show ? "text" : "password"; button.textContent = show ? "Hide" : "Show";
    button.setAttribute("aria-label", show ? "Hide password" : "Show password");
    button.setAttribute("aria-pressed", String(show));
  }));
  document.querySelectorAll("[data-print]").forEach(b => b.addEventListener("click", () => window.print()));
  document.querySelectorAll("[data-thermal]").forEach(b => b.addEventListener("click", () => {
    document.body.classList.toggle("thermal"); window.print();
  }));
  document.addEventListener("keydown", e => {
    const editing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
    if (!editing && e.key.toLowerCase() === "n" && !e.ctrlKey && !e.metaKey) location.href = "/sales/new/";
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      const search = document.querySelector('input[name="q"]');
      e.preventDefault();
      if (search && location.pathname === "/search/") search.focus(); else location.href = "/search/";
    }
  });
});