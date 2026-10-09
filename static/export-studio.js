(() => {
  "use strict";
  const selector = document.getElementById("export-dataset");
  const helper = document.getElementById("export-window-note");
  const hint = document.getElementById("export-scope-hint");
  const start = document.getElementById("export-start");
  const end = document.getElementById("export-end");
  if (!selector || !helper || !hint) return;
  function refresh() {
    const option = selector.options[selector.selectedIndex];
    const snapshot = option && option.dataset.snapshot === "true";
    helper.lastElementChild.textContent = snapshot
      ? "This report uses the latest available records. The date inputs do not limit its snapshot."
      : "This report includes records created within the selected start and end dates.";
    hint.textContent = "Only records within your authorized location and role scope are included.";
    if (start && end) {
      start.setAttribute("aria-describedby", "export-window-note");
      end.setAttribute("aria-describedby", "export-window-note");
    }
  }
  selector.addEventListener("change", refresh);
  refresh();

  const search = document.getElementById("export-library-query");
  const empty = document.getElementById("export-library-no-results");
  if (search && empty) {
    const groups = [...document.querySelectorAll(".export-library-group")];
    const updateLibrary = () => {
      const term = search.value.trim().toLocaleLowerCase();
      let visible = 0;
      groups.forEach((group) => {
        const items = [...group.querySelectorAll(".export-library-item")];
        let groupVisible = 0;
        items.forEach((item) => {
          const match = item.textContent.toLocaleLowerCase().includes(term);
          item.hidden = !match;
          if (match) groupVisible += 1;
        });
        group.hidden = groupVisible === 0;
        visible += groupVisible;
      });
      empty.hidden = visible !== 0;
    };
    search.addEventListener("input", updateLibrary);
  }
})();
