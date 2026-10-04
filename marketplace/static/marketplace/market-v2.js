document.addEventListener("DOMContentLoaded", () => {
  const escapeText = value => String(value ?? "");

  const buildAttachment = attachment => {
    const wrapper = document.createElement("a");
    wrapper.href = attachment.url;
    wrapper.className = attachment.is_image ? "support-image-attachment" : "support-file-attachment";
    if (attachment.is_image) {
      wrapper.target = "_blank";
      const img = document.createElement("img");
      img.src = attachment.preview_url;
      img.alt = escapeText(attachment.name);
      const label = document.createElement("span");
      label.textContent = attachment.name;
      wrapper.append(img, label);
    } else {
      const icon = document.createElement("b");
      icon.textContent = "⇩";
      const text = document.createElement("span");
      const name = document.createElement("strong");
      name.textContent = attachment.name;
      const type = document.createElement("small");
      type.textContent = attachment.mime;
      text.append(name, type);
      wrapper.append(icon, text);
    }
    return wrapper;
  };

  const buildMessage = message => {
    const article = document.createElement("article");
    article.className = "support-message " + message.sender;
    article.dataset.messageId = message.id;
    const bubble = document.createElement("div");
    bubble.className = "support-bubble";
    if (message.body) {
      const p = document.createElement("p");
      p.textContent = message.body;
      bubble.appendChild(p);
    }
    if (message.attachments?.length) {
      const attachments = document.createElement("div");
      attachments.className = "support-attachments";
      message.attachments.forEach(item => attachments.appendChild(buildAttachment(item)));
      bubble.appendChild(attachments);
    }
    const meta = document.createElement("small");
    meta.textContent = message.sender_label + " · " + message.created;
    article.append(bubble, meta);
    return article;
  };

  document.querySelectorAll("[data-live-thread]").forEach(stream => {
    const conversation = stream.dataset.conversation;
    if (!conversation) return;
    let last = Number(stream.dataset.lastMessage || 0);
    let timer = null;

    const nearBottom = () => stream.scrollHeight - stream.scrollTop - stream.clientHeight < 100;
    const scrollBottom = () => { stream.scrollTop = stream.scrollHeight; };
    scrollBottom();

    const poll = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch(
          "/market/support/conversations/" + conversation + "/updates/?after=" + last,
          {credentials: "same-origin", headers: {"Accept": "application/json"}}
        );
        if (!response.ok) return;
        const payload = await response.json();
        const shouldScroll = nearBottom();
        for (const message of payload.messages || []) {
          if (stream.querySelector('[data-message-id="' + message.id + '"]')) continue;
          stream.appendChild(buildMessage(message));
          last = Math.max(last, Number(message.id));
        }
        stream.dataset.lastMessage = String(last);
        if (shouldScroll) scrollBottom();
      } catch (_) {
        // Temporary network loss should never interrupt composing a message.
      }
    };
    const schedule = () => {
      window.clearInterval(timer);
      timer = window.setInterval(poll, 3500);
    };
    schedule();
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        poll();
        schedule();
      }
    });
  });

  document.querySelectorAll("[data-support-compose] input[type='file']").forEach(input => {
    input.addEventListener("change", () => {
      const label = input.closest(".support-attach-button");
      const text = label?.querySelector("span");
      if (text) {
        text.textContent = input.files?.[0]?.name
          ? input.files[0].name.slice(0, 28)
          : "Attach";
      }
    });
  });

  document.querySelectorAll(".commerce-product-card").forEach(card => {
    card.addEventListener("keydown", event => {
      if (event.key !== "Enter" || event.target.closest("a,button,input")) return;
      card.querySelector(".commerce-product-image")?.click();
    });
  });
});
