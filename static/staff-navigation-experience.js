/* Search visible, authorized staff destinations without changing permissions.
   Progressive enhancement: no JS still shows the complete existing navigation. */
(() => {
  "use strict";
  const input = document.querySelector("#sidebar-page-search");
  const nav = document.querySelector("#staff-sidebar-nav");
  const empty = document.querySelector("#sidebar-search-empty");
  if (!input || !nav) return;
  const links = [...nav.querySelectorAll("a[href]")];
  const sections = [...nav.querySelectorAll(".nav-label")];
  const groups = [];
  let current = null;
  [...nav.children].forEach(node => {
    if (node.matches?.(".nav-label")) {
      current = {heading:node, links:[]};
      groups.push(current);
    } else if (node.matches?.("a[href]")) {
      if (current) current.links.push(node);
    }
  });
  const original = new Map(links.map(link => [link,
    (link.textContent || "").replace(/\s+/g, " ").trim().toLocaleLowerCase()
  ]));
  function filterNavigation() {
    const query = input.value.trim().toLocaleLowerCase();
    for (const link of links) {
      const content = original.get(link) || "";
      link.hidden = !!query && !content.includes(query);
    }
    for (const group of groups) {
      const matchesHeading = !!query &&
        (group.heading.textContent || "").toLocaleLowerCase().includes(query);
      if (matchesHeading) group.links.forEach(link => { link.hidden = false; });
      group.heading.hidden = !!query && !group.links.some(link => !link.hidden);
    }
    if (empty) empty.hidden = !query || links.some(link => !link.hidden);
  }
  input.addEventListener("input", filterNavigation);
  input.addEventListener("keydown", event => {
    if (event.key === "Escape" && input.value) {
      input.value = "";
      filterNavigation();
      event.stopPropagation();
      return;
    }
    if (event.key === "Enter") {
      const visible = links.filter(link => !link.hidden);
      if (visible.length === 1) {
        event.preventDefault();
        visible[0].click();
      }
    }
  });
  window.addEventListener("pageshow", () => {
    input.value = "";
    filterNavigation();
  });
})();
