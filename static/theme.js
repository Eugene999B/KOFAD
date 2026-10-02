(() => {
  try {
    const saved = localStorage.getItem("kofad-theme") || "system";
    const resolved = saved === "system"
      ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : saved;
    document.documentElement.dataset.theme = resolved;
    document.documentElement.dataset.themePreference = saved;
  } catch (_) {}
})();