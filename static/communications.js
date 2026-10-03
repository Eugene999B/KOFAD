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
      sendHint.textContent = "SMS is queued immediately to Arkesel.";
    } else {
      body.maxLength = 1000;
      sendButton.textContent = "Prepare WhatsApp →";
      sendHint.textContent = "KOFAD prepares the chat and opens WhatsApp with your message ready.";
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
})();
