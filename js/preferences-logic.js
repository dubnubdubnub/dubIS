// @ts-check
/* js/preferences-logic.js — pure preference-key classification.

   Which preference keys make something on screen change, and what. No DOM and
   no store import, so the whole table is unit-testable and vitest never loads
   the app's module graph — the sibling of js/panel-collapse-logic.js, whose
   REOPEN_TRIGGERS table is guarded by tests/js/panel-collapse-events.test.js.

   Why a table exists at all: `preferencesSignal` used to be `.set()` with the
   SAME object every writer had just mutated in place, so js/signals.js's
   identity guard (`if (_value === v) return;`) short-circuited every publish and
   the grid's one subscriber never ran. Making the publish real (see
   `publishPreferences` in js/store.js) means a save now notifies — which raises
   the question this table answers: which saves should repaint, and which must
   not. Repainting the whole grid for `server_tabs`, or re-planning a cart for a
   keystroke in a text box, is a real cost paid for a key that cannot change a
   row.

   The vocabulary:

   - `repaints`  — the inventory grid re-renders. Only for a value baked into
                   row HTML at build time, where nothing else would notice.
   - `replans`   — the active cart's purchase plan is re-fetched. For a value
                   handed to the planner as an argument.
   - `lazy-read` — nothing re-runs; the value is read at the moment it is used,
                   so the next use already sees the new one.
   - `none`      — nothing re-runs *because the writer already applied it*: the
                   actor that changed the key owns its own DOM/CSS state, or the
                   key is consumed by whoever fetches next.

   `lazy-read` and `none` look identical from the outside and are not the same
   claim: "lazy-read" says a later read picks the new value up; "none" says the
   change is already on screen. Recording the wrong one hides nothing today and
   misleads whoever wires the next subscriber, which is why the test asserts the
   reaction per key rather than merely requiring a key to appear. */

/** @typedef {'repaints' | 'replans' | 'lazy-read' | 'none'} Reaction */

/** The complete reaction vocabulary. `PREFERENCE_REACTIONS` may only use these. */
export const REACTIONS = Object.freeze(/** @type {Reaction[]} */ ([
  'repaints', 'replans', 'lazy-read', 'none',
]));

/**
 * Who performs each reaction, so a claim in the table names a real subscriber
 * rather than an intention. Guarded by tests/js/preference-keys.test.js: a
 * reaction whose owner no longer exists is a lie about behaviour.
 * @type {Record<Reaction, string>}
 */
export const REACTION_OWNERS = Object.freeze({
  repaints: 'js/inventory/inv-events.js — effect on preferencesSignal → render()',
  replans: 'js/cart/cart-plan-store.js — effect on preferencesSignal → schedulePlanRefresh()',
  'lazy-read': 'nobody: the value is read at the moment it is used',
  none: 'nobody: the actor that wrote the key applied its own state',
});

/**
 * Every preference key, and what a change to it does.
 *
 * Keys are dotted paths. A top-level entry covers a whole slice; a nested path
 * overrides it for one member of that slice, which is how `behavior` gets two
 * different answers for two members of one object. Lookup walks up from the
 * changed path to its ancestors, so a key whose *children* are data-driven
 * (`thresholds`, one entry per section of SECTION_ORDER) needs only the parent
 * entry.
 *
 * Adding a preference key means adding a row here: tests/js/preference-keys.test.js
 * enumerates the keys the app actually reads and writes and fails on a key with
 * no row, exactly as the REOPEN_TRIGGERS test does for EventBus triggers.
 * @type {Record<string, Reaction>}
 */
export const PREFERENCE_REACTIONS = Object.freeze(/** @type {Record<string, Reaction>} */ ({
  // ── repaints ────────────────────────────────────────────
  // The per-category stock-value threshold is read per row at build time
  // (js/inventory/inv-row-build.js passes getThreshold(sectionKey) into
  // renderPartRowHtml, which bakes the colour into the row's inline style at
  // js/inventory/inv-html-builders.js). Once the HTML exists, nothing re-reads
  // it: a slider move is invisible until something else re-renders. That is the
  // user-visible bug this row exists to fix.
  thresholds: 'repaints',
  // The chosen preset moves the inherited value of every category the bundle
  // names, so it is baked into row HTML exactly like a slider value — without
  // this row, picking a preset would leave the grid coloured by the old one
  // until something else re-rendered.
  threshold_preset: 'repaints',
  // Which ROWS are in the grid is decided by this toggle (js/inventory/
  // inv-tree-render.js `categoryHit()` → filterByQuery, and js/bom-row-data.js
  // for the BOM table). It is read at render time and nothing else re-renders:
  // flip it with a category name still in the search box and, without a repaint,
  // the grid keeps showing the pre-toggle rows and the toggle looks broken. Same
  // shape as `thresholds` — a value baked into which rows exist and what their
  // HTML says — so `repaints`, not `lazy-read`.
  'behavior.categorySearch': 'repaints',

  // ── replans ─────────────────────────────────────────────
  // The reel ceiling is an ARGUMENT to the planner — cart-plan-store.js sends it
  // to `plan_cart` — so a saved ceiling leaves the on-screen plan recommending
  // reels the user has just said are too expensive. Re-fetch, don't repaint:
  // the grid does not show cart recommendations.
  'behavior.reelCeiling': 'replans',

  // ── lazy-read ───────────────────────────────────────────
  // Copy-on-select asks the store inside the copy handler (js/bom/bom-events.js
  // reads getBehaviorPrefs() when there is a selection), so the next copy uses
  // the new value with nothing to re-run.
  'behavior.autoCopySelection': 'lazy-read',
  // Parent fallback for `behavior`. Reached only by a member nobody classified —
  // which tests/js/preference-keys.test.js forbids by enumerating
  // BEHAVIOR_DEFAULTS — and it deliberately UNDER-claims (repaint nothing) for
  // such a member: the safe failure for an unknown preference is a stale pixel,
  // not a full grid rebuild on every keystroke.
  behavior: 'lazy-read',
  // All three shortcuts are read inside the keydown handlers
  // (js/keyboard.js / js/command-palette.js via getShortcutPrefs()); there is no
  // rendered surface for a shortcut to go stale on.
  'shortcuts.redo': 'lazy-read',
  'shortcuts.enterSubmitsModals': 'lazy-read',
  'shortcuts.vimNav': 'lazy-read',
  // Parent fallback for `shortcuts`: reached only by a member nobody classified
  // (which the test forbids) or by a whole-object replace whose members are
  // unchanged (which produces no path at all). Kept honest: every member of
  // SHORTCUT_DEFAULTS is lazy-read, so the fallback cannot over-claim.
  shortcuts: 'lazy-read',

  // ── none ────────────────────────────────────────────────
  // Grouping / sort / vendor scope: written by `persistAndRender` in
  // js/inventory/inv-events.js, which calls render() in the same tick. A repaint
  // reaction here would double-render every column-header click.
  inventory_view: 'none',
  // Named views: the saved-views menu re-renders itself after save/delete/rename
  // (js/inventory/saved-views.js), and the grid's filters come from the applied
  // view, not from this array.
  saved_views: 'none',
  // The server roster: js/server-list.js re-renders its rows off the roster it
  // was told about, and js/server-tabs.js re-renders the strip via
  // activeSourceSignal (syncTabsWithRoster), which is a separate publish.
  servers: 'none',
  // Which server data comes from. Nothing repaints on this: the next FETCH reads
  // it, and the fetch is driven by switchActiveSource → scheduleInventoryRefresh,
  // which re-renders through INVENTORY_UPDATED. Repainting here would render the
  // OLD server's rows a second time before the new server's answer arrived.
  server_url: 'none',
  server_tabs: 'none',
  active_tab: 'none',
  active_source: 'none',
  // Root zoom: js/ui-zoom.js writes the --ui-zoom custom property and re-measures
  // in the same call that persisted the value.
  ui_zoom: 'none',
  // Collapsed panels: js/panel-collapse.js's render() applies the class list
  // before persist() is ever reached.
  panels_collapsed: 'none',
  // Column widths: js/col-resize.js's applyColWidths() writes the widths onto
  // the table it just measured.
  column_widths: 'none',
  // Remembered dialog paths: read by the next file dialog call
  // (js/bom/bom-panel.js, js/bom/bom-events.js). Nothing renders them.
  lastBomDir: 'none',
  lastBomFile: 'none',
  lastImportDir: 'none',
  // The OCR template dropdown: read when the import drop zone is (re)rendered
  // (js/import/import-panel.js init), and the `change` handler that writes it has
  // already updated the <select> — it is the element the user just operated.
  lastOcrTemplate: 'none',
}));

/**
 * The reaction for one changed preference path.
 *
 * Walks up from the exact path to its ancestors, so `thresholds.Capacitors >
 * MLCC` resolves through `thresholds`. Throws for a path with no classified
 * ancestor: an unclassified key is a programmer error, and per the repo's error
 * policy it is raised at the write rather than silently published to subscribers
 * that would have to guess. (A data-driven child can never reach the throw,
 * because its parent is classified.)
 *
 * @param {string} path a dotted preference path, e.g. "behavior.reelCeiling"
 * @returns {Reaction}
 */
export function reactionFor(path) {
  if (typeof path !== 'string' || !path) {
    throw new RangeError(`Not a preference path: ${String(path)}`);
  }
  let candidate = path;
  for (;;) {
    const reaction = /** @type {Record<string, Reaction>} */ (PREFERENCE_REACTIONS)[candidate];
    if (reaction !== undefined) return reaction;
    const cut = candidate.lastIndexOf('.');
    if (cut <= 0) {
      throw new RangeError(
        `Preference key "${candidate}" has no entry in PREFERENCE_REACTIONS. `
        + 'Classify it as one of: ' + REACTIONS.join(' | ')
        + ' — see js/preferences-logic.js.',
      );
    }
    candidate = candidate.slice(0, cut);
  }
}

/**
 * The distinct reactions a set of changed paths triggers, in REACTIONS order.
 * @param {Iterable<string>} paths
 * @returns {Reaction[]}
 */
export function reactionsFor(paths) {
  /** @type {Set<Reaction>} */
  const seen = new Set();
  for (const p of paths) seen.add(reactionFor(p));
  return REACTIONS.filter(r => seen.has(r));
}

/** Does anything on the inventory grid need re-rendering? @param {Iterable<string>} paths */
export function repaintsGrid(paths) {
  return reactionsFor(paths).includes('repaints');
}

/** Does the active cart's purchase plan need re-fetching? @param {Iterable<string>} paths */
export function replansCart(paths) {
  return reactionsFor(paths).includes('replans');
}

/** @param {any} v */
function isPlainObject(v) {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

/**
 * Value equality for preference leaves. Preference values are JSON-serialisable
 * by definition — `savePreferences()` stringifies the whole bag on every write —
 * so a string compare is exact here, and it is the only compare that sees an
 * in-place mutation of a nested object (which every writer in store.js does).
 * @param {any} a
 * @param {any} b
 */
function sameValue(a, b) {
  if (a === b) return true;
  if (a === undefined || b === undefined) return false;
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * A deep, comparable copy of a preferences bag: the baseline a later diff runs
 * against. Must be a deep copy, because the writers mutate the live bag in place
 * — a shallow copy would mutate along with it and report "nothing changed".
 * @param {any} prefs
 * @returns {any}
 */
export function snapshotPreferences(prefs) {
  return prefs === undefined ? undefined : JSON.parse(JSON.stringify(prefs));
}

/**
 * @param {any} prev
 * @param {any} next
 * @param {string} prefix
 * @param {string[]} out
 */
function walk(prev, next, prefix, out) {
  if (isPlainObject(prev) && isPlainObject(next)) {
    const keys = new Set([...Object.keys(prev), ...Object.keys(next)]);
    for (const key of keys) {
      const path = prefix ? `${prefix}.${key}` : key;
      const a = prev[key];
      const b = next[key];
      if (isPlainObject(a) && isPlainObject(b)) {
        walk(a, b, path, out);
      } else if (!sameValue(a, b)) {
        out.push(path);
      }
    }
    return;
  }
  if (!sameValue(prev, next) && prefix) out.push(prefix);
}

/**
 * Which preference paths differ between two bags.
 *
 * Plain objects recurse (so `behavior.reelCeiling` is reported, not `behavior`);
 * arrays and scalars are leaves (so reordering `server_tabs` is one path, not
 * N). A key present on one side only is a path. Order follows key insertion
 * order of the two bags, which is stable enough to assert against but is not
 * part of the contract.
 *
 * @param {any} prev the bag as last published
 * @param {any} next the bag as it is now
 * @returns {string[]} dotted paths, empty when nothing moved
 */
export function changedPreferencePaths(prev, next) {
  /** @type {string[]} */
  const out = [];
  walk(prev === undefined ? {} : prev, next === undefined ? {} : next, '', out);
  return out;
}
