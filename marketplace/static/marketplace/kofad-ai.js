/* KOFAD Bot v2. No chat text or private customer data is persisted in storage. */
(function () {
  "use strict";
  const root = document.getElementById("kofad-ai-widget");
  if (!root) return;
  const panel = root.querySelector("#kofad-ai-panel");
  const launcher = root.querySelector("[data-kofad-ai-launcher]");
  const close = root.querySelector("[data-kofad-ai-close]");
  const newChat = root.querySelector("[data-kofad-ai-new]");
  const input = root.querySelector("[data-kofad-ai-input]");
  const form = root.querySelector("[data-kofad-ai-form]");
  const feed = root.querySelector("[data-kofad-ai-feed]");
  const send = root.querySelector("[data-kofad-ai-send]");
  const consent = root.querySelector("[data-kofad-ai-consent]");
  const starters = root.querySelector("[data-kofad-ai-starters]");
  const positionKey = "kofad-bot-position-v2";
  const welcome = feed.innerHTML;
  let busy = false, controller = null, drag = null, suppressClick = false;
  let history = [];
  const reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function clamp(n, low, high) {
    return Math.min(Math.max(n, low), Math.max(low, high));
  }

  function moveLauncher(x, y) {
    root.style.left = clamp(x, 8, window.innerWidth - root.offsetWidth - 8) + "px";
    root.style.top = clamp(y, 8, window.innerHeight - root.offsetHeight - 8) + "px";
    root.style.right = "auto";
    root.style.bottom = "auto";
    if (!panel.hidden) positionPanel();
  }

  function positionPanel() {
    const launcherRect = launcher.getBoundingClientRect();
    const width = Math.min(390, window.innerWidth - 18);
    const height = Math.min(574, window.innerHeight - 35);
    const margin = 9;
    // Pin the conversation to the nearest available side of the small launcher.
    // Mobile prefers an almost-full-width compact panel above its launcher.
    const left = clamp(launcherRect.right - width, margin, window.innerWidth - width - margin);
    const above = launcherRect.top - height - 10;
    const below = launcherRect.bottom + 10;
    const top = above >= margin ? above :
      (below + height <= window.innerHeight - margin ? below :
        clamp(window.innerHeight - height - 10, margin, window.innerHeight - height - margin));
    panel.style.left = left + "px";
    panel.style.top = top + "px";
    panel.style.right = "auto";
    panel.style.bottom = "auto";
  }

  function savePosition() {
    const rect = root.getBoundingClientRect();
    try {
      window.localStorage.setItem(positionKey, JSON.stringify({
        x: rect.left / Math.max(1, window.innerWidth - rect.width),
        y: rect.top / Math.max(1, window.innerHeight - rect.height)
      }));
    } catch (_e) { /* browser storage is optional */ }
  }

  function restorePosition() {
    try {
      const saved = JSON.parse(window.localStorage.getItem(positionKey) || "null");
      if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
        moveLauncher(
          clamp(saved.x, 0, 1) * (window.innerWidth - root.offsetWidth),
          clamp(saved.y, 0, 1) * (window.innerHeight - root.offsetHeight)
        );
      }
    } catch (_e) { /* retain CSS default */ }
  }

  launcher.addEventListener("pointerdown", function (event) {
    if (event.button !== 0 || !event.isPrimary) return;
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY,
      rect: root.getBoundingClientRect(), moved: false };
    try { launcher.setPointerCapture(event.pointerId); } catch (_e) { }
  });
  launcher.addEventListener("pointermove", function (event) {
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 7) drag.moved = true;
    if (!drag.moved) return;
    root.classList.add("is-dragging");
    moveLauncher(drag.rect.left + dx, drag.rect.top + dy);
  });
  function finishDrag(event) {
    if (!drag || event.pointerId !== drag.id) return;
    if (drag.moved) {
      suppressClick = true;
      savePosition();
    }
    drag = null;
    root.classList.remove("is-dragging");
  }
  launcher.addEventListener("pointerup", finishDrag);
  launcher.addEventListener("pointercancel", finishDrag);

  function show(open) {
    panel.hidden = !open;
    launcher.setAttribute("aria-expanded", String(open));
    if (open) {
      positionPanel();
      input.focus({ preventScroll: true });
    } else {
      launcher.focus({ preventScroll: true });
    }
  }
  launcher.addEventListener("click", function () {
    if (suppressClick) { suppressClick = false; return; }
    show(panel.hidden);
  });
  close.addEventListener("click", function () { show(false); });
  root.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !panel.hidden) show(false);
  });
  window.addEventListener("resize", function () {
    if (root.style.left) {
      const r = root.getBoundingClientRect();
      moveLauncher(r.left, r.top);
    }
    if (!panel.hidden) positionPanel();
  });
  restorePosition();

  function safelyLink(target, message) {
    const safe = String(message || "");
    const regex = /(https:\/\/[^\s<>"']+)/g;
    let start = 0, match;
    while ((match = regex.exec(safe)) !== null) {
      target.appendChild(document.createTextNode(safe.slice(start, match.index)));
      const original = match[1];
      const trimmed = original.replace(/[),.;!?]+$/, "");
      let url = null;
      try { url = new URL(trimmed); } catch (_e) { }
      if (url && url.protocol === "https:" &&
          ["kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com"].includes(url.hostname)) {
        const link = document.createElement("a");
        link.href = url.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = trimmed;
        target.appendChild(link);
        target.appendChild(document.createTextNode(original.slice(trimmed.length)));
      } else {
        target.appendChild(document.createTextNode(original));
      }
      start = regex.lastIndex;
    }
    target.appendChild(document.createTextNode(safe.slice(start)));
  }

  function scrollEnd() { feed.scrollTop = feed.scrollHeight; }

  function appendMessage(message, type, source, mode) {
    const bubble = document.createElement("div");
    bubble.className = "kofad-ai-message " + (type === "user" ? "user" : "bot");
    if (type === "bot" && !reducedMotion && message.length > 90) {
      // A visual answer reveal, not a claim about the model's internal reasoning.
      const main = document.createElement("span");
      bubble.appendChild(main);
      feed.appendChild(bubble);
      const duration = Math.min(850, message.length * 4);
      const start = performance.now();
      function reveal(now) {
        if (!bubble.isConnected) return;
        const size = Math.max(1, Math.floor(message.length * Math.min(1, (now - start) / duration)));
        main.textContent = message.slice(0, size);
        scrollEnd();
        if (size < message.length) requestAnimationFrame(reveal);
        else { main.textContent = ""; safelyLink(main, message); }
      }
      requestAnimationFrame(reveal);
    } else {
      safelyLink(bubble, message);
      feed.appendChild(bubble);
    }
    if (source) {
      const meta = document.createElement("div");
      meta.className = "kofad-ai-response-meta";
      const label = document.createElement("span");
      label.textContent = source === "free-ai" ? "AI-assisted · verify important details" :
        source === "private" ? "From your KOFAD account" :
        mode === "consent_required" ? "Local answer · enable advanced AI below" :
        mode === "daily_limit" ? "Local answer · free AI daily limit reached" :
        mode === "provider_unavailable" ? "Local answer · advanced AI unavailable" :
        "KOFAD quick answer";
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "Copy";
      copy.setAttribute("aria-label", "Copy KOFAD Bot response");
      copy.addEventListener("click", async function () {
        try {
          await navigator.clipboard.writeText(message);
          copy.textContent = "Copied";
        } catch (_e) { copy.textContent = "Select to copy"; }
      });
      meta.appendChild(label);
      meta.appendChild(copy);
      bubble.appendChild(meta);
    }
    scrollEnd();
    while (feed.children.length > 28) feed.removeChild(feed.children[0]);
  }

  function showThinking() {
    const node = document.createElement("div");
    node.className = "kofad-ai-thinking";
    node.setAttribute("role", "status");
    const dots = document.createElement("span");
    dots.className = "kofad-ai-thinking-indicator";
    dots.setAttribute("aria-hidden", "true");
    for (let i = 0; i < 3; i++) dots.appendChild(document.createElement("span"));
    const label = document.createElement("span");
    label.textContent = "Finding an answer";
    node.appendChild(dots);
    node.appendChild(label);
    feed.appendChild(node);
    scrollEnd();
    return { node, label };
  }

  function resizeInput() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 114) + "px";
  }

  function resetChat() {
    if (controller) controller.abort();
    controller = null;
    busy = false;
    history = [];
    feed.innerHTML = welcome;
    starters.hidden = false;
    input.value = "";
    input.disabled = false;
    send.disabled = false;
    resizeInput();
    input.focus({ preventScroll: true });
  }
  newChat.addEventListener("click", resetChat);
  input.addEventListener("input", resizeInput);
  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  async function ask(q) {
    const question = String(q || "").trim().slice(0, 600);
    if (!question || busy) return;
    busy = true;
    const thisRequest = new AbortController();
    controller = thisRequest;
    input.value = "";
    resizeInput();
    input.disabled = true;
    send.disabled = true;
    starters.hidden = true;
    const welcomeBlock = feed.querySelector(".kofad-ai-welcome");
    if (welcomeBlock) welcomeBlock.remove();
    appendMessage(question, "user");
    const thinking = showThinking();
    const thinkingTimer = setTimeout(function () {
      if (thinking.node.isConnected) thinking.label.textContent = "Checking KOFAD sources…";
    }, 3500);
    try {
      const csrf = form.querySelector("[name=csrfmiddlewaretoken]");
      const response = await fetch(root.dataset.chatUrl, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        signal: thisRequest.signal,
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf ? csrf.value : "" },
        body: JSON.stringify({
          message: question,
          ai_consent: consent.checked === true,
          history: history.slice(-6)
        })
      });
      const data = await response.json();
      if (controller !== thisRequest) return;
      thinking.node.remove();
      const text = response.ok ? String(data.reply || "I couldn't find an answer.") :
        String(data.error || "KOFAD Bot couldn't answer. Please try again.");
      appendMessage(text, "bot", response.ok ? data.source : "local", data.ai_mode);
      if (response.ok && ["free-ai", "local"].includes(data.source)) {
        history.push({ role: "user", content: question.slice(0, 320) });
        history.push({ role: "assistant", content: text.slice(0, 440) });
        history = history.slice(-6);
      } else if (data.source === "private") {
        history = []; // Never retain private answers as an AI follow-up context.
      }
    } catch (error) {
      if (controller !== thisRequest) return;
      thinking.node.remove();
      if (error.name !== "AbortError") {
        appendMessage("I'm having trouble connecting right now. Please try again or contact KOFAD support.", "bot");
      }
    } finally {
      clearTimeout(thinkingTimer);
      if (controller === thisRequest) {
        controller = null;
        busy = false;
        input.disabled = false;
        send.disabled = false;
        if (!panel.hidden) input.focus({ preventScroll: true });
      }
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
