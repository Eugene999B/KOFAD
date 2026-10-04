document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-product-gallery]").forEach(gallery => {
    const main = gallery.querySelector("[data-gallery-main] img");
    if (!main) return;
    gallery.querySelectorAll("[data-gallery-src]").forEach(button => {
      button.addEventListener("click", () => {
        const src = button.dataset.gallerySrc;
        if (!src) return;
        gallery.querySelectorAll("[data-gallery-src]").forEach(item => item.classList.remove("active"));
        button.classList.add("active");
        main.style.opacity = "0.25";
        const preload = new Image();
        preload.onload = () => {
          main.src = src;
          main.alt = button.dataset.galleryAlt || main.alt;
          main.style.opacity = "1";
        };
        preload.onerror = () => { main.style.opacity = "1"; };
        preload.src = src;
      });
    });
  });

  document.querySelectorAll("[data-market-search]").forEach(form => {
    const input = form.querySelector("[data-market-search-input]");
    const panel = form.querySelector("[data-market-suggestions]");
    if (!input || !panel) return;
    let timer = null;
    let controller = null;

    const hide = () => {
      panel.hidden = true;
      panel.replaceChildren();
    };
    const render = rows => {
      panel.replaceChildren();
      if (!rows.length) {
        hide();
        return;
      }
      for (const row of rows) {
        const link = document.createElement("a");
        link.href = row.url;
        if (row.image) {
          const img = document.createElement("img");
          img.src = row.image;
          img.alt = "";
          img.referrerPolicy = "no-referrer";
          link.appendChild(img);
        } else {
          const placeholder = document.createElement("span");
          placeholder.className = "market-suggestion-image";
          link.appendChild(placeholder);
        }
        const copy = document.createElement("div");
        const name = document.createElement("strong");
        name.textContent = row.name;
        const meta = document.createElement("small");
        meta.textContent = row.category + " · " + row.sku;
        copy.append(name, meta);
        const price = document.createElement("b");
        price.textContent = "GHS " + Number(row.price || 0).toFixed(2);
        link.append(copy, price);
        panel.appendChild(link);
      }
      panel.hidden = false;
    };
    const search = () => {
      const query = input.value.trim();
      if (query.length < 2) {
        hide();
        return;
      }
      controller?.abort();
      controller = new AbortController();
      fetch("/market/search/suggestions/?q=" + encodeURIComponent(query), {
        headers: {"Accept": "application/json"},
        credentials: "same-origin",
        signal: controller.signal,
      }).then(response => response.ok ? response.json() : {results: []})
        .then(payload => render(payload.results || []))
        .catch(error => {
          if (error.name !== "AbortError") hide();
        });
    };
    input.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(search, 180);
    });
    input.addEventListener("focus", () => {
      if (input.value.trim().length >= 2) search();
    });
    document.addEventListener("click", event => {
      if (!form.contains(event.target)) hide();
    });
    input.addEventListener("keydown", event => {
      if (event.key === "Escape") hide();
    });
  });

  document.querySelectorAll(".product-save-form").forEach(form => {
    form.addEventListener("submit", async event => {
      if (!window.fetch) return;
      event.preventDefault();
      const button = form.querySelector("button");
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          headers: {"Accept": "application/json"},
        });
        if (!response.ok) {
          form.submit();
          return;
        }
        const payload = await response.json();
        button.textContent = payload.saved ? "♥" : "♡";
        button.setAttribute("aria-label", payload.saved ? "Remove from wishlist" : "Save product");
        button.title = button.getAttribute("aria-label");
        document.querySelectorAll(".wishlist-nav-link b").forEach(badge => {
          badge.textContent = payload.count;
          badge.hidden = payload.count < 1;
        });
      } catch (_) {
        form.submit();
      }
    });
  });
});
