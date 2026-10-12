(function () {
  "use strict";
  const api = window.Telegram && window.Telegram.WebApp;
  if (api) {
    api.ready();
    api.expand();
  }
  const close = document.getElementById("close-miniapp");
  if (close && api) {
    close.addEventListener("click", function () { api.close(); });
  }
  const form = document.getElementById("kofad-tg-quick-form");
  if (!form) return;
  const data = document.getElementById("telegram-init-data");
  const status = document.getElementById("miniapp-status");
  form.addEventListener("submit", function (event) {
    const signed = api && api.initData;
    if (!signed || signed.length > 4096) {
      event.preventDefault();
      if (status) status.textContent =
        "Open this screen by tapping /login inside your KOFAD Telegram bot, not from a copied browser URL.";
      return;
    }
    data.value = signed;
  });
})();
