/* js/a11y/focus-trap.js — confine Tab within a modal and restore focus on close. */
const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(',');

let active = null;       // { el, prev, handler }

function visibleFocusables(el) {
  return Array.from(el.querySelectorAll(FOCUSABLE))
    .filter((n) => n.offsetParent !== null || n === document.activeElement);
}

export function trap(modalEl) {
  release();
  const prev = document.activeElement;
  const handler = (e) => {
    if (e.key !== 'Tab') return;
    const items = visibleFocusables(modalEl);
    if (!items.length) { e.preventDefault(); return; }
    const first = items[0], last = items[items.length - 1];
    const cur = document.activeElement;
    if (e.shiftKey && (cur === first || !modalEl.contains(cur))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (cur === last || !modalEl.contains(cur))) { e.preventDefault(); first.focus(); }
  };
  modalEl.addEventListener('keydown', handler);
  active = { el: modalEl, prev, handler };

  // Initial focus: [autofocus] -> first focusable -> the modal itself.
  // If focus is already inside the modal (e.g. an explicit .focus() was called
  // just before the rAF-deferred trap fires), honour it rather than overriding.
  if (!modalEl.contains(document.activeElement)) {
    const initial = modalEl.querySelector('[autofocus]') || visibleFocusables(modalEl)[0] || modalEl;
    if (initial === modalEl && !modalEl.hasAttribute('tabindex')) modalEl.tabIndex = -1;
    initial.focus();
  }
}

export function release() {
  if (!active) return;
  active.el.removeEventListener('keydown', active.handler);
  const { prev } = active;
  active = null;
  if (prev && typeof prev.focus === 'function') prev.focus();
}
