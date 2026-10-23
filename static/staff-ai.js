/* KOFAD staff assistant: chat is ephemeral in the browser, with server-scoped tools. */
"use strict";
(function () {
  const root = document.querySelector("[data-staff-ai-endpoint]");
  const form = document.getElementById("staff-ai-form");
  if (!root || !form) return;
  const log = document.getElementById("staff-ai-log");
  const field = document.getElementById("staff-ai-question");
  const submit = document.getElementById("staff-ai-submit");
  const status = document.getElementById("staff-ai-status");
  const reset = document.getElementById("staff-ai-reset");
  const token = form.querySelector("input[name=csrfmiddlewaretoken]");
  let busy = false;
  let lastTopic = "";
  let inFlight = null;

  function scrollEnd() { log.scrollTop = log.scrollHeight; }
  function resizeField() {
    field.style.height = "27px";
    field.style.height = Math.min(field.scrollHeight, 145) + "px";
  }
  function approvedAction(action) {
    if (!action || typeof action !== "object") return false;
    if (typeof action.label !== "string" || action.label.length > 60) return false;
    if (typeof action.href !== "string" || !/^\/[a-z0-9/?=&_-]+$/i.test(action.href)) return false;
    return ["/workspace/","/reports/","/inventory/","/online-orders/","/debts/",
      "/communications/","/email/","/closings/","/approvals/","/parties/",
      "/account/","/sales/new/","/settings/connections/","/documents/?kind=sale"].includes(action.href);
  }
  function bubble(speaker, message, actions) {
    const node = document.createElement("article");
    const user = speaker === "You";
    node.className = "staff-ai-message " + (user ? "is-user" : "is-assistant");
    if (!user) {
      const label = document.createElement("div");
      label.className = "staff-ai-speaker";
      const mark = document.createElement("span");
      mark.className = "staff-ai-speaker-dot";
      mark.textContent = "✦";
      const name = document.createElement("span");
      name.textContent = "KOFAD Assistant";
      label.append(mark, name);
      node.append(label);
    }
    const body = document.createElement("p");
    body.textContent = message;
    node.append(body);
    if (!user && message) {
      const toolbar = document.createElement("div");
      toolbar.className = "staff-ai-message-actions";
      (Array.isArray(actions) ? actions : []).filter(approvedAction).slice(0, 3).forEach(function (action) {
        const link = document.createElement("a");
        link.href = action.href;
        link.textContent = action.label + " ↗";
        toolbar.append(link);
      });
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "staff-ai-copy";
      copy.textContent = "Copy";
      copy.setAttribute("aria-label", "Copy assistant response");
      copy.addEventListener("click", async function () {
        try {
          await navigator.clipboard.writeText(message);
          copy.textContent = "Copied";
        } catch (_err) { copy.textContent = "Copy unavailable"; }
      });
      toolbar.append(copy);
      node.append(toolbar);
    }
    log.appendChild(node);
    scrollEnd();
    return node;
  }
  function setWorking(isWorking) {
    busy = isWorking;
    submit.disabled = isWorking;
    submit.setAttribute("aria-busy", String(isWorking));
  }
  async function ask(question) {
    const value = (question || "").trim();
    if (busy || value.length < 2 || value.length > 600) return;
    setWorking(true);
    bubble("You", value);
    field.value = "";
    resizeField();
    status.textContent = "Checking your permitted records…";
    const progress = bubble("KOFAD Assistant", "");
    progress.classList.add("is-thinking");
    const progressText = document.createElement("p");
    progressText.textContent = "Looking into that…";
    progress.append(progressText);
    scrollEnd();
    inFlight = new AbortController();
    try {
      const response = await fetch(root.dataset.staffAiEndpoint, {
        method: "POST", credentials: "same-origin", signal: inFlight.signal,
        headers: {"Content-Type": "application/json", "X-CSRFToken": token ? token.value : ""},
        body: JSON.stringify({message: value, context: lastTopic})
      });
      const data = await response.json();
      progress.remove();
      if (response.ok && typeof data.source === "string") lastTopic = data.source;
      bubble("KOFAD Assistant", data.reply || data.error || "That request couldn't be completed.",
        response.ok ? data.actions : []);
      status.textContent = response.ok
        ? "Permission-checked KOFAD results · Verify important decisions"
        : "Request not completed. Nothing was changed.";
    } catch (error) {
      progress.remove();
      if (error.name !== "AbortError") {
        bubble("KOFAD Assistant", "Connection unavailable. Please try again. No action was taken.");
        status.textContent = "Connection unavailable.";
      }
    } finally {
      inFlight = null;
      setWorking(false);
      field.focus({preventScroll:true});
    }
  }
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    ask(field.value);
  });
  field.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      ask(field.value);
    }
  });
  field.addEventListener("input", resizeField);
  reset.addEventListener("click", function () {
    if (inFlight) inFlight.abort();
    log.querySelectorAll(".staff-ai-message").forEach(function (node) { node.remove(); });
    lastTopic = "";
    field.value = "";
    resizeField();
    status.textContent = "Answers use your permitted KOFAD records.";
    field.focus({preventScroll:true});
  });
})();
