document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector("[data-kfd-rotator]");
  if (!root) return;

  const img = root.querySelector("[data-hero-image]");
  const count = root.querySelector("[data-hero-count]");
  const progress = root.querySelector("[data-hero-progress]");
  const caption = root.querySelector("[data-photo-caption]");
  const local = img.dataset.fallback || "/static/marketplace/kofad-home-hero-sharp.webp";
  const photos = [local,
    "https://images.unsplash.com/photo-1542838132-92c53300491e?auto=format&fit=crop&w=1300&q=80",
    "https://images.unsplash.com/photo-1555529669-e69e7aa0ba9a?auto=format&fit=crop&w=1300&q=80",
    "https://images.unsplash.com/photo-1586528116311-ad8dd3c8310d?auto=format&fit=crop&w=1300&q=80",
    "https://images.unsplash.com/photo-1553413077-190dd305871c?auto=format&fit=crop&w=1300&q=80",
    "https://images.unsplash.com/photo-1534723452862-4c874018d66d?auto=format&fit=crop&w=1300&q=80"
  ];
  const captions = [
    "Retail and wholesale shopping imagery",
    "Fresh groceries and everyday supplies",
    "Household shopping and daily essentials",
    "Warehousing and distribution imagery",
    "Wholesale stock and business supply",
    "Retail and supply chain imagery"
  ];
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const bad = new Set();
  let current = 0;
  let timer = null;
  let loading = false;
  let hovered = false;
  let keyboardFocus = false;

  function updateLabel() {
    if (count) count.textContent = String(current + 1).padStart(2, "0") + " / " + String(photos.length).padStart(2, "0");
    if (progress) progress.style.width = (100 * (current + 1) / photos.length) + "%";
    if (caption) caption.textContent = captions[current];
    img.alt = captions[current] + " (illustrative)";
  }

  function stop() {
    if (timer !== null) window.clearInterval(timer);
    timer = null;
  }

  function run() {
    stop();
    // Automatic by default; no visible play/pause control. Pause only when
    // reading with a mouse, navigating with a keyboard, off-tab, or when
    // the visitor has requested reduced motion at the OS/browser level.
    if (!reducedMotion.matches && !document.hidden && !hovered && !keyboardFocus) {
      timer = window.setInterval(() => show(current + 1), 8500);
    }
  }

  function show(index, remaining = photos.length) {
    if (loading || remaining <= 0) return;
    const next = (index + photos.length) % photos.length;
    if (bad.has(next)) {
      show(next + 1, remaining - 1);
      return;
    }
    if (next === 0) {
      img.src = local;
      current = 0;
      updateLabel();
      return;
    }
    loading = true;
    const preload = new Image();
    preload.onload = () => {
      img.src = preload.src;
      current = next;
      loading = false;
      updateLabel();
    };
    preload.onerror = () => {
      bad.add(next);
      loading = false;
      show(next + 1, remaining - 1);
    };
    preload.src = photos[next];
  }

  // Broken remote category imagery never replaces the sharp bundled fallback.
  document.querySelectorAll(".kfd-category img[data-remote-photo]").forEach(node => {
    const preload = new Image();
    preload.onload = () => { node.src = preload.src; };
    preload.onerror = () => {};
    preload.src = node.dataset.remotePhoto;
    node.addEventListener("error", () => { node.src = local; }, { once: true });
  });

  img.addEventListener("error", () => {
    if (img.src !== new URL(local, location.href).href) img.src = local;
    current = 0;
    updateLabel();
  });

  root.querySelector("[data-slide-prev]")?.addEventListener("click", () => {
    show(current - 1);
    run();
  });
  root.querySelector("[data-slide-next]")?.addEventListener("click", () => {
    show(current + 1);
    run();
  });

  root.addEventListener("pointerenter", event => {
    if (event.pointerType === "mouse") { hovered = true; stop(); }
  });
  root.addEventListener("pointerleave", event => {
    if (event.pointerType === "mouse") { hovered = false; run(); }
  });
  root.addEventListener("focusin", event => {
    if (event.target.matches(":focus-visible")) {
      keyboardFocus = true;
      stop();
    }
  });
  root.addEventListener("focusout", () => {
    queueMicrotask(() => {
      keyboardFocus = root.contains(document.activeElement) && document.activeElement.matches(":focus-visible");
      run();
    });
  });
  document.addEventListener("visibilitychange", run);
  reducedMotion.addEventListener?.("change", run);

  updateLabel();
  run();
});
