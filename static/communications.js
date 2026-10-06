(() => {
  const form = document.querySelector("#comms-compose");
  if (!form) return;

  const channelInput = document.querySelector("#comms-channel");
  const targetInput = document.querySelector("#comms-target");
  const body = document.querySelector("#comms-body");
  const sendButton = document.querySelector("#comms-send-button");
  const charCount = document.querySelector("#comms-char-count");
  const sendHint = document.querySelector("#comms-send-hint");
  const recipientSummary = document.querySelector("#comms-recipient-summary");
  const customerSearch = document.querySelector("#comms-customer-search");
  const customerRows = [...document.querySelectorAll(".comms-customer-row")];
  const customerChecks = customerRows.map(row => row.querySelector('input[type="checkbox"]'));
  const selectedCount = document.querySelector("#comms-selected-count");
  const oneCustomer = document.querySelector("#comms-one-customer");
  const oneSearch = document.querySelector("#comms-one-search");
  const oneRows = [...document.querySelectorAll("[data-one-customer]")];
  const manualPhone = document.querySelector("#comms-manual-phone");

  function selectedVisibleChecks() {
    return customerRows
      .filter(row => !row.classList.contains("hidden"))
      .map(row => row.querySelector('input[type="checkbox"]'));
  }

  function currentSelectedCount() {
    return customerChecks.filter(input => input?.checked).length;
  }

  function updateRecipientSummary() {
    const target = targetInput.value;
    if (target === "one") {
      recipientSummary.textContent = oneCustomer?.value ? "1 customer" : "Choose a customer";
    } else if (target === "selected") {
      const count = currentSelectedCount();
      recipientSummary.textContent = count + " selected";
      if (selectedCount) selectedCount.textContent = String(count);
    } else if (target === "all") {
      const all = document.querySelector(".comms-all-summary strong")?.textContent?.trim() || "All customers";
      recipientSummary.textContent = all;
    } else {
      recipientSummary.textContent = manualPhone?.value.trim() ? "1 number" : "Enter a number";
    }
  }

  function setChannel(channel) {
    channelInput.value = channel;
    document.querySelectorAll("[data-comms-channel]").forEach(button => {
      const active = button.dataset.commsChannel === channel;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });

    if (channel === "sms") {
      body.maxLength = 480;
      sendButton.textContent = "Send SMS →";
      sendHint.textContent = "SMS is sent directly to Arkesel.";
    } else {
      body.maxLength = 1000;
      const cloud = form.dataset.whatsappCloud === "true";
      sendButton.textContent = cloud ? "Send WhatsApp →" : "Prepare WhatsApp →";
      sendHint.textContent = cloud ? "Sent through WhatsApp Business. An approved template is used outside an active customer conversation." : "KOFAD prepares the chat and opens WhatsApp with your message ready.";
    }
    updateCharCount();
  }

  function setTarget(target) {
    targetInput.value = target;
    document.querySelectorAll("[data-comms-target]").forEach(button => {
      const active = button.dataset.commsTarget === target;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    document.querySelectorAll("[data-comms-pane]").forEach(pane => {
      pane.classList.toggle("hidden", pane.dataset.commsPane !== target);
    });
    updateRecipientSummary();
  }

  function updateCharCount() {
    if (!body || !charCount) return;
    charCount.textContent = body.value.length + " / " + body.maxLength;
  }

  document.querySelectorAll("[data-comms-channel]").forEach(button => {
    button.addEventListener("click", () => setChannel(button.dataset.commsChannel));
  });
  document.querySelectorAll("[data-comms-target]").forEach(button => {
    button.addEventListener("click", () => setTarget(button.dataset.commsTarget));
  });

  document.querySelectorAll("[data-comms-template]").forEach(button => {
    button.addEventListener("click", () => {
      body.value = button.dataset.commsTemplate || "";
      body.focus();
      updateCharCount();
    });
  });

  oneSearch?.addEventListener("input", () => {
    const q = oneSearch.value.trim().toLowerCase();
    if (oneCustomer && oneCustomer.value && !q) oneCustomer.value = "";
    oneRows.forEach(row => {
      row.classList.toggle("hidden", Boolean(q) && !row.dataset.search.includes(q));
      row.classList.toggle("active", row.dataset.oneCustomer === oneCustomer?.value);
    });
    updateRecipientSummary();
  });

  oneRows.forEach(row => row.addEventListener("click", () => {
    if (oneCustomer) oneCustomer.value = row.dataset.oneCustomer || "";
    if (oneSearch) oneSearch.value = [row.dataset.name, row.dataset.phone].filter(Boolean).join(" · ");
    oneRows.forEach(item => item.classList.toggle("active", item === row));
    updateRecipientSummary();
  }));

  customerSearch?.addEventListener("input", () => {
    const q = customerSearch.value.trim().toLowerCase();
    customerRows.forEach(row => {
      row.classList.toggle("hidden", Boolean(q) && !row.dataset.search.includes(q));
    });
  });

  document.querySelector("#comms-select-filtered")?.addEventListener("click", () => {
    selectedVisibleChecks().forEach(input => { if (input) input.checked = true; });
    updateRecipientSummary();
  });

  document.querySelector("#comms-clear-selected")?.addEventListener("click", () => {
    customerChecks.forEach(input => { if (input) input.checked = false; });
    updateRecipientSummary();
  });

  customerChecks.forEach(input => input?.addEventListener("change", updateRecipientSummary));
  oneCustomer?.addEventListener("change", updateRecipientSummary);
  manualPhone?.addEventListener("input", updateRecipientSummary);
  body?.addEventListener("input", updateCharCount);

  document.querySelectorAll("[data-history-channel]").forEach(button => {
    button.addEventListener("click", () => {
      const channel = button.dataset.historyChannel;
      document.querySelectorAll("[data-history-channel]").forEach(item => {
        item.classList.toggle("active", item === button);
      });
      document.querySelectorAll("[data-history-row]").forEach(row => {
        row.classList.toggle("hidden", channel !== "all" && row.dataset.historyRow !== channel);
      });
    });
  });

  setChannel(channelInput.value || "sms");
  setTarget(targetInput.value || "one");

  const SMS_STATUS_LABELS = {
    draft: "Ready to send",
    queued: "Queued",
    sending: "Sending…",
    accepted: "Sent",
    delivered: "Delivered",
    read: "Read",
    sent: "Sent",
    ready: "Ready to open",
    undelivered: "Not delivered",
    expired: "Expired",
    failed: "Failed",
    unknown: "Delivery unknown",
    simulated: "Test sent",
  };

  async function refreshSmsStatuses() {
    const rows = [...document.querySelectorAll('[data-history-row][data-message-id]')];
    if (!rows.length || document.hidden) return;
    const ids = rows.map(row => row.dataset.messageId).filter(Boolean);
    if (!ids.length) return;
    try {
      const response = await fetch("/api/communications/status/?ids=" + encodeURIComponent(ids.join(",")), {
        headers: {"Accept": "application/json"},
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const payload = await response.json();
      (payload.messages || []).forEach(item => {
        const status = document.querySelector('[data-sms-status="' + item.id + '"]');
        const error = document.querySelector('[data-sms-error="' + item.id + '"]');
        if (status) {
          status.textContent = SMS_STATUS_LABELS[item.status] || String(item.status || "").replaceAll("_", " ");
          status.dataset.deliveryState = item.status || "";
        }
        if (error) {
          error.textContent = item.last_error || "";
          error.classList.toggle("hidden", !item.last_error);
        }
      });
    } catch (_) {
      // Delivery tracking is best-effort in the browser; the server remains authoritative.
    }
  }

  refreshSmsStatuses();
  window.setInterval(refreshSmsStatuses, 4000);

})();
