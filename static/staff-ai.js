/* KOFAD staff assistant: private, session-only chat. No browser-side AI or HTML injection. */
"use strict";
(function () {
  const root = document.querySelector("[data-staff-ai-endpoint]");
  const form = document.getElementById("staff-ai-form");
  if (!root || !form) return;
  const log = document.getElementById("staff-ai-log");
  const landing = document.getElementById("staff-ai-landing");
  const field = document.getElementById("staff-ai-question");
  const submit = document.getElementById("staff-ai-submit");
  const status = document.getElementById("staff-ai-status");
  const reset = document.getElementById("staff-ai-reset");
  const token = form.querySelector("input[name=csrfmiddlewaretoken]");
  const validSources = new Set([
    "sales_activity", "sales_trend", "reports", "compare", "momo", "debt_activity",
    "receivables", "sms", "email", "inventory", "orders", "diagnostics", "tasks",
    "branches", "role", "insights", "operations"
  ]);
  const ignoredSources = new Set(["clarify", "conversation", "invalid", "help", "denied", "security"]);
  const approvedLinks = new Set([
    "/workspace/", "/reports/", "/inventory/", "/online-orders/", "/debts/",
    "/communications/", "/email/", "/closings/", "/approvals/", "/parties/",
    "/account/", "/sales/new/", "/settings/connections/", "/documents/?kind=sale"
  ]);
  let busy = false;
  let lastTopic = "";
  let recentTopics = [];
  let turnCount = 0;
  let inFlight = null;

  function scrollEnd() { log.scrollTop = log.scrollHeight; }
  function resizeField() {
    field.style.height = "28px";
    field.style.height = Math.min(field.scrollHeight, 145) + "px";
  }
  function showLanding() {
    if (landing) landing.hidden = !!log.querySelector(".staff-ai-message");
  }
  function approvedAction(action) {
    return !!action && typeof action.label === "string" &&
      action.label.length <= 60 && approvedLinks.has(action.href);
  }
  function textNode(tag, cls, value) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    node.textContent = value;
    return node;
  }
  function buildSummary(text, source) {
    const lines = text.split(/\n+/).map(s => s.trim()).filter(Boolean);
    if (text.length < 440 && lines.length <= 5) return null;
    // Use only original evidence, verbatim; never fabricate business figures.
    const important = source === "sms"
      ? lines.filter(s => /remaining .*sms credit|recorded delivery|messages drafted|sms activity/i.test(s))
      : source === "email"
        ? lines.filter(s => /received emails|incoming emails|email centre|outgoing letters|delivery confirmed/i.test(s))
        : source === "sales_activity"
          ? lines.filter(s => /finalized sale|gross recorded sales|net recorded sales|recorded sales/i.test(s))
          : lines;
    const limit = source === "sales_activity" ? 4 : 3;
    const selected = (important.length ? important : lines).slice(0, limit);
    return {headline: selected.join("\n"), full: text};
  }
  function assistantBubble(message, actions, source) {
    const node = document.createElement("article");
    node.className = "staff-ai-message is-assistant";
    node.appendChild(textNode("span", "staff-ai-speaker-dot", "✳"));
    const content = document.createElement("div");
    content.className = "staff-ai-content";
    const summary = buildSummary(message, source);
    content.appendChild(textNode("p", "staff-ai-reply", summary ? summary.headline : message));
    if (summary) {
      const detail = document.createElement("details");
      detail.className = "staff-ai-evidence";
      detail.appendChild(textNode("summary", "", "See full report"));
      detail.appendChild(textNode("pre", "", summary.full));
      content.appendChild(detail);
    }
    const allowed = (Array.isArray(actions) ? actions : []).filter(approvedAction).slice(0, 2);
    if (allowed.length || message.length > 650) {
      const toolbar = document.createElement("div");
      toolbar.className = "staff-ai-message-actions";
      allowed.forEach(function (action) {
        const link = document.createElement("a");
        link.href = action.href;
        link.textContent = action.label + " ↗";
        toolbar.append(link);
      });
      if (message.length > 650) {
        const copy = textNode("button", "staff-ai-copy", "Copy report");
        copy.type = "button";
        copy.addEventListener("click", async function () {
          try {
            await navigator.clipboard.writeText(message);
            copy.textContent = "Copied";
          } catch (_error) {
            copy.textContent = "Copy unavailable";
          }
        });
        toolbar.append(copy);
      }
      content.appendChild(toolbar);
    }
    node.appendChild(content);
    log.appendChild(node);
    showLanding();
    scrollEnd();
    return node;
  }
  function userBubble(message) {
    const node = textNode("article", "staff-ai-message is-user", message);
    log.appendChild(node);
    showLanding();
    scrollEnd();
  }
  function setBusy(working) {
    busy = working;
    submit.disabled = working;
    submit.setAttribute("aria-busy", String(working));
  }
  function remember(source) {
    if (typeof source !== "string" || ignoredSources.has(source) || !validSources.has(source)) return;
    lastTopic = source;
    recentTopics.push(source);
    recentTopics = recentTopics.slice(-5);
  }
  async function ask(question) {
    const value = String(question || "").trim();
    if (busy || value.length < 2 || value.length > 600) return;
    setBusy(true);
    userBubble(value);
    turnCount = Math.min(turnCount + 1, 40);
    field.value = "";
    resizeField();
    status.textContent = "Checking...";
    const progress = assistantBubble("", [], "conversation");
    progress.classList.add("is-thinking");
    const content = progress.querySelector(".staff-ai-reply");
    if (content) content.textContent = "";
    inFlight = new AbortController();
    try {
      const response = await fetch(root.dataset.staffAiEndpoint, {
        method: "POST",
        credentials: "same-origin",
        signal: inFlight.signal,
        headers: {"Content-Type": "application/json", "X-CSRFToken": token ? token.value : ""},
        body: JSON.stringify({message: value, context: lastTopic, recent_topics: recentTopics, turn_count: Math.max(0, turnCount - 1)})
      });
      const data = await response.json();
      progress.remove();
      if (response.ok) remember(data.source);
      assistantBubble(data.reply || data.error || "I couldn't complete that request. Please try again.",
        response.ok ? data.actions : [], data.source || "");
      status.textContent = response.ok ? "Response ready" : "Request not completed";
    } catch (error) {
      progress.remove();
      if (error.name !== "AbortError") {
        assistantBubble("I can't reach KOFAD right now. Please try again.", [], "conversation");
        status.textContent = "Connection unavailable";
      }
    } finally {
      inFlight = null;
      setBusy(false);
      // On phones, do not pull the keyboard up after every answer.
      if (!window.matchMedia("(pointer: coarse)").matches) field.focus({preventScroll:true});
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
    log.querySelectorAll(".staff-ai-message").forEach(node => node.remove());
    lastTopic = "";
    recentTopics = [];
    turnCount = 0;
    field.value = "";
    resizeField();
    showLanding();
    status.textContent = "New conversation";
    if (!window.matchMedia("(pointer: coarse)").matches) field.focus({preventScroll:true});
  });
  showLanding();
})();
