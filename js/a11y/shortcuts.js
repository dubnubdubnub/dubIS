/* js/a11y/shortcuts.js — central keyboard-shortcut dispatcher. */
import { getShortcutPrefs } from '../store.js';

const mod = (e) => (e.ctrlKey || e.metaKey) && !e.altKey;

export function matchesRedo(e, redoPref) {
  if (!mod(e)) return false;
  const y = e.key === 'y' || e.key === 'Y';
  const shiftZ = e.shiftKey && (e.key === 'z' || e.key === 'Z');
  if (redoPref === 'ctrl-y') return y && !e.shiftKey;
  if (redoPref === 'ctrl-shift-z') return shiftZ;
  return (y && !e.shiftKey) || shiftZ; // both
}

function isUndo(e) { return mod(e) && !e.shiftKey && (e.key === 'z' || e.key === 'Z'); }

function typingTarget(e) {
  const t = e.target;
  return t instanceof Element && t.closest('input, textarea, select, [contenteditable="true"]');
}

export function initShortcuts(cmd) {
  document.addEventListener('keydown', (e) => {
    const prefs = getShortcutPrefs();

    // Global (work even while typing): Undo/Redo/Save/Preferences.
    if (isUndo(e)) { e.preventDefault(); cmd.undo(); return; }
    if (matchesRedo(e, prefs.redo)) { e.preventDefault(); cmd.redo(); return; }
    if (mod(e) && (e.key === 's' || e.key === 'S')) { e.preventDefault(); cmd.save(); return; }
    if (mod(e) && e.key === ',') { e.preventDefault(); cmd.openPreferences(); return; }
    if (mod(e) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); cmd.openPalette(); return; }
    if (mod(e) && (e.key === '1' || e.key === '2' || e.key === '3')) {
      e.preventDefault(); cmd.focusPanel(Number(e.key)); return;
    }

    // Context-sensitive: skip while typing.
    if (typingTarget(e)) return;
    if (e.key === 'Escape') { cmd.exitMode(); return; }     // does not preventDefault; modal Escape still runs
    if (e.key === '?' || e.key === 'F1') { e.preventDefault(); cmd.showHelp(); return; }
  });
}
