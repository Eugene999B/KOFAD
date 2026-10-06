(() => {
  document.querySelectorAll("[data-receipt-channels]").forEach(form => {
    form.addEventListener("click", event => event.stopPropagation());
    form.addEventListener("keydown", event => event.stopPropagation());
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const status = form.querySelector("[role=status]");
      const channels = ["sms", "whatsapp"].filter(channel => form.elements[channel].checked);
      if (!channels.length) { status.textContent = "Choose SMS, WhatsApp, or both."; return; }
      const button = form.querySelector("button");
      button.disabled = true;
      const outcomes = [];
      for (const channel of channels) {
        status.textContent = "Sending " + channel.toUpperCase() + "…";
        try {
          const response = await fetch("/api/documents/" + form.dataset.document + "/send-sms/", {
            method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": form.elements.csrfmiddlewaretoken.value},
            body: JSON.stringify({channel})
          });
          const result = await response.json();
          outcomes.push(channel.toUpperCase() + ": " + (result.message || result.error || result.status));
        } catch (_) { outcomes.push(channel.toUpperCase() + ": result unavailable. Check the outbox before retrying."); }
      }
      status.textContent = outcomes.join(" ");
      button.disabled = false;
    });
  });
})();