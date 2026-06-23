// @ts-check
/**
 * js/components/command-palette.js — Linear-style Ctrl+K command palette.
 *
 * Exports:
 *   fuzzyScore(query, text)          → number  (0 = no match)
 *   rankCommands(query, commands)    → filtered+sorted Command[]
 *   CommandPalette({ getCommands })  → { open(context), close(), isOpen() }
 *
 * Command = { id, label, hint?, group?, keywords?:string[], run():void|Promise }
 */

import { escapeHtml } from '../dom/html.js';
import { on } from '../dom/delegate.js';
import { trap, release } from '../a11y/focus-trap.js';
import { AppLog } from '../api.js';

// ── Pure fuzzy matcher ────────────────────────────────────────────────────────

/**
 * Compute a fuzzy match score for `query` against `text`.
 * Returns 0 if there is no subsequence match; otherwise a positive number.
 * Higher scores mean better matches. Bonuses:
 *   - contiguous run of matched characters
 *   - word-boundary (matched char is at start of a word)
 *   - prefix (first matched char is at position 0)
 *
 * @param {string} query
 * @param {string} text
 * @returns {number}
 */
export function fuzzyScore(query, text) {
  if (!query || !text) return 0;

  const q = query.toLowerCase();
  const t = text.toLowerCase();

  let qi = 0;
  let score = 0;
  let lastMatchIdx = -1;
  let contiguousRun = 0;

  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] !== q[qi]) {
      contiguousRun = 0;
      continue;
    }

    // Subsequence match found at position ti
    let bonus = 10; // base per-char bonus

    // Contiguity bonus: reward runs of consecutive matched characters
    if (lastMatchIdx === ti - 1) {
      contiguousRun++;
      bonus += 5 * contiguousRun;
    } else {
      contiguousRun = 0;
    }

    // Word-boundary bonus: matched character is at start of a word
    if (ti === 0 || t[ti - 1] === ' ' || t[ti - 1] === '-' || t[ti - 1] === '_') {
      bonus += 8;
    }

    // Prefix bonus: very first match is at position 0
    if (ti === 0 && qi === 0) {
      bonus += 12;
    }

    score += bonus;
    lastMatchIdx = ti;
    qi++;
  }

  // Must consume the entire query (all characters matched in order)
  if (qi < q.length) return 0;

  return score;
}

/**
 * Filter and sort commands by fuzzy relevance to `query`.
 * Empty query → return all commands in original order.
 * Matches against label and keywords.
 *
 * @param {string} query
 * @param {Array<{id:string, label:string, keywords?:string[], run:Function}>} commands
 * @returns {Array<{id:string, label:string, keywords?:string[], run:Function}>}
 */
export function rankCommands(query, commands) {
  if (!query) return commands.slice();

  /** @type {Array<{cmd: object, score: number}>} */
  const scored = [];

  for (const cmd of commands) {
    const labelScore = fuzzyScore(query, cmd.label);
    let kwScore = 0;
    if (cmd.keywords) {
      for (const kw of cmd.keywords) {
        const s = fuzzyScore(query, kw);
        if (s > kwScore) kwScore = s;
      }
    }
    // Label match takes priority; keyword match is treated as a weaker signal
    const best = labelScore > 0 ? labelScore : kwScore * 0.7;
    if (best > 0) scored.push({ cmd, score: best });
  }

  // Sort descending by score
  scored.sort((a, b) => b.score - a.score);
  return scored.map(s => s.cmd);
}

// ── CommandPalette ────────────────────────────────────────────────────────────

/**
 * Create a command palette instance.
 *
 * @param {{ getCommands: (context: object) => Array<{id:string,label:string,hint?:string,group?:string,keywords?:string[],run:Function}> }} opts
 * @returns {{ open(context: object): void, close(): void, isOpen(): boolean }}
 */
export function CommandPalette({ getCommands }) {
  /** @type {HTMLElement|null} */
  let overlay = null;
  /** @type {Array<object>} */
  let visibleCmds = [];
  /** @type {number} */
  let activeIdx = -1;
  /** @type {Array<() => void>} */
  let removers = [];

  function isOpen() {
    return overlay !== null;
  }

  function close() {
    if (!overlay) return;
    release();
    overlay.remove();
    overlay = null;
    visibleCmds = [];
    activeIdx = -1;
    for (const r of removers) r();
    removers = [];
  }

  /**
   * Mark item at index `idx` as active (add cp-active class, remove from others).
   * @param {number} idx
   */
  function setActive(idx) {
    if (!overlay) return;
    activeIdx = idx;
    const items = overlay.querySelectorAll('.cp-item');
    items.forEach((el, i) => {
      el.classList.toggle('cp-active', i === idx);
    });
    // Scroll into view if needed
    if (idx >= 0 && idx < items.length && typeof items[idx].scrollIntoView === 'function') {
      items[idx].scrollIntoView({ block: 'nearest' });
    }
  }

  /**
   * Render the list of commands into the results container.
   * @param {Array<object>} cmds
   */
  function renderList(cmds) {
    if (!overlay) return;
    const list = overlay.querySelector('.cp-results');
    if (!list) return;

    visibleCmds = cmds;
    activeIdx = -1;

    // Group commands
    /** @type {Map<string, Array<object>>} */
    const groups = new Map();
    for (const cmd of cmds) {
      const g = cmd.group || '';
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(cmd);
    }

    let html = '';
    for (const [groupName, groupCmds] of groups) {
      if (groupName) {
        html += `<div class="cp-group">${escapeHtml(groupName)}</div>`;
      }
      for (const cmd of groupCmds) {
        const hint = cmd.hint ? `<span class="cp-hint">${escapeHtml(cmd.hint)}</span>` : '';
        html += `<div class="cp-item" data-cmd-id="${escapeHtml(cmd.id)}" role="option" aria-selected="false">${escapeHtml(cmd.label)}${hint}</div>`;
      }
    }

    list.innerHTML = html;
  }

  /**
   * Run the command at `activeIdx`.
   */
  function runActive() {
    if (activeIdx < 0 || activeIdx >= visibleCmds.length) return;
    const cmd = visibleCmds[activeIdx];
    close();
    try {
      const result = cmd.run();
      if (result && typeof result.then === 'function') {
        result.then(undefined, (err) => {
          AppLog.error('CommandPalette: command "' + cmd.label + '" failed: ' + (err && err.message || err));
        });
      }
    } catch (err) {
      AppLog.error('CommandPalette: command "' + cmd.label + '" failed: ' + (err && err.message || err));
    }
  }

  /**
   * Run the command identified by `cmdId`.
   * @param {string} cmdId
   */
  function runById(cmdId) {
    const cmd = visibleCmds.find(c => c.id === cmdId);
    if (!cmd) return;
    const idx = visibleCmds.indexOf(cmd);
    activeIdx = idx;
    runActive();
  }

  /**
   * Open the palette with the given context object.
   * Re-opening rebuilds from fresh getCommands(context).
   * @param {object} context
   */
  function open(context) {
    // Close any existing palette first
    if (overlay) close();

    const allCmds = getCommands(context);

    // Build overlay element
    overlay = document.createElement('div');
    overlay.className = 'cp-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Command palette');

    overlay.innerHTML = `
      <div class="cp-dialog">
        <div class="cp-search-wrap">
          <input
            class="cp-search"
            type="text"
            placeholder="Type a command…"
            autocomplete="off"
            spellcheck="false"
            autofocus
            aria-autocomplete="list"
            role="combobox"
            aria-expanded="true"
          >
        </div>
        <div class="cp-results" role="listbox"></div>
      </div>
    `;

    document.body.appendChild(overlay);
    renderList(allCmds);

    const searchInput = /** @type {HTMLInputElement} */ (overlay.querySelector('.cp-search'));

    // Focus input
    searchInput.focus();

    // Trap focus inside the palette
    trap(overlay);

    // ── Event wiring ──────────────────────────────────────────────────────────

    // Typing filters the list
    const onInput = () => {
      const q = searchInput.value;
      const filtered = rankCommands(q, allCmds);
      renderList(filtered);
    };
    searchInput.addEventListener('input', onInput);
    removers.push(() => searchInput.removeEventListener('input', onInput));

    // Keyboard navigation on the overlay
    const onKeydown = (/** @type {KeyboardEvent} */ e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        close();
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        const nextIdx = activeIdx < visibleCmds.length - 1 ? activeIdx + 1 : 0;
        setActive(nextIdx);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        const prevIdx = activeIdx > 0 ? activeIdx - 1 : visibleCmds.length - 1;
        setActive(prevIdx);
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        e.stopPropagation();
        runActive();
        return;
      }
    };
    overlay.addEventListener('keydown', onKeydown);
    removers.push(() => overlay && overlay.removeEventListener('keydown', onKeydown));

    // Click-outside closes
    const onMousedown = (/** @type {MouseEvent} */ e) => {
      const dialog = overlay && overlay.querySelector('.cp-dialog');
      if (dialog && !dialog.contains(/** @type {Node} */ (e.target))) {
        close();
      }
    };
    overlay.addEventListener('mousedown', onMousedown);
    removers.push(() => overlay && overlay.removeEventListener('mousedown', onMousedown));

    // Delegated click on items — run command
    const removeClickDelegation = on(overlay, 'click', '.cp-item', (_e, el) => {
      const id = /** @type {HTMLElement} */ (el).dataset.cmdId;
      if (id) runById(id);
    });
    removers.push(removeClickDelegation);

    // Delegated mouseover on items — set active
    const removeHoverDelegation = on(overlay, 'mouseover', '.cp-item', (_e, el) => {
      const id = /** @type {HTMLElement} */ (el).dataset.cmdId;
      const idx = visibleCmds.findIndex(c => c.id === id);
      if (idx >= 0) setActive(idx);
    });
    removers.push(removeHoverDelegation);
  }

  return { open, close, isOpen };
}
