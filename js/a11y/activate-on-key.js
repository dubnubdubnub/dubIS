/* js/a11y/activate-on-key.js — make a clickable non-button keyboard-activatable. */
export function activateOnKey(el) {
  if (!el || el.dataset.kbdActivate === '1') return;
  el.dataset.kbdActivate = '1';
  if (!el.hasAttribute('tabindex')) el.tabIndex = 0;
  if (el.tagName !== 'BUTTON' && !el.hasAttribute('role')) el.setAttribute('role', 'button');
  el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
      e.preventDefault();
      el.click();
    }
  });
}
