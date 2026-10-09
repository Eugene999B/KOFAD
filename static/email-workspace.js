/* Progressive enhancement for the KOFAD owner mailbox-permission matrix. */
document.addEventListener('DOMContentLoaded', () => {
  const form = document.querySelector('.mail-bulk-form');
  if (!form) return;
  const readBoxes = Array.from(form.querySelectorAll('input[name="read_mailboxes"]'));
  const replyBoxes = Array.from(form.querySelectorAll('input[name="send_mailboxes"]'));
  const count = form.querySelector('[data-permission-count]');
  const reads = new Map(readBoxes.map(node => [node.value, node]));
  const replies = new Map(replyBoxes.map(node => [node.value, node]));
  const update = () => {
    const readable = readBoxes.filter(box => box.checked && !box.disabled).length;
    const replyable = replyBoxes.filter(box => box.checked && !box.disabled).length;
    if (count) count.textContent = readable + ' readable · ' + replyable + ' reply-enabled';
  };
  for (const box of replyBoxes) {
    box.addEventListener('change', () => {
      if (box.checked) reads.get(box.value).checked = true;
      update();
    });
  }
  for (const box of readBoxes) {
    box.addEventListener('change', () => {
      if (!box.checked) replies.get(box.value).checked = false;
      update();
    });
  }
  const all = form.querySelector('[data-mail-action="read-all"]');
  if (all) all.addEventListener('click', () => {
    readBoxes.forEach(box => { if (!box.disabled) box.checked = true; });
    update();
  });
  const clear = form.querySelector('[data-mail-action="clear"]');
  if (clear) clear.addEventListener('click', () => {
    [...readBoxes, ...replyBoxes].forEach(box => { box.checked = false; });
    update();
  });
  update();
});
