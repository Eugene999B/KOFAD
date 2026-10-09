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
})();
