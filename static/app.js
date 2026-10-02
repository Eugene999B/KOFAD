document.addEventListener("DOMContentLoaded", () => {
  const mediaDark = matchMedia("(prefers-color-scheme: dark)");
  function themePreference() {
    try { return localStorage.getItem("kofad-theme") || "system"; } catch (_) { return "system"; }
  }
  function applyTheme(preference) {
    const resolved = preference === "system" ? (mediaDark.matches ? "dark" : "light") : preference;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.themePreference = preference;
    document.querySelectorAll("[data-theme-choice]").forEach(button => {
      const active = button.dataset.themeChoice === preference;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }
  applyTheme(themePreference());
  mediaDark.addEventListener?.("change", () => {
    if (themePreference() === "system") applyTheme("system");
  });
  document.querySelectorAll("[data-theme-choice]").forEach(button => button.addEventListener("click", () => {
    try { localStorage.setItem("kofad-theme", button.dataset.themeChoice); } catch (_) {}
    applyTheme(button.dataset.themeChoice);
  }));
  document.querySelectorAll("[data-theme-toggle]").forEach(button => button.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("kofad-theme", next); } catch (_) {}
    applyTheme(next);
  }));

  const soundToggles = document.querySelectorAll("[data-welcome-sound]");
  let soundEnabled = true;
  try { soundEnabled = localStorage.getItem("kofad-welcome-sound") !== "off"; } catch (_) {}
  soundToggles.forEach(toggle => {
    toggle.checked = soundEnabled;
    toggle.addEventListener("change", () => {
      soundEnabled = toggle.checked;
      try { localStorage.setItem("kofad-welcome-sound", soundEnabled ? "on" : "off"); } catch (_) {}
    });
  });
  function welcomeChime() {
    if (!soundEnabled || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return;
      const ctx = new AudioContext();
      const gain = ctx.createGain();
      gain.gain.setValueAtTime(0.0001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.045, ctx.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.48);
      gain.connect(ctx.destination);
      [523.25, 659.25, 783.99].forEach((frequency, index) => {
        const oscillator = ctx.createOscillator();
        oscillator.type = "sine";
        oscillator.frequency.value = frequency;
        oscillator.connect(gain);
        oscillator.start(ctx.currentTime + index * 0.08);
        oscillator.stop(ctx.currentTime + 0.42 + index * 0.08);
      });
      setTimeout(() => ctx.close(), 750);
    } catch (_) {}
  }
  const loginForm = document.querySelector(".login-card form");
  loginForm?.addEventListener("submit", () => {
    try { sessionStorage.setItem("kofad-welcome-after-login", "1"); } catch (_) {}
  });
  if (!document.body.classList.contains("login-page")) {
    try {
      if (sessionStorage.getItem("kofad-welcome-after-login") === "1") {
        sessionStorage.removeItem("kofad-welcome-after-login");
        setTimeout(welcomeChime, 180);
      }
    } catch (_) {}
  }
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