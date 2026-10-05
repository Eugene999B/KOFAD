document.addEventListener("DOMContentLoaded", () => {
  const body = document.body;
  const zone = body.dataset.sessionZone;
  const active = body.dataset.sessionActive === "true";
  if (!zone || !active) return;

  const logoutUrl = body.dataset.sessionLogoutUrl;
  const statusUrl = body.dataset.sessionStatusUrl;
  const loginUrl = body.dataset.sessionLoginUrl || "/";
  let leaving = false;
  let dialogOpen = false;
  let pendingLeave = null;

  const csrfToken = () => {
    const formToken = document.querySelector(
      'form[action="' + logoutUrl + '"] input[name="csrfmiddlewaretoken"]'
    )?.value;
    if (formToken) return formToken;
    const match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  };

  const isSameZone = href => {
    let url;
    try { url = new URL(href, window.location.href); } catch (_) { return true; }
    if (url.origin !== window.location.origin) return false;
    if (zone === "market") return url.pathname.startsWith("/market/");
    return (
      url.pathname !== "/" &&
      !url.pathname.startsWith("/market/") &&
      !url.pathname.startsWith("/login/") &&
      !url.pathname.startsWith("/forgot-password/")
    );
  };

  const referrerIsSameZone = (() => {
    if (!document.referrer) return false;
    try { return isSameZone(document.referrer); } catch (_) { return false; }
  })();

  const clearSensitiveClientState = () => {
    try {
      Object.keys(sessionStorage)
        .filter(key => key.startsWith("kofad-cart:") || key.startsWith("kofad-zone:"))
        .forEach(key => sessionStorage.removeItem(key));
      sessionStorage.setItem("kofad-zone-ended:" + zone, String(Date.now()));
    } catch (_) {}
  };

  const performLogout = async () => {
    if (leaving) return true;
    leaving = true;
    clearSensitiveClientState();
    try {
      const response = await fetch(logoutUrl, {
        method: "POST",
        credentials: "same-origin",
        keepalive: true,
        headers: {
          "X-CSRFToken": csrfToken(),
          "X-Requested-With": "XMLHttpRequest",
          "Accept": "application/json",
        },
      });
      return response.ok || response.redirected;
    } catch (_) {
      return false;
    }
  };

  const closeDialog = () => {
    const dialog = document.querySelector("[data-session-leave-dialog]");
    if (!dialog) return;
    dialog.hidden = true;
    document.body.classList.remove("session-leave-open");
    dialogOpen = false;
  };

  const createDialog = () => {
    let dialog = document.querySelector("[data-session-leave-dialog]");
    if (dialog) return dialog;
    dialog = document.createElement("div");
    dialog.className = "session-leave-dialog";
    dialog.dataset.sessionLeaveDialog = "";
    dialog.hidden = true;
    dialog.innerHTML = [
      '<div class="session-leave-backdrop"></div>',
      '<section class="session-leave-card" role="dialog" aria-modal="true" aria-labelledby="session-leave-title">',
      '<span class="session-leave-icon">↗</span>',
      '<p>' + (zone === "market" ? "KOFAD MARKET" : "KOFAD STAFF") + '</p>',
      '<h2 id="session-leave-title">Leave and sign out?</h2>',
      '<span class="session-leave-copy">Leaving this ' + (zone === "market" ? "customer account" : "staff workspace") + ' ends the signed-in session on this browser tab. Refreshing the page does not sign you out.</span>',
      '<div class="session-leave-actions">',
      '<button type="button" class="session-stay" data-session-stay>Stay signed in</button>',
      '<button type="button" class="session-confirm" data-session-confirm>Leave & sign out</button>',
      '</div></section>',
    ].join("");
    document.body.appendChild(dialog);

    dialog.querySelector("[data-session-stay]").addEventListener("click", () => {
      const action = pendingLeave;
      pendingLeave = null;
      closeDialog();
      if (action?.kind === "back") {
        history.pushState(
          { ...(history.state || {}), kofadZoneGuard: zone, kofadZonePath: location.pathname },
          "",
          location.href
        );
      }
    });

    dialog.querySelector(".session-leave-backdrop").addEventListener("click", () => {
      dialog.querySelector("[data-session-stay]").click();
    });

    dialog.querySelector("[data-session-confirm]").addEventListener("click", async event => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = "Signing out…";
      const action = pendingLeave;
      const signedOut = await performLogout();
      if (!signedOut) {
        leaving = false;
        button.disabled = false;
        button.textContent = "Leave & sign out";
        const copy = dialog.querySelector(".session-leave-copy");
        if (copy) copy.textContent = "We could not end the session yet. Check your connection and try again before leaving.";
        return;
      }
      closeDialog();
      if (action?.kind === "link" && action.href) {
        window.location.assign(action.href);
      } else if (action?.kind === "back") {
        history.back();
      } else {
        window.location.assign(loginUrl);
      }
    });
    return dialog;
  };

  const askToLeave = action => {
    if (dialogOpen || leaving) return;
    pendingLeave = action;
    const dialog = createDialog();
    dialog.hidden = false;
    document.body.classList.add("session-leave-open");
    dialogOpen = true;
    dialog.querySelector("[data-session-stay]")?.focus();
  };

  document.addEventListener("click", event => {
    const link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented) return;
    if (link.target === "_blank" || link.hasAttribute("download")) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const href = link.href;
    if (!href || href.startsWith("javascript:") || href.startsWith("#")) return;
    if (isSameZone(href)) return;
    event.preventDefault();
    askToLeave({kind: "link", href});
  });

  const guardState = {
    ...(history.state || {}),
    kofadZoneGuard: zone,
    kofadZonePath: location.pathname,
  };
  if (history.state?.kofadZoneGuard !== zone) {
    history.pushState(guardState, "", location.href);
  }

  window.addEventListener("popstate", () => {
    if (leaving) return;
    // Forwarding back onto this page's synthetic guard is still inside the zone.
    if (history.state?.kofadZoneGuard === zone) return;
    // If this page was reached from another page in the same authenticated zone,
    // the first Back only removes our synthetic guard. Continue to that prior page
    // without asking the user to sign out.
    if (referrerIsSameZone) {
      history.back();
      return;
    }
    // The page was entered from outside the zone (or directly), so the next Back
    // really leaves Market/Staff. Only this boundary needs confirmation.
    askToLeave({kind: "back"});
  });

  window.addEventListener("pageshow", async event => {
    if (!statusUrl) return;
    try {
      const response = await fetch(statusUrl, {
        credentials: "same-origin",
        cache: "no-store",
        headers: {"Accept": "application/json"},
      });
      const payload = response.ok ? await response.json() : {authenticated: false};
      if (!payload.authenticated) {
        clearSensitiveClientState();
        window.location.replace(loginUrl);
        return;
      }
      if (event.persisted && history.state?.kofadZoneGuard !== zone) {
        history.pushState(guardState, "", location.href);
      }
    } catch (_) {
      // A temporary connectivity issue should not destroy a valid local page.
    }
  });
});
