(() => {
  try {
    const saved = (window.KofadPrivacy ? window.KofadPrivacy.getPreference("kofad-theme", "light") : (localStorage.getItem("kofad-theme") || "light"));
    const resolved = saved === "system"
      ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : saved;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.themePreference = saved;
  } catch (_) {
    document.documentElement.dataset.theme = "light";
    document.documentElement.dataset.themePreference = "light";
  }
})();
