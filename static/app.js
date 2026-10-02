document.addEventListener("DOMContentLoaded", () => {
  document.querySelector("#menu-toggle")?.addEventListener("click", () => document.body.classList.toggle("nav-open"));
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