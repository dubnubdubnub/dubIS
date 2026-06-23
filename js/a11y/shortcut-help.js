/* js/a11y/shortcut-help.js — '?' / F1 overlay listing keyboard shortcuts. */
import { Modal, escHtml } from '../ui-helpers.js';
import { getShortcutPrefs } from '../store.js';

const ROWS = (redo) => [
  ['Ctrl+K', 'Command palette'],
  ['Ctrl+F', 'Focus search'],
  ['Ctrl+S', 'Save BOM'],
  ['Ctrl+Z', 'Undo'],
  [redo === 'ctrl-y' ? 'Ctrl+Y' : redo === 'ctrl-shift-z' ? 'Ctrl+Shift+Z' : 'Ctrl+Y or Ctrl+Shift+Z', 'Redo'],
  ['Ctrl+,', 'Preferences'],
  ['Ctrl+1 / 2 / 3', 'Focus Import / Inventory / BOM panel'],
  ['Arrows', 'Move between row buttons / scroll a focused region'],
  ['Enter', 'Confirm the open dialog'],
  ['Esc', 'Close dialog / exit linking or label mode'],
  ['? or F1', 'This help'],
];

export function initShortcutHelp() {
  const modal = Modal('help-modal', { cancelId: 'help-close' });
  function open() {
    const redo = getShortcutPrefs().redo;
    document.getElementById('help-body').innerHTML = ROWS(redo)
      .map(([k, d]) => `<div class="prefs-row"><label class="prefs-label">${escHtml(d)}</label><kbd>${escHtml(k)}</kbd></div>`)
      .join('');
    modal.open();
  }
  return { open };
}
