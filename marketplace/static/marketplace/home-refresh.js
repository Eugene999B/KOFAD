/* Real, credited Pexels photography; not company-facility photographs. */
document.addEventListener("DOMContentLoaded", () => {
  const hero = document.querySelector("[data-kfd-rotator]");
  if (!hero) return;
  const image = hero.querySelector("[data-hero-image]");
  const count = hero.querySelector("[data-hero-count]");
  const bar = hero.querySelector("[data-hero-progress]");
  const caption = hero.querySelector("[data-photo-caption]");
  const photos = [4483608,4481326,11835352,19138180,1366594,4481327,4483773,18139921,5951182,30625284,4481328,16211537,31112251,10907746,7018662,24862481,5380919,5490225,27088193,19330769,3985055,12519455,11040957,31112244,32856481].map(id => ({
    url: "https://images.pexels.com/photos/" + id + "/pexels-photo-" + id + ".jpeg?auto=compress&cs=tinysrgb&w=1600&h=900&fit=crop",
    attribution: "Real retail and logistics photography · Pexels"
  }));
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let current = 0, timer = null, loading = false, paused = reduceMotion;
  function show(n) {
    if (loading) return;
    const next = (n + photos.length) % photos.length;
    if (next === current && image.src) return;
    loading = true;
    const preload = new Image();
    preload.onload = () => {
      image.classList.add("is-switching");
      // Immediate assignment remains readable for reduced motion and slow devices.
      image.src = preload.src;
      image.alt = "Illustrative photograph of shopping, inventory or logistics";
      current = next;
      if (count) count.textContent = String(current + 1).padStart(2, "0") + " / 25";
      if (bar) bar.style.width = (((current + 1) / photos.length) * 100).toFixed(2) + "%";
      if (caption) caption.textContent = photos[next].attribution;
      requestAnimationFrame(() => image.classList.remove("is-switching"));
      loading = false;
    };
    preload.onerror = () => { loading = false; if (next !== 0) show(next + 1); };
    preload.src = photos[next].url;
  }
  function stop() { if (timer) clearInterval(timer); timer = null; }
  function start() { stop(); if (!paused && !document.hidden) timer = setInterval(() => show(current + 1), 8500); }
  hero.querySelector("[data-slide-prev]")?.addEventListener("click", () => { show(current - 1); start(); });
  hero.querySelector("[data-slide-next]")?.addEventListener("click", () => { show(current + 1); start(); });
  const pause = hero.querySelector("[data-slide-pause]");
  pause?.addEventListener("click", () => {
    paused = !paused;
    pause.textContent = paused ? "▶" : "Ⅱ";
    pause.setAttribute("aria-label", paused ? "Resume photo slideshow" : "Pause photo slideshow");
    start();
  });
  hero.addEventListener("pointerenter", stop);
  hero.addEventListener("pointerleave", start);
  hero.addEventListener("focusin", stop);
  hero.addEventListener("focusout", start);
  document.addEventListener("visibilitychange", start);
  if (image) {
    const expected = photos[0].url;
    image.addEventListener("error", () => show(current + 1), {once: true});
    if (image.src !== expected && image.src === "") image.src = expected;
  }
  if (count) count.textContent = "01 / 25";
  if (bar) bar.style.width = "4%";
  start();
});
