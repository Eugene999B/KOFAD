document.addEventListener("DOMContentLoaded", () => {
  const search = document.querySelector("[data-help-search]");
  if (!search) return;
  const input = search.querySelector("input");
  const clear = search.querySelector("[data-help-clear]");
  const answers = Array.from(document.querySelectorAll("[data-help-answer]"));
  const result = document.querySelector("[data-help-result]");
  const empty = document.querySelector("[data-help-empty]");
  const update = () => {
    const words = input.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    let count = 0;
    answers.forEach(answer => {
      const text = answer.textContent.toLocaleLowerCase();
      const match = words.every(word => text.includes(word));
      answer.hidden = !match;
      answer.open = match && words.length > 0;
      if (match) count += 1;
    });
    result.textContent = words.length ? count + (count === 1 ? " answer found" : " answers found") : "";
    empty.hidden = count > 0;
  };
  search.hidden = false;
  input.addEventListener("input", update);
  clear.addEventListener("click", () => { input.value = ""; update(); input.focus(); });
});
