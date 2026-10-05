document.addEventListener("DOMContentLoaded", () => {
  const mediaDark = matchMedia("(prefers-color-scheme: dark)");
  function themePreference() {
    try { return localStorage.getItem("kofad-theme") || "light"; } catch (_) { return "light"; }
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
    document.querySelectorAll("[data-theme-toggle]").forEach(button => {
      const dark = resolved === "dark";
      const label = button.querySelector("[data-theme-label]");
      const icon = button.querySelector("[data-theme-icon]");
      if (label) label.textContent = dark ? "Light mode" : "Dark mode";
      if (icon) icon.textContent = dark ? "☀" : "☾";
      button.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
      button.setAttribute("title", dark ? "Switch to light mode" : "Switch to dark mode");
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
  const loginPassword = document.querySelector("[data-login-password]");
  if (loginPassword && document.body.classList.contains("login-page")) {
    let passwordEntryActive = false;
    let passwordScrubber = null;
    const guardPasswordUntilInteraction = () => {
      passwordEntryActive = false;
      loginPassword.value = "";
      loginPassword.setAttribute("autocomplete", "new-password");
      loginPassword.dataset.autofillState = "guarded";
      if (passwordScrubber) clearInterval(passwordScrubber);
      let checks = 0;
      passwordScrubber = window.setInterval(() => {
        checks += 1;
        if (passwordEntryActive || document.activeElement === loginPassword || checks > 20) {
          clearInterval(passwordScrubber);
          passwordScrubber = null;
          return;
        }
        if (loginPassword.value) loginPassword.value = "";
      }, 100);
    };
    const allowPasswordEntry = () => {
      if (passwordEntryActive) return;
      passwordEntryActive = true;
      loginPassword.setAttribute("autocomplete", "current-password");
      loginPassword.dataset.autofillState = "interactive";
      if (passwordScrubber) {
        clearInterval(passwordScrubber);
        passwordScrubber = null;
      }
    };
    loginPassword.addEventListener("pointerdown", allowPasswordEntry);
    loginPassword.addEventListener("focus", allowPasswordEntry);
    guardPasswordUntilInteraction();
    window.addEventListener("pageshow", guardPasswordUntilInteraction);
  }
  const loginForm = document.querySelector(".login-card form");
  if (loginForm) {
    let loginSubmitting = false;
    const loginSubmitButton = loginForm.querySelector('button[type="submit"],input[type="submit"],button:not([type])');
    loginForm.addEventListener("submit", event => {
      if (loginSubmitting) {
        event.preventDefault();
        return;
      }
      loginSubmitting = true;
      loginForm.setAttribute("aria-busy", "true");
      if (loginSubmitButton) loginSubmitButton.disabled = true;
      try { sessionStorage.setItem("kofad-welcome-after-login", "1"); } catch (_) {}
    });
    window.addEventListener("pageshow", event => {
      if (!event.persisted) return;
      loginSubmitting = false;
      loginForm.removeAttribute("aria-busy");
      if (loginSubmitButton) loginSubmitButton.disabled = false;
    });
  }
  if (!document.body.classList.contains("login-page")) {
    try {
      if (sessionStorage.getItem("kofad-welcome-after-login") === "1") {
        sessionStorage.removeItem("kofad-welcome-after-login");
        const overlay = document.querySelector("#login-welcome-overlay");
        if (overlay) {
          overlay.classList.add("show");
          overlay.setAttribute("aria-hidden", "false");
          const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
          window.setTimeout(() => {
            overlay.classList.add("leaving");
            window.setTimeout(() => {
              overlay.classList.remove("show", "leaving");
              overlay.setAttribute("aria-hidden", "true");
            }, reduced ? 0 : 420);
          }, reduced ? 650 : 1850);
        }
        setTimeout(welcomeChime, 180);
      }
    } catch (_) {}
  }
  document.querySelectorAll('form[action="/logout/"]').forEach(form => form.addEventListener("submit",() => {
    try { Object.keys(sessionStorage).filter(key => key.startsWith("kofad-cart:")).forEach(key => sessionStorage.removeItem(key)); } catch (_) {}
  }));
  const toggle = document.querySelector("#menu-toggle"), sidebar = document.querySelector(".sidebar");
  const backdrop = document.querySelector(".nav-backdrop"), body = document.querySelector(".body"), dock = document.querySelector(".mobile-dock");

  // Preserve navigation position on desktop and mobile. Mobile focus previously
  // pulled the long sidebar back to the top after its saved scroll had restored.
  const sidebarScrollKey = "kofad-sidebar-scroll-v1";
  let restoreSidebarScroll = () => {};
  if (sidebar) {
    const readSidebarScroll = () => {
      try { return Math.max(0, Number(sessionStorage.getItem(sidebarScrollKey) || 0)); }
      catch (_) { return 0; }
    };
    const rememberSidebarScroll = () => {
      try { sessionStorage.setItem(sidebarScrollKey, String(Math.round(sidebar.scrollTop))); } catch (_) {}
    };
    restoreSidebarScroll = () => {
      const saved = readSidebarScroll();
      const apply = () => {
        const maximum = Math.max(0, sidebar.scrollHeight - sidebar.clientHeight);
        sidebar.scrollTop = Math.min(saved, maximum);
      };
      requestAnimationFrame(() => requestAnimationFrame(apply));
      window.setTimeout(apply, 90);
    };
    restoreSidebarScroll();
    let sidebarScrollFrame = null;
    sidebar.addEventListener("scroll", () => {
      if (sidebarScrollFrame) return;
      sidebarScrollFrame = requestAnimationFrame(() => {
        sidebarScrollFrame = null;
        rememberSidebarScroll();
      });
    }, {passive:true});
    sidebar.querySelectorAll("a").forEach(link => {
      link.addEventListener("pointerdown", rememberSidebarScroll, {passive:true});
      link.addEventListener("click", rememberSidebarScroll);
    });
    window.addEventListener("pageshow", restoreSidebarScroll);
    window.addEventListener("pagehide", rememberSidebarScroll);
  }
  const mobile = matchMedia("(max-width:950px)");
  function menu(open, restore = true) {
    document.body.classList.toggle("nav-open", open);
    if (!toggle) return;
    toggle.setAttribute("aria-expanded", String(open)); backdrop.hidden = !open;
    body.inert = open; if (dock) dock.inert = open;
    if (open) {
      sidebar.querySelector(".nav-close")?.focus({preventScroll:true});
      restoreSidebarScroll();
    } else if (restore) toggle.focus();
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
  const printClasses = ["print-a4", "print-thermal80", "print-thermal58"];
  function resetPrintMode() {
    printClasses.forEach(name => document.body.classList.remove(name));
  }
  document.querySelectorAll("[data-print-mode]").forEach(button => button.addEventListener("click", () => {
    resetPrintMode();
    const mode = button.dataset.printMode || "a4";
    document.body.classList.add(mode === "thermal80" ? "print-thermal80" : mode === "thermal58" ? "print-thermal58" : "print-a4");
    requestAnimationFrame(() => requestAnimationFrame(() => window.print()));
  }));
  window.addEventListener("afterprint", resetPrintMode);
  document.querySelectorAll("[data-print]").forEach(button => button.addEventListener("click", () => {
    resetPrintMode(); document.body.classList.add("print-a4"); window.print();
  }));
  document.querySelectorAll("[data-thermal]").forEach(button => button.addEventListener("click", () => {
    resetPrintMode(); document.body.classList.add("print-thermal80"); window.print();
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