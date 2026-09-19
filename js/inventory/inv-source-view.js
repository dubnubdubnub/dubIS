// @ts-check
/* inv-source-view.js — the DOM half of merged-view provenance.

   Two jobs, both of which exist because a merged inventory row looks EXACTLY
   like an ordinary one unless something says otherwise:

   1. The per-source breakdown that a multi-source row's badge expands. It is a
      SIBLING element inserted after the row, not a child of it: `.inv-part-row`
      is a flex row with `max-height: 64px; overflow: hidden`
      (css/panels/inventory.css), so anything nested inside it would be clipped
      to nothing rather than pushing the row taller.

   2. The banner that appears when the last merged fetch could not reach a
      server. The fan-out degrades rather than fails (server/fanout.py), so a
      partial view is a 200 full of plausible-looking numbers that are simply
      missing a whole machine's stock — silently under-reporting is the worst
      thing this feature could do.

   Every decision lives in js/inventory/inv-source-logic.js; this file only puts
   the answers on screen, the same split js/server-tabs.js has with
   js/server-tabs-logic.js.

   The insertion is a POST-RENDER PASS rather than something `createPartRow`
   does, and that is load-bearing: rows are appended from seven different places
   (inv-tree-render.js's four paths, vendor piles, groups view, BOM mode), and a
   builder cannot insert a sibling for a row that has not been appended to
   anything yet. One pass over the finished body — exactly what
   inv-import-markers.js already does — touches one call site instead of seven.
*/

import { escHtml } from '../ui-helpers.js';
import { store } from '../store.js';
import { invPartKey } from '../part-keys.js';
import { sourceStatusSignal, effect } from '../signals.js';
import state from './inv-state.js';
import { sourceEntries, partialViewNote } from './inv-source-logic.js';

// ── Per-source breakdown ──────────────────────────────────

/**
 * Open or close one row's per-source breakdown.
 *
 * Flips the state and asks for a re-render rather than poking the DOM directly,
 * which is how `expandedAlts` / `expandedMembers` / `expandedGroups` all behave
 * — the expander's own `aria-expanded` and chevron are rendered from this set,
 * so a direct insertion would leave the badge claiming the opposite of what is
 * on screen.
 *
 * @param {string} partKey invPartKey of the row
 */
export function toggleSourceBreakdown(partKey) {
  if (!partKey) return;
  if (state.expandedSources.has(partKey)) state.expandedSources.delete(partKey);
  else state.expandedSources.add(partKey);
  if (state._render) state._render();
}

/** @param {Array<{id: string, name: string, qty: number}>} entries */
function breakdownHtml(entries) {
  return entries.map(function (s) {
    return '<span class="inv-source-line">' +
      '<span class="inv-source-line-name">' + escHtml(s.name) + '</span>' +
      '<span class="inv-source-line-qty">' + s.qty + '</span>' +
      '</span>';
  }).join('');
}

/**
 * Insert a breakdown element after every expanded row. Called once per render,
 * from inventory-panel.js, after the tree is built.
 *
 * Reads the item back out of `store.inventory` by part key rather than holding
 * a reference from render time: the merge guarantees one row per part key, so
 * the lookup is exact, and it keeps this module out of the row builder's way.
 */
export function refreshSourceBreakdowns() {
  if (!state.body) return;
  // Nothing expanded is the overwhelmingly common case (every single-server
  // view, always) — leave without touching the DOM at all.
  if (!state.expandedSources.size) return;

  /** @type {Map<string, any>} */
  const byKey = new Map();
  for (const item of store.inventory || []) {
    const key = invPartKey(item);
    if (key && !byKey.has(key)) byKey.set(key, item);
  }

  const rows = state.body.querySelectorAll('.inv-part-row');
  for (const row of rows) {
    const key = /** @type {HTMLElement} */ (row).dataset.partId || '';
    if (!state.expandedSources.has(key)) continue;
    const entries = sourceEntries(byKey.get(key));
    // An expanded key whose row is no longer multi-source (a refresh dropped a
    // server, or the view is no longer merged) simply renders nothing. The set
    // is left alone on purpose: switching back to All must restore what the
    // user had open, and forgetting it here would make the expander feel like
    // it resets itself at random.
    if (entries.length < 2) continue;
    const el = document.createElement('div');
    el.className = 'inv-source-breakdown';
    el.dataset.partKey = key;
    el.innerHTML = breakdownHtml(entries);
    row.insertAdjacentElement('afterend', el);
  }
}

// ── "These totals are incomplete" ─────────────────────────

/**
 * Bind the partial-view banner to the last fetch's per-source status.
 *
 * An effect on `sourceStatusSignal` (js/signals.js) rather than an EventBus
 * listener, for the reason every signal in this app exists: the status is
 * cross-panel *state* that a panel mounting after the fetch still has to see,
 * and a listener that was not subscribed when a message fired never learns it
 * fired.
 *
 * The banner lives between the panel header and the scrolling body, so it stays
 * on screen while the user scrolls the rows whose totals it is qualifying — and
 * it is `hidden` whenever nothing failed, which is every single-server view, so
 * it costs the panels no height at all in the normal case.
 */
export function initSourceBanner() {
  const host = document.getElementById('inv-source-banner');
  if (!host) return;
  effect(function () {
    const note = partialViewNote(sourceStatusSignal.get());
    host.classList.toggle('hidden', !note.degraded);
    if (!note.degraded) {
      host.textContent = '';
      return;
    }
    // The per-source error is in the tooltip rather than the sentence: what the
    // user needs at a glance is "these numbers are missing a server", not
    // "ConnectError: [Errno 61] Connection refused".
    host.textContent = '⚠ ' + note.message;
    host.title = note.failed
      .map(function (f) { return f.name + ': ' + (f.error || 'no answer'); })
      .join('\n');
  });
}
