/* js/a11y/scrollable.js — make an overflow container keyboard-scrollable. */
import { getShortcutPrefs } from '../store.js';

const VIM = { h: 'ArrowLeft', j: 'ArrowDown', k: 'ArrowUp', l: 'ArrowRight' };

export function scrollDelta(key, clientHeight) {
  const page = Math.round(clientHeight * 0.9);
  switch (key) {
    case 'ArrowDown': return 40;
    case 'ArrowUp': return -40;
    case 'PageDown': return page;
    case 'PageUp': return -page;
    case 'Home': return -Infinity;
    case 'End': return Infinity;
    default: return null;
  }
}

export function makeScrollable(el) {
  if (!el || el.dataset.kbdScroll === '1') return;
  el.dataset.kbdScroll = '1';
  if (!el.hasAttribute('tabindex')) el.tabIndex = 0;
  if (!el.hasAttribute('role')) el.setAttribute('role', 'region');

  el.addEventListener('keydown', (e) => {
    // Only act when the region itself is focused, not a child control/grid cell.
    if (e.target !== el) return;
    let key = e.key;
    if (getShortcutPrefs().vimNav && VIM[key]) key = VIM[key];
    const d = scrollDelta(key, el.clientHeight);
    if (d === null) return;
    e.preventDefault();
    if (d === -Infinity) el.scrollTop = 0;
    else if (d === Infinity) el.scrollTop = el.scrollHeight;
    else el.scrollTop += d;
  });
}
