document.addEventListener("DOMContentLoaded", () => {
  const menu = document.querySelector(".shop-account-menu");
  if (!menu) return;

  const close = () => {
    if (menu.open) menu.removeAttribute("open");
  };

  document.addEventListener("click", event => {
    if (menu.open && !menu.contains(event.target)) close();
  });

  document.addEventListener("keydown", event => {
    if (event.key === "Escape") close();
  });

  menu.querySelectorAll("a").forEach(link => link.addEventListener("click", close));
});
