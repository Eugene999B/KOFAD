(function () {
  "use strict";
  const root = document.getElementById("kofad-ai-widget");
  if (!root) return;
  const panel = root.querySelector("#kofad-ai-panel");
  const launcher = root.querySelector("[data-kofad-ai-launcher]");
  const close = root.querySelector("[data-kofad-ai-close]");
  const input = root.querySelector("[data-kofad-ai-input]");
  const form = root.querySelector("[data-kofad-ai-form]");
  const feed = root.querySelector("[data-kofad-ai-feed]");
  const send = root.querySelector("[data-kofad-ai-send]");
  const consent = root.querySelector("[data-kofad-ai-consent]");
  let busy = false;

  function show(open) {
    panel.hidden = !open;
    launcher.setAttribute("aria-expanded", String(open));
    if (open) input.focus();
    else launcher.focus();
  }
  launcher.addEventListener("click", function () { show(panel.hidden); });
  close.addEventListener("click", function () { show(false); });
  root.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !panel.hidden) show(false);
  });

  function addMessage(message, type, source) {
    const div = document.createElement("div");
    div.className = "kofad-ai-message " + (type === "user" ? "user" : "bot");
    // Never render HTML sent by a model, product listing or user.
    // Render only canonical KOFAD/HTTPS links with isolated DOM nodes.
    const safe = String(message || "");
    const regex = /(https:\/\/[^\s<>"']+)/g;
    let start = 0, match;
    while ((match = regex.exec(safe)) !== null) {
      div.appendChild(document.createTextNode(safe.slice(start, match.index)));
      let text = match[1].replace(/[),.;!?]+$/, "");
      let url;
      try { url = new URL(text); } catch (e) { url = null; }
      if (url && url.protocol === "https:" &&
          (url.hostname === "kofadimpex.com" ||
           url.hostname === "market.kofadimpex.com" ||
           url.hostname === "staff.kofadimpex.com")) {
        const link = document.createElement("a");
        link.href = url.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = text;
        div.appendChild(link);
        div.appendChild(document.createTextNode(match[1].slice(text.length)));
      } else {
        div.appendChild(document.createTextNode(match[1]));
      }
      start = regex.lastIndex;
    }
    div.appendChild(document.createTextNode(safe.slice(start)));
    if (source === "free-ai") {
      const sub = document.createElement("div");
      sub.className = "kofad-ai-sub";
      sub.textContent = "Free AI response · verify current details at checkout";
      div.appendChild(sub);
    }
    feed.appendChild(div);
    while (feed.children.length > 30) feed.removeChild(feed.children[0]);
    feed.scrollTop = feed.scrollHeight;
    return div;
  }

  async function ask(q) {
    const question = String(q || "").trim().slice(0, 600);
    if (!question || busy) return;
    busy = true;
    input.value = "";
    input.disabled = true;
    send.disabled = true;
    addMessage(question, "user");
    const typing = addMessage("KOFAD Bot is checking your question…", "bot");
    try {
      const csrf = form.querySelector("[name=csrfmiddlewaretoken]");
      const response = await fetch(root.dataset.chatUrl, {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrf ? csrf.value : ""
        },
        body: JSON.stringify({
          message: question,
          ai_consent: consent.checked === true
        })
      });
      const data = await response.json();
      typing.remove();
      addMessage(response.ok ? data.reply : (data.error || "KOFAD Bot cannot respond right now."),
                 "bot", response.ok ? data.source : "local");
    } catch (_err) {
      typing.remove();
      addMessage("I couldn't reach KOFAD right now. Please retry or contact our team at /contact/.", "bot");
    } finally {
      busy = false;
      input.disabled = false;
      send.disabled = false;
      input.focus();
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    ask(input.value);
  });
  root.querySelectorAll("[data-kofad-ai-suggestion]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (panel.hidden) show(true);
      ask(button.dataset.kofadAiSuggestion);
    });
  });
})();
