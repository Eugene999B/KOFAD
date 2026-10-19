"use strict";
(function () {
  const root = document.querySelector("[data-staff-ai-endpoint]");
  const form = document.getElementById("staff-ai-form");
  if (!root || !form) return;
  const log = document.getElementById("staff-ai-log");
  const field = document.getElementById("staff-ai-question");
  const submit = document.getElementById("staff-ai-submit");
  const status = document.getElementById("staff-ai-status");
  const token = form.querySelector("input[name=csrfmiddlewaretoken]");
  let busy = false;
  let lastTopic = "";

  function bubble(speaker, message) {
    const node = document.createElement("div");
    node.className = "staff-ai-message " + (speaker === "You" ? "is-user" : "is-assistant");
    const label = document.createElement("strong");
    label.textContent = speaker;
    const body = document.createElement("p");
    body.textContent = message;
    node.append(label, body);
    log.appendChild(node);
    log.scrollTop = log.scrollHeight;
  }
  async function ask(question) {
    const value = question.trim();
    if (busy || value.length < 2 || value.length > 600) return;
    busy = true;
    submit.disabled = true;
    bubble("You", value);
    field.value = "";
    status.textContent = "Checking your authorized KOFAD records…";
    try {
      const response = await fetch(root.dataset.staffAiEndpoint, {
        method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CSRFToken": token ? token.value : ""},
        body: JSON.stringify({message: value, context: lastTopic})
      });
      const data = await response.json();
      bubble("KOFAD Staff Assistant", data.reply || data.error || "That question couldn't be completed.");
      status.textContent = "Results stay within your staff access. Please verify critical business decisions.";
    } catch (_) {
      bubble("KOFAD Staff Assistant", "I couldn't connect right now. Please try again or open your normal staff workspace.");
      status.textContent = "Connection unavailable. No action was taken.";
    } finally {
      busy = false;
      submit.disabled = false;
      field.focus();
    }
  }
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    ask(field.value);
  });
  document.querySelectorAll("[data-ai-prompt]").forEach(function (button) {
    button.addEventListener("click", function () { ask(button.dataset.aiPrompt || ""); });
  });
})();
