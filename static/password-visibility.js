"use strict";
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll('input[type="password"]').forEach((input, index) => {
    if (input.dataset.kfEyeReady) return;
    input.dataset.kfEyeReady = "true";
    const field = document.createElement("span");
    field.className = "kf-password-eye-field";
    input.parentNode.insertBefore(field,input);
    field.append(input);
    if (!input.id) input.id = "kf-eye-" + index;
    const toggle = document.createElement("button");
    toggle.type="button";
    toggle.className="kf-password-eye-button";
    toggle.setAttribute("aria-controls",input.id);
    const eye = '<svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
    const closed = '<svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m3 3 18 18"/><path d="M9 5.5A10 10 0 0 1 12 5c6.4 0 10 7 10 7a13 13 0 0 1-3.3 4.1"/><path d="M6.4 6.5C3.6 8.5 2 12 2 12s3.6 7 10 7c1.4 0 2.6-.3 3.7-.8"/></svg>';
    const render=()=>{const show=input.type!=="password"; toggle.setAttribute("aria-label",show?"Hide password":"Show password");toggle.setAttribute("aria-pressed",String(show));toggle.title=show?"Hide password":"Show password";toggle.innerHTML=show?closed:eye;};
    toggle.addEventListener("click",()=>{input.type=input.type==="password"?"text":"password";render();});
    field.append(toggle);render();
    input.form?.addEventListener("submit",()=>{input.type="password";render();});
  });
});
