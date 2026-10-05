(() => {
  "use strict";

  const excludedAncestorSelector = [
    ".receipt",
    ".document-receipt",
    ".success-mini-receipt",
    ".sale-success-dialog",
    ".payment-dialog-stage",
    ".kofad-dialog",
    ".debt-payment-dialog",
    "[data-mobile-table='scroll']",
  ].join(",");

  const cleanHeader = value => String(value || "").replace(/\s+/g, " ").trim();

  const prepareTable = table => {
    if (!table || table.dataset.mobilePrepared === "true") return;
    if (table.closest(excludedAncestorSelector)) return;
    // Dense editable/approval sheets need their column geometry because users
    // enter values across a row. Keep those horizontally scrollable instead of
    // turning them into cards.
    if (table.querySelector("input, select, textarea, button")) return;

    const headers = [...table.querySelectorAll("thead th")].map(cell =>
      cleanHeader(cell.textContent)
    );
    if (!headers.length) return;

    table.dataset.mobilePrepared = "true";
    table.classList.add("mobile-card-table");

    table.querySelectorAll("tbody tr").forEach(row => {
      const cells = [...row.children].filter(cell => cell.tagName === "TD");
      let headerIndex = 0;

      cells.forEach(cell => {
        const colspan = Math.max(1, Number(cell.getAttribute("colspan") || 1));
        if (colspan > 1) {
          cell.dataset.mobileLabel = "";
          cell.classList.add("mobile-table-message");
          headerIndex += colspan;
          return;
        }

        const label = cleanHeader(headers[headerIndex] || "");
        cell.dataset.mobileLabel = label;
        if (!label) cell.classList.add("mobile-table-actions");
        headerIndex += 1;
      });
    });
  };

  const prepareAll = root => {
    const scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll(".table-wrap > table").forEach(prepareTable);
  };

  const start = () => {
    prepareAll(document);

    const observer = new MutationObserver(records => {
      records.forEach(record => {
        record.addedNodes.forEach(node => {
          if (!(node instanceof Element)) return;
          if (node.matches(".table-wrap > table")) prepareTable(node);
          prepareAll(node);
        });
      });
    });
    observer.observe(document.body, {childList:true, subtree:true});
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, {once:true});
  } else {
    start();
  }
})();
